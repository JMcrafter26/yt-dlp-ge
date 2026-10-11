"""JS runtime bridge: builds harnesses, runs them via deno→node→bun→quickjs.

The global-extractor JS files are not bundled. This module wraps each fetched
JS extractor in a small generated harness that:

1. Reads a JSON payload ``{"html": ..., "url": ...}`` from stdin.
2. Provides a ``fetch`` shim for quickjs (calls back to Python).
3. Invokes the extractor's ``<name>Extractor(html, url)`` function.
4. Prints the result as JSON after a ``___GE_RESULT___`` sentinel on stdout.

The harness is executed via the first available supported runtime in the
configured chain. Each runtime gets its own generated file (``.mjs``/``.cjs``)
so module syntax is always correct.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ._config import Config, LayeredConfig
from ._errors import (
    DebugContext,
    ExtractionError,
    JSFetchError,
    JSRuntimeError,
    RuntimeAttempt,
)
from ._fetcher import FetchedJS, JsFetcher

SENTINEL = '___GE_RESULT___'

# Minimum supported runtime versions (mirrors yt-dlp's _jsruntime detection).
RUNTIME_CHAIN = ('deno', 'node', 'bun', 'quickjs')
MIN_VERSIONS = {
    'deno': (2, 3, 0),
    'node': (22, 0, 0),
    'bun': (1, 2, 11),
    'quickjs': (2023, 12, 9),
}


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

@dataclass
class RuntimeInfo:
    name: str
    path: str
    version: str
    ext: str  # harness file extension for this runtime


def _parse_version(text: str, pattern: str) -> tuple[int, ...]:
    m = re.search(pattern, text)
    if not m:
        return (0,)
    parts = []
    for piece in m.group(1).split('.'):
        num = ''
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _detect_runtime(name: str) -> RuntimeInfo | None:
    exe = shutil.which(name if name != 'quickjs' else 'qjs')
    if not exe:
        return None
    try:
        if name == 'deno':
            out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=5)
            version = _parse_version(out.stdout, r'^deno (\S+)')
            if version >= MIN_VERSIONS['deno']:
                return RuntimeInfo('deno', exe, '.'.join(map(str, version)), '.mjs')
        elif name == 'node':
            out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=5)
            version = _parse_version(out.stdout, r'^v(\S+)')
            if version >= MIN_VERSIONS['node']:
                return RuntimeInfo('node', exe, '.'.join(map(str, version)), '.mjs')
        elif name == 'bun':
            out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=5)
            version = _parse_version(out.stdout, r'^(\S+)')
            if version >= MIN_VERSIONS['bun']:
                return RuntimeInfo('bun', exe, '.'.join(map(str, version)), '.mjs')
        elif name == 'quickjs':
            # qjs has no --version; --help exits 1 but prints version info.
            out = subprocess.run([exe, '--help'], capture_output=True, text=True, timeout=5)
            combined = out.stdout + out.stderr
            is_ng = 'QuickJS-ng' in combined
            version = _parse_version(combined, r'^QuickJS(?:-ng)?\s+version\s+(\S+)')
            if is_ng or version >= MIN_VERSIONS['quickjs']:
                label = 'quickjs-ng' if is_ng else 'quickjs'
                return RuntimeInfo(label, exe, '.'.join(map(str, version)), '.mjs')
    except (subprocess.TimeoutExpired, OSError):
        return None
    return None


def detect_runtimes(override: str = 'auto') -> list[RuntimeInfo]:
    """Return supported runtimes in chain order, optionally filtered to one."""
    override = (override or 'auto').lower()
    if override == 'auto':
        names = list(RUNTIME_CHAIN)
    else:
        if override not in RUNTIME_CHAIN:
            raise JSRuntimeError(
                f'unknown js_runtime: {override!r}',
                solutions=[f'use one of: {", ".join(RUNTIME_CHAIN)}, or auto'],
            )
        names = [override]
    runtimes = []
    for name in names:
        info = _detect_runtime(name)
        if info:
            runtimes.append(info)
    return runtimes


# ---------------------------------------------------------------------------
# Harness generation
# ---------------------------------------------------------------------------

# Stdin-reading preamble per runtime. The payload is a JSON object
# {"html": ..., "url": ...} written to stdin by the Python side.
_STDIN_PREAMBLE = {
    'deno': (
        'const __payload = JSON.parse(await new Response(Deno.stdin.readable).text());\n'
    ),
    'node': (
        "import { readFileSync } from 'node:fs';\n"
        "const __payload = JSON.parse(readFileSync(0, 'utf8'));\n"
    ),
    'bun': (
        'const __payload = JSON.parse(await Bun.stdin.text());\n'
    ),
    'quickjs': (
        "import * as std from 'std';\n"
        "const __payload = JSON.parse(std.in.readAsString(16777216));\n"
    ),
}

# Python-backed fetch shim for quickjs (which has no network stack).
# The shim shells out to a small Python helper that performs the HTTP request.
_QUICKJS_FETCH_SHIM = r'''
import * as os from 'os';

function geFetch(url, options) {
    options = options || {};
    const method = (options.method || 'GET').toUpperCase();
    const headers = options.headers || {};
    const body = options.body;
    const req = JSON.stringify({ url: url, method: method, headers: headers, body: body });
    const helper = '__HELPER_PATH__';
    const arg = "'" + req.replace(/'/g, "'\\''") + "'";
    const cmd = 'python3 ' + JSON.stringify(helper) + ' ' + arg;
    try {
        const pipe = os.popen(cmd, 'r');
        const out = pipe.readAsString();
        pipe.close();
        const resp = JSON.parse(out);
        return Promise.resolve({
            ok: resp.status >= 200 && resp.status < 400,
            status: resp.status,
            url: resp.url || url,
            text: function() { return Promise.resolve(resp.body || ''); },
            json: function() { return Promise.resolve(JSON.parse(resp.body || 'null')); },
        });
    } catch (e) {
        return Promise.resolve({ ok: false, status: 0, url: url,
            text: function() { return Promise.resolve(''); },
            json: function() { return Promise.resolve(null); } });
    }
}
if (typeof fetch !== 'function') {
    var fetch = geFetch;
}

// Polyfills for engines missing these (quickjs < 2024).
if (typeof atob !== 'function') {
    function atob(input) {
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
        let str = String(input).replace(/=+$/, '');
        let output = '';
        for (let bc = 0, bs = 0, buffer, idx = 0; (buffer = str.charAt(idx++)); ) {
            let position = chars.indexOf(buffer);
            if (position === -1) continue;
            bs = bc % 4 ? bs * 64 + position : position;
            if (bc++ % 4) output += String.fromCharCode(255 & (bs >> ((-2 * bc) & 6)));
        }
        return output;
    }
}
if (typeof Promise.any !== 'function') {
    Promise.any = function(promises) {
        return new Promise(function(resolve, reject) {
            let pending = 0, errors = [], hasResolved = false;
            promises.forEach(function(p, i) {
                pending++;
                Promise.resolve(p).then(function(v) {
                    if (!hasResolved) { hasResolved = true; resolve(v); }
                }, function(e) {
                    errors[i] = e;
                    if (--pending === 0) reject(new AggregateError(errors, 'All promises were rejected'));
                });
            });
            if (pending === 0) reject(new AggregateError([], 'All promises were rejected'));
        });
    };
}
'''

# Runner epilogue: calls the extractor and prints the result after the sentinel.
_RUNNER = '''
(async () => {
  try {
    const __result = await __FN__(__payload.html, __payload.url);
    console.log('__SENTINEL__' + JSON.stringify({ ok: true, result: __result === undefined ? null : __result }));
  } catch (__e) {
    console.log('__SENTINEL__' + JSON.stringify({ ok: false, error: String((__e && __e.message) || __e) }));
  }
})();
'''


def _extract_scheme_block(js: str) -> str:
    """Return the content between /* SCHEME START */ and /* SCHEME END */."""
    m = re.search(r'/\*\s*SCHEME START\s*\*/(.*?)/\*\s*SCHEME END\s*\*/', js, re.DOTALL)
    if not m:
        raise ExtractionError('no SCHEME START/END block found in extractor JS')
    return m.group(1).strip()


def _extract_external_helpers(js: str, scheme: str) -> str:
    """Extract top-level helper functions (soraFetch, etc.) not in the scheme block."""
    helpers = []
    for name in ('soraFetch', 'fetchv2'):
        if re.search(rf'function\s+{name}\s*\(', scheme):
            continue  # already inside the scheme block
        m = re.search(rf'(async\s+)?function\s+{name}\s*\(', js)
        if not m:
            continue
        body_start = _find_body_brace(js, m.end() - 1)
        if body_start == -1:
            continue
        end = _match_brace(js, body_start)
        if end == -1:
            continue
        helpers.append(js[m.start():end + 1].strip())
    return '\n\n'.join(helpers)


def _find_body_brace(js: str, paren_open: int) -> int:
    """Given the index of a ``(``, return the index of the ``{`` opening the body.

    Balances parentheses first so default parameter values containing braces
    (e.g. ``options = { headers: {} }``) are skipped correctly.
    """
    depth = 0
    in_str = None
    i = paren_open
    while i < len(js):
        ch = js[i]
        if in_str:
            if ch == '\\':
                i += 1
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'", '`'):
            in_str = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                found = js.find('{', i)
                return found
        i += 1
    return -1


def _match_brace(js: str, brace_open: int) -> int:
    """Return the index of the ``}`` closing the brace at *brace_open*, or -1."""
    depth = 0
    in_str = None
    i = brace_open
    while i < len(js):
        ch = js[i]
        if in_str:
            if ch == '\\':
                i += 1
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'", '`'):
            in_str = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _detect_extractor_name(scheme: str, fallback: str) -> str:
    """Detect the extractor function name from the scheme block.

    The ``@name`` doc tag is a human-readable label (e.g. ``SmoothPre
    Extractor`` or ``streamTapeExtractor``) and often does *not* match the
    real function name, so actual ``function`` declarations take precedence.
    """
    candidates = re.findall(r'(?:async\s+)?function\s+(\w+)\s*\(', scheme)
    if candidates:
        m = re.search(r'@name\s+(\w+)', scheme)
        if m and m.group(1) in candidates:
            return m.group(1)
        wanted = f'{fallback.lower()}extractor'
        for cand in candidates:
            if cand.lower() == wanted:
                return cand
        for cand in candidates:
            if cand.lower().endswith('extractor'):
                return cand
        return candidates[0]
    m = re.search(r'@name\s+(\w+)', scheme)
    if m:
        return m.group(1)
    return f'{fallback}Extractor'


def build_harness(js: str, extractor_name: str, runtime: RuntimeInfo,
                  helper_path: str = '') -> str:
    """Build the full harness source for a given runtime."""
    scheme = _extract_scheme_block(js)
    helpers = _extract_external_helpers(js, scheme)
    fn_name = _detect_extractor_name(scheme, extractor_name)

    parts = []
    if runtime.name.startswith('quickjs'):
        shim = _QUICKJS_FETCH_SHIM.replace('__HELPER_PATH__', helper_path or 'python3')
        parts.append(shim)
    parts.append(scheme)
    if helpers:
        parts.append(helpers)
    # quickjs and quickjs-ng share the same stdin-reading preamble.
    parts.append(_STDIN_PREAMBLE.get(runtime.name, _STDIN_PREAMBLE['quickjs']))
    runner = _RUNNER.replace('__FN__', fn_name).replace('__SENTINEL__', SENTINEL)
    parts.append(runner)
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Harness execution
# ---------------------------------------------------------------------------

def _write_fetch_helper(dir_path: Path) -> str:
    """Write the Python fetch helper used by the quickjs fetch shim."""
    helper = dir_path / '_ge_fetch_helper.py'
    helper.write_text('''\
#!/usr/bin/env python3
"""HTTP fetch helper for the quickjs fetch shim (called back from JS)."""
import json
import sys
import urllib.error
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'


def main():
    try:
        req = json.loads(sys.argv[1])
    except Exception:
        sys.stdout.write(json.dumps({'status': 0, 'url': '', 'body': '', 'error': 'bad request'}))
        return
    url = req.get('url', '')
    method = (req.get('method') or 'GET').upper()
    headers = req.get('headers') or {}
    body = req.get('body')
    if isinstance(body, str):
        body = body.encode('utf-8')
    r = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        try:
            r.add_header(k, v)
        except Exception:
            pass
    r.add_header('User-Agent', UA)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            data = resp.read().decode('utf-8', errors='replace')
            sys.stdout.write(json.dumps({'status': resp.status, 'url': resp.url, 'body': data}))
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode('utf-8', errors='replace')
        except Exception:
            body_text = ''
        sys.stdout.write(json.dumps({'status': e.code, 'url': url, 'body': body_text}))
    except Exception as e:
        sys.stdout.write(json.dumps({'status': 0, 'url': url, 'body': '', 'error': str(e)}))


if __name__ == '__main__':
    main()
''', encoding='utf-8')
    return str(helper)


def parse_result(stdout: str) -> dict | None:
    """Extract the JSON result dict from harness stdout."""
    idx = stdout.rfind(SENTINEL)
    if idx == -1:
        return None
    json_str = stdout[idx + len(SENTINEL):].strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        end = json_str.rfind('}')
        if end != -1:
            try:
                return json.loads(json_str[:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def extract_url_from_result(result) -> str | None:
    """Pull a stream URL out of whatever the extractor returned."""
    if isinstance(result, str):
        return result if result.startswith('http') else None
    if isinstance(result, dict):
        for key in ('direct_access_url', 'file', 'url', 'source', 'src',
                    'link', 'stream_url', 'streamUrl', 'download_url'):
            val = result.get(key)
            if isinstance(val, str) and val.startswith('http'):
                return val
        for val in result.values():
            url = extract_url_from_result(val)
            if url:
                return url
    if isinstance(result, list):
        for item in result:
            url = extract_url_from_result(item)
            if url:
                return url
    return None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class Bridge:
    """High-level bridge: fetch JS → run via JS runtime → return stream URL."""

    def __init__(self, config: Config, fetcher: JsFetcher, names: list[str]):
        self._config = config
        self._fetcher = fetcher
        self._names = list(names)
        self._runtime_cache: dict[str, list[RuntimeInfo]] = {}
        self._last_update_check: float | None = None
        self._helper_dir = config.cache_dir / 'helpers'

    # -- runtime access -----------------------------------------------------

    def _get_runtimes(self, override: str = 'auto') -> list[RuntimeInfo]:
        key = (override or 'auto').lower()
        if key not in self._runtime_cache:
            self._runtime_cache[key] = detect_runtimes(key)
        return self._runtime_cache[key]

    # -- auto-update --------------------------------------------------------

    def ensure_updated(self) -> bool:
        """Run a version check / update if due (best-effort, non-fatal)."""
        if not self._config.get_bool('auto_update'):
            return False
        if not self._config.get_bool('fetch_from_repo'):
            return False
        now = time.time()
        if (self._last_update_check is not None
                and now - self._last_update_check < self._config.get_int('update_interval')):
            return False
        self._last_update_check = now
        try:
            return self._fetcher.update(force=False, names=self._names)
        except JSFetchError:
            return False

    # -- extraction ---------------------------------------------------------

    def extract(self, name: str, html: str, url: str, video_id: str = '',
                configuration_arg=None) -> dict:
        """Run the named extractor and return a stream-info dict.

        Returns a dict with keys: ``url``, ``ext``, ``protocol``,
        ``http_headers``, ``formats``.
        """
        config = (LayeredConfig(self._config, configuration_arg)
                  if configuration_arg else self._config)
        ctx = DebugContext(extractor_name=name, url=url, video_id=video_id)

        # Best-effort auto-update check (throttled by update_interval).
        try:
            self.ensure_updated()
        except Exception:
            pass

        fetched = self._fetcher.get(name, fetch_from_repo=config.get_bool('fetch_from_repo'))
        ctx.js_content = fetched.content
        ctx.js_source = fetched.source

        runtimes = self._get_runtimes(str(config.get('js_runtime', 'auto')))
        if not runtimes:
            raise JSRuntimeError(
                'no supported JS runtime found (need deno>=2.3, node>=22, '
                'bun>=1.2.11, or quickjs>=2023.12.9)',
                solutions=['install one of: deno, node (>=22), bun, or quickjs'],
                debug_context=ctx,
            )

        payload = json.dumps({'html': html, 'url': url})
        timeout = config.get_int('timeout', 30)

        for runtime in runtimes:
            attempt = RuntimeAttempt(runtime=runtime.name, version=runtime.version)
            ctx.runtime_attempts.append(attempt)
            helper_path = ''
            if runtime.name.startswith('quickjs'):
                try:
                    self._helper_dir.mkdir(parents=True, exist_ok=True)
                    helper_path = _write_fetch_helper(self._helper_dir)
                except OSError:
                    helper_path = ''
            harness = build_harness(fetched.content, name, runtime, helper_path)
            tmp = None
            try:
                tmp = tempfile.NamedTemporaryFile(
                    mode='w', suffix=runtime.ext, delete=False, encoding='utf-8')
                tmp.write(harness)
                tmp.close()
                cmd = self._runtime_cmd(runtime, tmp.name)
                proc = subprocess.run(
                    cmd, input=payload, capture_output=True, text=True, timeout=timeout)
                attempt.stdout = proc.stdout
                attempt.stderr = proc.stderr
                parsed = parse_result(proc.stdout)
                if parsed is None:
                    attempt.error = (f'no result from {runtime.name} '
                                    f'(exit {proc.returncode})')
                    if proc.stderr.strip():
                        attempt.error += f': {proc.stderr.strip()[:300]}'
                    continue
                if not parsed.get('ok'):
                    attempt.error = str(parsed.get('error', 'unknown JS error'))
                    continue
                stream_url = extract_url_from_result(parsed.get('result'))
                if not stream_url:
                    attempt.error = 'extractor returned no stream URL'
                    ctx.result_payload = parsed.get('result')
                    continue
                attempt.success = True
                return self._build_stream_result(stream_url, url)
            except subprocess.TimeoutExpired:
                attempt.timed_out = True
                attempt.error = f'timed out after {timeout}s'
            except OSError as e:
                attempt.error = f'failed to execute {runtime.name}: {e}'
            finally:
                if tmp is not None:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass

        raise ExtractionError(
            f'extractor {name!r} failed on all available JS runtimes',
            solutions=['run with -v to see the full debug log',
                       'check that the site is not blocking the JS extractor',
                       'try a different js_runtime setting'],
            debug_context=ctx,
        )

    def _runtime_cmd(self, runtime: RuntimeInfo, harness_path: str) -> list[str]:
        if runtime.name == 'deno':
            # --allow-net is required for fetch; stdin read needs no permission.
            return [runtime.path, 'run', '--allow-net', '--allow-read', harness_path]
        return [runtime.path, harness_path]

    @staticmethod
    def _build_stream_result(stream_url: str, page_url: str) -> dict:
        is_m3u8 = '.m3u8' in stream_url
        ext = 'm3u8' if is_m3u8 else 'mp4'
        protocol = 'm3u8_native' if is_m3u8 else 'https'
        headers = {'Referer': page_url}
        return {
            'url': stream_url,
            'ext': ext,
            'protocol': protocol,
            'http_headers': headers,
            'formats': [{
                'format_id': 'source',
                'url': stream_url,
                'ext': ext,
                'protocol': protocol,
                'http_headers': headers,
            }],
        }

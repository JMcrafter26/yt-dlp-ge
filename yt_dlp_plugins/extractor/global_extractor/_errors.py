"""Error types, debug diagnostics, and GitHub issue template for the plugin.

All exceptions raised by the plugin derive from :class:`GlobalExtractorError`
so that the extractor entry point can catch them uniformly, emit a verbose
debug log, and re-raise a clean user-facing message.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from typing import Any


class GlobalExtractorError(Exception):
    """Base exception for all plugin errors.

    Attributes:
        solutions: Optional list of user-facing hints appended to the message.
        debug_context: Optional :class:`DebugContext` with full diagnostics.
    """

    def __init__(self, msg: str, *, solutions: list[str] | None = None,
                 debug_context: 'DebugContext | None' = None):
        super().__init__(msg)
        self.solutions = solutions or []
        self.debug_context = debug_context

    def user_message(self) -> str:
        """Return the error message with any solution hints appended."""
        msg = str(self)
        if self.solutions:
            msg = f'{msg} ({"; ".join(self.solutions)})'
        return msg


class ConfigError(GlobalExtractorError):
    """Invalid or unknown configuration value."""


class JSFetchError(GlobalExtractorError):
    """Failed to fetch a JS extractor module from the repo."""

    def __init__(self, msg: str, *, http_status: int | None = None,
                 http_headers: dict | None = None, **kwargs):
        super().__init__(msg, **kwargs)
        self.http_status = http_status
        self.http_headers = http_headers or {}


class JSRuntimeError(GlobalExtractorError):
    """No usable JS runtime is available (or the selected one failed)."""


class ExtractionError(GlobalExtractorError):
    """The JS extractor ran but did not produce a usable stream URL."""


@dataclass
class RuntimeAttempt:
    """Record of a single JS runtime execution attempt."""

    runtime: str
    version: str = ''
    success: bool = False
    error: str = ''
    stderr: str = ''
    stdout: str = ''
    timed_out: bool = False


@dataclass
class DebugContext:
    """Full diagnostic snapshot captured during an extraction failure.

    Consumed by :func:`build_debug_log` (verbose ``-v`` output) and
    :func:`build_issue_template` (GitHub issue body).
    """

    extractor_name: str = ''
    url: str = ''
    video_id: str = ''
    js_content: str = ''
    js_source: str = ''            # 'cache' | 'repo' | 'bundled'
    http_status: int | None = None
    http_headers: dict = field(default_factory=dict)
    runtime_attempts: list[RuntimeAttempt] = field(default_factory=list)
    result_payload: Any = None
    error_message: str = ''
    extra: dict = field(default_factory=dict)

    def runtime_table(self) -> str:
        if not self.runtime_attempts:
            return '_No runtime attempts were made._'
        lines = ['| Runtime | Version | Result | Detail |', '| --- | --- | --- | --- |']
        for attempt in self.runtime_attempts:
            result = 'ok' if attempt.success else 'FAIL'
            detail = 'success' if attempt.success else _one_line(attempt.error or attempt.stderr)
            lines.append(f'| {attempt.runtime} | {attempt.version or "?"} | {result} | {detail} |')
        return '\n'.join(lines)

    def js_stderr_preview(self) -> str:
        """Return the most recent non-empty stderr from the runtime attempts."""
        for attempt in reversed(self.runtime_attempts):
            if attempt.stderr and attempt.stderr.strip():
                return attempt.stderr.strip()
        return ''


def _one_line(text: str, limit: int = 200) -> str:
    """Collapse *text* to a single truncated line for table cells."""
    text = ' '.join(str(text).split())
    if len(text) > limit:
        text = text[:limit] + '...'
    return text.replace('|', '\\|').replace('\n', ' ')


def _safe_js_preview(js_content: str, limit: int = 4000) -> str:
    """Return a truncated JS snippet safe to embed in a Markdown code fence."""
    if len(js_content) > limit:
        return js_content[:limit] + f'\n// ... [{len(js_content) - limit} more chars truncated]'
    return js_content


def _yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return 'unknown'


def _plugin_version() -> str:
    try:
        from ._version import __version__  # type: ignore
        return __version__
    except Exception:
        return '0.1.0'


def build_debug_log(ctx: DebugContext) -> str:
    """Build a verbose debug log string (written via ``write_debug``)."""
    lines = [
        '=== global-extractor plugin debug info ===',
        f'extractor : {ctx.extractor_name or "?"}',
        f'url       : {ctx.url or "?"}',
        f'video_id  : {ctx.video_id or "?"}',
        f'js_source : {ctx.js_source or "?"}',
        f'plugin    : {_plugin_version()}  yt-dlp: {_yt_dlp_version()}',
        f'python    : {sys.version.split()[0]}  os: {platform.system()} {platform.release()}',
    ]
    if ctx.http_status is not None:
        lines.append(f'http      : status={ctx.http_status}')
    if ctx.runtime_attempts:
        lines.append('runtimes  :')
        for attempt in ctx.runtime_attempts:
            lines.append(
                f'  - {attempt.runtime} ({attempt.version or "?"}): '
                f'{"ok" if attempt.success else "failed"} {_one_line(attempt.error or attempt.stderr, 300)}')
    if ctx.result_payload is not None:
        lines.append(f'payload   : {ctx.result_payload!r}')
    if ctx.js_stderr_preview():
        lines.append(f'js_stderr : {ctx.js_stderr_preview()}')
    if ctx.js_content:
        lines.append('js_file   :')
        lines.append(ctx.js_content)
    lines.append('=== end debug info ===')
    return '\n'.join(lines)


def build_issue_template(ctx: DebugContext) -> str:
    """Build a GitHub issue template for reporting an extraction failure."""
    return f"""## global-extractor plugin: extraction failure

<!-- Thanks for reporting. Please fill in the sections below. -->

### Environment
- **Plugin version:** `{_plugin_version()}`
- **yt-dlp version:** `{_yt_dlp_version()}`
- **OS:** {platform.system()} {platform.release()}
- **Python:** {sys.version.split()[0]}
- **JS runtime(s) available:** {', '.join(a.runtime for a in ctx.runtime_attempts) or 'none detected'}

### Target
- **Extractor:** `{ctx.extractor_name or '?'}`
- **URL:** {ctx.url or '?'}
- **Video ID:** {ctx.video_id or '?'}

### What happened?
{ctx.error_message or 'Extraction failed.'}

### JS runtime attempts
{ctx.runtime_table()}

### HTTP
- **Status:** {ctx.http_status if ctx.http_status is not None else 'n/a'}

### JS stderr
```
{_one_line(ctx.js_stderr_preview(), 2000) or '(empty)'}
```

### JS file used ({ctx.js_source or '?'})
```js
{_safe_js_preview(ctx.js_content)}
```

### Steps to reproduce
1. Run `yt-dlp -v "<URL>"`
2. See the error above

### Additional context
<!-- Anything else: proxy, cookies, geo, etc. -->
"""

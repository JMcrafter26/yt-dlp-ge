"""Fetches JS extractor modules from the global-extractor repo and manages cache.

The plugin is a *pure bridge*: no JS is bundled. On first use, each extractor
JS file is downloaded from the repo and cached on disk under
``~/.cache/yt-dlp/global-extractor/js/``. A ``version.json`` snapshot in the
cache root records the upstream extractor version so :meth:`JsFetcher.update`
can detect changes and re-fetch stale modules.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ._config import Config
from ._errors import JSFetchError

VERSION_PATH = 'global-extractor/VERSION.json'
EXTRACTOR_PATH = 'extractors/{name}.js'
DOMAINS_PATHS = ('domains.json', 'global-extractor/domains.json')
DOMAINS_FILE = 'domains.json'
VERSION_FILE = 'version.json'
USER_AGENT = 'yt-dlp-global-extractor-plugin'
DEFAULT_TIMEOUT = 15


@dataclass
class FetchedJS:
    """A JS extractor module and where it came from."""

    name: str
    content: str
    source: str  # 'cache' | 'repo'


class JsFetcher:
    """Download, cache, and version-track global-extractor JS modules."""

    def __init__(self, config: Config):
        self._config = config
        self._cache_dir = config.cache_dir / 'js'
        self._version_file = config.cache_dir / VERSION_FILE

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # -- URLs ---------------------------------------------------------------

    def _raw_url(self, path: str) -> str:
        repo = str(self._config.get('repo_url')).rstrip('/')
        # Support both https://github.com/Owner/Repo and raw URLs.
        if '/raw/' in repo:
            base = repo.split('/raw/', 1)[0]
            return f'{base}/raw/main/{path}'
        return f'{repo}/raw/main/{path}'

    def _extractor_url(self, name: str) -> str:
        return self._raw_url(EXTRACTOR_PATH.format(name=name))

    def _version_url(self) -> str:
        return self._raw_url(VERSION_PATH)

    # -- low-level HTTP -----------------------------------------------------

    def _fetch_url(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, headers={
            'User-Agent': USER_AGENT,
            'Accept': '*/*',
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as e:
            raise JSFetchError(
                f'HTTP {e.code} fetching {url}',
                http_status=e.code,
                http_headers=dict(e.headers.items()) if e.headers else {},
                solutions=['check the repo_url setting', 'verify network access to GitHub'],
            ) from e
        except urllib.error.URLError as e:
            raise JSFetchError(
                f'network error fetching {url}: {e.reason}',
                solutions=['check your network connection', 'set fetch_from_repo=False to use the cache'],
            ) from e

    # -- version tracking ---------------------------------------------------

    def _read_cached_version(self) -> dict | None:
        try:
            return json.loads(self._version_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def _write_cached_version(self, version_info: dict) -> None:
        self._version_file.parent.mkdir(parents=True, exist_ok=True)
        info = dict(version_info)
        info['fetched_at'] = time.time()
        info['plugin'] = USER_AGENT
        self._version_file.write_text(json.dumps(info, indent=2), encoding='utf-8')

    # -- public API ---------------------------------------------------------

    def update(self, force: bool = False, names: list[str] | None = None) -> bool:
        """Re-fetch the version file and any changed extractor modules.

        Also refreshes the cached ``domains.json`` when the repo hosts one
        (missing file is not an error). Returns True when at least one
        module (or the version file, or the domain list) changed.
        """
        if not self._config.get_bool('fetch_from_repo'):
            return False
        try:
            _, _, data = self._fetch_url(self._version_url())
            version_info = json.loads(data.decode('utf-8'))
        except (JSFetchError, ValueError) as e:
            raise JSFetchError(f'failed to fetch version info: {e}') from e

        cached = self._read_cached_version()
        changed = bool(force or not cached
                       or cached.get('extractor') != version_info.get('extractor'))
        self._write_cached_version(version_info)

        if changed and names:
            for name in names:
                try:
                    self._download_extractor(name)
                except JSFetchError:
                    # Keep the old cached copy; a partial update is not fatal.
                    continue

        if self._refresh_domains():
            try:
                from ._registry import invalidate_domains_cache
                invalidate_domains_cache()
            except Exception:
                pass
            changed = True
        return changed

    def _refresh_domains(self) -> bool:
        """Fetch ``domains.json`` from the repo into the cache.

        Tries known locations; a missing file simply keeps the existing
        cache. Returns True when the cached copy changed.
        """
        for path in DOMAINS_PATHS:
            try:
                _, _, data = self._fetch_url(self._raw_url(path))
                content = data.decode('utf-8')
                json.loads(content)  # validate before caching
            except (JSFetchError, ValueError):
                continue
            cache_file = self._cache_dir.parent / DOMAINS_FILE
            try:
                old = cache_file.read_text(encoding='utf-8')
            except OSError:
                old = None
            if old != content:
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(content, encoding='utf-8')
                except OSError:
                    pass
                return True
            return False
        return False

    def get(self, name: str, fetch_from_repo: bool | None = None) -> FetchedJS:
        """Return the JS content for *name*, fetching from the repo on miss."""
        cache_file = self._cache_dir / f'{name}.js'
        if cache_file.is_file():
            try:
                return FetchedJS(name, cache_file.read_text(encoding='utf-8'), 'cache')
            except OSError:
                pass

        if fetch_from_repo is None:
            fetch_from_repo = self._config.get_bool('fetch_from_repo')
        if not fetch_from_repo:
            raise JSFetchError(
                f'no cached JS module for {name!r} and fetch_from_repo is disabled',
                solutions=['run once with fetch_from_repo=True to populate the cache',
                           'or set fetch_from_repo=True'],
            )
        return self._download_extractor(name)

    def _download_extractor(self, name: str) -> FetchedJS:
        url = self._extractor_url(name)
        try:
            status, headers, data = self._fetch_url(url)
        except JSFetchError:
            # Fall back to any stale cached copy before giving up.
            cache_file = self._cache_dir / f'{name}.js'
            if cache_file.is_file():
                return FetchedJS(name, cache_file.read_text(encoding='utf-8'), 'cache')
            raise
        content = data.decode('utf-8')
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            (self._cache_dir / f'{name}.js').write_text(content, encoding='utf-8')
        except OSError:
            pass  # caching is best-effort
        return FetchedJS(name, content, 'repo')

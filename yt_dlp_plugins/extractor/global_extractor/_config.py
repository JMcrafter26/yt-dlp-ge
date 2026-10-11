"""Configuration for the global-extractor plugin.

Values are resolved from (in order of priority):

1. Extractor arguments: ``--extractor-args "global_extractor:key=value"``
   (accessed via :meth:`InfoExtractor._configuration_arg`).
2. Environment variables: ``YTDLP_GLOBAL_EXTRACTOR_<KEY>``.
3. Plugin config file: ``~/.config/yt-dlp/plugins/global_extractor.conf``.
4. Built-in defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._errors import ConfigError

PACKAGE_NAME = 'global_extractor'
ENV_PREFIX = 'YTDLP_GLOBAL_EXTRACTOR_'
CONFIG_FILENAME = 'global_extractor.conf'
# User-local domain overrides (same shape as the bundled domains.json).
DOMAINS_OVERRIDE_FILENAME = 'global_extractor_domains.json'
# Cached repo-fetched copy of domains.json (refreshed by JsFetcher.update).
CACHED_DOMAINS_FILENAME = 'domains.json'

DEFAULTS: dict[str, object] = {
    'fetch_from_repo': True,
    'repo_url': 'https://github.com/JMcrafter26/global-extractor',
    'js_runtime': 'auto',        # auto | deno | node | bun | quickjs
    'auto_update': True,
    'force_broken': False,
    'cache_dir': '~/.cache/yt-dlp/global-extractor',
    'timeout': 30,               # seconds per JS runtime attempt
    'update_interval': 86400,    # seconds between auto version checks
    # Extra VOE mirror domains (VOE rotates custom domains to evade blocks).
    # Comma-separated, e.g. 'megankiddifference.com,example.net'.
    'voe_domains': '',
    # Force a specific extractor for URLs no registered extractor claims.
    # Enables the manual-selection extractor, e.g. 'voe'. Import-time
    # (env/file) for URL routing; per-call extractor-args can refine it.
    'force_extractor': '',
}

VALID_RUNTIMES = ('auto', 'deno', 'node', 'bun', 'quickjs')

# Config keys holding comma-separated extra domains for a given extractor.
# Read at plugin import time (env var / config file).
EXTRA_DOMAINS_KEYS = {
    'voe_domains': 'voe',
}


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'on', 'y'):
        return True
    if text in ('0', 'false', 'no', 'off', 'n'):
        return False
    raise ConfigError(f'invalid boolean value: {value!r}')


def _parse_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f'invalid integer value: {value!r}')


_COERCE = {
    'fetch_from_repo': _parse_bool,
    'auto_update': _parse_bool,
    'force_broken': _parse_bool,
    'js_runtime': str,
    'repo_url': str,
    'cache_dir': str,
    'timeout': _parse_int,
    'update_interval': _parse_int,
    'voe_domains': str,
    'force_extractor': str,
}


def _config_file_path() -> Path:
    """Return the plugin config file path (``$XDG_CONFIG_HOME`` aware)."""
    return _config_dir_path() / CONFIG_FILENAME


def _config_dir_path() -> Path:
    """Return the plugin config directory (``$XDG_CONFIG_HOME`` aware)."""
    xdg = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg) if xdg else Path.home() / '.config'
    return base / 'yt-dlp' / 'plugins'


class Config:
    """Layered configuration view.

    Args:
        configuration_arg: Optional callable (usually
            :meth:`InfoExtractor._configuration_arg`) used to read
            ``--extractor-args "global_extractor:..."`` values. When
            provided, extractor args take top priority.
    """

    def __init__(self, configuration_arg=None):
        self._configuration_arg = configuration_arg
        self._values: dict[str, object] = dict(DEFAULTS)
        self._load_config_file()
        self._load_env()

    # -- loading -----------------------------------------------------------

    def _load_config_file(self) -> None:
        try:
            text = _config_file_path().read_text(encoding='utf-8')
        except OSError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if key in _COERCE:
                try:
                    self._values[key] = _COERCE[key](value)
                except ConfigError:
                    continue  # ignore malformed lines

    def _load_env(self) -> None:
        for key in DEFAULTS:
            raw = os.environ.get(ENV_PREFIX + key.upper())
            if raw is None:
                continue
            try:
                self._values[key] = _COERCE[key](raw)
            except ConfigError:
                continue

    # -- access ------------------------------------------------------------

    def get(self, key: str, default: object = None) -> object:
        """Return the resolved value for *key*."""
        if key not in DEFAULTS:
            raise ConfigError(f'unknown config key: {key!r}')
        if self._configuration_arg is not None:
            try:
                values = self._configuration_arg(key)
            except Exception:
                values = None
            if values:
                try:
                    return _COERCE[key](values[0])
                except ConfigError:
                    pass
        return self._values.get(key, DEFAULTS.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    @property
    def cache_dir(self) -> Path:
        return Path(str(self.get('cache_dir'))).expanduser()

    def validate(self) -> None:
        """Raise :class:`ConfigError` if the configuration is inconsistent."""
        runtime = str(self.get('js_runtime')).lower()
        if runtime not in VALID_RUNTIMES:
            raise ConfigError(
                f'invalid js_runtime: {runtime!r} (expected one of {", ".join(VALID_RUNTIMES)})')
        if self.get_int('timeout', 30) < 1:
            raise ConfigError('timeout must be >= 1')
        if self.get_int('update_interval', 86400) < 0:
            raise ConfigError('update_interval must be >= 0')


class LayeredConfig:
    """A read-only view that layers extractor args over a base :class:`Config`.

    Used by the bridge so per-call ``--extractor-args`` values are honoured
    without re-reading the config file / environment for every extraction.
    """

    def __init__(self, base: Config, configuration_arg):
        self._base = base
        self._configuration_arg = configuration_arg

    def get(self, key: str, default: object = None) -> object:
        if key in DEFAULTS and self._configuration_arg is not None:
            try:
                values = self._configuration_arg(key)
            except Exception:
                values = None
            if values:
                try:
                    return _COERCE[key](values[0])
                except ConfigError:
                    pass
        return self._base.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    @property
    def cache_dir(self) -> Path:
        return self._base.cache_dir

"""Dynamically generates extractor classes from ``extractors.json`` metadata.

yt-dlp's plugin loader collects every public class whose name ends in ``IE``
from the plugin module. We build one subclass of :class:`GlobalExtractorIE`
per enabled extractor using ``type()``, wiring in the URL pattern, working
flag, and extractor name from the metadata file.

Host matching has two layers so mirror domains can be updated without code
changes:

1. The static ``pattern`` baked into ``_VALID_URL`` at import time (plus any
   domains known then from :func:`load_domains`).
2. A dynamic hostname check in :meth:`GlobalExtractorIE.suitable` against
   the merged domain lists (bundled ``domains.json`` < repo-cached copy <
   user-local override file), so domain updates apply without reinstalling.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._config import Config

try:
    from importlib.resources import files as _resource_files
except ImportError:  # pragma: no cover - Python < 3.9
    _resource_files = None

logger = logging.getLogger(__name__)

# In-memory cache for merged domain lists (suitable() runs per URL).
_DOMAINS_CACHE: dict = {'t': 0.0, 'data': {}}
_DOMAINS_TTL = 60.0


def load_metadata() -> dict:
    """Load the bundled ``extractors.json`` metadata.

    Uses :func:`importlib.resources.files` so the plugin works when installed
    as a zip/egg/whl, with a plain-path fallback for drop-in installs.
    """
    if _resource_files is not None:
        try:
            text = (_resource_files(__package__)
                    .joinpath('extractors.json')
                    .read_text(encoding='utf-8'))
            return json.loads(text)
        except Exception:
            pass
    from pathlib import Path
    text = (Path(__file__).parent / 'extractors.json').read_text(encoding='utf-8')
    return json.loads(text)


def _read_json_file(path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _bundled_domains() -> dict[str, list[str]]:
    """Return the bundled ``domains.json`` domain lists."""
    data: dict = {}
    if _resource_files is not None:
        try:
            text = (_resource_files(__package__)
                    .joinpath('domains.json')
                    .read_text(encoding='utf-8'))
            data = json.loads(text)
        except Exception:
            data = {}
    if not data:
        from pathlib import Path
        try:
            data = json.loads(
                (Path(__file__).parent / 'domains.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
    domains = data.get('domains', {}) if isinstance(data, dict) else {}
    return {str(k): list(v) for k, v in domains.items() if isinstance(v, list)}


def load_domains(config: 'Config | None' = None) -> dict[str, list[str]]:
    """Return merged per-extractor domain lists.

    Priority (low to high): bundled ``domains.json`` < repo-cached copy <
    user-local override file < legacy per-extractor options (``voe_domains``).
    Results are cached briefly; never raises.
    """
    now = time.monotonic()
    if now - _DOMAINS_CACHE.get('t', 0.0) < _DOMAINS_TTL and _DOMAINS_CACHE.get('data'):
        return _DOMAINS_CACHE['data']

    merged: dict[str, list[str]] = _bundled_domains()

    def _overlay(source: dict) -> None:
        if not isinstance(source, dict):
            return
        for name, domains in source.items():
            if not isinstance(domains, list):
                continue
            clean = [str(d).strip().lstrip('.').lower() for d in domains if str(d).strip()]
            if not clean:
                continue
            merged[str(name)] = sorted(set(merged.get(str(name), [])) | set(clean))

    if config is not None:
        try:
            from ._config import CACHED_DOMAINS_FILENAME
            _overlay(_read_json_file(
                config.cache_dir / CACHED_DOMAINS_FILENAME).get('domains', {}))
        except Exception:
            pass
        try:
            from ._config import DOMAINS_OVERRIDE_FILENAME, _config_dir_path
            _overlay(_read_json_file(
                _config_dir_path() / DOMAINS_OVERRIDE_FILENAME).get('domains', {}))
        except Exception:
            pass
        try:
            from ._config import EXTRA_DOMAINS_KEYS
            for key, extractor_name in EXTRA_DOMAINS_KEYS.items():
                try:
                    raw = str(config.get(key) or '')
                except Exception:
                    raw = ''
                extra = [d.strip().lstrip('.').lower() for d in raw.split(',') if d.strip()]
                if extra:
                    merged[extractor_name] = sorted(set(merged.get(extractor_name, [])) | set(extra))
        except Exception:
            pass

    _DOMAINS_CACHE.update(t=now, data=merged)
    return merged


def invalidate_domains_cache() -> None:
    """Drop the cached domain lists (used after a repo update)."""
    _DOMAINS_CACHE.update(t=0.0, data={})


def host_matches_domain(host: str, domain: str) -> bool:
    """Check *host* against a listed *domain* (exact or subdomain)."""
    host = (host or '').lower().rstrip('.')
    domain = (domain or '').lower().lstrip('.')
    if not host or not domain:
        return False
    return host == domain or host.endswith('.' + domain)


def dynamic_domains_for(name: str, config: 'Config | None' = None) -> list[str]:
    """Return the merged domain list for extractor *name* (never raises)."""
    try:
        return list(load_domains(config).get(name, []))
    except Exception:
        return []


def class_name_for(name: str) -> str:
    """Return the extractor class name for *name* (``doodstream`` -> ``DoodstreamIE``)."""
    return f'{name[0].upper()}{name[1:]}IE'


def build_classes(metadata: dict, force_broken: bool = False,
                  config: 'Config | None' = None) -> dict[str, type]:
    """Build extractor classes for every enabled extractor in *metadata*.

    Broken extractors are skipped unless *force_broken* is True. When
    *config* is given, per-extractor extra domains (e.g. ``voe_domains``)
    are appended to the URL pattern. Returns a dict mapping class name
    -> class.
    """
    # Imported here to avoid a circular import at module load time.
    from ._extractor import GlobalExtractorIE

    # Domains known at import time are baked into the URL pattern; further
    # updates are picked up dynamically via suitable().
    baked: dict[str, list[str]] = {}
    if config is not None:
        try:
            for name, domains in load_domains(config).items():
                if domains:
                    baked[name] = domains
        except Exception:
            pass

    classes: dict[str, type] = {}
    for name, meta in metadata.get('extractors', {}).items():
        working = bool(meta.get('working', False))
        if not working and not force_broken:
            continue
        pattern = meta.get('pattern')
        if not pattern:
            logger.warning('extractor %r has no URL pattern; skipping', name)
            continue
        if name in baked:
            additions = '|'.join(re.escape(d) for d in baked[name])
            pattern = f'{pattern}|{additions}'
        cls_name = class_name_for(name)
        cls = type(
            cls_name,
            (GlobalExtractorIE,),
            {
                # NOTE: the alternation *must* be wrapped in (?:...) so that
                # the scheme/host anchoring applies to every alternative.
                '_VALID_URL': rf'https?://(?:www\.)?(?:{pattern})(?:/(?P<id>[^/?#&]+))?(?:[/?#]\S*)?',
                '_WORKING': True,
                'IE_NAME': f'global_extractor.{name}',
                '_extractor_name': name,
                '_extractor_meta': meta,
                '__module__': __package__,
                '__doc__': (
                    f'global-extractor bridge for {name} '
                    f'(URL pattern: {pattern}). '
                    f'{"Marked broken upstream; enabled via force_broken." if not working else ""}'
                ).strip(),
            },
        )
        classes[cls_name] = cls
    return classes


def any_specific_matches(url: str, metadata: dict | None = None,
                         config: 'Config | None' = None) -> bool:
    """Check whether *url* matches any specific (non-manual) extractor.

    Uses the static patterns plus the merged dynamic domain lists. Used by
    the manual-selection extractor so it never shadows a specific one.
    Never raises.
    """
    try:
        from urllib.parse import urlparse
        if metadata is None:
            metadata = load_metadata()
        try:
            force_broken = bool(config.get_bool('force_broken')) if config is not None else False
        except Exception:
            force_broken = False
        domains = load_domains(config)
        host = (urlparse(url).hostname or '').lower()
        for name, meta in metadata.get('extractors', {}).items():
            if not bool(meta.get('working', False)) and not force_broken:
                continue
            pattern = meta.get('pattern')
            if pattern:
                try:
                    if re.match(rf'https?://(?:www\.)?(?:{pattern})(?:[/?#]\S*)?', url):
                        return True
                except re.error:
                    pass
            for domain in domains.get(name, []):
                if host_matches_domain(host, domain):
                    return True
    except Exception:
        pass
    return False

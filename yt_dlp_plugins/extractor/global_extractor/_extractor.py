"""The single extractor class that bridges to the global-extractor JS modules.

Each registered extractor (see :mod:`_registry`) is a thin subclass of
:class:`GlobalExtractorIE` carrying only metadata (``_VALID_URL``,
``_extractor_name``). All real work happens in :meth:`_real_extract`, which
downloads the page, hands it to the JS bridge, and normalises the returned
stream URL into a yt-dlp info dict.
"""

from __future__ import annotations

import logging
import time

from yt_dlp.extractor.common import InfoExtractor

from ._errors import ConfigError, GlobalExtractorError, build_debug_log

logger = logging.getLogger(__name__)

# Module-level bridge singleton, set by the package __init__ at plugin load.
_BRIDGE = None

# Shared import-time config (env/file layers only; suitable() has no
# downloader for extractor-args). Refreshed at most once a minute.
_SHARED_CONFIG: dict = {'t': 0.0, 'config': None}
_SHARED_CONFIG_TTL = 60.0


def _shared_config():
    """Return a cached :class:`Config` (never raises; defaults on error)."""
    now = time.monotonic()
    if now - _SHARED_CONFIG.get('t', 0.0) > _SHARED_CONFIG_TTL or _SHARED_CONFIG.get('config') is None:
        try:
            from ._config import Config
            _SHARED_CONFIG.update(t=now, config=Config())
        except Exception:
            pass
    config = _SHARED_CONFIG.get('config')
    if config is None:
        from ._config import Config
        config = Config()
        _SHARED_CONFIG.update(t=now, config=config)
    return config


def set_bridge(bridge) -> None:
    """Install the shared :class:`~._jsruntime.Bridge` instance."""
    global _BRIDGE
    _BRIDGE = bridge


def _get_bridge():
    """Return the shared bridge, building a default one if necessary."""
    global _BRIDGE
    if _BRIDGE is None:
        from ._config import Config
        from ._fetcher import JsFetcher
        from ._jsruntime import Bridge
        config = Config()
        _BRIDGE = Bridge(config, JsFetcher(config), names=[])
    return _BRIDGE


class GlobalExtractorIE(InfoExtractor):
    """Bridge extractor that delegates to a global-extractor JS module.

    Subclasses are generated dynamically and set ``_VALID_URL`` and
    ``_extractor_name``; everything else is inherited.
    """

    _VALID_URL = None
    _WORKING = True
    IE_NAME = 'global_extractor'
    _extractor_name: str | None = None

    def __init__(self, downloader=None, *args, **kwargs):
        super().__init__(downloader, *args, **kwargs)
        # The extractor name comes from the class attribute set by the
        # registry (see _registry.build_classes). The first positional arg is
        # always the downloader in yt-dlp's convention -- never steal it.
        if self._extractor_name is None:
            self._extractor_name = type(self)._extractor_name

    # -- matching -------------------------------------------------------------

    @classmethod
    def suitable(cls, url: str) -> bool:
        """Match static patterns plus dynamically updated domain lists.

        Besides ``_VALID_URL``, the URL's host is checked against the merged
        domain lists (bundled ``domains.json`` < repo-cached copy <
        user-local overrides), so new mirror domains work without code
        changes or reinstalls. This method is self-contained (local imports
        only) as required for yt-dlp lazy extractors. Never raises.
        """
        try:
            if cls._match_valid_url(url) is not None:
                return True
        except Exception:
            pass
        try:
            from urllib.parse import urlparse
            from ._registry import dynamic_domains_for, host_matches_domain
            host = (urlparse(url).hostname or '').lower()
            if not host:
                return False
            for domain in dynamic_domains_for(cls._extractor_name or '', _shared_config()):
                if host_matches_domain(host, domain):
                    return True
        except Exception:
            pass
        return False

    # -- URL / id handling ----------------------------------------------------

    #: Path segments that are route prefixes, not video ids.
    _ROUTE_PREFIXES = frozenset({
        'e', 'd', 'v', 'embed', 'file', 'video', 'watch', 'play', 'player',
    })

    @classmethod
    def _match_id(cls, url: str) -> str:
        """Return a best-effort video id for *url* (never raises).

        The registry ``_VALID_URL`` patterns are intentionally lenient (they
        cover whole provider domains, whose path/query layouts differ per
        site), so the ``id`` regex group may be missing or unhelpful (a route
        prefix like ``e`` or a filename like ``shell.php``). Fall back to
        query params, the last path segment, or the host name.
        """
        try:
            m = cls._match_valid_url(url)
        except Exception:
            m = None
        if m is not None:
            try:
                gid = m.group('id')
            except (IndexError, AttributeError):
                gid = None
            if (gid and '.' not in gid and '/' not in gid
                    and gid not in cls._ROUTE_PREFIXES):
                return gid
        try:
            from urllib.parse import parse_qs, urlparse
            parts = urlparse(url)
            qs = parse_qs(parts.query)
            for key in ('videoid', 'id', 'v'):
                if qs.get(key):
                    return qs[key][0]
            segs = [s for s in parts.path.split('/') if s not in ('', 'e', 'd', 'v', 'embed')]
            if segs:
                last = segs[-1].split('.')[0].split('?')[0]
                if last:
                    return last
            if parts.netloc:
                return parts.netloc
        except Exception:
            pass
        return 'video'

    # -- bridge access ------------------------------------------------------

    def _bridge(self):
        return _get_bridge()

    def _layered_configuration_arg(self):
        """Extractor-args lookup checking ``global_extractor:`` then the IE key.

        yt-dlp scopes ``--extractor-args`` by extractor key (e.g. ``voe`` for
        ``VoeIE``). Users document options under the shared ``global_extractor``
        scope, so check that first and fall back to the per-extractor scope.
        Never raises: returns ``[]`` when no downloader/args are present.
        """
        base_arg = self._configuration_arg

        def layered(key, default=None, **kwargs):
            for ie_key in ('global_extractor', None):
                try:
                    values = base_arg(key, ie_key=ie_key, **kwargs)
                except Exception:
                    values = []
                if values:
                    return values
            return [] if default is None else default

        return layered

    # -- extraction ---------------------------------------------------------

    def _real_extract(self, url: str) -> dict:
        name = self._extractor_name or 'unknown'
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)

        try:
            result = self._bridge().extract(
                name, webpage, url, video_id=video_id,
                configuration_arg=self._layered_configuration_arg(),
            )
        except GlobalExtractorError as e:
            self._report_error(e)
            raise

        title = (
            self._html_search_meta('title', webpage, default=None)
            or self._og_search_title(webpage, default=None)
            or video_id
        )
        return {
            'id': video_id,
            'title': title,
            'webpage_url': url,
            'ext': result.get('ext'),
            'protocol': result.get('protocol'),
            'http_headers': result.get('http_headers'),
            'formats': result.get('formats'),
        }

    # -- error reporting ----------------------------------------------------

    def _report_error(self, error: GlobalExtractorError) -> None:
        """Write the verbose debug log and re-raise with a clean message."""
        ctx = getattr(error, 'debug_context', None)
        if ctx is not None:
            try:
                self.write_debug(build_debug_log(ctx))
            except Exception:
                logger.exception('failed to write debug log')
        message = error.user_message()
        self.write_debug(f'extraction failed: {message}')
        # Re-raise so yt-dlp surfaces a clean ERROR line to the user.
        raise error


class GlobalExtractorManualIE(GlobalExtractorIE):
    """Manual-selection fallback for URLs no specific extractor claims.

    Mirror domains rotate constantly; when a URL matches nothing, map it
    explicitly with ``force_extractor`` (env
    ``YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR``, config file, or per-call
    ``--extractor-args``). Entries are ``name`` or ``name@domain`` (comma
    separated)::

        YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR='voe@my-mirror.example' yt-dlp "<url>"
        yt-dlp --extractor-args "global_extractor:force_extractor=voe@my-mirror.example" "<url>"

    A bare ``name`` (no ``@domain``) claims *every* otherwise-unmatched URL
    while set, so prefer the scoped form and only set it for that command.
    Routing requires the env var / config file (``suitable()`` runs before
    any downloader exists); the per-call extractor-arg can then refine or
    confirm the choice. This class never shadows a specific extractor.
    """

    _VALID_URL = r'https?://[^/]+/\S*'
    IE_NAME = 'global_extractor.manual'
    _extractor_name = 'manual'

    @staticmethod
    def _parse_entries(raw: str) -> list[tuple[str, str | None]]:
        """Parse ``'voe, dood@host.example'`` into [(name, domain|None)]."""
        entries = []
        for item in str(raw or '').split(','):
            item = item.strip().lower()
            if not item:
                continue
            if '@' in item:
                name, _, domain = item.partition('@')
                name, domain = name.strip(), domain.strip().lstrip('.')
                if name and domain:
                    entries.append((name, domain))
            else:
                entries.append((item, None))
        return entries

    @classmethod
    def _forced_name(cls, url: str, raw: str) -> str | None:
        """Return the forced extractor for *url*, or None when not claimed."""
        try:
            from urllib.parse import urlparse
            from ._registry import host_matches_domain
            host = (urlparse(url).hostname or '').lower()
        except Exception:
            return None
        for name, domain in cls._parse_entries(raw):
            if domain is None:
                return name  # bare name claims any unmatched URL
            if host and host_matches_domain(host, domain):
                return name
        return None

    @classmethod
    def suitable(cls, url: str) -> bool:
        try:
            if not cls._match_valid_url(url):
                return False
        except Exception:
            return False
        try:
            raw = str(_shared_config().get('force_extractor') or '')
        except Exception:
            raw = ''
        if not raw.strip():
            return False
        if cls._forced_name(url, raw) is None:
            return False
        # Never steal URLs a specific extractor already claims.
        try:
            from ._registry import any_specific_matches, load_metadata
            if any_specific_matches(url, load_metadata(), _shared_config()):
                return False
        except Exception:
            pass
        return True

    def _real_extract(self, url: str) -> dict:
        try:
            from ._registry import load_metadata
            known = load_metadata().get('extractors', {})
        except Exception:
            known = {}
        # Per-call extractor-args win; fall back to the env/file value that
        # enabled routing here.
        raw = ''
        try:
            values = self._layered_configuration_arg()('force_extractor') or []
            if values:
                raw = str(values[0])
        except Exception:
            pass
        if not raw.strip():
            try:
                raw = str(_shared_config().get('force_extractor') or '')
            except Exception:
                raw = ''
        name = self._forced_name(url, raw)
        if not name or name not in known:
            valid = ', '.join(sorted(known)) if known else '(none)'
            raise ConfigError(
                f'no extractor selected for {url!r} (force_extractor={raw!r}); '
                f'valid names: {valid}',
                solutions=['use the scoped form, e.g. '
                           '--extractor-args "global_extractor:force_extractor=voe@example.com"',
                           'a bare name claims every unmatched URL while set'],
            )
        self._extractor_name = name
        self.to_screen(f'Using manually selected extractor: {name}')
        return super()._real_extract(url)

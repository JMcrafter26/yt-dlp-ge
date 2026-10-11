"""yt-dlp plugin: bridge for JMcrafter26/global-extractor JS extractors.

This package is loaded by yt-dlp's plugin system from the
``yt_dlp_plugins.extractor`` namespace. It reads ``extractors.json``,
dynamically generates one extractor class per enabled provider, and bridges
each request to the corresponding JS module fetched from the repo on demand.

Set ``YTDLP_NO_PLUGINS=1`` to disable all yt-dlp plugins (handled by yt-dlp
itself); this module also guards against registering anything when that
variable is set, so it is safe to import in other contexts.
"""

from __future__ import annotations

import os as _os

# When plugins are disabled yt-dlp never imports this module, but guard anyway
# so importing the package directly (e.g. for tests) is side-effect free.
if _os.environ.get('YTDLP_NO_PLUGINS'):
    __all__: list[str] = []
else:
    from ._config import Config
    from ._extractor import GlobalExtractorIE, GlobalExtractorManualIE, set_bridge
    from ._fetcher import JsFetcher
    from ._jsruntime import Bridge
    from ._registry import build_classes, load_metadata

    # Shared configuration (extractor args are layered on per-call by the bridge).
    _config = Config()
    try:
        _config.validate()
    except Exception:
        # Never let a malformed user config break plugin import; fall back to
        # defaults (per-call extractor args still work).
        _config = Config()
        _config._values.clear()
        from ._config import DEFAULTS as _DEFAULTS
        _config._values.update(_DEFAULTS)

    # Static metadata: name -> {pattern, working, ...}
    _metadata = load_metadata()

    # Fetcher + JS runtime bridge (shared cache across all extractors).
    _fetcher = JsFetcher(_config)
    _bridge = Bridge(_config, _fetcher, names=list(_metadata.get('extractors', {})))
    set_bridge(_bridge)

    # Dynamically generated extractor classes, e.g. VoeIE, DoodstreamIE, ...
    # (config is passed so import-time options like voe_domains apply).
    _classes = build_classes(
        _metadata, force_broken=_config.get_bool('force_broken'), config=_config)

    # Manual-selection fallback (always registered; only claims URLs when
    # force_extractor is set and nothing specific matches).
    _classes['GlobalExtractorManualIE'] = GlobalExtractorManualIE

    # Expose them at module level so yt-dlp's plugin loader finds them.
    globals().update(_classes)
    __all__ = list(_classes)

    # Trigger a best-effort auto-update check in the background of first use;
    # actual per-extraction update checks happen inside the bridge.

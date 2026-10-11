"""``yt-dlp-ge`` command: yt-dlp with plugin shortcuts.

yt-dlp plugins cannot add their own command-line flags, so this thin wrapper
translates a couple of convenience options into the equivalent configuration
and hands everything else to yt-dlp untouched::

    yt-dlp-ge --extractor voe@my-mirror.example "<url>"
    # equivalent to:
    # YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR='voe@my-mirror.example' yt-dlp "<url>"

    yt-dlp-ge --extractor voe "<url>"
    # equivalent to:
    # YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR=voe yt-dlp "<url>"
"""

from __future__ import annotations

import os
import sys

ENV_FORCE_EXTRACTOR = 'YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR'


def parse_wrapper_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """Split ``--extractor`` out of *argv*; return (extractor, rest)."""
    extractor: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--extractor' and i + 1 < len(argv):
            extractor = argv[i + 1]
            i += 2
        elif arg.startswith('--extractor='):
            extractor = arg.split('=', 1)[1]
            i += 1
        else:
            rest.append(arg)
            i += 1
    return extractor, rest


def main(argv: list[str] | None = None) -> int | None:
    argv = sys.argv[1:] if argv is None else list(argv)
    extractor, rest = parse_wrapper_args(argv)
    if extractor:
        os.environ[ENV_FORCE_EXTRACTOR] = extractor
    from yt_dlp import main as ydl_main
    sys.argv = ['yt-dlp', *rest]
    return ydl_main(rest)


if __name__ == '__main__':
    sys.exit(main())

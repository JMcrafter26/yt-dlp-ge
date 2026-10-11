# Contributing

Bug reports and pull requests are welcome. A few notes to save both of us
time.

## Where things live

Extractor logic lives in
[JMcrafter26/global-extractor](https://github.com/JMcrafter26/global-extractor),
not here. This repo is only the bridge: configuration, downloading and
caching the JS files, running them, and mapping URLs to extractors. If a
specific host broke, check whether its extractor still works upstream first —
in most cases the fix belongs there, and this plugin picks it up on the next
cache refresh with no release needed.

## Adding or fixing an extractor

No Python code is required for a new host. Two files matter:

1. `yt_dlp_plugins/extractor/global_extractor/extractors.json` — add an
   entry with a URL `pattern` (a regex fragment matched against the host part)
   and the `working` flag. Set `working` to `false` unless the extractor
   passes the upstream test suite; broken ones are only registered with
   `force_broken=true`.
2. `yt_dlp_plugins/extractor/global_extractor/domains.json` — add the host's
   known domains to its list.

The pattern only needs to cover the canonical domains. Throwaway mirrors go
in `domains.json`, which users can also update themselves without touching a
release.

## Changing code

- Python 3.10+ compatible, no third-party dependencies. yt-dlp is the only
  requirement.
- Line length 120, `ruff check` clean (`pip install ruff`, then run
  `ruff check .` from the repo root).
- Keep `suitable()` self-contained: yt-dlp calls it without a downloader and
  may use lazy loading, so only local imports there and never raise out of
  it — return `False` instead.
- Anything that touches the network must fail gracefully back to cache where
  possible, and every failure path should end up with a useful debug context
  (see `_errors.py`).

## Checking your changes

There is no test suite yet; verify by hand. From the repo root, with a yt-dlp
checkout available:

```bash
# everything still imports and compiles
python3 -m compileall -q yt_dlp_plugins

# plugin loads, expected number of classes (12 working + manual fallback)
PYTHONPATH=/path/to/yt-dlp:. python3 -c "
import yt_dlp_plugins.extractor.global_extractor as m
print(sorted(m.__all__))
"
```

For an extractor change, run a real extraction through `YoutubeDL` with the
page HTML mocked, or against a live URL, and confirm the returned info dict
has the right id and a working format URL. For domain changes, check
`suitable()` against the new mirror URL with and without overrides, e.g.:

```bash
YTDLP_GLOBAL_EXTRACTOR_VOE_DOMAINS='new-mirror.example' PYTHONPATH=/path/to/yt-dlp:. python3 -c "
import yt_dlp_plugins.extractor.global_extractor as m
print(m.VoeIE.suitable('https://new-mirror.example/e/abc123'))
"
```

## Filing issues

- Broken host, wrong stream URL, outdated mirror: include the full URL, the
  `=== global-extractor plugin debug info ===` block from a `yt-dlp -v` run,
  and your yt-dlp version.
- Plugin bug (config ignored, crash, bad error message): same, plus how you
  installed the plugin and which JS runtime you have (`deno --version`,
  `node --version`, …).

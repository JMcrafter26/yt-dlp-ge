# yt-dlp-ge

A [yt-dlp](https://github.com/yt-dlp/yt-dlp) plugin that adds support for the
video hosts covered by
[JMcrafter26/global-extractor](https://github.com/JMcrafter26/global-extractor)
(VOE, DoodStream, StreamTape, FileMoon and others).

The plugin itself contains no extractor logic. It downloads the JavaScript
extractor modules from the global-extractor repo when they are needed, caches
them on disk, and runs them through a JavaScript runtime (deno, node, bun or
quickjs). When a host fixes or breaks something upstream, you usually just get
the fix with the next cache refresh instead of waiting for a plugin release.

## Requirements

- Python 3.10 or newer
- yt-dlp (2024.01.01 or newer)
- One JavaScript runtime. The plugin tries them in this order and uses the
  first one it finds:

  | Runtime | Minimum version |
  | ------- | --------------- |
  | deno    | 2.3.0           |
  | node    | 22.0.0          |
  | bun     | 1.2.11          |
  | quickjs | 2023.12.9       |

  Any single one is enough. If you have none installed, `node` is normally the
  easiest to get.

## Installation

Install with pip:

```bash
pip install yt-dlp-ge
```

Or straight from git:

```bash
pip install git+https://github.com/JMcrafter26/yt-dlp-ge.git
```

Or from a checkout:

```bash
pip install .
```

Alternatively, drop the `yt_dlp_plugins` directory into a folder inside one of
yt-dlp's plugin locations. Note the extra directory level: yt-dlp looks for a
`yt_dlp_plugins` folder *inside* each package folder, so the layout has to be
exactly this:

```bash
mkdir -p ~/.config/yt-dlp/plugins/global-extractor
cp -r yt_dlp_plugins ~/.config/yt-dlp/plugins/global-extractor/
```

That gives you
`~/.config/yt-dlp/plugins/global-extractor/yt_dlp_plugins/extractor/global_extractor/`,
which is what yt-dlp picks up. The other locations yt-dlp searches work the
same way (`%APPDATA%/yt-dlp/plugins` on Windows, `/etc/yt-dlp/plugins`, and
anything on `PYTHONPATH`).

Once installed there is nothing to enable. Just pass a supported URL:

```bash
yt-dlp "https://voe.sx/e/xxxxxxxxxx"
yt-dlp "https://dood.stream/d/xxxxxxxxxx"
```

Pip installs also provide a `yt-dlp-ge` command. It is plain
yt-dlp with one extra flag, `--extractor`, that pins a URL to a specific
extractor (handy for new mirror domains that match nothing yet):

```bash
yt-dlp-ge --extractor voe@my-mirror.example "<url>"
yt-dlp-ge --extractor voe "<url>"
```

Everything else on the command line passes straight through to yt-dlp.

## Configuration

Everything is optional. Values come from three places, in this order:

1. Per-run extractor arguments:
   ```bash
   yt-dlp --extractor-args "global_extractor:js_runtime=node" "<url>"
   ```
2. Environment variables, `YTDLP_GLOBAL_EXTRACTOR_<NAME>`:
   ```bash
   export YTDLP_GLOBAL_EXTRACTOR_JS_RUNTIME=deno
   ```
3. A config file at `~/.config/yt-dlp/plugins/global_extractor.conf`
   (one `key=value` per line, `#` comments allowed):
   ```ini
   js_runtime = auto
   fetch_from_repo = true
   timeout = 30
   ```

| Option            | Default                                            | What it does |
| ----------------- | -------------------------------------------------- | ------------ |
| `fetch_from_repo` | `true`                                             | Download extractor JS from GitHub on demand. With `false`, only previously cached files are used. |
| `repo_url`        | `https://github.com/JMcrafter26/global-extractor`  | Where JS and domain lists are fetched from. Import-time only. |
| `js_runtime`      | `auto`                                             | `auto`, `deno`, `node`, `bun` or `quickjs`. |
| `auto_update`     | `true`                                             | Re-check the repo for newer extractor files every `update_interval` seconds. |
| `force_broken`    | `false`                                            | Also register extractors that are currently broken upstream. Import-time only. |
| `force_extractor` | *(empty)*                                          | Manually assign an extractor to unmatched URLs, `name` or `name@domain`. See below. |
| `voe_domains`     | *(empty)*                                          | Extra VOE mirror domains, comma separated. Import-time only. |
| `cache_dir`       | `~/.cache/yt-dlp/global-extractor`                 | Where downloaded JS and the version snapshot live. Import-time only. |
| `timeout`         | `30`                                               | Seconds each JS runtime gets per attempt. |
| `update_interval` | `86400`                                            | Seconds between automatic update checks. |

Options marked import-time only are read once when yt-dlp loads the plugin,
so set those through the environment or the config file. `--extractor-args`
is evaluated per download and cannot change them (`force_extractor` is the
exception: it needs the env/file value for URL routing, but per-run args can
then narrow it down).

## Supported sites

Registered by default (upstream tests passing):

doodstream, earnvids, mp4upload, packer, sendvid, sibnet, streamtape,
uqload, videospk, vidmoly, vidoza, voe

Only with `force_broken=true` (broken upstream, may or may not work):

bigwarp, dailymotion, filemoon, lulustream, megacloud, oneupload, savefiles,
smoothpre, streamup, supervideo, uploadcx, vk

Also `force_broken`-only, untested (not in the upstream test table):

fourshared, playerwish, streamhg, streamwish

Note: enabling `vk` or `dailymotion` overrides yt-dlp's built-in extractors
for those sites, and `sibnet` already overrides the built-in one. That is
intentional, but only while this plugin is installed.

## Mirror domains

Hosts like VOE rotate throwaway domains (`voe.sx`, `megankiddifference.com`,
whatever comes next) faster than any release cycle. Domain matching therefore
does not live in code. It comes from a `domains.json` list, merged from four
sources, later ones winning:

1. The `domains.json` file shipped with the plugin.
2. A copy fetched from the repo during update checks (`domains.json`, falling
   back to `global-extractor/domains.json`). If the file is not there, the
   cached copy is kept, so nothing breaks. If you maintain such a file in the
   global-extractor repo in the same shape, every install picks up new
   mirrors on its next check.
3. Your own overrides in
   `~/.config/yt-dlp/plugins/global_extractor_domains.json`:
   ```json
   {"domains": {"voe": ["new-mirror.example"]}}
   ```
4. The `voe_domains` option, merged into the `voe` list.

New entries take effect without reinstalling (at most a minute delay from an
internal cache). If you find a new mirror, adding it to source 2 or 3 is
usually all it takes.

## Manual extractor selection

If a URL matches no extractor at all — typically a brand-new mirror domain —
you can assign one by hand:

```bash
YTDLP_GLOBAL_EXTRACTOR_FORCE_EXTRACTOR='voe@my-mirror.example' yt-dlp "<url>"
```

Entries are `name` or `name@domain`, comma separated for several at once. The
scoped `name@domain` form only claims that host and is what you should use. A
bare `name` claims every otherwise-unmatched URL while it is set, including
unrelated sites, so only use it for that one command.

Two things to know: routing happens before yt-dlp hands anything to the
plugin, so the env var (or config file) is required to get the URL routed to
the fallback in the first place; `--extractor-args` alone cannot do that.
And the fallback never overrides a specific extractor — if a normal match
exists, it handles the URL. An unknown extractor name fails with the list of
valid ones.

## Updating

With `auto_update` left on (the default), the plugin compares the repo's
version file against its cache at most once per `update_interval` and
re-downloads changed extractor files by itself.

To force a refresh without downloading anything:

```bash
python - <<'PY'
from yt_dlp_plugins.extractor.global_extractor._config import Config
from yt_dlp_plugins.extractor.global_extractor._fetcher import JsFetcher
from yt_dlp_plugins.extractor.global_extractor._registry import load_metadata

cfg = Config()
fetcher = JsFetcher(cfg)
names = list(load_metadata()['extractors'])
print('changed:', fetcher.update(force=True, names=names))
PY
```

To stop all network access to GitHub (the video pages themselves are still
fetched by yt-dlp), set `fetch_from_repo=false`. Only cached extractor files
are used then.

## Troubleshooting

**No JS runtime found.** Install one of deno, node 22+, bun or quickjs and
make sure it is on `PATH`. The plugin reports which runtimes it tried when
you run with `-v`.

**A download fails.** Re-run with `yt-dlp -v` and look for the
`=== global-extractor plugin debug info ===` block. It records the plugin and
yt-dlp versions, every runtime attempt and why it failed, HTTP statuses, the
JS error output, and the exact extractor file that ran. That block is what to
paste into a bug report.

**A site that used to work broke.** The host probably changed its pages. Force
a cache refresh (see above) — upstream fixes usually land within hours and
flow through automatically. If it is still broken afterwards, file an issue
at <https://github.com/JMcrafter26/yt-dlp-ge/issues> with the debug block.

**A new mirror domain is not recognized.** Add it via `voe_domains`, the
`global_extractor_domains.json` override file, or `force_extractor` as a
stopgap, as described above.

## How it works

Each upstream extractor file contains a marked section with a function that
takes page HTML and returns a stream URL. The plugin downloads the embed page
with yt-dlp, fetches the matching JS file (or takes it from cache), wraps it
in a small generated harness that feeds it the HTML over stdin and reads the
result back from stdout, and runs that through the first working JS runtime.
The returned URL becomes a normal yt-dlp format.

```
yt_dlp_plugins/extractor/global_extractor/
├── __init__.py      # entry point, registers the generated classes
├── _config.py       # layered configuration
├── _fetcher.py      # JS download, cache, version tracking, domain refresh
├── _jsruntime.py    # runtime detection, harness generation, execution
├── _errors.py       # exceptions, debug log, issue template
├── _registry.py     # builds extractor classes from metadata + domains
├── _extractor.py    # the one bridge extractor class, plus manual fallback
├── extractors.json  # static metadata: name, URL pattern, working flag
└── domains.json     # updatable domain lists per extractor
```

The `js/` cache directory is created under `cache_dir` at runtime. Nothing is
bundled.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This extension is under a custom license ([LICENSE](LICENSE)): personal,
non-commercial use, with verbatim redistribution allowed. Commercial use and
incorporating it into other software, products, services, or download/scraping
sites of any kind are prohibited without written permission.

That license covers only the code in this repository. It does **not** cover
the JavaScript extractor modules: those are downloaded at runtime from the
[global-extractor](https://github.com/JMcrafter26/global-extractor) project
and remain under that project's own license. See [NOTICE](NOTICE) for the
full statement.

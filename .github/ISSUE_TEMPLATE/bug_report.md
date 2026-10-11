---
name: Bug report
about: Something that used to work broke, or never worked
title: "[bug] "
labels: bug
---

## What happened

<!-- One or two sentences: which URL, what you expected, what you got. -->

## Environment

- Plugin version/install method:
- yt-dlp version (`yt-dlp --version`):
- OS and Python version:
- JS runtime (`deno --version` / `node --version` / ...):
- Config in use (env vars, config file entries, extractor args — redact secrets):

## Debug info

<!-- Re-run with `yt-dlp -v` and paste the
`=== global-extractor plugin debug info ===` block here, in full. -->

```
(paste here)
```

## Checklist

- [ ] I forced a cache refresh and the problem persists (see README, Updating)
- [ ] For a new mirror domain, I tried `force_extractor` / the domains override file

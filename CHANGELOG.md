# Changelog

## 0.1.0 — initial release

- Bridge plugin: one generated yt-dlp extractor per global-extractor host,
  JS fetched on demand from the upstream repo and executed via
  deno / node / bun / quickjs with fallbacks.
- 12 working extractors registered by default; 16 more behind
  `force_broken`.
- Updatable `domains.json` (bundled, repo-fetched, user overrides) for
  rotating mirror domains without code changes.
- Manual extractor selection (`force_extractor`, `name` or `name@domain`)
  for URLs nothing claims.
- Layered configuration (extractor args, environment, config file),
  automatic cache updates, offline mode, debug log plus issue template on
  failures.

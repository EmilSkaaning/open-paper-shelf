# Vendored pdf.js viewer

`web/viewer.mjs`, `web/viewer.css`, and `web/viewer.html` are the built
standalone viewer app from pdf.js **v4.10.38**'s GitHub Release zip. This
build output isn't published to npm, so unlike the engine/worker/cmaps/fonts
(loaded from jsDelivr's `pdfjs-dist` package — see `viewer.mjs`'s
`AppOptions` defaults), it can't be served from a CDN and has to stay
vendored here.

Two local patches on top of the stock v4.10.38 build:

- `web/viewer.mjs`: `cMapUrl`, `standardFontDataUrl`, `workerSrc`, and
  `sandboxBundleSrc` in `defaultOptions` are hardcoded to jsDelivr URLs
  instead of the stock relative paths (`../web/cmaps/`, etc.) — see commit
  `0eba2db`.
- `web/viewer.html`: one added `<script type="module" src="autosave.mjs">`
  tag, loading our custom highlight-autosave integration (see
  `web/autosave.mjs`, which only calls pdf.js's public API and isn't a
  patch of pdf.js itself).

`web/viewer.css` is unmodified stock.

## Upgrading pdf.js

Don't hand-merge a newer release on top of these files. Instead:

1. Download the new version's Release zip from
   https://github.com/mozilla/pdf.js/releases and take its `web/viewer.mjs`,
   `web/viewer.css`, and `web/viewer.html` as-is.
2. Re-apply the two patches above (CDN URLs + autosave script tag),
   updating the pinned `pdfjs-dist@4.10.38` version string in the CDN URLs
   to match.
3. Diff against this repo's previous `viewer.mjs`/`viewer.html` to confirm
   only those patches differ from stock.

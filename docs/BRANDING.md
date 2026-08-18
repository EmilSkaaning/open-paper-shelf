# Brand Guide — Paper Butler

This is the source of truth for the app's visual identity. Any agent or
contributor touching UI copy, colors, fonts, or icons should follow this
guide rather than improvising. Personality: **clean & academic** — a
personal research library, not a corporate SaaS dashboard.

## Palette — "Midnight Athenaeum"

The app defaults to dark mode, and now also supports an explicit light
mode for users who prefer it. This replaces the earlier "Forest Library"
green palette, which read as "dirty" in practice. The mechanism
(dark-first base, serif/sans typography pairing, layout) is unchanged;
only the hue family moved from green to a purple-leaning midnight base
with a sparing gold accent.

### Dark (default)

| Token | Hex | Use |
|---|---|---|
| Background | `#0D0B13` | app background |
| Secondary background | `#191420` | sidebar, cards, containers |
| Primary accent | `#8B7FD6` | buttons, links, active states |
| Text | `#E8E4EF` | body/heading text |
| Muted text | `#9C97AD` | captions, timestamps, secondary UI |
| Accent 2 (sparingly) | `#D4AF37` | rare highlight only — never a primary button color |

### Light

| Token | Hex | Use |
|---|---|---|
| Background | `#F7F4FC` | app background |
| Secondary background | `#ECE6F7` | sidebar, cards, containers |
| Primary accent | `#5B4FA8` | buttons, links, active states |
| Text | `#221934` | body/heading text |
| Muted text | `#6B6180` | captions, timestamps, secondary UI |
| Accent 2 (sparingly) | `#9C7A12` | rare highlight only — never a primary button color |

The light-mode accents are deeper/more saturated than their dark-mode
counterparts (`#5B4FA8` vs `#8B7FD6`, `#9C7A12` vs `#D4AF37`) because the
dark-mode values fail AA contrast on a light background — verify contrast
again if either accent is adjusted.

Configured in `.streamlit/config.toml` under `[theme]`. Don't hardcode
these hex values elsewhere in Python/CSS if a Streamlit theme token or the
existing `_FONT_CSS` block in `frontend/branding.py` can express it instead.

## Typography

- **Headings / wordmark**: Source Serif 4 (serif, literary)
- **Body / UI**: Inter (sans-serif, Streamlit-app standard)

Loaded via the Google Fonts CSS injection in `frontend/branding.py`'s
`apply_branding()` — call it once per page, right after
`st.set_page_config`. If the deployed Streamlit version is bumped to
≥1.35, prefer migrating this to native `[theme.fontFaces]` in
`config.toml` instead of the CSS-injection workaround.

## Logo / Icon

**Status: Final.** The mark is the **Open Book & Butler's Bow Tie**
concept — a large `#8B7FD6` violet bow tie with a gold `#D4AF37` knot
floating above a `#8B7FD6` violet open-book outline, on a solid
rounded-square `#191420` background plate (matching the dark-theme
secondary background) so contrast stays consistent whether the
surrounding UI is in light or dark mode. Shipped as
`frontend/assets/paper-butler-logo.svg`, wired into
`st.set_page_config(page_icon=...)` and `st.logo(...)` in `app.py`.

Generation prompt for [nanobanana](https://ai.google.dev) (Gemini) that
produced the shipped asset — swap `#8B7FD6` / `#D4AF37` for final chosen
accent hexes if they change, and swap `#191420` if the plate color
changes:

> Flat vector app icon on a solid rounded-square `#191420` background
> plate: an open book viewed from the front, pages fanned in a gentle
> curve, rendered as a clean `#8B7FD6` violet outline; above the book,
> not touching the spine, place a large bow tie in solid `#8B7FD6`
> violet — sized roughly as wide as the book itself, clearly the
> dominant shape of the icon — with a small gold `#D4AF37` square knot
> at its center, as if the book is greeting the reader wearing a
> butler's bow tie. No gradients, hard geometric edges.

A dedicated wordmark lockup (icon + "Paper Butler", for
`st.logo(image=...)`'s separate wordmark slot) has not been generated
yet — currently the icon alone is passed to `st.logo(...)`.

## Do / Don't

- Do keep a single accent color per screen; don't introduce new brand
  colors ad hoc.
- Do reuse `STATUS_ICONS` / emoji conventions already in
  `frontend/constants.py` rather than inventing new iconography inline.
- Don't add a second font family without updating this doc first.

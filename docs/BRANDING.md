# Brand Guide — Open Paper Shelf

This is the source of truth for the app's visual identity. Any agent or
contributor touching UI copy, colors, fonts, or icons should follow this
guide rather than improvising. Personality: **clean & academic** — a
personal research library, not a corporate SaaS dashboard.

## Palette — "Forest Library" (dark, default)

The app defaults to dark mode. Do not introduce a light theme without an
explicit decision to support one.

| Token | Hex | Use |
|---|---|---|
| Background | `#14170F` | app background |
| Secondary background | `#1E241A` | sidebar, cards, containers |
| Primary accent | `#6FA97A` | buttons, links, active states |
| Text | `#EDE7D9` | body/heading text |
| Muted text | `#A8AC9E` | captions, timestamps, secondary UI |
| Accent 2 (sparingly) | `#C9A227` | rare highlight only — never a primary button color |

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

**Status: WIP, not final.** A first-draft mark (open-book concept, gold
line art on the dark background) lives at `frontend/assets/logo.png` and
is wired into `st.set_page_config(page_icon=...)` and `st.logo(...)` in
`app.py` as a placeholder so the direction is visible in the running app.
Both the palette and this asset are still under active iteration — expect
them to change before this branch is considered done.

Once a final asset is chosen, it should ship as:

- A square icon (works as favicon and Streamlit `st.logo(icon_image=...)`)
- A wordmark lockup (icon + "Open Paper Shelf", for `st.logo(image=...)`)

## Do / Don't

- Do keep a single accent color per screen; don't introduce new brand
  colors ad hoc.
- Do reuse `STATUS_ICONS` / emoji conventions already in
  `frontend/constants.py` rather than inventing new iconography inline.
- Don't add a second font family without updating this doc first.

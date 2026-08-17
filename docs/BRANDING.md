# Brand Guide — Paper Butler

This is the source of truth for the app's visual identity. Any agent or
contributor touching UI copy, colors, fonts, or icons should follow this
guide rather than improvising. Personality: **clean & academic** — a
personal research library, not a corporate SaaS dashboard.

## Palette — "Midnight Athenaeum"

The app defaults to dark mode, and now also supports an explicit light
mode for users who prefer it. This replaces the earlier "Forest Library"
green palette, which read as "dirty" in practice — see
[paper-butler/docs/BRANDING_NOTES.md](../paper-butler/docs/BRANDING_NOTES.md)
for that history. The mechanism (dark-first base, serif/sans typography
pairing, layout) is unchanged; only the hue family moved from green to a
purple-leaning midnight base with a sparing gold accent.

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

**Status: WIP, not final.** The original open-book mark at
`frontend/assets/logo.png` (wired into `st.set_page_config(page_icon=...)`
and `st.logo(...)` in `app.py` as a placeholder) is being replaced now
that the palette moved to Midnight Athenaeum. Two candidate concepts are
shortlisted, both drawn as a flat single-color silhouette on a transparent
background so they scale from a full logo lockup down to a 16px favicon:

- **Bow-Tie Spine** — a bow tie built from two page-corner triangles
  around a small gold knot. Simplest shape of the shortlist; holds up
  best at favicon size.
- **Quill & Ink** — a feather silhouette with a gold ink drop at the nib.

Generation prompts for [nanobanana](https://ai.google.dev) (Gemini), three
distinct composition takes per concept — swap `#8B7FD6` for the final
chosen accent hex if it changes:

### Bow-Tie Spine prompts

> **1 — flat geometric (front-on):** Flat vector icon of a minimalist bow
> tie viewed straight-on, both wings sharp triangular page corners, small
> rounded square knot at center, single color `#8B7FD6` violet silhouette
> on transparent background, hard geometric edges, no gradients, app icon
> style.

> **2 — 3D-ish folded ribbon:** Vector app icon of a bow tie rendered as a
> folded ribbon of paper, subtle darker violet shading on the inner fold
> creases to suggest depth, gold `#D4AF37` knot wrapped around the
> center, otherwise a single `#8B7FD6` violet form, transparent
> background, slight dimensionality but still flat/print-style, no
> photorealism.

> **3 — open book viewed from above, bow-tie silhouette:** Flat vector
> icon of an open book viewed directly from above, its two page-spreads
> fanned outward and pinched at the spine so the overall silhouette reads
> as a bow tie, small gold `#D4AF37` rectangle marking the spine/knot,
> single `#8B7FD6` violet silhouette, transparent background, clean
> minimal curves, app icon style.

### Quill & Ink prompts

> **1 — classic feather, ink drop:** Flat vector icon of a single quill
> feather, long sweeping curved silhouette from tip to nib, faint barb
> lines along the spine, small round ink drop in `#D4AF37` gold at the
> nib tip, rest of the icon a single `#8B7FD6` violet silhouette,
> transparent background, clean minimal linework, app icon style.

> **2 — quill nib as monogram:** Flat vector icon of a calligraphy quill
> nib split into two pointed tines forming a narrow "V", the gap between
> the tines filled with a small gold `#D4AF37` ink droplet, feather shaft
> rendered as a short single `#8B7FD6` violet stroke above it, transparent
> background, bold graphic mark rather than a full feather, app icon
> style, reads clearly at 16px.

> **3 — quill writing across an open page:** Flat vector icon of a quill
> laid diagonally across a small open book, feather silhouette in
> `#8B7FD6` violet, book rendered only as two thin curved page-edge lines
> beneath it in the same violet, a single gold `#D4AF37` dot where the
> nib meets the page, transparent background, minimal flat composition,
> app icon style.

Once a final asset is chosen, it should ship as:

- A square icon (works as favicon and Streamlit `st.logo(icon_image=...)`)
- A wordmark lockup (icon + "Paper Butler", for `st.logo(image=...)`)

## Do / Don't

- Do keep a single accent color per screen; don't introduce new brand
  colors ad hoc.
- Do reuse `STATUS_ICONS` / emoji conventions already in
  `frontend/constants.py` rather than inventing new iconography inline.
- Don't add a second font family without updating this doc first.

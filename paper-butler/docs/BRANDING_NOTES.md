# Branding iteration notes

Working notes for the `feat/17-branding` WIP branch. See
[docs/BRANDING.md](../../docs/BRANDING.md) at the repo root for the
current shipped palette/typography/logo reference — this file tracks
open feedback and next steps, not the settled state.

## Status (2026-08-04)

- Colors, fonts, and layout implementation (`.streamlit/config.toml`,
  `frontend/branding.py`, the centered logo-over-title header) are
  working well structurally — no further plumbing changes needed there.
- The **green hue of the "Forest Library" palette reads as "dirty"** in
  the running app and needs to change. The mechanism (dark base theme,
  serif/sans pairing, centered logo lockup) stays; only the accent color
  family is in question.

## Logo concept — keep and iterate

The winning direction from the original nanobanana prompt set was the
**open book viewed from above** concept:

> "Flat vector icon of a simplified open book viewed from above, two
> symmetric curved page 'wings', with a small folded page-corner detail
> on the right side, single color `#6FA97A` forest green silhouette on
> transparent background, clean geometric curves, app icon style"

Next iteration should reuse this exact composition (symmetric curved
wings + folded corner, flat single-color silhouette) and only vary the
accent color once a replacement palette is chosen below - don't
regenerate the shape from scratch.

## Open question: replace the green accent

The `#6FA97A` forest green (and the `#14170F` / `#1E241A` dark-green
backgrounds it sits on) is the part under reconsideration. Options to
explore next session, still within the "clean & academic, dark-mode
default" brief:

- A different accent hue on the same warm dark-neutral base (e.g. swap
  green for a muted gold/brass, a deep academic blue, or an oxblood -
  see the "Journal Ivory" and "arXiv Neutral" alternatives already
  scouted in `docs/BRANDING.md`'s history, adapted to dark mode).
- A less saturated / higher-value green if the "dirty" read is about
  saturation rather than hue itself - worth testing before abandoning
  green altogether, since the logo concept was designed around it.
- Re-run the dark-mode WCAG contrast check (was AA-passing for
  `#6FA97A` on `#14170F`) for whatever accent replaces it.

Once a direction is picked, update the palette table in
[docs/BRANDING.md](../../docs/BRANDING.md), `.streamlit/config.toml`, and
regenerate the logo asset in the new accent color before replacing
`frontend/assets/logo.png`.

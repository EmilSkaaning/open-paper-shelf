# Branding iteration notes

Working notes for the `feat/17-branding` WIP branch. See
[docs/BRANDING.md](../../docs/BRANDING.md) at the repo root for the
current shipped palette/typography/logo reference — this file tracks
open feedback and next steps, not the settled state.

## Status (2026-08-14)

- Colors, fonts, and layout implementation (`.streamlit/config.toml`,
  `frontend/branding.py`, the centered logo-over-title header) are
  working well structurally — no further plumbing changes needed there.
- The green accent question is **resolved**: the palette moved from
  "Forest Library" (green) to **"Midnight Athenaeum"** (purple-leaning
  midnight base, sparing gold accent), with an explicit light-mode
  variant added alongside the dark default. See the palette tables in
  [docs/BRANDING.md](../../docs/BRANDING.md).
- The logo concept is **open again** — the open-book mark is being
  replaced (see below) rather than just recolored, since the new palette
  prompted a broader icon rethink.

## Logo concept — shortlist

Four new concepts were mocked up (quill & ink, bookmark ribbon monogram,
owl-in-book, bow-tie book spine). Two are shortlisted for nanobanana
generation:

- **Bow-Tie Spine** — bow tie built from two page-corner triangles around
  a small gold knot. Simplest shape; holds up best at favicon size.
- **Quill & Ink** — feather silhouette with a gold ink drop at the nib.

The open-book concept below is kept for reference/fallback but is not the
current front-runner.

> Original open-book prompt (superseded): "Flat vector icon of a
> simplified open book viewed from above, two symmetric curved page
> 'wings', with a small folded page-corner detail on the right side,
> single color `#6FA97A` forest green silhouette on transparent
> background, clean geometric curves, app icon style"

### Bow-Tie Spine & Quill & Ink — nanobanana prompts

The full set of 3 distinct nanobanana prompt variants per concept (flat
geometric / dimensional / open-book-derived for Bow-Tie Spine; classic
feather / nib monogram / writing-across-a-page for Quill & Ink) now lives
directly in [docs/BRANDING.md](../../docs/BRANDING.md)'s Logo / Icon
section — kept there as the single source of truth so the two docs don't
drift out of sync.

## Next steps

1. Generate both concepts via nanobanana using the prompts above and
   compare in the running app.
2. Pick a winner (or iterate further on one).
3. Regenerate as clean vector paths, ship as both a square icon and a
   wordmark lockup (see [docs/BRANDING.md](../../docs/BRANDING.md)'s
   Logo / Icon section), and replace `frontend/assets/logo.png`.
4. Re-run the WCAG AA contrast check for `#8B7FD6` on `#14101F` (dark)
   and `#5B4FA8` on `#F7F4FC` (light) once the asset is final.

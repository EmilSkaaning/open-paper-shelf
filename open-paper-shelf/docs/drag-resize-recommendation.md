# Recommendation: draggable divider between PDF and metadata panels

## Question

Should the PDF/metadata panel split (`src/frontend/app.py`, `col_pdf, col_meta = st.columns(...)`)
support a user-draggable divider, in addition to the fixed-ratio fix in issue #19?

## Recommendation: defer

Do not build a custom drag-to-resize component now. Track it as a follow-up tied to any future
migration off Streamlit.

## Rationale

- Streamlit has no native resizable/draggable split-pane support. `st.columns` only accepts a
  fixed ratio per render; achieving drag-resize would require a custom bidirectional Streamlit
  component (a JS frontend bundle plus a Python bridge via `streamlit.components.v1`).
- This repo has already tried and reverted a hand-rolled CSS/HTML approach in Streamlit
  (commit `5ba693e`, custom icon styling) because it didn't render reliably across environments
  and doesn't port cleanly to a future frontend rewrite. A custom resize component is a larger,
  higher-risk version of the same problem: it would need its own build step, browser-compat
  testing, and maintenance, for a "nice to have" rather than a blocking UX issue.
- The one-line column-ratio change in this PR (`st.columns([3.5, 1])`) already addresses the
  actual complaint (too little room for the PDF) at effectively zero cost or risk.

## When to revisit

Reconsider drag-resize if/when the frontend moves off Streamlit to a framework with native
split-pane/resizable-panel primitives (e.g. a React-based rewrite). At that point a draggable
divider becomes a small addition rather than a bespoke component to build and maintain.

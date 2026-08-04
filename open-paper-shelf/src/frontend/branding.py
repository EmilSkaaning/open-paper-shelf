"""Brand typography for the Streamlit frontend.

Injects the "Forest Library" brand fonts (Source Serif 4 for headings,
Inter for body/UI text) via a CSS override, since Streamlit's built-in
`font` theme key only accepts "sans serif" / "serif" / "monospace" on the
Streamlit version this app targets - see docs/BRANDING.md.
"""

import streamlit as st

_FONT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400;600;700&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

h1, h2, h3, [data-testid="stHeading"] {
    font-family: 'Source Serif 4', serif;
}
</style>
"""


def apply_branding() -> None:
    """Injects the Open Paper Shelf brand fonts into the current page.

    Must be called once per page, after `st.set_page_config`.

    Args:
        None

    Returns:
        None
    """
    st.markdown(_FONT_CSS, unsafe_allow_html=True)

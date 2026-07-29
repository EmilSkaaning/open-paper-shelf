"""Small text-manipulation helpers shared across the frontend."""

import re


def strip_pdf_suffix(name: str) -> str:
    """Removes a trailing .pdf extension from a paper title, if present.

    Args:
        name: The candidate title, typically derived from an uploaded
            filename or user-edited text.

    Returns:
        str: `name` with a trailing ".pdf" (any case) suffix removed.
        Falls back to the original `name` if stripping it would leave an
        empty string (e.g. a file literally named ".pdf").
    """
    stripped = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return stripped if stripped else name

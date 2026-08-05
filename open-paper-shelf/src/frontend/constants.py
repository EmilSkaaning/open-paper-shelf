"""Shared constants for the Streamlit frontend."""

from backend.huggingface_client import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GENERATION_MODEL,
)

GENERATE_METADATA_HELP = (
    f"Generates a title, abstract, and tags with {DEFAULT_GENERATION_MODEL}, "
    f"and a similarity-detection embedding with {DEFAULT_EMBEDDING_MODEL}."
)

BULK_GENERATE_DELAY_SECONDS: float = 1.5


STATUS_ICONS: dict[str, str] = {
    "Unread": "📄",
    "Reading": "📖",
    "Read": "✅",
    "TODO": "📌",
}

STATUS_LABELS: dict[str, str] = {
    status: f"{icon} {status}" for status, icon in STATUS_ICONS.items()
}
LABEL_TO_STATUS: dict[str, str] = {
    label: status for status, label in STATUS_LABELS.items()
}

# Not a ReadingStatus - a paper's reading status (Unread/Reading/Read/TODO)
# and its similar-embedding flag are independent, so a paper can be e.g.
# both "Unread" and duplicate-flagged at once. This is a pseudo-option added
# to the status filter multiselect (alongside STATUS_LABELS) that filters by
# `get_duplicate_pids` membership instead of by `status`.
SIMILAR_FILTER_LABEL: str = "⚠️ Similar"

PDF_FILENAME: str = "paper.pdf"
EDITED_PDF_FILENAME: str = "paper_edited.pdf"
META_FILENAME: str = "meta.json"
PDF_MIME_TYPE: str = "application/pdf"
JSON_MIME_TYPE: str = "application/json"
PAPER_ID_PATTERN: str = r"^[a-f0-9]{32}$"
DEFAULT_FASTAPI_URL: str = "http://localhost:8000"

# Above this many skipped duplicate files, the upload notice shows just the
# count instead of listing every filename, so a large batch can't produce an
# unreadably long message.
MAX_DUPLICATE_NAMES_TO_LIST: int = 5

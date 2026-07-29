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

PDF_FILENAME: str = "paper.pdf"
META_FILENAME: str = "meta.json"
PDF_MIME_TYPE: str = "application/pdf"
JSON_MIME_TYPE: str = "application/json"
PAPER_ID_PATTERN: str = r"^[a-f0-9]{32}$"
DEFAULT_FASTAPI_URL: str = "http://localhost:8000"

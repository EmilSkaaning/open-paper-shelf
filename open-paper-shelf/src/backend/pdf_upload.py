"""Validating and persisting a browser-edited PDF's saved bytes.

The pdf.js viewer embedded by the frontend lets users highlight a paper's
PDF directly in the browser; those edits are auto-saved by the viewer's own
JavaScript, which POSTs the re-serialized PDF bytes back to this backend.
This module validates the bytes are a real PDF and persists them as the
paper's "edited" copy, separate from the untouched "raw" original, then
syncs the change to the library's Google Drive index.
"""

import logging
import re
from io import BytesIO
from pathlib import Path

from google.oauth2.credentials import Credentials
from pypdf import PdfReader

from backend.drive import (
    PAPERS_DIR,
    get_papers_folder,
    upload_file_to_folder,
    upload_library_index,
)
from backend.models import LibraryIndex, PaperIndexEntry

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES: bytes = b"%PDF-"
PDF_MIME_TYPE: str = "application/pdf"
EDITED_PDF_FILENAME: str = "paper_edited.pdf"
INDEX_FILENAME: str = "id-mapping.json"
MAX_EDITED_PDF_BYTES: int = 50 * 1024 * 1024

# Mirrors frontend.constants.PAPER_ID_PATTERN; duplicated (rather than
# imported) so the backend package does not depend on the frontend package.
PAPER_ID_PATTERN: str = r"^[a-f0-9]{32}$"
# Google Drive file/folder IDs are alphanumeric plus '-'/'_'. Enforcing this
# before lib_id/pid are used to build filesystem paths rejects path-traversal
# payloads (e.g. "..", "/") before they ever reach the filesystem.
DRIVE_ID_PATTERN: str = r"^[A-Za-z0-9_-]{10,100}$"


class InvalidPdfError(ValueError):
    """Raised when uploaded bytes are not a real, parseable PDF."""


class InvalidIdError(ValueError):
    """Raised when a lib_id or paper_id fails the safe-identifier check."""


def validate_pdf_bytes(data: bytes) -> None:
    """Validates that `data` is a real, parseable PDF.

    Does not trust the uploaded file's name or MIME type - checks the PDF
    magic bytes and performs a real parse, so garbage or renamed non-PDF
    files are rejected rather than silently accepted.

    Args:
        data: The raw bytes of the uploaded/auto-saved file.

    Raises:
        InvalidPdfError: If `data` does not start with the PDF magic bytes,
            has no pages, or cannot be parsed as a PDF.
    """
    if not data.startswith(PDF_MAGIC_BYTES):
        raise InvalidPdfError("File does not look like a PDF.")
    try:
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) == 0:
            raise InvalidPdfError("PDF has no pages.")
    except InvalidPdfError:
        raise
    except Exception as e:
        raise InvalidPdfError(f"File could not be parsed as a PDF: {e}") from e


def validate_drive_id(value: str, label: str = "identifier") -> None:
    """Validates that `value` is safe to use as a Google Drive ID / path segment.

    Args:
        value: The candidate lib_id or folder ID.
        label: A human-readable name for the value, used in the error message.

    Raises:
        InvalidIdError: If `value` does not match DRIVE_ID_PATTERN.
    """
    if not re.match(DRIVE_ID_PATTERN, value):
        raise InvalidIdError(f"Invalid {label}: {value!r}")


def validate_paper_id(value: str) -> None:
    """Validates that `value` is a well-formed paper ID.

    Args:
        value: The candidate paper ID.

    Raises:
        InvalidIdError: If `value` does not match PAPER_ID_PATTERN.
    """
    if not re.match(PAPER_ID_PATTERN, value):
        raise InvalidIdError(f"Invalid paper id: {value!r}")


def persist_edited_pdf(
    creds: Credentials,
    paper_info: PaperIndexEntry,
    local_edited_path: Path,
    data: bytes,
) -> PaperIndexEntry:
    """Validates, writes locally, and uploads an edited PDF's bytes.

    Nothing is written to disk or uploaded to Drive if `data` fails
    validation.

    Args:
        creds: The Google OAuth credentials.
        paper_info: The paper's current index entry; `paper_info.folder_id`
            is used as the Drive folder to upload into.
        local_edited_path: Local destination path for the edited copy.
        data: The raw bytes of the browser-annotated PDF.

    Returns:
        PaperIndexEntry: `paper_info` updated with the new
        `edited_pdf_file_id`.

    Raises:
        InvalidPdfError: If `data` is not a valid PDF.
    """
    validate_pdf_bytes(data)
    local_edited_path.write_bytes(data)
    file_id = upload_file_to_folder(
        creds,
        paper_info.folder_id,
        local_edited_path,
        EDITED_PDF_FILENAME,
        PDF_MIME_TYPE,
    )
    return paper_info.model_copy(update={"edited_pdf_file_id": file_id})


def save_edited_pdf(
    creds: Credentials, lib_id: str, pid: str, data: bytes
) -> PaperIndexEntry:
    """Persists an auto-saved, annotated PDF and syncs the library index.

    Validates `lib_id`/`pid` and `data`, writes the edited copy into the
    local cache, uploads it to the paper's Drive folder, then updates and
    re-uploads the library's id-mapping.json so the new edited copy is
    discoverable on any device.

    Args:
        creds: The Google OAuth credentials.
        lib_id: The Google Drive folder ID of the library.
        pid: The paper's unique ID.
        data: The raw bytes of the browser-annotated PDF.

    Returns:
        PaperIndexEntry: The paper's index entry, updated with the new
        `edited_pdf_file_id`.

    Raises:
        InvalidIdError: If `lib_id` or `pid` is not a safe identifier.
        InvalidPdfError: If `data` exceeds the size cap or is not a valid PDF.
        FileNotFoundError: If no local library index exists for `lib_id`.
        KeyError: If `pid` is not present in the library index.
    """
    validate_drive_id(lib_id, label="library id")
    validate_paper_id(pid)
    if len(data) > MAX_EDITED_PDF_BYTES:
        raise InvalidPdfError("Edited PDF exceeds the maximum allowed size.")
    validate_pdf_bytes(data)

    local_lib_dir = PAPERS_DIR / lib_id
    local_index_path = local_lib_dir / INDEX_FILENAME
    if not local_index_path.exists():
        raise FileNotFoundError(f"No local library index for lib_id={lib_id!r}")
    index = LibraryIndex.model_validate_json(
        local_index_path.read_text(encoding="utf-8")
    )
    paper_info = index.papers.get(pid)
    if paper_info is None:
        raise KeyError(f"Unknown paper id {pid!r} in library {lib_id!r}")

    local_paper_dir = local_lib_dir / pid
    local_paper_dir.mkdir(parents=True, exist_ok=True)
    local_edited_path = local_paper_dir / EDITED_PDF_FILENAME

    updated_entry = persist_edited_pdf(creds, paper_info, local_edited_path, data)
    updated_index = index.model_copy(
        update={"papers": {**index.papers, pid: updated_entry}}
    )
    local_index_path.write_text(
        updated_index.model_dump_json(indent=2), encoding="utf-8"
    )

    papers_folder_id = get_papers_folder(creds, lib_id)
    upload_library_index(creds, papers_folder_id, updated_index)

    return updated_entry

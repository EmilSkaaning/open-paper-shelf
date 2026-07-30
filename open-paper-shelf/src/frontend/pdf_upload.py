"""Validating and persisting a user re-uploaded, browser-annotated PDF copy.

The browser's native PDF viewer (e.g. Chrome, Firefox) already lets users
highlight and comment on a paper's PDF; those edits only exist as an
in-browser or downloaded file until the user re-uploads that file here. This
module validates the re-uploaded bytes are a real PDF and persists them as
the paper's "edited" copy, separate from the untouched "raw" original.
"""

import logging
from io import BytesIO
from pathlib import Path

from google.oauth2.credentials import Credentials
from pypdf import PdfReader

from backend.drive import upload_file_to_folder
from backend.models import PaperIndexEntry
from frontend.constants import EDITED_PDF_FILENAME, PDF_MIME_TYPE

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES: bytes = b"%PDF-"


class InvalidPdfError(ValueError):
    """Raised when uploaded bytes are not a real, parseable PDF."""


def validate_pdf_bytes(data: bytes) -> None:
    """Validates that `data` is a real, parseable PDF.

    Does not trust the uploaded file's name or MIME type - checks the PDF
    magic bytes and performs a real parse, so garbage or renamed non-PDF
    files are rejected rather than silently accepted.

    Args:
        data: The raw bytes of the user-uploaded file.

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


def persist_edited_pdf(
    creds: Credentials,
    paper_info: PaperIndexEntry,
    local_edited_path: Path,
    data: bytes,
) -> PaperIndexEntry:
    """Validates, writes locally, and uploads a re-uploaded edited PDF.

    Nothing is written to disk or uploaded to Drive if `data` fails
    validation.

    Args:
        creds: The Google OAuth credentials.
        paper_info: The paper's current index entry; `paper_info.folder_id`
            is used as the Drive folder to upload into.
        local_edited_path: Local destination path for the edited copy.
        data: The raw bytes of the user re-uploaded, browser-annotated PDF.

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

"""Unit tests for validating and persisting user re-uploaded edited PDFs."""

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from backend.models import PaperIndexEntry
from frontend.constants import EDITED_PDF_FILENAME, PDF_MIME_TYPE
from frontend.pdf_upload import InvalidPdfError, persist_edited_pdf, validate_pdf_bytes


def _real_pdf_bytes(num_pages: int = 1) -> bytes:
    """Builds real, parseable PDF bytes via pypdf (not a mock).

    Args:
        num_pages: The number of blank pages to include.

    Returns:
        bytes: The serialized PDF's raw bytes.
    """
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestValidatePdfBytes:
    """Test suite for validate_pdf_bytes."""

    def test_accepts_real_pdf_bytes(self) -> None:
        """Test a genuine, parseable PDF passes validation without raising."""
        validate_pdf_bytes(_real_pdf_bytes())

    def test_rejects_non_pdf_bytes(self) -> None:
        """Test plain garbage bytes are rejected, not silently accepted."""
        with pytest.raises(InvalidPdfError):
            validate_pdf_bytes(b"not a pdf at all")

    def test_rejects_empty_bytes(self) -> None:
        """Test empty bytes are rejected, not silently accepted."""
        with pytest.raises(InvalidPdfError):
            validate_pdf_bytes(b"")

    def test_rejects_pdf_magic_bytes_with_corrupted_body(self) -> None:
        """Test bytes that merely start with the PDF magic number but are
        otherwise unparseable are rejected by the real parse, not just the
        magic-byte check."""
        with pytest.raises(InvalidPdfError):
            validate_pdf_bytes(b"%PDF-1.4\ntotally corrupted garbage")


class TestPersistEditedPdf:
    """Test suite for persist_edited_pdf."""

    def test_valid_pdf_is_written_and_uploaded(
        self, tmp_path: Path, mocker: MagicMock
    ) -> None:
        """Test a valid re-uploaded PDF is written locally, uploaded to
        Drive under the edited-copy filename/MIME type, and the returned
        index entry carries the new edited_pdf_file_id."""
        mock_upload = mocker.patch(
            "frontend.pdf_upload.upload_file_to_folder", return_value="edited-file-id"
        )
        paper_info = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        local_edited_path = tmp_path / EDITED_PDF_FILENAME
        data = _real_pdf_bytes()

        updated = persist_edited_pdf(
            creds=MagicMock(),
            paper_info=paper_info,
            local_edited_path=local_edited_path,
            data=data,
        )

        assert local_edited_path.read_bytes() == data
        mock_upload.assert_called_once()
        args, _ = mock_upload.call_args
        assert args[1] == "folder1"
        assert args[2] == local_edited_path
        assert args[3] == EDITED_PDF_FILENAME
        assert args[4] == PDF_MIME_TYPE
        assert updated.edited_pdf_file_id == "edited-file-id"
        assert paper_info.edited_pdf_file_id == ""

    def test_invalid_pdf_raises_before_write_or_upload(
        self, tmp_path: Path, mocker: MagicMock
    ) -> None:
        """Test invalid bytes raise before anything is written to disk or
        uploaded to Drive."""
        mock_upload = mocker.patch("frontend.pdf_upload.upload_file_to_folder")
        paper_info = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        local_edited_path = tmp_path / EDITED_PDF_FILENAME

        with pytest.raises(InvalidPdfError):
            persist_edited_pdf(
                creds=MagicMock(),
                paper_info=paper_info,
                local_edited_path=local_edited_path,
                data=b"not a pdf",
            )

        assert not local_edited_path.exists()
        mock_upload.assert_not_called()

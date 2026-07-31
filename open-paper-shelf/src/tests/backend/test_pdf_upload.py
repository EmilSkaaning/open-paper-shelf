"""Unit tests for validating and persisting browser-auto-saved edited PDFs."""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter
from pytest_mock import MockerFixture

from backend.models import LibraryIndex, PaperIndexEntry
from backend.pdf_upload import (
    EDITED_PDF_FILENAME,
    PDF_MIME_TYPE,
    InvalidIdError,
    InvalidPdfError,
    persist_edited_pdf,
    save_edited_pdf,
    validate_drive_id,
    validate_paper_id,
    validate_pdf_bytes,
)


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


class TestValidateIds:
    """Test suite for validate_drive_id / validate_paper_id."""

    @pytest.mark.parametrize(
        "value", ["../../etc/passwd", "a/b", "..", "", "a" * 200, "id with spaces"]
    )
    def test_validate_drive_id_rejects_unsafe_values(self, value: str) -> None:
        """Test path-traversal payloads and other unsafe strings are rejected."""
        with pytest.raises(InvalidIdError):
            validate_drive_id(value)

    def test_validate_drive_id_accepts_realistic_folder_id(self) -> None:
        """Test a realistic Drive folder ID (alnum, '-', '_') passes."""
        validate_drive_id("1WgpM8mYe-_qVHKZCF2qS6JW251Ja_tqL")

    @pytest.mark.parametrize("value", ["not-hex", "a" * 31, "a" * 33, ""])
    def test_validate_paper_id_rejects_malformed_values(self, value: str) -> None:
        """Test paper IDs that aren't exactly 32 lowercase hex chars are rejected."""
        with pytest.raises(InvalidIdError):
            validate_paper_id(value)

    def test_validate_paper_id_accepts_well_formed_id(self) -> None:
        """Test a well-formed 32-char lowercase hex paper ID passes."""
        validate_paper_id("a" * 32)


class TestPersistEditedPdf:
    """Test suite for persist_edited_pdf."""

    def test_valid_pdf_is_written_and_uploaded(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test a valid annotated PDF is written locally, uploaded to Drive
        under the edited-copy filename/MIME type, and the returned index
        entry carries the new edited_pdf_file_id."""
        mock_upload = mocker.patch(
            "backend.pdf_upload.upload_file_to_folder", return_value="edited-file-id"
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
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test invalid bytes raise before anything is written to disk or
        uploaded to Drive."""
        mock_upload = mocker.patch("backend.pdf_upload.upload_file_to_folder")
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


class TestSaveEditedPdf:
    """Test suite for save_edited_pdf (the auto-save endpoint's core logic)."""

    def _seed_local_index(
        self, papers_dir: Path, lib_id: str, pid: str
    ) -> PaperIndexEntry:
        """Writes a local id-mapping.json cache with a single paper entry.

        Args:
            papers_dir: The stand-in PAPERS_DIR root.
            lib_id: The library folder ID.
            pid: The paper ID.

        Returns:
            PaperIndexEntry: The seeded entry for `pid`.
        """
        entry = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        index = LibraryIndex(papers={pid: entry})
        lib_dir = papers_dir / lib_id
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "id-mapping.json").write_text(
            index.model_dump_json(indent=2), encoding="utf-8"
        )
        return entry

    def test_saves_locally_and_syncs_drive_index(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test a valid save persists the edited copy locally, uploads it to
        Drive, and re-uploads the library index with the new file id."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)
        lib_id = "lib" + "a" * 20
        pid = "b" * 32
        self._seed_local_index(tmp_path, lib_id, pid)
        mocker.patch(
            "backend.pdf_upload.upload_file_to_folder", return_value="edited-id"
        )
        mocker.patch(
            "backend.pdf_upload.get_papers_folder", return_value="papers-folder-id"
        )
        mock_upload_index = mocker.patch("backend.pdf_upload.upload_library_index")
        data = _real_pdf_bytes()

        updated = save_edited_pdf(MagicMock(), lib_id, pid, data)

        assert updated.edited_pdf_file_id == "edited-id"
        local_edited_path = tmp_path / lib_id / pid / EDITED_PDF_FILENAME
        assert local_edited_path.read_bytes() == data
        saved_index = json.loads(
            (tmp_path / lib_id / "id-mapping.json").read_text(encoding="utf-8")
        )
        assert saved_index["papers"][pid]["edited_pdf_file_id"] == "edited-id"
        mock_upload_index.assert_called_once()

    def test_rejects_path_traversal_lib_id_before_touching_disk(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test an unsafe lib_id is rejected before any filesystem access."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)
        mock_upload = mocker.patch("backend.pdf_upload.upload_file_to_folder")

        with pytest.raises(InvalidIdError):
            save_edited_pdf(MagicMock(), "../../etc", "b" * 32, _real_pdf_bytes())

        mock_upload.assert_not_called()

    def test_rejects_malformed_pid(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Test a malformed paper id is rejected before any filesystem access."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)

        with pytest.raises(InvalidIdError):
            save_edited_pdf(
                MagicMock(), "lib" + "a" * 20, "not-a-pid", _real_pdf_bytes()
            )

    def test_raises_when_no_local_index_exists(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test a lib_id with no cached index raises rather than crashing on
        a missing-file error deep in the call stack."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)

        with pytest.raises(FileNotFoundError):
            save_edited_pdf(MagicMock(), "lib" + "a" * 20, "b" * 32, _real_pdf_bytes())

    def test_raises_when_pid_not_in_index(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test a pid absent from the library's index raises KeyError."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)
        lib_id = "lib" + "a" * 20
        self._seed_local_index(tmp_path, lib_id, "b" * 32)

        with pytest.raises(KeyError):
            save_edited_pdf(MagicMock(), lib_id, "c" * 32, _real_pdf_bytes())

    def test_rejects_oversized_payload(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Test a payload over MAX_EDITED_PDF_BYTES is rejected without
        touching disk."""
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)
        mocker.patch("backend.pdf_upload.MAX_EDITED_PDF_BYTES", 10)
        lib_id = "lib" + "a" * 20
        pid = "b" * 32
        self._seed_local_index(tmp_path, lib_id, pid)
        mock_upload = mocker.patch("backend.pdf_upload.upload_file_to_folder")

        with pytest.raises(InvalidPdfError):
            save_edited_pdf(MagicMock(), lib_id, pid, _real_pdf_bytes())

        mock_upload.assert_not_called()

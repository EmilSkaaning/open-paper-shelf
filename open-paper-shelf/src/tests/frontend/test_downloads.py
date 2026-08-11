"""Tests for frontend.downloads.zip_marked_pdfs."""

import io
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from backend.models import LibraryIndex, PaperIndexEntry
from frontend import downloads
from frontend.constants import EDITED_PDF_FILENAME, PDF_FILENAME


def _entry(
    title: str = "A Paper",
    pdf_file_id: str = "pdf-id",
    edited_pdf_file_id: str = "",
) -> PaperIndexEntry:
    return PaperIndexEntry(
        title=title,
        pdf_file_id=pdf_file_id,
        meta_file_id="meta-id",
        folder_id="folder-id",
        edited_pdf_file_id=edited_pdf_file_id,
    )


def _write_pdf(path: Path, content: bytes = b"%PDF-1.4 fake") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class TestZipMarkedPdfs:
    def test_includes_all_when_pdfs_already_cached(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        index = LibraryIndex(papers={"p1": _entry(title="Paper One")})
        _write_pdf(tmp_path / "p1" / PDF_FILENAME)
        download_mock = mocker.patch.object(downloads, "download_file")

        result = downloads.zip_marked_pdfs(
            creds=mocker.Mock(), pids=["p1"], index=index, local_lib_dir=tmp_path
        )

        assert result.included == ["Paper One"]
        assert result.skipped == []
        download_mock.assert_not_called()
        with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
            assert zf.namelist() == ["Paper One.pdf"]

    def test_downloads_missing_pdf_before_zipping(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        index = LibraryIndex(papers={"p1": _entry(title="Paper One")})

        def fake_download(creds: MagicMock, file_id: str, dest_path: Path) -> None:
            _write_pdf(dest_path)

        download_mock = mocker.patch.object(
            downloads, "download_file", side_effect=fake_download
        )
        creds = mocker.Mock()

        result = downloads.zip_marked_pdfs(
            creds=creds, pids=["p1"], index=index, local_lib_dir=tmp_path
        )

        download_mock.assert_called_once_with(
            creds, "pdf-id", tmp_path / "p1" / PDF_FILENAME
        )
        assert result.included == ["Paper One"]
        assert result.skipped == []

    def test_prefers_edited_pdf_when_present(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        index = LibraryIndex(
            papers={"p1": _entry(title="Paper One", edited_pdf_file_id="edited-id")}
        )
        _write_pdf(tmp_path / "p1" / PDF_FILENAME, b"raw")
        _write_pdf(tmp_path / "p1" / EDITED_PDF_FILENAME, b"edited")
        download_mock = mocker.patch.object(downloads, "download_file")

        result = downloads.zip_marked_pdfs(
            creds=mocker.Mock(), pids=["p1"], index=index, local_lib_dir=tmp_path
        )

        download_mock.assert_not_called()
        with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
            assert zf.read("Paper One.pdf") == b"edited"

    def test_skips_paper_on_download_failure(
        self, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        index = LibraryIndex(
            papers={
                "p1": _entry(title="Broken Paper"),
                "p2": _entry(title="Good Paper", pdf_file_id="pdf-id-2"),
            }
        )
        _write_pdf(tmp_path / "p2" / PDF_FILENAME)

        def fake_download(creds: MagicMock, file_id: str, dest_path: Path) -> None:
            raise RuntimeError("boom")

        mocker.patch.object(downloads, "download_file", side_effect=fake_download)

        with caplog.at_level(logging.ERROR):
            result = downloads.zip_marked_pdfs(
                creds=mocker.Mock(),
                pids=["p1", "p2"],
                index=index,
                local_lib_dir=tmp_path,
            )

        assert result.included == ["Good Paper"]
        assert result.skipped == ["Broken Paper"]
        assert "Broken Paper" in caplog.text
        assert "boom" in caplog.text

    def test_dedupes_identical_titles(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        index = LibraryIndex(
            papers={
                "p1": _entry(title="Same Title", pdf_file_id="id-1"),
                "p2": _entry(title="Same Title", pdf_file_id="id-2"),
            }
        )
        _write_pdf(tmp_path / "p1" / PDF_FILENAME, b"one")
        _write_pdf(tmp_path / "p2" / PDF_FILENAME, b"two")
        mocker.patch.object(downloads, "download_file")

        result = downloads.zip_marked_pdfs(
            creds=mocker.Mock(),
            pids=["p1", "p2"],
            index=index,
            local_lib_dir=tmp_path,
        )

        assert sorted(result.included) == ["Same Title", "Same Title"]
        with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
            names = zf.namelist()
            assert len(names) == 2
            assert len(set(names)) == 2
            assert "Same Title.pdf" in names
            assert any(
                name.startswith("Same Title_") and name.endswith(".pdf")
                for name in names
                if name != "Same Title.pdf"
            )

    def test_skips_unknown_pid(self, tmp_path: Path, mocker: MockerFixture) -> None:
        index = LibraryIndex(papers={})
        mocker.patch.object(downloads, "download_file")

        result = downloads.zip_marked_pdfs(
            creds=mocker.Mock(),
            pids=["missing-pid"],
            index=index,
            local_lib_dir=tmp_path,
        )

        assert result.included == []
        assert result.skipped == ["missing-pid"]

    def test_empty_pids_returns_empty_result(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        index = LibraryIndex(papers={})
        mocker.patch.object(downloads, "download_file")

        result = downloads.zip_marked_pdfs(
            creds=mocker.Mock(), pids=[], index=index, local_lib_dir=tmp_path
        )

        assert result.included == []
        assert result.skipped == []
        with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
            assert zf.namelist() == []


class TestZipDownloadFilename:
    def test_builds_timestamp_library_format(self) -> None:
        name = downloads.zip_download_filename(
            "My Library", now=datetime(2026, 8, 11, 14, 5, 9)
        )

        assert name == "2026-08-11-140509-My Library.zip"

    def test_sanitizes_unsafe_characters_in_library_name(self) -> None:
        name = downloads.zip_download_filename(
            "Lib/With:Bad*Chars?", now=datetime(2026, 8, 11, 14, 5, 9)
        )

        assert name == "2026-08-11-140509-Lib_With_Bad_Chars_.zip"

    def test_defaults_to_current_timestamp_when_not_given(self) -> None:
        before = datetime.now().replace(microsecond=0)
        name = downloads.zip_download_filename("My Library")
        after = datetime.now().replace(microsecond=0) + timedelta(seconds=1)

        stamp = name.removesuffix("-My Library.zip")
        parsed = datetime.strptime(stamp, "%Y-%m-%d-%H%M%S")
        assert before <= parsed <= after

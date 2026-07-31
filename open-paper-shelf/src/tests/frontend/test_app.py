"""Unit tests for the Streamlit frontend application."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError
from pytest_mock import MockerFixture

import frontend.app as app
import frontend.auth as auth
import frontend.library as library
import frontend.library_filters as library_filters
import frontend.metadata_generation as metadata_generation
import frontend.uploads as uploads
from backend.huggingface_client import GeneratedMetadata
from backend.models import LibraryIndex, PaperIndexEntry, PaperMetadata
from tests.frontend.conftest import make_uploaded_file


def _make_402_error() -> HfHubHTTPError:
    """Builds an HfHubHTTPError shaped like an HF 402 Payment Required response."""
    response = httpx.Response(
        status_code=402, request=httpx.Request("POST", "https://example.com")
    )
    return HfHubHTTPError("Payment Required", response=response)


class TestFakeStColumnsFixture:
    """Test suite for the fake_st fixture's st.columns() behavior."""

    def test_columns_returns_matching_arity_for_int_and_list_specs(
        self, fake_st: MagicMock
    ) -> None:
        """Test st.columns() returns a tuple sized to the requested spec,
        whether given as an int or a list of widths."""
        two = fake_st.columns(2)
        three = fake_st.columns([1, 2, 2])

        assert len(two) == 2
        assert len(three) == 3
        assert two[0] is not two[1]
        assert three[0] is not three[1] is not three[2]


class TestSyncLibraryIndex:
    """Test suite for sync_library_index."""

    def test_no_remote_file(self, fake_st: MagicMock, mocker: MockerFixture) -> None:
        """Test an empty index is used when no remote index file exists yet."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = None
        mocker.patch.object(library, "get_library_index_file", return_value=None)

        app.sync_library_index(creds=MagicMock())

        assert fake_st.session_state.index == LibraryIndex()
        assert fake_st.session_state.last_sync_time is None

    def test_download_failure_falls_back_to_empty_index(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a download failure is reported and falls back to an empty index."""
        local_path = tmp_path / "id-mapping.json"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path
        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mocker.patch.object(
            app, "download_file", side_effect=RuntimeError("network blip")
        )

        app.sync_library_index(creds=MagicMock())

        assert fake_st.session_state.index == LibraryIndex()
        fake_st.error.assert_called_once()

    def test_download_failure_keeps_existing_local_cache(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: if a download fails but a valid local cache
        already exists, syncing must fall back to that cache instead of
        wiping out the user's library view. Wiping it here would make a
        transient network error look like an empty library."""
        local_path = tmp_path / "id-mapping.json"
        cached_data = {
            "papers": {
                "abc123": {
                    "title": "Cached Paper",
                    "pdf_file_id": "pdf1",
                    "meta_file_id": "meta1",
                    "folder_id": "folder1",
                }
            }
        }
        local_path.write_text(json.dumps(cached_data), encoding="utf-8")
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path
        fake_st.session_state.last_sync_time = "t0"
        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mocker.patch.object(
            app, "download_file", side_effect=RuntimeError("network blip")
        )

        app.sync_library_index(creds=MagicMock())

        assert fake_st.session_state.index == LibraryIndex(**cached_data)
        assert fake_st.session_state.last_sync_time == "t0"
        fake_st.error.assert_called_once()

    def test_corrupted_local_file_falls_back_to_empty_index(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a corrupted local cache is reported and replaced by an empty index."""
        local_path = tmp_path / "id-mapping.json"
        local_path.write_text("not valid json")
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path
        fake_st.session_state.last_sync_time = "t1"
        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(library, "download_file")

        app.sync_library_index(creds=MagicMock())

        mock_download.assert_not_called()
        assert fake_st.session_state.index == LibraryIndex()
        fake_st.error.assert_called_once()

    def test_skips_download_but_local_file_missing(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: if last_sync_time already matches the remote
        index but the local cache file turns out missing by the time it's
        read (e.g. deleted out-of-band between the two exists() checks),
        syncing must fall back to an empty index instead of crashing."""
        local_path = MagicMock()
        local_path.exists.side_effect = [True, False]
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path
        fake_st.session_state.last_sync_time = "t1"
        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(library, "download_file")

        app.sync_library_index(creds=MagicMock())

        mock_download.assert_not_called()
        assert fake_st.session_state.index == LibraryIndex()
        fake_st.error.assert_not_called()

    def test_happy_path_parses_downloaded_index(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a freshly downloaded index file is parsed into session state."""
        local_path = tmp_path / "id-mapping.json"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path

        valid_data = {
            "papers": {
                "abc123": {
                    "title": "A Paper",
                    "pdf_file_id": "pdf1",
                    "meta_file_id": "meta1",
                    "folder_id": "folder1",
                }
            }
        }

        def fake_download(creds: MagicMock, file_id: str, dest_path: Path) -> None:
            dest_path.write_text(json.dumps(valid_data))

        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mocker.patch.object(library, "download_file", side_effect=fake_download)

        app.sync_library_index(creds=MagicMock())

        assert fake_st.session_state.index == LibraryIndex(**valid_data)
        assert fake_st.session_state.last_sync_time == "t1"
        fake_st.error.assert_not_called()

    def test_reads_local_index_as_utf8(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: the local index cache must be read with an
        explicit utf-8 encoding. Path.read_text() otherwise defaults to the
        platform encoding (e.g. cp1252 on Windows), which would raise
        UnicodeDecodeError for titles with non-ASCII characters like smart
        quotes, since the file is always written as utf-8."""
        local_path = tmp_path / "id-mapping.json"
        valid_data = {
            "papers": {
                "abc123": {
                    "title": "A Paper — “Smart Quotes”",
                    "pdf_file_id": "pdf1",
                    "meta_file_id": "meta1",
                    "folder_id": "folder1",
                }
            }
        }
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = local_path
        fake_st.session_state.last_sync_time = "t1"
        mocker.patch.object(
            library,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(library, "download_file")
        mock_read_text = mocker.patch.object(
            Path, "read_text", return_value=json.dumps(valid_data)
        )
        mocker.patch.object(Path, "exists", return_value=True)

        app.sync_library_index(creds=MagicMock())

        mock_download.assert_not_called()
        mock_read_text.assert_called_once_with(encoding="utf-8")
        assert fake_st.session_state.index == LibraryIndex(**valid_data)
        fake_st.error.assert_not_called()


class TestStripPdfSuffix:
    """Test suite for strip_pdf_suffix."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("paper.pdf", "paper"),
            ("Paper.PDF", "Paper"),
            ("my paper.Pdf", "my paper"),
            ("no-extension", "no-extension"),
            ("weird.pdf.pdf", "weird.pdf"),
        ],
    )
    def test_strips_trailing_pdf_suffix_case_insensitively(
        self, raw: str, expected: str
    ) -> None:
        """Test a trailing .pdf suffix (any case) is removed exactly once."""
        assert app.strip_pdf_suffix(raw) == expected

    def test_falls_back_to_original_when_stripping_would_be_empty(self) -> None:
        """Regression test: a filename that's just an extension (e.g. a
        file literally named '.pdf') must not become an empty title."""
        assert app.strip_pdf_suffix(".pdf") == ".pdf"


class TestUploadPapers:
    """Test suite for upload_papers."""

    def test_all_succeed(self, fake_st: MagicMock, mocker: MockerFixture) -> None:
        """Test every uploaded file is stored and added to the index."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            uploads, "create_paper_folder", side_effect=["folder1", "folder2"]
        )
        mocker.patch.object(
            uploads,
            "upload_file_to_folder",
            side_effect=["pdf1", "meta1", "pdf2", "meta2"],
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is True
        assert len(fake_st.session_state.index.papers) == 2
        fake_st.error.assert_not_called()

    def test_strips_pdf_extension_from_default_title(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test an uploaded file's default title never contains '.pdf'."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("Attention Is All You Need.pdf")]
        mocker.patch.object(uploads, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=["pdf1", "meta1"]
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is True
        (entry,) = fake_st.session_state.index.papers.values()
        assert entry.title == "Attention Is All You Need"

    def test_partial_failure_reports_error_and_keeps_successful_ones(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a failed file must not be silently dropped, and
        the caller must be told (via the return value) that not everything
        succeeded, so it doesn't unconditionally show success/rerun past a
        real error."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            uploads,
            "create_paper_folder",
            side_effect=["folder1", RuntimeError("boom")],
        )
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=["pdf1", "meta1"]
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 1
        fake_st.error.assert_called_once()
        assert "b.pdf" in fake_st.error.call_args[0][0]

    def test_rejects_invalid_filename(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: an invalid uploaded filename must be reported as
        a per-file error, not silently ignored or an unhandled crash."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("")]
        mocker.patch.object(uploads, "create_paper_folder")
        mocker.patch.object(uploads, "upload_file_to_folder")

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 0

    def test_pdf_upload_failure_cleans_up_orphaned_folder(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: if the PDF upload fails after the Drive folder
        was already created, the orphaned folder must be deleted instead of
        leaking storage in the user's Drive."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf")]
        mocker.patch.object(uploads, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=RuntimeError("boom")
        )
        mock_delete = mocker.patch.object(uploads, "delete_paper_folder")

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 0
        mock_delete.assert_called_once_with(mocker.ANY, "folder1")

    def test_meta_upload_failure_cleans_up_orphaned_folder(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: if the meta.json upload fails after the PDF
        already uploaded successfully, the orphaned folder (containing the
        PDF) must still be deleted."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf")]
        mocker.patch.object(uploads, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=["pdf1", RuntimeError("boom")]
        )
        mock_delete = mocker.patch.object(uploads, "delete_paper_folder")

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 0
        mock_delete.assert_called_once_with(mocker.ANY, "folder1")

    def test_create_folder_failure_does_not_attempt_cleanup(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: if no folder was ever created on Drive, cleanup
        must not be attempted, since there is nothing to delete."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf")]
        mocker.patch.object(
            uploads, "create_paper_folder", side_effect=RuntimeError("boom")
        )
        mock_delete = mocker.patch.object(uploads, "delete_paper_folder")

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        mock_delete.assert_not_called()

    def test_cleanup_failure_is_reported_but_original_error_still_surfaces(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: if cleaning up the orphaned folder itself fails,
        that failure must be reported without masking the original upload
        error or crashing the whole batch."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf")]
        mocker.patch.object(uploads, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=RuntimeError("boom")
        )
        mocker.patch.object(
            uploads, "delete_paper_folder", side_effect=RuntimeError("cleanup failed")
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 0
        assert fake_st.error.call_count == 2
        assert "cleanup failed" in fake_st.error.call_args_list[0][0][0]
        assert "boom" in fake_st.error.call_args_list[1][0][0]

    def test_on_progress_called_once_per_file_with_index_total_and_name(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test on_progress is invoked once per file, in order, each call
        carrying the 1-based index, the total file count, and that file's
        own name - regardless of whether the upload succeeded."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            uploads, "create_paper_folder", side_effect=["folder1", "folder2"]
        )
        mocker.patch.object(
            uploads,
            "upload_file_to_folder",
            side_effect=["pdf1", "meta1", "pdf2", "meta2"],
        )
        on_progress = MagicMock()

        app.upload_papers(
            creds=MagicMock(), uploaded_files=files, on_progress=on_progress
        )

        assert on_progress.call_args_list == [
            mocker.call(1, 2, "a.pdf"),
            mocker.call(2, 2, "b.pdf"),
        ]

    def test_on_progress_still_called_after_a_failed_file(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a failed file must not stall progress reporting -
        on_progress must still fire for it so the caller's progress bar keeps
        advancing through the whole batch."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            uploads,
            "create_paper_folder",
            side_effect=["folder1", RuntimeError("boom")],
        )
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=["pdf1", "meta1"]
        )
        on_progress = MagicMock()

        app.upload_papers(
            creds=MagicMock(), uploaded_files=files, on_progress=on_progress
        )

        assert on_progress.call_args_list == [
            mocker.call(1, 2, "a.pdf"),
            mocker.call(2, 2, "b.pdf"),
        ]

    def test_on_progress_failure_does_not_abort_remaining_files(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a broken on_progress callback (e.g. a stale
        Streamlit widget) must not abort the batch - remaining files must
        still be uploaded and on_progress must still be attempted for each."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            uploads, "create_paper_folder", side_effect=["folder1", "folder2"]
        )
        mocker.patch.object(
            uploads,
            "upload_file_to_folder",
            side_effect=["pdf1", "meta1", "pdf2", "meta2"],
        )
        on_progress = MagicMock(side_effect=RuntimeError("widget gone"))

        result = app.upload_papers(
            creds=MagicMock(), uploaded_files=files, on_progress=on_progress
        )

        assert result is True
        assert len(fake_st.session_state.index.papers) == 2
        assert on_progress.call_args_list == [
            mocker.call(1, 2, "a.pdf"),
            mocker.call(2, 2, "b.pdf"),
        ]

    def test_on_progress_defaults_to_none_and_is_optional(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: callers that don't pass on_progress (e.g. all the
        pre-existing tests above) must keep working unchanged."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf")]
        mocker.patch.object(uploads, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            uploads, "upload_file_to_folder", side_effect=["pdf1", "meta1"]
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is True


class TestSyncPaperMetadata:
    """Test suite for sync_paper_metadata."""

    def test_download_succeeds(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a successful download is reported as safe to edit."""
        local_meta_path = tmp_path / "meta.json"
        paper_info = MagicMock(meta_file_id="meta1")
        mock_download = mocker.patch.object(metadata_generation, "download_file")

        result = app.sync_paper_metadata(MagicMock(), paper_info, local_meta_path)

        mock_download.assert_called_once()
        assert result is True
        fake_st.error.assert_not_called()

    @pytest.mark.parametrize(
        "local_copy_exists, expected_result",
        [(False, False), (True, True)],
        ids=["no_local_copy_is_unsafe", "stale_local_copy_is_safe"],
    )
    def test_download_fails(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        local_copy_exists: bool,
        expected_result: bool,
    ) -> None:
        """Regression test: a failed download is only safe to edit past
        (return True) when a pre-existing local cache survives it - with no
        local copy at all, editing would risk overwriting real Drive data
        with defaults.

        Args:
            fake_st: The mocked streamlit module.
            mocker: The pytest-mock fixture.
            tmp_path: Pytest's per-test temporary directory.
            local_copy_exists: Whether a local metadata cache pre-exists.
            expected_result: The expected "safe to edit" return value.
        """
        local_meta_path = tmp_path / "meta.json"
        if local_copy_exists:
            local_meta_path.write_text('{"title": "cached"}')
        paper_info = MagicMock(meta_file_id="meta1")
        mocker.patch.object(
            metadata_generation,
            "download_file",
            side_effect=RuntimeError("network blip"),
        )

        result = app.sync_paper_metadata(MagicMock(), paper_info, local_meta_path)

        assert result is expected_result
        fake_st.error.assert_called_once()


class TestAuthenticateUser:
    """Test suite for authenticate_user (OAuth flow)."""

    def test_returns_cached_credentials_without_oauth_flow(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test valid cached credentials short-circuit the OAuth flow entirely."""
        cached_creds = MagicMock()
        mocker.patch.object(
            auth, "load_credentials_from_file", return_value=cached_creds
        )

        result = app.authenticate_user()

        assert result is cached_creds
        fake_st.error.assert_not_called()

    def test_uses_cached_flow_when_session_state_is_lost(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: even if st.session_state was reset across the
        Google redirect, the flow cached in the global OAUTH_FLOWS dict
        (keyed by state) must still be found and used, so login succeeds."""
        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_flow.credentials = mock_creds
        app.OAUTH_FLOWS["state1"] = mock_flow
        fake_st.query_params = {"code": "abc123", "state": "state1"}
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)
        mock_save_creds = mocker.patch.object(auth, "save_credentials")

        with pytest.raises(stop_rerun):
            app.authenticate_user()

        mock_flow.fetch_token.assert_called_once_with(code="abc123")
        mock_save_creds.assert_called_once_with(mock_creds)
        fake_st.error.assert_not_called()
        assert "state1" not in app.OAUTH_FLOWS

    def test_rejects_unknown_state(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """An unknown/forged state (not in OAUTH_FLOWS) must be rejected as a
        possible CSRF attempt, never call fetch_token."""
        fake_st.query_params = {"code": "abc123", "state": "forged-state"}
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)

        result = app.authenticate_user()

        assert result is None
        fake_st.error.assert_called_once()
        assert "State mismatch" in fake_st.error.call_args[0][0]

    def test_rejects_missing_state_param(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a callback with a code but no state param must
        be rejected as a CSRF mismatch rather than raising."""
        fake_st.query_params = {"code": "abc123"}
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)

        result = app.authenticate_user()

        assert result is None
        fake_st.error.assert_called_once()
        assert "State mismatch" in fake_st.error.call_args[0][0]

    def test_fetch_token_failure_is_reported(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test an exception from flow.fetch_token is caught and reported."""
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = RuntimeError("token exchange failed")
        app.OAUTH_FLOWS["state1"] = mock_flow
        fake_st.query_params = {"code": "abc123", "state": "state1"}
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)

        result = app.authenticate_user()

        assert result is None
        fake_st.error.assert_called_once()
        assert "token exchange failed" in fake_st.error.call_args[0][0]

    def test_starts_new_flow_and_shows_login_button(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the first-visit path builds a new flow, caches it, and shows
        a login button when no auth_flow is cached in session state yet."""
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "new-state",
        )
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)
        mocker.patch.object(auth, "get_oauth_flow", return_value=mock_flow)
        mock_add_flow = mocker.patch.object(auth, "add_oauth_flow")

        result = app.authenticate_user()

        assert result is None
        assert fake_st.session_state.auth_flow is mock_flow
        assert fake_st.session_state.auth_url == "https://accounts.google.com/auth"
        assert fake_st.session_state.oauth_state == "new-state"
        mock_add_flow.assert_called_once_with("new-state", mock_flow)
        fake_st.link_button.assert_called_once_with(
            "Login with Google", "https://accounts.google.com/auth"
        )

    def test_flow_creation_failure_is_reported(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test a failure to build the OAuth flow surfaces a clear error instead of crashing."""
        mocker.patch.object(auth, "load_credentials_from_file", return_value=None)
        mocker.patch.object(auth, "get_oauth_flow", side_effect=RuntimeError("boom"))

        result = app.authenticate_user()

        assert result is None
        fake_st.error.assert_called_once()
        assert "Could not start Google sign-in" in fake_st.error.call_args[0][0]


class TestInitLibraryState:
    """Test suite for init_library_state."""

    def test_sets_session_state_and_creates_local_dir(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test session state is reset and the library's local dir is created."""
        mocker.patch.object(library, "PAPERS_DIR", tmp_path)

        app.init_library_state(MagicMock(), "lib_123", "papers_123", "My Lib")

        assert fake_st.session_state.current_lib_id == "lib_123"
        assert fake_st.session_state.current_lib_name == "My Lib"
        assert fake_st.session_state.current_papers_id == "papers_123"
        assert fake_st.session_state.local_lib_dir == tmp_path / "lib_123"
        assert fake_st.session_state.local_lib_dir.exists()
        assert (
            fake_st.session_state.local_index_path
            == tmp_path / "lib_123" / "id-mapping.json"
        )
        assert fake_st.session_state.selected_paper is None


class TestMainLibrarySelection:
    """Test suite for main()'s library selection/creation screen."""

    def test_open_existing_library(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking "Open Library" opens the selected library and reruns.

        Uses two libraries so the manual picker (not the single-library
        auto-select path) is what's under test.
        """
        fake_st.session_state.root_id = "root_123"
        fake_st.button.side_effect = lambda label, **kw: label == "Open Library"
        fake_st.selectbox.return_value = "lib1"
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(
            app,
            "list_libraries",
            return_value=[
                {"id": "lib1", "name": "Lib One"},
                {"id": "lib2", "name": "Lib Two"},
            ],
        )
        mocker.patch.object(app, "get_papers_folder", return_value="papers_1")
        mock_init = mocker.patch.object(app, "init_library_state")

        with pytest.raises(stop_rerun):
            app.main()

        mock_init.assert_called_once_with(mocker.ANY, "lib1", "papers_1", "Lib One")

    def test_single_library_auto_opens_without_button_click(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test the sole library is opened automatically, with no picker
        shown and no button click required."""
        fake_st.session_state.root_id = "root_123"
        fake_st.button.return_value = False
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(
            app, "list_libraries", return_value=[{"id": "lib1", "name": "Lib One"}]
        )
        mocker.patch.object(app, "get_papers_folder", return_value="papers_1")
        mock_init = mocker.patch.object(app, "init_library_state")

        with pytest.raises(stop_rerun):
            app.main()

        mock_init.assert_called_once_with(mocker.ANY, "lib1", "papers_1", "Lib One")
        fake_st.subheader.assert_not_called()

    def test_manual_selection_flag_blocks_single_library_auto_open(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test a pending manual-selection request (from Switch Library)
        keeps showing the picker even when only one library exists."""
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.manual_library_selection = True
        fake_st.button.return_value = False
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(
            app, "list_libraries", return_value=[{"id": "lib1", "name": "Lib One"}]
        )
        mock_init = mocker.patch.object(app, "init_library_state")

        app.main()

        mock_init.assert_not_called()
        fake_st.subheader.assert_called_once_with("Select or Create a Library")

    def test_creates_new_library(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking "Create Library" creates and opens a new library."""
        fake_st.session_state.root_id = "root_123"
        fake_st.button.side_effect = lambda label, **kw: label == "Create Library"
        fake_st.text_input.return_value = "My New Lib"
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "list_libraries", return_value=[])
        mocker.patch.object(
            app,
            "create_library",
            return_value={
                "lib_id": "new_lib",
                "papers_id": "new_papers",
                "lib_name": "My New Lib",
            },
        )
        mock_init = mocker.patch.object(app, "init_library_state")
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        with pytest.raises(stop_rerun):
            app.main()

        mock_init.assert_called_once_with(
            mocker.ANY, "new_lib", "new_papers", "My New Lib"
        )
        mock_upload_index.assert_called_once_with(
            mocker.ANY, "new_papers", LibraryIndex()
        )
        fake_st.success.assert_called_once()

    def test_returns_early_when_not_authenticated(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test main() returns immediately if the user isn't authenticated."""
        mocker.patch.object(app, "authenticate_user", return_value=None)

        app.main()

        fake_st.title.assert_not_called()

    def test_no_button_clicked_stays_on_selection_screen(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the selection screen is shown without rerunning when no
        library is opened or created yet."""
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "get_or_create_root_folder", return_value="root_new")
        mocker.patch.object(app, "list_libraries", return_value=[])

        app.main()

        assert fake_st.session_state.root_id == "root_new"
        fake_st.rerun.assert_not_called()


class TestDeleteSelectedPapers:
    """Test suite for delete_selected_papers."""

    def test_deletes_all_and_uploads_index_once(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test every requested paper is removed from Drive and the index,
        with a single index upload for the whole batch."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        fake_st.session_state.selected_paper = None
        mock_delete_folder = mocker.patch.object(library, "delete_paper_folder")
        mock_upload_index = mocker.patch.object(library, "upload_library_index")

        result = app.delete_selected_papers(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        assert result is True
        assert mock_delete_folder.call_count == 2
        assert index.papers == {}
        mock_upload_index.assert_called_once_with(
            mocker.ANY, "papers_123", index, deleted_pids={pid1, pid2}
        )

    def test_drive_failure_on_one_paper_keeps_it_and_continues(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: if Drive deletion fails for one paper, it must
        stay in the index while the rest still get deleted, matching the
        old single-paper delete's per-item error handling."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        fake_st.session_state.selected_paper = None
        mocker.patch.object(
            library, "delete_paper_folder", side_effect=[RuntimeError("boom"), None]
        )
        mock_upload_index = mocker.patch.object(library, "upload_library_index")

        result = app.delete_selected_papers(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        assert result is False
        assert pid1 in index.papers
        assert pid2 not in index.papers
        fake_st.error.assert_called_once()
        mock_upload_index.assert_called_once_with(
            mocker.ANY, "papers_123", index, deleted_pids={pid2}
        )

    def test_index_upload_failure_restores_all_removed_entries(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: mirrors the old single-delete behavior — if the
        index upload fails after Drive folders were already deleted, every
        removed entry must be restored locally so a future sync doesn't
        merge it back in as a broken entry."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
        )
        index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = pid
        mocker.patch.object(library, "delete_paper_folder")
        mocker.patch.object(
            library, "upload_library_index", side_effect=RuntimeError("network blip")
        )

        result = app.delete_selected_papers(
            creds=MagicMock(),
            pids=[pid],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        assert result is False
        assert index.papers[pid] == entry
        assert fake_st.session_state.selected_paper == pid
        fake_st.error.assert_called_once()

    def test_clears_selected_paper_and_removes_local_cache_when_deleted(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the open paper is deselected and its local cache dir is
        removed once it's actually deleted."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        fake_st.session_state.selected_paper = pid
        local_paper_dir = tmp_path / pid
        local_paper_dir.mkdir(parents=True)
        (local_paper_dir / "paper.pdf").write_bytes(b"x")
        mocker.patch.object(library, "delete_paper_folder")
        mocker.patch.object(library, "upload_library_index")

        app.delete_selected_papers(
            creds=MagicMock(),
            pids=[pid],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        assert fake_st.session_state.selected_paper is None
        assert not local_paper_dir.exists()


class TestGetAllTags:
    """Test suite for get_all_tags."""

    def test_returns_sorted_deduplicated_tags_across_papers(self) -> None:
        """Test tags from every paper are merged, deduped, and sorted."""
        index = LibraryIndex(
            papers={
                "a" * 32: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    tags=["nlp", "ai"],
                ),
                "b" * 32: PaperIndexEntry(
                    title="Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    tags=["ai", "vision"],
                ),
            }
        )
        assert app.get_all_tags(index) == ["ai", "nlp", "vision"]

    def test_returns_empty_list_for_empty_library(self) -> None:
        """Test an empty index returns an empty tag list."""
        assert app.get_all_tags(LibraryIndex()) == []


class TestGetDuplicatePids:
    """Test suite for get_duplicate_pids."""

    def test_papers_with_matching_embeddings_are_flagged(self) -> None:
        """Test two papers with near-identical embeddings both appear in
        the result."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0, 0.0],
                ),
                pid2: PaperIndexEntry(
                    title="Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    embedding=[1.0, 0.0, 0.0],
                ),
            }
        )
        assert app.get_duplicate_pids(index) == {pid1, pid2}

    def test_paper_with_no_embedding_is_excluded(self) -> None:
        """Test a paper with an empty embedding is never flagged, even if
        another paper's embedding happens to be empty too."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        assert app.get_duplicate_pids(index) == set()

    def test_below_threshold_match_is_excluded(self) -> None:
        """Test a lone paper whose embedding doesn't closely match any
        other paper's is not flagged."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0, 0.0],
                ),
                pid2: PaperIndexEntry(
                    title="Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    embedding=[0.0, 1.0, 0.0],
                ),
            }
        )
        assert app.get_duplicate_pids(index) == set()

    def test_unchanged_index_reuses_cached_result_without_recomputing(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test calling get_duplicate_pids twice with an index whose
        embeddings haven't changed reuses the cached result instead of
        rerunning the O(N^2) similarity scan."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0, 0.0],
                ),
                pid2: PaperIndexEntry(
                    title="Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    embedding=[1.0, 0.0, 0.0],
                ),
            }
        )
        spy = mocker.spy(library_filters, "find_similar_papers")

        first = app.get_duplicate_pids(index)
        call_count_after_first = spy.call_count
        second = app.get_duplicate_pids(index)

        assert first == second == {pid1, pid2}
        assert spy.call_count == call_count_after_first

    def test_changed_embedding_invalidates_cache_and_recomputes(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test changing a paper's embedding between calls invalidates the
        cached result rather than returning stale duplicate pids."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0, 0.0],
                ),
                pid2: PaperIndexEntry(
                    title="Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    embedding=[1.0, 0.0, 0.0],
                ),
            }
        )
        spy = mocker.spy(library_filters, "find_similar_papers")

        first = app.get_duplicate_pids(index)
        call_count_after_first = spy.call_count
        index.papers[pid2] = index.papers[pid2].model_copy(
            update={"embedding": [0.0, 1.0, 0.0]}
        )
        second = app.get_duplicate_pids(index)

        assert first == {pid1, pid2}
        assert second == set()
        assert spy.call_count > call_count_after_first


class TestGetMissingMetadataPids:
    """Test suite for get_missing_metadata_pids."""

    def test_paper_with_no_tags_or_embedding_is_included(self) -> None:
        """Test a never-generated paper (no tags, no embedding) is flagged."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        assert app.get_missing_metadata_pids(index) == {pid}

    def test_paper_with_tags_is_excluded(self) -> None:
        """Test a paper with tags (but no embedding) is not flagged."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    tags=["nlp"],
                )
            }
        )
        assert app.get_missing_metadata_pids(index) == set()

    def test_paper_with_embedding_is_excluded(self) -> None:
        """Test a paper with an embedding (but no tags) is not flagged."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0],
                )
            }
        )
        assert app.get_missing_metadata_pids(index) == set()


class TestFilterPapers:
    """Test suite for filter_papers."""

    def _papers(self) -> dict[str, PaperIndexEntry]:
        """Builds a fixed set of index entries covering every filter axis."""
        return {
            "a" * 32: PaperIndexEntry(
                title="Attention Is All You Need",
                pdf_file_id="p1",
                meta_file_id="m1",
                folder_id="f1",
                tags=["ai", "nlp"],
                status="Read",
            ),
            "b" * 32: PaperIndexEntry(
                title="Diffusion Models Beat GANs",
                pdf_file_id="p2",
                meta_file_id="m2",
                folder_id="f2",
                tags=["vision"],
                status="Reading",
            ),
            "c" * 32: PaperIndexEntry(
                title="Zebrafish Locomotion",
                pdf_file_id="p3",
                meta_file_id="m3",
                folder_id="f3",
                tags=[],
                status="Unread",
            ),
            "not-a-hex-id": PaperIndexEntry(
                title="Legacy Entry",
                pdf_file_id="p4",
                meta_file_id="m4",
                folder_id="f4",
            ),
        }

    def test_no_filters_returns_all_valid_entries_sorted_by_title(self) -> None:
        """Test an empty search/status/tags filter returns every non-legacy
        paper, sorted by title."""
        result = app.filter_papers(self._papers(), "", [], [])
        assert [pid for pid, _ in result] == ["a" * 32, "b" * 32, "c" * 32]

    def test_search_query_matches_title_substring(self) -> None:
        """Test the search query filters by a case-insensitive title match."""
        result = app.filter_papers(self._papers(), "diffusion", [], [])
        assert [pid for pid, _ in result] == ["b" * 32]

    def test_status_filter_restricts_to_selected_statuses(self) -> None:
        """Test selecting statuses keeps only papers with a matching status."""
        result = app.filter_papers(self._papers(), "", ["Read", "Unread"], [])
        assert {pid for pid, _ in result} == {"a" * 32, "c" * 32}

    def test_tags_filter_matches_any_selected_tag(self) -> None:
        """Test selecting tags keeps papers with at least one matching tag."""
        result = app.filter_papers(self._papers(), "", [], ["nlp", "vision"])
        assert {pid for pid, _ in result} == {"a" * 32, "b" * 32}

    def test_combined_filters_are_ANDed_together(self) -> None:
        """Test search, status, and tags filters all apply simultaneously."""
        result = app.filter_papers(self._papers(), "attention", ["Read"], ["ai"])
        assert [pid for pid, _ in result] == ["a" * 32]

    def test_legacy_non_hex_key_is_always_skipped(self) -> None:
        """Regression test: a malformed index key must never appear, even
        with no filters applied."""
        result = app.filter_papers(self._papers(), "", [], [])
        assert "not-a-hex-id" not in [pid for pid, _ in result]


class TestMainLibraryView:
    """Test suite for main()'s library view (sidebar entry, switch, rows)."""

    def test_syncs_library_index_when_missing_from_session(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the library index is synced from Drive when not yet cached
        in session state."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        def fake_sync(creds: MagicMock) -> None:
            fake_st.session_state.index = LibraryIndex()

        mock_sync = mocker.patch.object(
            app, "sync_library_index", side_effect=fake_sync
        )

        app.main()

        mock_sync.assert_called_once()

    def test_shows_library_name_caption(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the current library's friendly name is displayed above
        Switch Library, not the opaque Drive file ID."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_lib_name = "ewk_b1dcfe5a"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert any(
            "ewk_b1dcfe5a" in str(call.args) for call in fake_st.caption.call_args_list
        )

    def test_switch_lib_button_clears_library_session_state(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking "Switch Library" clears the current library's
        cached session state so the selection screen reappears.

        Mirrors Streamlit's real behavior for on_click buttons: the
        callback runs, then Streamlit immediately reruns the script,
        halting execution at that point - modeled here by raising
        stop_rerun right after invoking the callback.
        """
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_lib_name = "ewk_b1dcfe5a"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.session_state.last_sync_time = "t1"
        fake_st.file_uploader.return_value = None

        def button_side_effect(label: str, *args: Any, **kwargs: Any) -> bool:
            if label == "Switch Library":
                kwargs["on_click"]()
                raise stop_rerun
            return False

        fake_st.button.side_effect = button_side_effect
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert "current_lib_id" not in fake_st.session_state
        assert "current_lib_name" not in fake_st.session_state
        assert "current_papers_id" not in fake_st.session_state
        assert "index" not in fake_st.session_state
        assert "last_sync_time" not in fake_st.session_state

    def test_switch_lib_button_clears_confirm_delete_pids(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking "Switch Library" also drops a staged batch-delete
        confirmation, so it can't bleed into the next library opened."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_lib_name = "ewk_b1dcfe5a"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.session_state.confirm_delete_pids = ["a" * 32]
        fake_st.file_uploader.return_value = None

        def button_side_effect(label: str, *args: Any, **kwargs: Any) -> bool:
            if label == "Switch Library":
                kwargs["on_click"]()
                raise stop_rerun
            return False

        fake_st.button.side_effect = button_side_effect
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert "confirm_delete_pids" not in fake_st.session_state

    def test_selecting_paper_row_sets_selected_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking a paper's row selects it and reruns to show it.

        Also covers a legacy/malformed index key (not a 32-char hex id)
        being skipped during the search filter rather than rendered.
        """
        pid = "f" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        legacy_entry = PaperIndexEntry(
            title="Legacy Paper",
            pdf_file_id="pdf2",
            meta_file_id="meta2",
            folder_id="f2",
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={pid: entry, "not-a-hex-id": legacy_entry}
        )
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == f"btn_{pid}"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert fake_st.session_state.selected_paper == pid

    def test_selecting_different_paper_row_clears_confirm_delete_pids(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking a paper's row dismisses any staged batch-delete
        confirmation instead of letting it persist into the newly selected
        paper's view."""
        pid = "f" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.confirm_delete_pids = ["a" * 32]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == f"btn_{pid}"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert fake_st.session_state.selected_paper == pid
        assert fake_st.session_state.confirm_delete_pids is None

    def test_invalid_selected_paper_id_shows_error_and_stops(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test a malformed selected_paper id is rejected instead of being
        used to index into the library."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = "not-a-valid-id"
        fake_st.file_uploader.return_value = None
        fake_st.stop.side_effect = stop_rerun
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        fake_st.error.assert_called_once()
        assert "Invalid paper ID" in fake_st.error.call_args[0][0]

    def test_status_filter_hides_non_matching_paper_row(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test picking a status in the filter hides papers with a
        different status from the rendered rows."""
        pid_read, pid_unread = "a" * 32, "b" * 32
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={
                pid_read: PaperIndexEntry(
                    title="Read Paper",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    status="Read",
                ),
                pid_unread: PaperIndexEntry(
                    title="Unread Paper",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    status="Unread",
                ),
            }
        )
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.multiselect.side_effect = lambda label, **kw: (
            ["✅ Read"] if label == "Status" else []
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        rendered_keys = {c.kwargs.get("key") for c in fake_st.button.call_args_list}
        assert f"btn_{pid_read}" in rendered_keys
        assert f"btn_{pid_unread}" not in rendered_keys

    def test_stale_tags_filter_selection_is_dropped(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test a previously selected tag that no longer exists on any
        paper (e.g. its last paper was deleted or retagged) is silently
        dropped from the persisted filter selection instead of leaving a
        stale value that no longer matches the widget's current options."""
        pid = "a" * 32
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="Urgent Paper",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    tags=["urgent"],
                )
            }
        )
        fake_st.session_state.selected_paper = None
        fake_st.session_state.tags_filter = ["urgent", "obsolete-tag"]
        fake_st.file_uploader.return_value = None
        fake_st.multiselect.side_effect = lambda label, **kw: (
            fake_st.session_state.get(kw.get("key"), []) if label == "Tags" else []
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert fake_st.session_state.tags_filter == ["urgent"]
        fake_st.error.assert_not_called()

    def test_paper_row_icon_reflects_status(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test each paper's row icon matches its status."""
        pid = "a" * 32
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="Reading Paper",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    status="Reading",
                )
            }
        )
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        row_button_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == f"btn_{pid}"
        )
        assert "📖" in row_button_call.args[0]

    def test_paper_row_flags_similar_embeddings_persistently(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test papers with a matching embedding show a warning marker in
        the Library Papers list, computed fresh from the saved index
        regardless of session-only generation state."""
        dupe_pid, unique_pid = "a" * 32, "b" * 32
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={
                dupe_pid: PaperIndexEntry(
                    title="Dupe One",
                    pdf_file_id="p1",
                    meta_file_id="m1",
                    folder_id="f1",
                    embedding=[1.0, 0.0, 0.0],
                ),
                "c" * 32: PaperIndexEntry(
                    title="Dupe Two",
                    pdf_file_id="p2",
                    meta_file_id="m2",
                    folder_id="f2",
                    embedding=[1.0, 0.0, 0.0],
                ),
                unique_pid: PaperIndexEntry(
                    title="Unique Paper",
                    pdf_file_id="p3",
                    meta_file_id="m3",
                    folder_id="f3",
                    embedding=[0.0, 1.0, 0.0],
                ),
            }
        )
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        dupe_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == f"btn_{dupe_pid}"
        )
        unique_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == f"btn_{unique_pid}"
        )
        assert "⚠️" in dupe_call.args[0]
        assert "⚠️" not in unique_call.args[0]


class TestMainUploadFlow:
    """Test suite for main()'s sidebar upload flow."""

    def test_uploader_is_wrapped_in_expander_and_height_capped_container(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the uploader lives inside a collapsed-by-default expander,
        with the file list itself inside a fixed-height container so
        selecting many files doesn't grow the sidebar unbounded."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        fake_st.expander.assert_any_call("Upload Paper(s)", expanded=False)
        fake_st.expander.assert_any_call("Library Papers", expanded=True)
        assert any(
            call.kwargs.get("height") == 150
            for call in fake_st.container.call_args_list
        )

    def test_upload_button_triggers_upload_and_reruns_on_success(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test a successful upload uploads the index and reruns."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = [make_uploaded_file("a.pdf")]
        fake_st.button.side_effect = lambda label, **kw: label == "Upload"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mock_upload_papers = mocker.patch.object(
            app, "upload_papers", return_value=True
        )
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        with pytest.raises(stop_rerun):
            app.main()

        mock_upload_papers.assert_called_once()
        mock_upload_index.assert_called_once()
        fake_st.success.assert_called_once()

    def test_upload_button_reports_partial_failure_without_rerun(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a partially failed upload must warn instead of
        unconditionally showing success and rerunning past the error."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = [make_uploaded_file("a.pdf")]
        fake_st.button.side_effect = lambda label, **kw: label == "Upload"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "upload_papers", return_value=False)
        mocker.patch.object(app, "upload_library_index")

        app.main()

        fake_st.warning.assert_called_once()
        fake_st.success.assert_not_called()
        fake_st.rerun.assert_not_called()

    def test_upload_shows_determinate_progress_bar_not_spinner(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: uploading must show a determinate st.progress
        bar keyed to file count/name (updated via upload_papers'
        on_progress callback), not an indeterminate st.spinner, and must
        clear the bar once the batch finishes."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        fake_st.file_uploader.return_value = files
        fake_st.button.side_effect = lambda label, **kw: label == "Upload"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        def fake_upload_papers(
            creds: object,
            uploaded_files: object,
            on_progress: Any = None,
        ) -> bool:
            if on_progress is not None:
                on_progress(1, 2, "a.pdf")
                on_progress(2, 2, "b.pdf")
            return True

        mocker.patch.object(app, "upload_papers", side_effect=fake_upload_papers)
        mock_progress = MagicMock()
        fake_st.progress.return_value = mock_progress

        with pytest.raises(stop_rerun):
            app.main()

        fake_st.progress.assert_called_once_with(0.0, text="Uploading (0/2)...")
        assert mock_progress.progress.call_args_list == [
            mocker.call(0.5, text="Uploading (1/2): a.pdf"),
            mocker.call(1.0, text="Uploading (2/2): b.pdf"),
        ]
        mock_progress.empty.assert_called_once()
        fake_st.spinner.assert_not_called()
        mock_upload_index.assert_called_once()

    def test_progress_bar_stays_visible_until_after_index_upload(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: the progress bar must not be cleared until after
        upload_library_index() finishes, so the UI never looks frozen with
        no loading indicator while the index syncs to Drive (Jules review
        finding on PR #35)."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        files = [make_uploaded_file("a.pdf")]
        fake_st.file_uploader.return_value = files
        fake_st.button.side_effect = lambda label, **kw: label == "Upload"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "upload_papers", return_value=True)
        call_order: list[str] = []
        mocker.patch.object(
            app,
            "upload_library_index",
            side_effect=lambda *a, **kw: call_order.append("upload_library_index"),
        )
        mock_progress = MagicMock()
        mock_progress.empty.side_effect = lambda: call_order.append("progress.empty")
        fake_st.progress.return_value = mock_progress

        with pytest.raises(stop_rerun):
            app.main()

        assert call_order == ["upload_library_index", "progress.empty"]

    def test_progress_cleared_even_if_index_upload_raises(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Regression test: if upload_library_index() raises, the progress
        bar must still be cleared and last_sync_time still reset, instead of
        being left frozen on screen under Streamlit's error traceback
        (Jules review finding on PR #35)."""
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.session_state.last_sync_time = "t0"
        files = [make_uploaded_file("a.pdf")]
        fake_st.file_uploader.return_value = files
        fake_st.button.side_effect = lambda label, **kw: label == "Upload"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "upload_papers", return_value=True)
        mocker.patch.object(
            app,
            "upload_library_index",
            side_effect=RuntimeError("network blip"),
        )
        mock_progress = MagicMock()
        fake_st.progress.return_value = mock_progress

        with pytest.raises(RuntimeError, match="network blip"):
            app.main()

        mock_progress.empty.assert_called_once()
        assert fake_st.session_state.last_sync_time is None


class TestMainDeleteFlow:
    """Test suite for main()'s sidebar icon-bar delete flow."""

    def test_trash_with_no_checked_papers_warns(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking trash with nothing checked just warns."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.checkbox.return_value = False
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == "trash_icon"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        fake_st.warning.assert_any_call("No papers selected.")
        assert "confirm_delete_pids" not in fake_st.session_state

        trash_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == "trash_icon"
        )
        assert trash_call.kwargs.get("type") == "secondary"

    def test_trash_icon_stays_secondary_when_a_paper_is_checked(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the bin icon keeps its fixed "secondary" type regardless of
        checkbox state, even if the checked paper is currently hidden by a
        search/status/tag filter."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.session_state[f"chk_{pid}"] = True
        fake_st.button.return_value = False
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        trash_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == "trash_icon"
        )
        assert trash_call.kwargs.get("type") == "secondary"

    def test_trash_with_checked_paper_shows_confirmation(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking trash with a checked paper stages a confirmation
        instead of deleting immediately."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.session_state[f"chk_{pid}"] = True
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == "trash_icon"
        mock_delete = mocker.patch.object(app, "delete_selected_papers")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert fake_st.session_state.confirm_delete_pids == [pid]
        mock_delete.assert_not_called()

    def test_confirming_delete_calls_delete_selected_papers_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Confirm on a staged deletion actually deletes and
        clears the confirmation state."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.session_state.confirm_delete_pids = [pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "confirm_delete_btn"
        )
        mock_delete = mocker.patch.object(app, "delete_selected_papers")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        mock_delete.assert_called_once_with(
            mocker.ANY, [pid], fake_st.session_state.index, "papers_123", tmp_path
        )
        assert fake_st.session_state.confirm_delete_pids is None

    def test_cancelling_delete_clears_confirmation_without_deleting(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Cancel on a staged deletion clears it without
        touching Drive or the index."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.confirm_delete_pids = [pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "cancel_delete_btn"
        )
        mock_delete = mocker.patch.object(app, "delete_selected_papers")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        mock_delete.assert_not_called()
        assert fake_st.session_state.confirm_delete_pids is None

    def test_confirming_failed_delete_skips_rerun_but_clears_confirmation(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Regression test: if delete_selected_papers reports a failure
        (e.g. a Drive deletion or the index upload failed and already showed
        an st.error), Confirm must not rerun - rerunning would restart the
        script and wipe out that error before the user ever sees it - but
        the stale confirmation must still be cleared."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.session_state.confirm_delete_pids = [pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "confirm_delete_btn"
        )
        mock_delete = mocker.patch.object(
            app, "delete_selected_papers", return_value=False
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()  # must not raise (no rerun on failure)

        mock_delete.assert_called_once_with(
            mocker.ANY, [pid], fake_st.session_state.index, "papers_123", tmp_path
        )
        fake_st.rerun.assert_not_called()
        assert fake_st.session_state.confirm_delete_pids is None

    def test_confirming_partial_failure_recomputes_filtered_papers(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Regression test: a partial batch-delete failure must not leave
        the sidebar rendering a row for a paper delete_selected_papers
        already removed from the index. Before the fix, filtered_papers
        was computed before the delete ran, so a stale row for the removed
        paper stayed on screen; clicking it would set selected_paper to a
        pid missing from index.papers and crash the main-area lookup with
        a KeyError."""
        deleted_pid = "a" * 32
        kept_pid = "b" * 32
        deleted_entry = PaperIndexEntry(
            title="Deleted Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="f1",
        )
        kept_entry = PaperIndexEntry(
            title="Kept Paper", pdf_file_id="pdf2", meta_file_id="meta2", folder_id="f2"
        )
        index = LibraryIndex(papers={deleted_pid: deleted_entry, kept_pid: kept_entry})
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = index
        fake_st.session_state.selected_paper = None
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.session_state.confirm_delete_pids = [deleted_pid, kept_pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "confirm_delete_btn"
        )

        def fake_delete(
            _creds: MagicMock,
            _pids: list[str],
            idx: LibraryIndex,
            _papers_id: str,
            _local_dir: Path,
        ) -> bool:
            """Mimics delete_selected_papers removing one paper but
            reporting an overall failure for the batch."""
            idx.papers.pop(deleted_pid)
            return False

        mock_delete = mocker.patch.object(
            app, "delete_selected_papers", side_effect=fake_delete
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()  # must not raise (no rerun on failure)

        mock_delete.assert_called_once()
        rendered_keys = {c.kwargs.get("key") for c in fake_st.button.call_args_list}
        assert f"btn_{deleted_pid}" not in rendered_keys
        assert f"btn_{kept_pid}" in rendered_keys


class TestPersistGeneratedMetadata:
    """Test suite for persist_generated_metadata."""

    def test_returns_false_and_skips_drive_calls_when_nothing_staged(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a pid with no staged draft is a no-op."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        mock_upload_file = mocker.patch.object(
            metadata_generation, "upload_file_to_folder"
        )

        result = app.persist_generated_metadata(
            creds=MagicMock(),
            pid=pid,
            index=index,
            local_meta_path=tmp_path / "meta.json",
        )

        assert result is False
        mock_upload_file.assert_not_called()

    def test_writes_uploads_and_updates_index_with_no_existing_local_cache(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a staged draft is written, uploaded, and applied to the
        index entry when there is no pre-existing local meta.json."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Old Title", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
        )
        index = LibraryIndex(papers={pid: entry})
        fake_st.session_state[f"generated_{pid}"] = {
            "title": "New Title",
            "abstract": "A new abstract.",
            "tags": ["nlp", "transformers"],
            "embedding": [0.1, 0.2],
        }
        fake_st.session_state[f"dupes_{pid}"] = [("other", "Other Paper", 0.9)]
        mock_upload_file = mocker.patch.object(
            metadata_generation, "upload_file_to_folder"
        )
        local_meta_path = tmp_path / "meta.json"

        result = app.persist_generated_metadata(
            creds=MagicMock(), pid=pid, index=index, local_meta_path=local_meta_path
        )

        assert result is True
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["title"] == "New Title"
        assert saved["abstract"] == "A new abstract."
        assert saved["tags"] == ["nlp", "transformers"]
        assert saved["embedding"] == [0.1, 0.2]
        mock_upload_file.assert_called_once_with(
            mocker.ANY, "f1", local_meta_path, "meta.json", "application/json"
        )
        assert index.papers[pid].title == "New Title"
        assert index.papers[pid].tags == ["nlp", "transformers"]
        assert index.papers[pid].embedding == [0.1, 0.2]
        assert f"generated_{pid}" not in fake_st.session_state
        assert f"dupes_{pid}" not in fake_st.session_state

    def test_preserves_notes_citation_and_status_from_existing_local_cache(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test fields not covered by the draft survive from the paper's
        existing local metadata cache."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Old Title", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
        )
        index = LibraryIndex(papers={pid: entry})
        local_meta_path = tmp_path / "meta.json"
        existing = PaperMetadata(
            title="Old Title",
            notes="My notes",
            citation="Some Citation",
            status="Reading",
        )
        local_meta_path.write_text(existing.model_dump_json(indent=2), encoding="utf-8")
        fake_st.session_state[f"generated_{pid}"] = {
            "title": "New Title",
            "abstract": "A new abstract.",
            "tags": ["nlp"],
            "embedding": [0.5],
        }
        mocker.patch.object(metadata_generation, "upload_file_to_folder")

        app.persist_generated_metadata(
            creds=MagicMock(), pid=pid, index=index, local_meta_path=local_meta_path
        )

        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["notes"] == "My notes"
        assert saved["citation"] == "Some Citation"
        assert saved["status"] == "Reading"
        assert saved["title"] == "New Title"

    def test_falls_back_to_defaults_when_local_cache_is_corrupt(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a corrupt local meta.json doesn't abort persistence, and the
        user is warned that the existing saved metadata could not be
        recovered."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Old Title", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
        )
        index = LibraryIndex(papers={pid: entry})
        local_meta_path = tmp_path / "meta.json"
        local_meta_path.write_text("not valid json", encoding="utf-8")
        fake_st.session_state[f"generated_{pid}"] = {
            "title": "New Title",
            "abstract": "A new abstract.",
            "tags": ["nlp"],
            "embedding": [0.5],
        }
        mocker.patch.object(metadata_generation, "upload_file_to_folder")

        result = app.persist_generated_metadata(
            creds=MagicMock(), pid=pid, index=index, local_meta_path=local_meta_path
        )

        assert result is True
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["title"] == "New Title"
        assert any(
            "Could not recover" in str(call.args)
            for call in fake_st.warning.call_args_list
        )


class TestGenerateMetadataForSelected:
    """Test suite for generate_metadata_for_selected."""

    def test_downloads_missing_pdf_then_generates_for_each_paper(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a paper with no locally cached PDF gets it downloaded before
        generation, and every requested paper is processed."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        mock_download = mocker.patch.object(metadata_generation, "download_file")
        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mock_generate = mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper", return_value=True
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        assert mock_download.call_count == 2
        assert mock_generate.call_count == 2
        mock_generate.assert_any_call(pid1, tmp_path / pid1 / "paper.pdf")
        mock_generate.assert_any_call(pid2, tmp_path / pid2 / "paper.pdf")

    def test_skips_download_when_pdf_already_cached(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test an already-downloaded PDF isn't re-fetched from Drive."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        local_pdf_path = tmp_path / pid / "paper.pdf"
        local_pdf_path.parent.mkdir(parents=True)
        local_pdf_path.write_bytes(b"pdf-bytes")
        mock_download = mocker.patch.object(metadata_generation, "download_file")
        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper", return_value=True
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        mock_download.assert_not_called()

    def test_pdf_download_failure_reports_error_and_continues(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a failed PDF download is reported without stopping the rest
        of the batch."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        mocker.patch.object(
            metadata_generation,
            "download_file",
            side_effect=[RuntimeError("network blip"), None],
        )
        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mock_generate = mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper", return_value=True
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        fake_st.error.assert_called_once()
        mock_generate.assert_called_once_with(pid2, tmp_path / pid2 / "paper.pdf")

    def test_skips_pid_missing_from_index(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a stale/unknown pid is skipped rather than crashing."""
        mock_generate = mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper"
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=["missing" * 5],
            index=LibraryIndex(),
            papers_id="papers_123",
            local_lib_dir=tmp_path,
        )

        mock_generate.assert_not_called()

    def test_paces_between_papers_but_not_after_the_last(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test sleep_fn is called between papers, but not once after the
        final paper in the batch."""
        pids = ["a" * 32, "b" * 32, "c" * 32]
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title=pid, pdf_file_id=pid, meta_file_id=pid, folder_id=pid
                )
                for pid in pids
            }
        )
        for pid in pids:
            (tmp_path / pid).mkdir(parents=True)
            (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper", return_value=True
        )
        mock_sleep = mocker.Mock()

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=pids,
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=mock_sleep,
        )

        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(app.BULK_GENERATE_DELAY_SECONDS)

    def test_stops_batch_when_hf_quota_exceeded(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the batch stops immediately after a paper flags
        hf_quota_exceeded, instead of retrying guaranteed-to-fail papers."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        for pid in (pid1, pid2):
            (tmp_path / pid).mkdir(parents=True)
            (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")

        def fake_generate(pid: str, _local_pdf_path: Path) -> bool:
            fake_st.session_state["hf_quota_exceeded"] = True
            return False

        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mock_generate = mocker.patch.object(
            metadata_generation,
            "generate_metadata_for_paper",
            side_effect=fake_generate,
        )
        mock_sleep = mocker.Mock()

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=mock_sleep,
        )

        mock_generate.assert_called_once_with(pid1, tmp_path / pid1 / "paper.pdf")
        mock_sleep.assert_not_called()
        assert any(
            "Stopped after 1/2 paper(s)" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_persists_each_paper_and_uploads_index_once_after_the_batch(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a successful batch persists every paper's draft and
        uploads the whole-library index exactly once, after the loop."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        for pid in (pid1, pid2):
            (tmp_path / pid).mkdir(parents=True)
            (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")

        def fake_generate(pid: str, _local_pdf_path: Path) -> bool:
            fake_st.session_state[f"generated_{pid}"] = {
                "title": f"Title {pid}",
                "abstract": "Abstract.",
                "tags": ["nlp"],
                "embedding": [0.1],
            }
            return True

        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation,
            "generate_metadata_for_paper",
            side_effect=fake_generate,
        )
        mocker.patch.object(metadata_generation, "upload_file_to_folder")
        mock_upload_index = mocker.patch.object(
            metadata_generation, "upload_library_index"
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        assert index.papers[pid1].title == f"Title {pid1}"
        assert index.papers[pid2].title == f"Title {pid2}"
        mock_upload_index.assert_called_once_with(mocker.ANY, "papers_123", index)

    def test_persist_failure_for_one_paper_is_reported_and_batch_continues(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a persistence failure for one paper is reported without
        stopping the rest of the batch, and the surviving paper is still
        applied to the index."""
        pid1, pid2 = "a" * 32, "b" * 32
        index = LibraryIndex(
            papers={
                pid1: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                ),
                pid2: PaperIndexEntry(
                    title="Two", pdf_file_id="p2", meta_file_id="m2", folder_id="f2"
                ),
            }
        )
        for pid in (pid1, pid2):
            (tmp_path / pid).mkdir(parents=True)
            (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")

        def fake_generate(pid: str, _local_pdf_path: Path) -> bool:
            fake_st.session_state[f"generated_{pid}"] = {
                "title": f"Title {pid}",
                "abstract": "Abstract.",
                "tags": ["nlp"],
                "embedding": [0.1],
            }
            return True

        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation,
            "generate_metadata_for_paper",
            side_effect=fake_generate,
        )
        mocker.patch.object(
            metadata_generation,
            "upload_file_to_folder",
            side_effect=[RuntimeError("boom"), None],
        )
        mock_upload_index = mocker.patch.object(
            metadata_generation, "upload_library_index"
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid1, pid2],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        assert any(
            "failed to save it" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert index.papers[pid1].title == "One"
        assert index.papers[pid2].title == f"Title {pid2}"
        mock_upload_index.assert_called_once_with(mocker.ANY, "papers_123", index)

    def test_does_not_upload_index_when_nothing_was_persisted(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the index isn't uploaded at all when every paper in the
        batch failed to generate (nothing to save)."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        (tmp_path / pid).mkdir(parents=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation, "generate_metadata_for_paper", return_value=False
        )
        mock_upload_index = mocker.patch.object(
            metadata_generation, "upload_library_index"
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        mock_upload_index.assert_not_called()

    def test_index_upload_failure_after_batch_is_reported(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a failure uploading the whole-library index after the batch
        is reported rather than raised."""
        pid = "a" * 32
        index = LibraryIndex(
            papers={
                pid: PaperIndexEntry(
                    title="One", pdf_file_id="p1", meta_file_id="m1", folder_id="f1"
                )
            }
        )
        (tmp_path / pid).mkdir(parents=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")

        def fake_generate(pid: str, _local_pdf_path: Path) -> bool:
            fake_st.session_state[f"generated_{pid}"] = {
                "title": "New Title",
                "abstract": "Abstract.",
                "tags": ["nlp"],
                "embedding": [0.1],
            }
            return True

        mocker.patch.object(metadata_generation, "sync_paper_metadata")
        mocker.patch.object(
            metadata_generation,
            "generate_metadata_for_paper",
            side_effect=fake_generate,
        )
        mocker.patch.object(metadata_generation, "upload_file_to_folder")
        mocker.patch.object(
            metadata_generation,
            "upload_library_index",
            side_effect=RuntimeError("network blip"),
        )

        app.generate_metadata_for_selected(
            creds=MagicMock(),
            pids=[pid],
            index=index,
            papers_id="papers_123",
            local_lib_dir=tmp_path,
            sleep_fn=lambda _: None,
        )

        assert any(
            "syncing the index failed" in str(call.args)
            for call in fake_st.error.call_args_list
        )


class TestMainBulkGenerateFlow:
    """Test suite for main()'s sidebar icon-bar bulk generate flow."""

    def test_bulk_generate_with_no_checked_papers_warns(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking the bulk generate icon with nothing checked just warns."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.checkbox.return_value = False
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "bulk_generate_icon"
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        fake_st.warning.assert_any_call("No papers selected.")
        assert "confirm_generate_pids" not in fake_st.session_state

        generate_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == "bulk_generate_icon"
        )
        assert generate_call.kwargs.get("type") == "secondary"

    def test_bulk_generate_icon_stays_secondary_when_a_paper_is_checked(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the bulk generate icon keeps its fixed "secondary" type
        regardless of checkbox state, distinguishing it from the "primary"
        delete icon by native styling alone (issue #23)."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.session_state[f"chk_{pid}"] = True
        fake_st.button.return_value = False
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        generate_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == "bulk_generate_icon"
        )
        assert generate_call.kwargs.get("type") == "secondary"

    def test_icon_bar_columns_use_no_gap(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the icon-bar columns are requested with gap=None, so the
        bin/generate icons render adjacent using only native Streamlit
        layout (no CSS injection)."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.checkbox.return_value = False
        fake_st.button.return_value = False
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        icon_bar_call = next(
            c
            for c in fake_st.columns.call_args_list
            if c.args and c.args[0] == [1, 1, 1, 7]
        )
        assert "gap" in icon_bar_call.kwargs
        assert icon_bar_call.kwargs["gap"] is None

    def test_bulk_generate_with_checked_paper_shows_confirmation(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking the bulk generate icon with a checked paper stages
        a confirmation instead of generating immediately."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.session_state[f"chk_{pid}"] = True
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "bulk_generate_icon"
        )
        mock_generate = mocker.patch.object(app, "generate_metadata_for_selected")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert fake_st.session_state.confirm_generate_pids == [pid]
        mock_generate.assert_not_called()

    def test_generate_missing_icon_stages_confirmation_for_missing_papers(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking the wizard icon stages a confirmation for every
        paper with no generated metadata yet, ignoring checkbox selection."""
        pid_missing, pid_has_tags = "a" * 32, "b" * 32
        entry_missing = PaperIndexEntry(
            title="Missing", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        entry_has_tags = PaperIndexEntry(
            title="Has Tags",
            pdf_file_id="pdf2",
            meta_file_id="meta2",
            folder_id="f2",
            tags=["nlp"],
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(
            papers={pid_missing: entry_missing, pid_has_tags: entry_has_tags}
        )
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "generate_missing_icon"
        )
        mock_generate = mocker.patch.object(app, "generate_metadata_for_selected")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert fake_st.session_state.confirm_generate_pids == [pid_missing]
        mock_generate.assert_not_called()

    def test_generate_missing_icon_with_none_missing_shows_info(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test clicking the wizard icon when every paper already has
        metadata shows an info message instead of staging a confirmation."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Has Tags",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="f1",
            tags=["nlp"],
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "generate_missing_icon"
        )
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        assert "confirm_generate_pids" not in fake_st.session_state
        assert any(
            "already has metadata" in str(call.args)
            for call in fake_st.info.call_args_list
        )

    def test_generate_missing_icon_uses_secondary_type(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test the generate-missing icon renders as "secondary", the
        same fixed type as the other two icon-bar buttons."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.file_uploader.return_value = None
        fake_st.button.return_value = False
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        app.main()

        generate_missing_call = next(
            c
            for c in fake_st.button.call_args_list
            if c.kwargs.get("key") == "generate_missing_icon"
        )
        assert generate_missing_call.kwargs.get("type") == "secondary"

    def test_confirming_bulk_generate_calls_generate_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Confirm on a staged bulk generation actually
        generates and clears the confirmation state."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.session_state.confirm_generate_pids = [pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "confirm_generate_btn"
        )
        mock_generate = mocker.patch.object(app, "generate_metadata_for_selected")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        mock_generate.assert_called_once_with(
            mocker.ANY, [pid], fake_st.session_state.index, "papers_123", tmp_path
        )
        assert fake_st.session_state.confirm_generate_pids is None

    def test_cancelling_bulk_generate_clears_confirmation_without_generating(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Cancel on a staged bulk generation clears it
        without generating anything."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Some Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = None
        fake_st.session_state.confirm_generate_pids = [pid]
        fake_st.file_uploader.return_value = None
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == "cancel_generate_btn"
        )
        mock_generate = mocker.patch.object(app, "generate_metadata_for_selected")
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        mock_generate.assert_not_called()
        assert fake_st.session_state.confirm_generate_pids is None


class TestMainMetadataView:
    """Test suite for main()'s paper detail / metadata editing view."""

    def _select_paper(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path, pid: str
    ) -> PaperIndexEntry:
        """Configures session state to open a single selected paper's view.

        Args:
            fake_st: The mocked streamlit module.
            mocker: The pytest-mock fixture.
            tmp_path: Pytest's per-test temporary directory.
            pid: The paper id to select.

        Returns:
            PaperIndexEntry: The index entry registered for the paper.
        """
        entry = PaperIndexEntry(
            title="A Paper", pdf_file_id="pdf1", meta_file_id="meta1", folder_id="f1"
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = pid
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.file_uploader.return_value = None
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "download_file")
        return entry

    def test_pdf_download_failure_shows_warning_without_crashing(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: if fetching the PDF from Drive fails, the app
        must report the error and fall back to a warning in place of the PDF
        viewer, instead of letting the exception propagate and crash the
        page."""
        pid = "a" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(
            app, "download_file", side_effect=RuntimeError("network blip")
        )
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Failed to load PDF" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert any(
            "PDF could not be loaded" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_recovers_valid_fields_after_validation_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: a corrupted local meta.json (e.g. an invalid
        status value) must not crash the paper view - valid fields are kept
        and the title falls back to the index entry's title."""
        pid = "b" * 32
        entry = self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text(
            json.dumps({"title": "Cached Title", "status": "NotAStatus"})
        )
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "recovering valid fields" in str(call.args)
            for call in fake_st.warning.call_args_list
        )
        assert entry.title == "A Paper"

    def test_falls_back_to_blank_form_when_field_recovery_itself_fails(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: if recovering valid fields from an invalid
        meta.json itself raises, the paper must fall back to a blank
        metadata form AND the user must be warned that their saved
        notes/citation/status could not be recovered - not silently
        dropped."""
        pid = "b" * 32
        entry = self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text(
            json.dumps({"title": "Cached Title", "status": "NotAStatus"})
        )
        fake_st.form_submit_button.return_value = False

        real_paper_metadata = app.PaperMetadata
        call_count = {"n": 0}

        def fake_paper_metadata(**kwargs: Any) -> PaperMetadata:
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("recovery boom")
            return real_paper_metadata(**kwargs)

        mocker.patch.object(app, "PaperMetadata", side_effect=fake_paper_metadata)

        app.main()

        assert any(
            "Could not recover" in str(call.args)
            for call in fake_st.warning.call_args_list
        )
        assert entry.title == "A Paper"

    def test_form_submit_saves_and_uploads_metadata(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test submitting the metadata form saves it locally, uploads it to
        Drive, updates the index entry's title if it changed, and reruns so
        the sidebar reflects the new status/tags immediately."""
        pid = "c" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mock_upload_file = mocker.patch.object(app, "upload_file_to_folder")
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        fake_st.text_input.side_effect = lambda label, **kw: {
            "Title": "Updated Title",
            "Tags (comma separated)": "tag1, tag2",
            "Citation": "Cite X",
        }.get(label, kw.get("value", ""))
        fake_st.selectbox.return_value = "✅ Read"
        fake_st.text_area.return_value = "Some notes"
        fake_st.form_submit_button.return_value = True

        with pytest.raises(stop_rerun):
            app.main()

        local_meta_path = tmp_path / pid / "meta.json"
        assert local_meta_path.exists()
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["title"] == "Updated Title"
        assert saved["tags"] == ["tag1", "tag2"]
        assert saved["status"] == "Read"
        mock_upload_file.assert_called_once()
        mock_upload_index.assert_called_once()
        fake_st.success.assert_called_once()

    def test_form_submit_syncs_index_even_without_title_change(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: saving tags/status must update and re-upload
        the index even when the title is unchanged, since the sidebar's
        filters and status icon now read tags/status from the index
        instead of from each paper's meta.json."""
        pid = "f" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mocker.patch.object(app, "upload_file_to_folder")
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        fake_st.text_input.side_effect = lambda label, **kw: {
            "Title": "A Paper",
            "Tags (comma separated)": "urgent",
        }.get(label, kw.get("value", ""))
        fake_st.selectbox.return_value = "📖 Reading"
        fake_st.text_area.return_value = ""
        fake_st.form_submit_button.return_value = True

        with pytest.raises(stop_rerun):
            app.main()

        mock_upload_index.assert_called_once()
        assert fake_st.session_state.index.papers[pid].tags == ["urgent"]
        assert fake_st.session_state.index.papers[pid].status == "Reading"

    def test_form_submit_strips_pdf_from_edited_title(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: typing '.pdf' into the title field on save must
        not let it end up in the stored title."""
        pid = "9" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mocker.patch.object(app, "upload_file_to_folder")
        mocker.patch.object(app, "upload_library_index")

        fake_st.text_input.side_effect = lambda label, **kw: {
            "Title": "Renamed Paper.pdf",
        }.get(label, kw.get("value", ""))
        fake_st.selectbox.return_value = "📄 Unread"
        fake_st.text_area.return_value = ""
        fake_st.form_submit_button.return_value = True

        with pytest.raises(stop_rerun):
            app.main()

        local_meta_path = tmp_path / pid / "meta.json"
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["title"] == "Renamed Paper"
        assert fake_st.session_state.index.papers[pid].title == "Renamed Paper"

    def test_unparseable_metadata_file_reports_generic_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Regression test: a metadata file that isn't even valid JSON (as
        opposed to valid JSON with an invalid field) must surface a generic
        load error rather than crashing or being mistaken for a
        ValidationError."""
        pid = "d" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text("not valid json")
        fake_st.form_submit_button.return_value = False

        app.main()

        fake_st.error.assert_called_once()
        assert "Could not load metadata" in fake_st.error.call_args[0][0]

    def test_metadata_unavailable_disables_editing_warning(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a failed metadata sync warns that editing is disabled,
        rather than silently allowing edits over unconfirmed data."""
        pid = "e" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=False)
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Could not load the latest metadata" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_generate_button_disabled_when_pdf_unavailable(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the Generate metadata button is disabled when the PDF
        could not be loaded, since generation needs the local PDF."""
        pid = "1" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(
            app, "download_file", side_effect=RuntimeError("network blip")
        )
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.form_submit_button.return_value = False

        app.main()

        generate_call = next(
            call
            for call in fake_st.button.call_args_list
            if call.args and call.args[0] == "✨ Generate metadata"
        )
        assert generate_call.kwargs["disabled"] is True

    def test_generate_button_has_tooltip_naming_the_models_used(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the Generate metadata button explains what it does and
        which Hugging Face models it calls."""
        pid = "9" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.form_submit_button.return_value = False

        app.main()

        generate_call = next(
            call
            for call in fake_st.button.call_args_list
            if call.args and call.args[0] == "✨ Generate metadata"
        )
        help_text = generate_call.kwargs["help"]
        assert app.DEFAULT_GENERATION_MODEL in help_text
        assert app.DEFAULT_EMBEDDING_MODEL in help_text

    def test_generate_button_stages_confirm_when_metadata_already_exists(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test clicking Generate metadata on a paper that already has an
        abstract/tags stages a confirmation instead of regenerating right
        away, so the existing draft isn't silently overwritten."""
        pid = "c1" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text(
            json.dumps({"title": "A Paper", "abstract": "Existing abstract."})
        )
        mock_generate = mocker.patch.object(app, "generate_metadata_for_paper")
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert fake_st.session_state[f"confirm_regenerate_{pid}"] is True
        mock_generate.assert_not_called()

    def test_generate_button_stages_confirm_when_form_has_unsaved_edits(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test clicking Generate metadata while the title field has an
        unsaved edit (no saved abstract/tags yet) stages a confirmation
        instead of generating immediately, so the edit isn't silently
        discarded when the draft overwrites the title_{pid} widget state."""
        pid = "c3" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.session_state[f"title_{pid}"] = "An Unsaved Edited Title"
        mock_generate = mocker.patch.object(app, "generate_metadata_for_paper")
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert fake_st.session_state[f"confirm_regenerate_{pid}"] is True
        mock_generate.assert_not_called()

    def test_confirming_regenerate_generates_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Regenerate on a staged confirmation actually
        generates and clears the confirmation state."""
        pid = "c2" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text(
            json.dumps({"title": "A Paper", "abstract": "Existing abstract."})
        )
        fake_st.session_state[f"confirm_regenerate_{pid}"] = True
        mock_generate = mocker.patch.object(
            app, "generate_metadata_for_paper", return_value=True
        )
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == f"confirm_regenerate_btn_{pid}"
        )
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        mock_generate.assert_called_once_with(pid, tmp_path / pid / "paper.pdf")
        assert f"confirm_regenerate_{pid}" not in fake_st.session_state

    def test_cancelling_regenerate_clears_confirmation_without_generating(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Cancel on a staged confirmation clears it without
        generating anything."""
        pid = "c3" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        local_meta_path = tmp_path / pid / "meta.json"
        local_meta_path.parent.mkdir(parents=True, exist_ok=True)
        local_meta_path.write_text(
            json.dumps({"title": "A Paper", "abstract": "Existing abstract."})
        )
        fake_st.session_state[f"confirm_regenerate_{pid}"] = True
        mock_generate = mocker.patch.object(app, "generate_metadata_for_paper")
        fake_st.button.side_effect = lambda label, **kw: (
            kw.get("key") == f"cancel_regenerate_btn_{pid}"
        )
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        mock_generate.assert_not_called()
        assert f"confirm_regenerate_{pid}" not in fake_st.session_state

    def test_generate_button_click_stages_draft_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking Generate metadata stages a draft (and duplicate
        matches) in session state and reruns, without touching Drive."""
        pid = "2" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        generated = GeneratedMetadata(
            title="Gen Title", abstract="Gen Abstract", tags=["ai", "nlp"]
        )
        mocker.patch.object(
            metadata_generation, "generate_paper_metadata", return_value=generated
        )
        mocker.patch.object(metadata_generation, "embed_text", return_value=[0.1] * 384)
        mocker.patch.object(metadata_generation, "find_similar_papers", return_value=[])
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        draft = fake_st.session_state[f"generated_{pid}"]
        assert draft["title"] == "Gen Title"
        assert draft["abstract"] == "Gen Abstract"
        assert draft["tags"] == ["ai", "nlp"]
        assert draft["embedding"] == [0.1] * 384
        assert fake_st.session_state[f"dupes_{pid}"] == []
        # Regression: the form's widget keys must be overwritten directly,
        # since Streamlit ignores value=... once a keyed widget has rendered.
        assert fake_st.session_state[f"title_{pid}"] == "Gen Title"
        assert fake_st.session_state[f"abstract_{pid}"] == "Gen Abstract"
        assert fake_st.session_state[f"tags_{pid}"] == "ai, nlp"

    def test_generate_button_passes_existing_library_tags(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test generation is given every tag already used in the library,
        so it's biased toward reusing them instead of inventing new ones."""
        pid = "2b" * 16
        other_pid = "2c" * 16
        entry = self._select_paper(fake_st, mocker, tmp_path, pid)
        fake_st.session_state.index.papers[other_pid] = PaperIndexEntry(
            title="Other Paper",
            pdf_file_id="pdf2",
            meta_file_id="meta2",
            folder_id="f2",
            tags=["nlp", "ai"],
        )
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        mock_generate = mocker.patch.object(
            metadata_generation,
            "generate_paper_metadata",
            return_value=GeneratedMetadata(title="T", abstract="A", tags=["ai"]),
        )
        mocker.patch.object(metadata_generation, "embed_text", return_value=[0.1] * 384)
        mocker.patch.object(metadata_generation, "find_similar_papers", return_value=[])
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False
        assert entry.tags == []

        with pytest.raises(stop_rerun):
            app.main()

        mock_generate.assert_called_once_with("paper text", existing_tags=["ai", "nlp"])

    def test_generate_button_reports_unreadable_pdf_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a corrupt/unreadable PDF surfaces an error instead of crashing."""
        pid = "5" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation,
            "extract_pdf_text",
            side_effect=ValueError("Could not read PDF: bad"),
        )
        mock_generate = mocker.patch.object(
            metadata_generation, "generate_paper_metadata"
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        mock_generate.assert_not_called()
        assert any(
            "Could not read PDF" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert f"generated_{pid}" not in fake_st.session_state

    def test_generate_button_empty_pdf_text_skips_api_calls(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test that when no text can be extracted from the PDF, a warning
        is shown and no Hugging Face calls are made."""
        pid = "3" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(metadata_generation, "extract_pdf_text", return_value="")
        mock_generate = mocker.patch.object(
            metadata_generation, "generate_paper_metadata"
        )
        mock_embed = mocker.patch.object(metadata_generation, "embed_text")
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        mock_generate.assert_not_called()
        mock_embed.assert_not_called()
        assert any(
            "Could not extract text" in str(call.args)
            for call in fake_st.warning.call_args_list
        )
        assert f"generated_{pid}" not in fake_st.session_state

    def test_generate_button_reports_hf_token_missing_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a missing HF_TOKEN surfaces a specific, non-crashing error."""
        pid = "4" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        mocker.patch.object(
            metadata_generation,
            "generate_paper_metadata",
            side_effect=app.HFTokenMissingError("Set HF_TOKEN"),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Set HF_TOKEN" in str(call.args) for call in fake_st.error.call_args_list
        )
        assert f"generated_{pid}" not in fake_st.session_state

    def test_generate_button_reports_generic_failure(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a generic Hugging Face failure is reported without crashing."""
        pid = "5" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        mocker.patch.object(
            metadata_generation,
            "generate_paper_metadata",
            side_effect=RuntimeError("boom"),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Metadata generation failed" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert f"generated_{pid}" not in fake_st.session_state
        assert fake_st.session_state["hf_quota_exceeded"] is False

    def test_generate_button_reports_hf_quota_exceeded_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a 402 Payment Required error surfaces a quota-specific
        message and flags hf_quota_exceeded for bulk-loop callers."""
        pid = "6" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        mocker.patch.object(
            metadata_generation,
            "generate_paper_metadata",
            side_effect=_make_402_error(),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "402 Payment Required" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert f"generated_{pid}" not in fake_st.session_state
        assert fake_st.session_state["hf_quota_exceeded"] is True

    def test_generate_button_reports_non_quota_http_error(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a non-402 HfHubHTTPError falls through to the generic
        failure message and does not set hf_quota_exceeded."""
        pid = "7" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        response = httpx.Response(
            status_code=500, request=httpx.Request("POST", "https://example.com")
        )
        mocker.patch.object(
            metadata_generation,
            "generate_paper_metadata",
            side_effect=HfHubHTTPError("Internal error", response=response),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Metadata generation failed" in str(call.args)
            for call in fake_st.error.call_args_list
        )
        assert fake_st.session_state["hf_quota_exceeded"] is False

    def test_generate_button_stages_draft_when_embedding_hits_quota_error(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test title/abstract/tags stay staged (and hf_quota_exceeded is
        set) when the already-succeeded generation call is followed by a
        402 on the embedding call, instead of the whole draft being
        discarded."""
        pid = "8a" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        generated = GeneratedMetadata(
            title="Gen Title", abstract="Gen Abstract", tags=["ai"]
        )
        mocker.patch.object(
            metadata_generation, "generate_paper_metadata", return_value=generated
        )
        mocker.patch.object(
            metadata_generation, "embed_text", side_effect=_make_402_error()
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        draft = fake_st.session_state[f"generated_{pid}"]
        assert draft["title"] == "Gen Title"
        assert draft["abstract"] == "Gen Abstract"
        assert draft["tags"] == ["ai"]
        assert fake_st.session_state["hf_quota_exceeded"] is True
        assert any(
            "embedding failed" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_generate_button_preserves_existing_embedding_on_embed_failure(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test a previously-computed embedding isn't blanked out when a
        re-generation's embedding call fails - only the new text is staged."""
        pid = "8b" * 16
        entry = self._select_paper(fake_st, mocker, tmp_path, pid)
        entry = entry.model_copy(update={"embedding": [0.9] * 384})
        fake_st.session_state.index.papers[pid] = entry
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        generated = GeneratedMetadata(
            title="New Title", abstract="New Abstract", tags=[]
        )
        mocker.patch.object(
            metadata_generation, "generate_paper_metadata", return_value=generated
        )
        mocker.patch.object(
            metadata_generation, "embed_text", side_effect=RuntimeError("boom")
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        draft = fake_st.session_state[f"generated_{pid}"]
        assert draft["embedding"] == [0.9] * 384
        assert fake_st.session_state["hf_quota_exceeded"] is False

    def test_generate_button_stages_draft_when_embedding_token_missing(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test the draft stays staged when the embedding call fails because
        no HF token is configured for it."""
        pid = "8c" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        generated = GeneratedMetadata(title="T", abstract="A", tags=[])
        mocker.patch.object(
            metadata_generation, "generate_paper_metadata", return_value=generated
        )
        mocker.patch.object(
            metadata_generation,
            "embed_text",
            side_effect=app.HFTokenMissingError("Set HF_TOKEN"),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        assert fake_st.session_state[f"generated_{pid}"]["title"] == "T"
        assert any(
            "Set HF_TOKEN" in str(call.args) for call in fake_st.error.call_args_list
        )

    def test_generate_button_stages_draft_when_embedding_hits_non_quota_http_error(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test a non-402 HfHubHTTPError from the embedding call falls
        through to the generic embed-failure warning without setting
        hf_quota_exceeded."""
        pid = "8d" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        (tmp_path / pid).mkdir(parents=True, exist_ok=True)
        (tmp_path / pid / "paper.pdf").write_bytes(b"pdf-bytes")
        mocker.patch.object(
            metadata_generation, "extract_pdf_text", return_value="paper text"
        )
        generated = GeneratedMetadata(title="T", abstract="A", tags=[])
        mocker.patch.object(
            metadata_generation, "generate_paper_metadata", return_value=generated
        )
        response = httpx.Response(
            status_code=500, request=httpx.Request("POST", "https://example.com")
        )
        mocker.patch.object(
            metadata_generation,
            "embed_text",
            side_effect=HfHubHTTPError("Internal error", response=response),
        )
        fake_st.button.side_effect = lambda label, **kw: label == "✨ Generate metadata"
        fake_st.form_submit_button.return_value = False

        with pytest.raises(stop_rerun):
            app.main()

        assert fake_st.session_state[f"generated_{pid}"]["title"] == "T"
        assert fake_st.session_state["hf_quota_exceeded"] is False
        assert any(
            "embedding failed" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_duplicate_warning_rendered_when_dupes_present(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test staged duplicate matches render a non-blocking warning."""
        pid = "6" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.session_state[f"dupes_{pid}"] = [("other", "Other Paper", 0.95)]
        fake_st.form_submit_button.return_value = False

        app.main()

        assert any(
            "Other Paper" in str(call.args) and "95%" in str(call.args)
            for call in fake_st.warning.call_args_list
        )

    def test_no_duplicate_warning_when_no_dupes(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test no duplicate warning is shown when nothing was staged."""
        pid = "7" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.form_submit_button.return_value = False

        app.main()

        assert not any(
            "% match" in str(call.args) for call in fake_st.warning.call_args_list
        )

    def test_form_prefills_from_draft_when_present(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the form fields prefill from a staged draft rather than the
        Drive-synced meta.json, since the draft holds the latest generation."""
        pid = "8" * 32
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        fake_st.session_state[f"generated_{pid}"] = {
            "title": "Draft Title",
            "abstract": "Draft Abstract",
            "tags": ["draft-tag"],
            "embedding": [0.2] * 384,
        }
        fake_st.form_submit_button.return_value = False

        app.main()

        text_input_calls = {
            call.args[0]: call.kwargs.get("value")
            for call in fake_st.text_input.call_args_list
        }
        text_area_calls = {
            call.args[0]: call.kwargs.get("value")
            for call in fake_st.text_area.call_args_list
        }
        assert text_input_calls["Title"] == "Draft Title"
        assert text_input_calls["Tags (comma separated)"] == "draft-tag"
        assert text_area_calls["Abstract / TL;DR"] == "Draft Abstract"

    def test_save_after_generate_persists_abstract_and_embedding_and_clears_draft(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test saving after a Generate persists the drafted abstract and
        embedding to meta.json and the index, and clears the staged draft
        so a later plain edit isn't shadowed by a stale generation."""
        pid = "a1" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mocker.patch.object(app, "upload_file_to_folder")
        mocker.patch.object(app, "upload_library_index")
        fake_st.session_state[f"generated_{pid}"] = {
            "title": "Draft Title",
            "abstract": "Draft Abstract",
            "tags": ["draft-tag"],
            "embedding": [0.3] * 384,
        }

        fake_st.text_input.side_effect = lambda label, **kw: {
            "Title": "Draft Title",
            "Tags (comma separated)": "draft-tag",
        }.get(label, kw.get("value", ""))
        fake_st.text_area.side_effect = lambda label, **kw: {
            "Abstract / TL;DR": "Draft Abstract",
        }.get(label, kw.get("value", ""))
        fake_st.selectbox.return_value = "📄 Unread"
        fake_st.form_submit_button.return_value = True

        with pytest.raises(stop_rerun):
            app.main()

        local_meta_path = tmp_path / pid / "meta.json"
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["abstract"] == "Draft Abstract"
        assert saved["embedding"] == [0.3] * 384
        assert fake_st.session_state.index.papers[pid].embedding == [0.3] * 384
        assert f"generated_{pid}" not in fake_st.session_state
        assert f"dupes_{pid}" not in fake_st.session_state

    def test_save_without_generate_leaves_embedding_and_abstract_unchanged(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Regression test: saving without ever clicking Generate must not
        touch abstract/embedding (no draft exists to pull from)."""
        pid = "b2" * 16
        self._select_paper(fake_st, mocker, tmp_path, pid)
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mocker.patch.object(app, "upload_file_to_folder")
        mocker.patch.object(app, "upload_library_index")

        fake_st.text_input.side_effect = lambda label, **kw: {
            "Title": "A Paper",
            "Tags (comma separated)": "",
        }.get(label, kw.get("value", ""))
        fake_st.text_area.return_value = ""
        fake_st.selectbox.return_value = "📄 Unread"
        fake_st.form_submit_button.return_value = True

        with pytest.raises(stop_rerun):
            app.main()

        local_meta_path = tmp_path / pid / "meta.json"
        saved = json.loads(local_meta_path.read_text(encoding="utf-8"))
        assert saved["abstract"] == ""
        assert saved["embedding"] == []

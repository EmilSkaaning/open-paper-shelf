"""Unit tests for the Streamlit frontend application."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

import frontend.app as app
from backend.models import LibraryIndex, PaperIndexEntry
from tests.frontend.conftest import make_uploaded_file


class TestSyncLibraryIndex:
    """Test suite for sync_library_index."""

    def test_no_remote_file(self, fake_st: MagicMock, mocker: MockerFixture) -> None:
        """Test an empty index is used when no remote index file exists yet."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.local_index_path = None
        mocker.patch.object(app, "get_library_index_file", return_value=None)

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
            app,
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
            app,
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
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(app, "download_file")

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
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(app, "download_file")

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
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mocker.patch.object(app, "download_file", side_effect=fake_download)

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
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        )
        mock_download = mocker.patch.object(app, "download_file")
        mock_read_text = mocker.patch.object(
            Path, "read_text", return_value=json.dumps(valid_data)
        )
        mocker.patch.object(Path, "exists", return_value=True)

        app.sync_library_index(creds=MagicMock())

        mock_download.assert_not_called()
        mock_read_text.assert_called_once_with(encoding="utf-8")
        assert fake_st.session_state.index == LibraryIndex(**valid_data)
        fake_st.error.assert_not_called()


class TestUploadPapers:
    """Test suite for upload_papers."""

    def test_all_succeed(self, fake_st: MagicMock, mocker: MockerFixture) -> None:
        """Test every uploaded file is stored and added to the index."""
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.index = LibraryIndex()
        files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]
        mocker.patch.object(
            app, "create_paper_folder", side_effect=["folder1", "folder2"]
        )
        mocker.patch.object(
            app,
            "upload_file_to_folder",
            side_effect=["pdf1", "meta1", "pdf2", "meta2"],
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is True
        assert len(fake_st.session_state.index.papers) == 2
        fake_st.error.assert_not_called()

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
            app, "create_paper_folder", side_effect=["folder1", RuntimeError("boom")]
        )
        mocker.patch.object(app, "upload_file_to_folder", side_effect=["pdf1", "meta1"])

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
        mocker.patch.object(app, "create_paper_folder")
        mocker.patch.object(app, "upload_file_to_folder")

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
        mocker.patch.object(app, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            app, "upload_file_to_folder", side_effect=RuntimeError("boom")
        )
        mock_delete = mocker.patch.object(app, "delete_paper_folder")

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
        mocker.patch.object(app, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            app, "upload_file_to_folder", side_effect=["pdf1", RuntimeError("boom")]
        )
        mock_delete = mocker.patch.object(app, "delete_paper_folder")

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
            app, "create_paper_folder", side_effect=RuntimeError("boom")
        )
        mock_delete = mocker.patch.object(app, "delete_paper_folder")

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
        mocker.patch.object(app, "create_paper_folder", return_value="folder1")
        mocker.patch.object(
            app, "upload_file_to_folder", side_effect=RuntimeError("boom")
        )
        mocker.patch.object(
            app, "delete_paper_folder", side_effect=RuntimeError("cleanup failed")
        )

        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

        assert result is False
        assert len(fake_st.session_state.index.papers) == 0
        assert fake_st.error.call_count == 2
        assert "cleanup failed" in fake_st.error.call_args_list[0][0][0]
        assert "boom" in fake_st.error.call_args_list[1][0][0]


class TestSyncPaperMetadata:
    """Test suite for sync_paper_metadata."""

    def test_download_succeeds(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a successful download is reported as safe to edit."""
        local_meta_path = tmp_path / "meta.json"
        paper_info = MagicMock(meta_file_id="meta1")
        mock_download = mocker.patch.object(app, "download_file")

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
            app, "download_file", side_effect=RuntimeError("network blip")
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
            app, "load_credentials_from_file", return_value=cached_creds
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
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)
        mock_save_creds = mocker.patch.object(app, "save_credentials")

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
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)

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
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)

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
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)

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
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)
        mocker.patch.object(app, "get_oauth_flow", return_value=mock_flow)
        mock_add_flow = mocker.patch.object(app, "add_oauth_flow")

        result = app.authenticate_user()

        assert result is None
        assert fake_st.session_state.auth_flow is mock_flow
        assert fake_st.session_state.auth_url == "https://accounts.google.com/auth"
        assert fake_st.session_state.oauth_state == "new-state"
        mock_add_flow.assert_called_once_with("new-state", mock_flow)
        fake_st.link_button.assert_called_once_with(
            "Login with Google", "https://accounts.google.com/auth"
        )

    def test_missing_credentials_file_is_reported(
        self, fake_st: MagicMock, mocker: MockerFixture
    ) -> None:
        """Test a missing credentials.json surfaces a clear error instead of crashing."""
        mocker.patch.object(app, "load_credentials_from_file", return_value=None)
        mocker.patch.object(app, "get_oauth_flow", side_effect=FileNotFoundError())

        result = app.authenticate_user()

        assert result is None
        fake_st.error.assert_called_once()
        assert "credentials.json" in fake_st.error.call_args[0][0]


class TestInitLibraryState:
    """Test suite for init_library_state."""

    def test_sets_session_state_and_creates_local_dir(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test session state is reset and the library's local dir is created."""
        mocker.patch.object(app, "PAPERS_DIR", tmp_path)

        app.init_library_state(MagicMock(), "lib_123", "papers_123")

        assert fake_st.session_state.current_lib_id == "lib_123"
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
        """Test clicking "Open Library" opens the selected library and reruns."""
        fake_st.session_state.root_id = "root_123"
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        fake_st.button.side_effect = lambda label, **kw: label == "Open Library"
        fake_st.selectbox.return_value = "lib1"
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(
            app, "list_libraries", return_value=[{"id": "lib1", "name": "Lib One"}]
        )
        mocker.patch.object(app, "get_papers_folder", return_value="papers_1")
        mock_init = mocker.patch.object(app, "init_library_state")

        with pytest.raises(stop_rerun):
            app.main()

        mock_init.assert_called_once_with(mocker.ANY, "lib1", "papers_1")

    def test_creates_new_library(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test clicking "Create Library" creates and opens a new library."""
        fake_st.session_state.root_id = "root_123"
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        fake_st.button.side_effect = lambda label, **kw: label == "Create Library"
        fake_st.text_input.return_value = "My New Lib"
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "list_libraries", return_value=[])
        mocker.patch.object(
            app,
            "create_library",
            return_value={"lib_id": "new_lib", "papers_id": "new_papers"},
        )
        mock_init = mocker.patch.object(app, "init_library_state")
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        with pytest.raises(stop_rerun):
            app.main()

        mock_init.assert_called_once_with(mocker.ANY, "new_lib", "new_papers")
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
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "get_or_create_root_folder", return_value="root_new")
        mocker.patch.object(app, "list_libraries", return_value=[])

        app.main()

        assert fake_st.session_state.root_id == "root_new"
        fake_st.rerun.assert_not_called()


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
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex()
        fake_st.session_state.selected_paper = None
        fake_st.session_state.last_sync_time = "t1"
        fake_st.file_uploader.return_value = None

        def button_side_effect(label: str, *args: Any, **kwargs: Any) -> bool:
            if label == "🔙 Switch Library":
                kwargs["on_click"]()
                raise stop_rerun
            return False

        fake_st.button.side_effect = button_side_effect
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert "current_lib_id" not in fake_st.session_state
        assert "current_papers_id" not in fake_st.session_state
        assert "index" not in fake_st.session_state
        assert "last_sync_time" not in fake_st.session_state

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
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == f"btn_{pid}"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())

        with pytest.raises(stop_rerun):
            app.main()

        assert fake_st.session_state.selected_paper == pid

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


class TestMainUploadFlow:
    """Test suite for main()'s sidebar upload flow."""

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


class TestMainDeleteFlow:
    """Test suite for main()'s sidebar paper delete flow."""

    def test_delete_button_removes_paper_and_reruns(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        stop_rerun: type[BaseException],
    ) -> None:
        """Test deleting a paper removes it from Drive, the index, and disk."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Doomed Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = pid
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.file_uploader.return_value = None
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == f"del_{pid}"
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mock_delete_folder = mocker.patch.object(app, "delete_paper_folder")
        mock_upload_index = mocker.patch.object(app, "upload_library_index")

        with pytest.raises(stop_rerun):
            app.main()

        mock_delete_folder.assert_called_once_with(mocker.ANY, "folder1")
        assert pid not in fake_st.session_state.index.papers
        mock_upload_index.assert_called_once()
        assert fake_st.session_state.selected_paper is None

    def test_delete_button_restores_paper_when_index_upload_fails(
        self,
        fake_st: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Regression test: if uploading the updated index fails after the
        Drive folder was already deleted, the paper must be restored in the
        local index rather than left popped. Leaving it popped would make
        the next full sync merge it back in from the (unchanged) remote
        index as a broken entry pointing at a folder that no longer
        exists."""
        pid = "a" * 32
        entry = PaperIndexEntry(
            title="Doomed Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        fake_st.session_state.current_lib_id = "lib_123"
        fake_st.session_state.current_papers_id = "papers_123"
        fake_st.session_state.root_id = "root_123"
        fake_st.session_state.index = LibraryIndex(papers={pid: entry})
        fake_st.session_state.selected_paper = pid
        fake_st.session_state.local_lib_dir = tmp_path
        fake_st.file_uploader.return_value = None
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        fake_st.button.side_effect = lambda label, **kw: kw.get("key") == f"del_{pid}"
        fake_st.form_submit_button.return_value = False
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "download_file")
        mocker.patch.object(app, "sync_paper_metadata", return_value=True)
        mock_delete_folder = mocker.patch.object(app, "delete_paper_folder")
        mocker.patch.object(
            app, "upload_library_index", side_effect=RuntimeError("network blip")
        )

        app.main()

        mock_delete_folder.assert_called_once_with(mocker.ANY, "folder1")
        assert fake_st.session_state.index.papers[pid] == entry
        assert fake_st.session_state.selected_paper == pid
        fake_st.error.assert_called_once()
        fake_st.rerun.assert_not_called()


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
        fake_st.columns.return_value = (MagicMock(), MagicMock())
        mocker.patch.object(app, "st_keyup", return_value="")
        mocker.patch.object(app, "authenticate_user", return_value=MagicMock())
        mocker.patch.object(app, "download_file")
        return entry

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

    def test_form_submit_saves_and_uploads_metadata(
        self, fake_st: MagicMock, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test submitting the metadata form saves it locally, uploads it to
        Drive, and updates the index entry's title if it changed."""
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
        fake_st.selectbox.return_value = "Read"
        fake_st.text_area.return_value = "Some notes"
        fake_st.form_submit_button.return_value = True

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

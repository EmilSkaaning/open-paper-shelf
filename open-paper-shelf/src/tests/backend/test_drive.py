"""Unit tests for the Google Drive integration module."""

import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from pytest_mock import MockerFixture

from backend.drive import (
    _escape_drive_query_value,
    _get_or_create_folder,
    add_oauth_flow,
    create_library,
    create_paper_folder,
    delete_paper_folder,
    download_file,
    get_library_index_file,
    get_or_create_root_folder,
    get_oauth_flow,
    get_papers_folder,
    list_libraries,
    load_credentials_from_file,
    save_credentials,
    upload_file_to_folder,
    upload_library_index,
    OAUTH_FLOWS,
    REDIRECT_URI,
    SCOPES,
    TOKEN_PATH,
)
from backend.models import LibraryIndex


class TestAddOauthFlow:
    """Test suite for add_oauth_flow."""

    def test_eviction(self, mocker: MockerFixture) -> None:
        """Test cache eviction of the oldest flow once capacity is reached."""
        OAUTH_FLOWS.clear()
        for i in range(100):
            add_oauth_flow(f"state_{i}", mocker.MagicMock())

        assert len(OAUTH_FLOWS) == 100
        assert "state_0" in OAUTH_FLOWS

        add_oauth_flow("state_100", mocker.MagicMock())
        assert len(OAUTH_FLOWS) == 100
        assert "state_0" not in OAUTH_FLOWS
        assert "state_100" in OAUTH_FLOWS

    def test_eviction_survives_dict_emptied_concurrently(
        self, mocker: MockerFixture
    ) -> None:
        """Regression test: if another thread clears/evicts the cache down to
        empty between the capacity check and the eviction pop, add_oauth_flow
        must not raise StopIteration."""
        OAUTH_FLOWS.clear()
        for i in range(100):
            add_oauth_flow(f"state_{i}", mocker.MagicMock())
        mocker.patch("backend.drive.next", side_effect=StopIteration)

        add_oauth_flow("state_new", mocker.MagicMock())

        assert "state_new" in OAUTH_FLOWS

    def test_eviction_survives_concurrent_double_pop(
        self, mocker: MockerFixture
    ) -> None:
        """Regression test: if two threads both decide to evict the same
        oldest key at once, the loser's pop() of an already-removed key must
        not raise KeyError."""
        OAUTH_FLOWS.clear()
        for i in range(100):
            add_oauth_flow(f"state_{i}", mocker.MagicMock())
        victim_key = next(iter(OAUTH_FLOWS))

        def racy_next(_iterator: Any) -> str:
            """Simulates a second thread evicting victim_key first."""
            OAUTH_FLOWS.pop(victim_key)
            return victim_key

        mocker.patch("backend.drive.next", side_effect=racy_next)

        add_oauth_flow("state_new", mocker.MagicMock())

        assert "state_new" in OAUTH_FLOWS
        assert victim_key not in OAUTH_FLOWS
        assert len(OAUTH_FLOWS) == 100


class TestEscapeDriveQueryValue:
    """Test suite for _escape_drive_query_value."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("simple", "simple"),
            ("O'Brien", "O\\'Brien"),
            ("back\\slash", "back\\\\slash"),
            ("trailing\\", "trailing\\\\"),
            ("quote'then\\backslash", "quote\\'then\\\\backslash"),
        ],
        ids=[
            "no_special_chars",
            "single_quote",
            "embedded_backslash",
            "trailing_backslash",
            "quote_and_backslash",
        ],
    )
    def test_escapes_backslashes_before_quotes(self, raw: str, expected: str) -> None:
        """Test backslashes are escaped before quotes, in that order.

        Args:
            raw: The unescaped input string.
            expected: The expected escaped output.
        """
        assert _escape_drive_query_value(raw) == expected


class TestGetOrCreateFolderEscaping:
    """Test suite for _get_or_create_folder's query escaping."""

    def test_trailing_backslash_does_not_unterminate_query(
        self, mocker: MockerFixture
    ) -> None:
        """Regression test: a folder name ending in a backslash must not
        leave the closing quote of the `name = '...'` clause escaped away,
        which would produce a malformed Drive API query."""
        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {"files": [{"id": "f1"}]}

        _get_or_create_folder(mock_service, "weird_name\\")

        query = mock_service.files().list.call_args.kwargs["q"]
        assert query.startswith("name = 'weird_name\\\\' and")


class TestGetOauthFlow:
    """Test suite for get_oauth_flow."""

    def test_uses_resolved_client_config(self, mocker: MockerFixture) -> None:
        """Test a Flow is built from the resolved OAuth client config."""
        sentinel_config = {"installed": {"client_id": "sentinel"}}
        mocker.patch("backend.drive.get_client_config", return_value=sentinel_config)
        mock_from_config = mocker.patch("backend.drive.Flow.from_client_config")

        get_oauth_flow()

        mock_from_config.assert_called_once_with(
            sentinel_config, scopes=SCOPES, redirect_uri=REDIRECT_URI
        )


class TestLoadCredentialsFromFile:
    """Test suite for load_credentials_from_file."""

    def test_no_token_file(self, mocker: MockerFixture) -> None:
        """Test returns None when token.json does not exist."""
        mocker.patch("backend.drive.Path.exists", return_value=False)

        assert load_credentials_from_file() is None

    @pytest.mark.parametrize(
        "is_valid, is_expired, has_refresh, expected_refresh_called",
        [
            (True, False, False, False),
            (False, True, True, True),
            (False, True, False, False),
        ],
        ids=["valid", "expired_with_refresh", "expired_no_refresh"],
    )
    def test_credential_states(
        self,
        mocker: MockerFixture,
        is_valid: bool,
        is_expired: bool,
        has_refresh: bool,
        expected_refresh_called: bool,
    ) -> None:
        """Test loading credentials in each valid/expired/refreshable state.

        Args:
            mocker (MockerFixture): The pytest-mock fixture.
            is_valid: Whether the loaded credentials report as valid.
            is_expired: Whether the loaded credentials report as expired.
            has_refresh: Whether the loaded credentials carry a refresh token.
            expected_refresh_called: Whether a refresh + re-save is expected.
        """
        mocker.patch("backend.drive.Path.exists", return_value=True)

        mock_creds = mocker.MagicMock(spec=Credentials)
        mock_creds.valid = is_valid
        mock_creds.expired = is_expired
        mock_creds.refresh_token = "dummy_token" if has_refresh else None
        mock_creds.to_json.return_value = '{"token": "new"}'

        mocker.patch(
            "backend.drive.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        )
        mock_open = mocker.patch("builtins.open", mocker.mock_open())

        result = load_credentials_from_file()

        if expected_refresh_called:
            mock_creds.refresh.assert_called_once()
            mock_open.assert_called_once_with(TOKEN_PATH, "w")
            assert result is mock_creds
        elif not is_valid:
            assert result is None
        else:
            assert result is mock_creds


class TestSaveCredentials:
    """Test suite for save_credentials."""

    def test_saves_to_file(self, mocker: MockerFixture) -> None:
        """Test credentials are serialized and written to token.json."""
        mock_creds = mocker.MagicMock(spec=Credentials)
        mock_creds.to_json.return_value = '{"dummy": "data"}'
        mock_open = mocker.patch("builtins.open", mocker.mock_open())

        save_credentials(mock_creds)

        mock_open.assert_called_once_with(TOKEN_PATH, "w")
        mock_open().write.assert_called_once_with('{"dummy": "data"}')


class TestFolderLookups:
    """Test suite for the single-folder lookup/creation helpers."""

    @pytest.mark.parametrize(
        "func, extra_args, expected_id",
        [
            (get_or_create_root_folder, (), "root_123"),
            (get_papers_folder, ("lib_123",), "papers_123"),
            (create_paper_folder, ("papers_123", "p_uuid"), "p_folder"),
        ],
        ids=["root_folder", "papers_folder", "paper_folder"],
    )
    def test_returns_existing_folder_id(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        func: Callable[..., str],
        extra_args: tuple[Any, ...],
        expected_id: str,
    ) -> None:
        """Test each helper returns the id of a folder that already exists.

        Args:
            mock_build: Mock replacing backend.drive.build.
            mock_creds: Mock Google OAuth credentials.
            func: The drive.py function under test.
            extra_args: Positional args to pass after creds.
            expected_id: The folder id the mocked Drive API returns.
        """
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {
            "files": [{"id": expected_id}]
        }

        assert func(mock_creds, *extra_args) == expected_id


class TestListLibraries:
    """Test suite for list_libraries."""

    def test_returns_library_folders(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test the library folders under root_id are returned."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "lib1", "name": "My Lib"}]
        }

        libs = list_libraries(mock_creds, "root_123")

        assert len(libs) == 1
        assert libs[0]["id"] == "lib1"

    def test_paginates_beyond_first_page(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Regression test: a user with more than one page of library
        folders must have every page fetched via pageToken, not just the
        first. Without pagination, libraries beyond the first page would be
        silently dropped from the returned list."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_list = mock_service.files.return_value.list
        mock_list.return_value.execute.side_effect = [
            {"files": [{"id": "lib1", "name": "Lib 1"}], "nextPageToken": "page2"},
            {"files": [{"id": "lib2", "name": "Lib 2"}]},
        ]

        libs = list_libraries(mock_creds, "root_123")

        assert [lib["id"] for lib in libs] == ["lib1", "lib2"]
        assert mock_list.call_args_list[0].kwargs["pageToken"] is None
        assert mock_list.call_args_list[1].kwargs["pageToken"] == "page2"


class TestCreateLibrary:
    """Test suite for create_library."""

    def test_creates_library_and_papers_folder(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test a library folder and its nested papers folder are created."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.side_effect = [
            {"id": "new_lib"},
            {"id": "new_papers"},
        ]

        res = create_library(mock_creds, "root_123", "TestLib")

        assert res["lib_id"] == "new_lib"
        assert res["papers_id"] == "new_papers"
        assert "TestLib" in res["lib_name"]


class TestGetLibraryIndexFile:
    """Test suite for get_library_index_file."""

    @pytest.mark.parametrize(
        "files, expected",
        [
            (
                [{"id": "idx", "modifiedTime": "time"}],
                {"id": "idx", "modifiedTime": "time"},
            ),
            ([], None),
        ],
        ids=["found", "not_found"],
    )
    def test_get_library_index_file(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        files: list[dict[str, str]],
        expected: dict[str, str] | None,
    ) -> None:
        """Test the index file metadata is returned, or None if absent."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": files}

        assert get_library_index_file(mock_creds, "papers_123") == expected


class TestUploadLibraryIndex:
    """Test suite for upload_library_index."""

    def test_creates_new_index_file_when_none_exists(
        self, mock_build: MagicMock, mock_creds: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: with no existing id-mapping.json, upload must
        create rather than update the remote file."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": []}
        mocker.patch("backend.drive.MediaFileUpload")

        upload_library_index(mock_creds, "papers_123", LibraryIndex())

        mock_service.files().create.assert_called()
        mock_service.files().update.assert_not_called()

    @pytest.mark.parametrize(
        "remote_bytes",
        [b'{"papers": {}}', b"not valid json"],
        ids=["valid_remote_index", "corrupted_remote_index_is_skipped"],
    )
    def test_updates_existing_index_file(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        mocker: MockerFixture,
        remote_bytes: bytes,
    ) -> None:
        """Regression test: a malformed remote id-mapping.json must not crash
        the upload - the local index should still be written and uploaded."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": [{"id": "idx"}]}
        mock_service.files().get_media().execute.return_value = remote_bytes
        mocker.patch("backend.drive.MediaFileUpload")

        upload_library_index(mock_creds, "papers_123", LibraryIndex())

        mock_service.files().update.assert_called_once()

    def test_reraises_non_404_http_error(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test a non-404 error fetching the remote index is re-raised."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": [{"id": "idx"}]}
        resp = MagicMock(status=500)
        mock_service.files().get_media().execute.side_effect = HttpError(
            resp, b"server error"
        )

        with pytest.raises(HttpError):
            upload_library_index(mock_creds, "papers_123", LibraryIndex())

    def test_merges_remote_papers_not_present_locally(
        self, mock_build: MagicMock, mock_creds: MagicMock, mocker: MockerFixture
    ) -> None:
        """Regression test: a paper only present in the remote index (e.g.
        uploaded from another device) must be merged back into the local
        index, unless it was explicitly deleted locally."""
        remote_entry = {
            "title": "Remote Paper",
            "pdf_file_id": "pdf1",
            "meta_file_id": "meta1",
            "folder_id": "folder1",
        }
        remote_data = {
            "papers": {"remote_pid": remote_entry, "deleted_pid": remote_entry}
        }
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": [{"id": "idx"}]}
        mock_service.files().get_media().execute.return_value = json.dumps(
            remote_data
        ).encode("utf-8")
        mocker.patch("backend.drive.MediaFileUpload")

        index = LibraryIndex()
        upload_library_index(
            mock_creds, "papers_123", index, deleted_pids={"deleted_pid"}
        )

        assert "remote_pid" in index.papers
        assert "deleted_pid" not in index.papers


class TestUploadFileToFolder:
    """Test suite for upload_file_to_folder."""

    @pytest.mark.parametrize(
        "existing_files, expected_id",
        [
            ([], "uploaded_123"),
            ([{"id": "existing_123"}], "existing_123"),
        ],
        ids=["creates_new_file", "updates_existing_file"],
    )
    def test_upload_file_to_folder(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
        existing_files: list[dict[str, str]],
        expected_id: str,
    ) -> None:
        """Test uploading creates a new file or updates an existing one.

        Args:
            mock_build: Mock replacing backend.drive.build.
            mock_creds: Mock Google OAuth credentials.
            mocker: The pytest-mock fixture.
            tmp_path: Pytest's per-test temporary directory.
            existing_files: The Drive API's mocked list() response.
            expected_id: The file id expected to be returned.
        """
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": existing_files}
        mock_service.files().create().execute.return_value = {"id": "uploaded_123"}

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"content")

        mocker.patch("backend.drive.MediaFileUpload")
        res = upload_file_to_folder(
            mock_creds, "folder_123", file_path, "paper.pdf", "application/pdf"
        )

        assert res == expected_id

    def test_escapes_filename_with_backslash_and_quote(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Regression test: a filename containing a backslash and a quote
        must have both escaped, in that order, so the lookup query isn't
        malformed."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "uploaded_123"}

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"content")

        mocker.patch("backend.drive.MediaFileUpload")
        upload_file_to_folder(
            mock_creds, "folder_123", file_path, "weird\\name's.pdf", "application/pdf"
        )

        query = mock_service.files().list.call_args.kwargs["q"]
        assert "weird\\\\name\\'s.pdf" in query


class TestDownloadFile:
    """Test suite for download_file."""

    def test_download_file_success(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Test a successful download is placed at the destination path."""
        mock_build.return_value = MagicMock()
        dest_path = tmp_path / "paper.pdf"

        mock_downloader = mocker.patch("backend.drive.MediaIoBaseDownload")
        mock_downloader.return_value.next_chunk.return_value = (None, True)

        download_file(mock_creds, "file_123", dest_path)

        assert dest_path.exists()

    def test_cleans_up_temp_file_on_failure(
        self,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Regression test: a failed download must not leave a stray temp
        file next to the destination path."""
        mock_build.return_value = MagicMock()
        dest_path = tmp_path / "paper.pdf"

        mock_downloader = mocker.patch("backend.drive.MediaIoBaseDownload")
        mock_downloader.return_value.next_chunk.side_effect = RuntimeError(
            "network blip"
        )

        with pytest.raises(RuntimeError):
            download_file(mock_creds, "file_123", dest_path)

        assert not dest_path.exists()
        assert list(tmp_path.iterdir()) == []


class TestDeletePaperFolder:
    """Test suite for delete_paper_folder."""

    def test_deletes_folder_by_id(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test the paper's folder is deleted by its Drive file id."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        delete_paper_folder(mock_creds, "folder_123")

        mock_service.files().delete.assert_called_once_with(fileId="folder_123")

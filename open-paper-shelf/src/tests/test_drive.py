"""Tests for the Google Drive integration module."""

from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture
from google.oauth2.credentials import Credentials

from backend.drive import (
    get_oauth_flow,
    load_credentials_from_file,
    save_credentials,
    get_or_create_library_folder,
    SCOPES,
    REDIRECT_URI,
    FOLDER_NAME,
    FOLDER_MIME_TYPE,
    CREDENTIALS_PATH,
    TOKEN_PATH,
)


class TestAddOauthFlow:
    """Test suite for add_oauth_flow."""

    def test_eviction(self, mocker: MockerFixture) -> None:
        """Test cache eviction when capacity is reached."""
        from backend.drive import add_oauth_flow, OAUTH_FLOWS

        # Clear existing flows
        OAUTH_FLOWS.clear()

        # Add max flows
        for i in range(100):
            add_oauth_flow(f"state_{i}", mocker.MagicMock())

        assert len(OAUTH_FLOWS) == 100
        assert "state_0" in OAUTH_FLOWS

        # Add one more, should evict state_0
        add_oauth_flow("state_100", mocker.MagicMock())
        assert len(OAUTH_FLOWS) == 100
        assert "state_0" not in OAUTH_FLOWS
        assert "state_100" in OAUTH_FLOWS


class TestGetOauthFlow:
    """Test suite for get_oauth_flow."""

    def test_missing_credentials_file(self, mocker: MockerFixture) -> None:
        """Test FileNotFoundError is raised when credentials.json is missing."""
        mocker.patch("backend.drive.Path.exists", return_value=False)

        with pytest.raises(FileNotFoundError, match="credentials.json not found"):
            get_oauth_flow()

    def test_flow_creation_success(self, mocker: MockerFixture) -> None:
        """Test Flow is created correctly when credentials exist."""
        mocker.patch("backend.drive.Path.exists", return_value=True)
        mock_from_secrets = mocker.patch("backend.drive.Flow.from_client_secrets_file")

        get_oauth_flow()

        mock_from_secrets.assert_called_once_with(
            str(CREDENTIALS_PATH), scopes=SCOPES, redirect_uri=REDIRECT_URI
        )


class TestLoadCredentials:
    """Test suite for load_credentials_from_file."""

    def test_no_token_file(self, mocker: MockerFixture) -> None:
        """Test returns None when token.json does not exist."""
        mocker.patch("backend.drive.Path.exists", return_value=False)

        assert load_credentials_from_file() is None

    @pytest.mark.parametrize(
        "is_valid, is_expired, has_refresh, expected_refresh_called",
        [
            (True, False, False, False),  # Valid credentials
            (False, True, True, True),  # Expired but can refresh
            (False, True, False, False),  # Expired and cannot refresh
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
        """Test various credential states using parametrize."""
        mocker.patch("backend.drive.Path.exists", return_value=True)

        mock_creds = mocker.MagicMock(spec=Credentials)
        mock_creds.valid = is_valid
        mock_creds.expired = is_expired
        mock_creds.refresh_token = "dummy_token" if has_refresh else None

        mocker.patch(
            "backend.drive.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        )
        mock_open = mocker.patch("builtins.open", mocker.mock_open())
        mock_creds.to_json.return_value = '{"token": "new"}'

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
        """Test credentials are saved to token.json."""
        mock_creds = mocker.MagicMock(spec=Credentials)
        mock_creds.to_json.return_value = '{"dummy": "data"}'
        mock_open = mocker.patch("builtins.open", mocker.mock_open())

        save_credentials(mock_creds)

        mock_open.assert_called_once_with(TOKEN_PATH, "w")
        mock_open().write.assert_called_once_with('{"dummy": "data"}')


class TestGetOrCreateLibraryFolder:
    """Test suite for get_or_create_library_folder."""

    @pytest.fixture
    def mock_build(self, mocker: MockerFixture) -> MagicMock:
        """Fixture to mock the googleapiclient.discovery.build function."""
        return mocker.patch("backend.drive.build")

    def test_folder_exists(self, mocker: MockerFixture, mock_build: MagicMock) -> None:
        """Test returns existing folder ID when it is found."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "existing_folder_id", "name": FOLDER_NAME}]
        }
        mock_creds = mocker.MagicMock(spec=Credentials)

        folder_id = get_or_create_library_folder(mock_creds)

        assert folder_id == "existing_folder_id"
        mock_service.files.return_value.create.assert_not_called()

    def test_folder_created(self, mocker: MockerFixture, mock_build: MagicMock) -> None:
        """Test creates new folder and returns ID when not found."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": []
        }
        mock_create_request = mocker.MagicMock()
        mock_create_request.execute.return_value = {"id": "new_folder_id"}
        mock_service.files.return_value.create.return_value = mock_create_request
        mock_creds = mocker.MagicMock(spec=Credentials)

        folder_id = get_or_create_library_folder(mock_creds)

        assert folder_id == "new_folder_id"
        mock_service.files.return_value.create.assert_called_once()
        create_kwargs = mock_service.files.return_value.create.call_args[1]
        assert create_kwargs["body"] == {
            "name": FOLDER_NAME,
            "mimeType": FOLDER_MIME_TYPE,
        }


class TestListPdfsInLibrary:
    """Test suite for list_pdfs_in_library."""

    @pytest.fixture
    def mock_build(self, mocker: MockerFixture) -> MagicMock:
        return mocker.patch("backend.drive.build")

    def test_pagination(self, mocker: MockerFixture, mock_build: MagicMock) -> None:
        """Test handles pagination correctly."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        # Setup mock to return two pages of results
        mock_list = mock_service.files.return_value.list.return_value
        mock_list.execute.side_effect = [
            {"files": [{"id": "1", "name": "f1"}], "nextPageToken": "token1"},
            {"files": [{"id": "2", "name": "f2"}]},
        ]

        from backend.drive import list_pdfs_in_library

        mock_creds = mocker.MagicMock(spec=Credentials)

        results = list_pdfs_in_library(mock_creds, "folder1")

        assert len(results) == 2
        assert results[0]["id"] == "1"
        assert results[1]["id"] == "2"
        assert mock_service.files.return_value.list.call_count == 2


class TestDownloadPdf:
    """Test suite for download_pdf."""

    @pytest.fixture
    def mock_build(self, mocker: MockerFixture) -> MagicMock:
        return mocker.patch("backend.drive.build")

    def test_download_success(
        self, mocker: MockerFixture, mock_build: MagicMock
    ) -> None:
        """Test file download flows correctly."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        mock_request = mocker.MagicMock()
        mock_service.files.return_value.get_media.return_value = mock_request

        mock_downloader = mocker.patch("backend.drive.MediaIoBaseDownload")
        mock_downloader_instance = mocker.MagicMock()
        mock_downloader.return_value = mock_downloader_instance
        # Simulate download completing in 2 chunks
        mock_downloader_instance.next_chunk.side_effect = [(None, False), (None, True)]

        mock_named_temp = mocker.patch("backend.drive.tempfile.NamedTemporaryFile")
        mock_temp_instance = mocker.MagicMock()
        mock_named_temp.return_value.__enter__.return_value = mock_temp_instance
        mock_temp_instance.name = "/tmp/dummy.tmp"

        mock_tmp_path = mocker.MagicMock()

        def mock_path_constructor(name):
            return mock_tmp_path

        mocker.patch("backend.drive.Path", side_effect=mock_path_constructor)

        from backend.drive import download_pdf
        from pathlib import Path

        mock_creds = mocker.MagicMock(spec=Credentials)
        dest_path = Path("/dummy/dest.pdf")

        download_pdf(mock_creds, "file1", dest_path)

        mock_service.files.return_value.get_media.assert_called_once_with(
            fileId="file1"
        )
        mock_downloader.assert_called_once_with(mock_temp_instance, mock_request)
        assert mock_downloader_instance.next_chunk.call_count == 2
        mock_tmp_path.rename.assert_called_once_with(dest_path)


class TestDeletePdf:
    """Test suite for delete_pdf."""

    @pytest.fixture
    def mock_build(self, mocker: MockerFixture) -> MagicMock:
        return mocker.patch("backend.drive.build")

    def test_delete_success(self, mocker: MockerFixture, mock_build: MagicMock) -> None:
        """Test API delete call is invoked."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        from backend.drive import delete_pdf

        mock_creds = mocker.MagicMock(spec=Credentials)

        delete_pdf(mock_creds, "file1")

        mock_service.files.return_value.delete.assert_called_once_with(fileId="file1")
        mock_service.files.return_value.delete.return_value.execute.assert_called_once()


class TestUploadPdf:
    """Test suite for upload_pdf."""

    @pytest.fixture
    def mock_build(self, mocker: MockerFixture) -> MagicMock:
        return mocker.patch("backend.drive.build")

    def test_upload_without_display_name(
        self, mocker: MockerFixture, mock_build: MagicMock
    ) -> None:
        """Test upload uses file path name if display name not provided."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        mock_create = mock_service.files.return_value.create
        mock_create.return_value.execute.return_value = {"id": "new_file_id"}

        mock_media = mocker.patch("backend.drive.MediaFileUpload")

        from backend.drive import upload_pdf
        from pathlib import Path

        mock_creds = mocker.MagicMock(spec=Credentials)

        test_path = Path("/tmp/local_test.pdf")

        file_id = upload_pdf(mock_creds, "folder1", test_path)

        assert file_id == "new_file_id"
        mock_media.assert_called_once_with(
            str(test_path), mimetype="application/pdf", resumable=True
        )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == {"name": "local_test.pdf", "parents": ["folder1"]}

    def test_upload_with_display_name(
        self, mocker: MockerFixture, mock_build: MagicMock
    ) -> None:
        """Test upload overrides file path name with display_name."""
        mock_service = mocker.MagicMock()
        mock_build.return_value = mock_service

        mock_create = mock_service.files.return_value.create
        mock_create.return_value.execute.return_value = {"id": "new_file_id"}

        mocker.patch("backend.drive.MediaFileUpload")

        from backend.drive import upload_pdf
        from pathlib import Path

        mock_creds = mocker.MagicMock(spec=Credentials)

        test_path = Path("/tmp/local_test.pdf")

        file_id = upload_pdf(
            mock_creds, "folder1", test_path, display_name="clean_name.pdf"
        )

        assert file_id == "new_file_id"

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == {"name": "clean_name.pdf", "parents": ["folder1"]}

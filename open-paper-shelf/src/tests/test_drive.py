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

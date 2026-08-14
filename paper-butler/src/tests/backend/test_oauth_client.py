"""Unit tests for OAuth client config resolution."""

from pytest_mock import MockerFixture

from backend.oauth_client import (
    _BUNDLED_CLIENT_CONFIG,
    get_client_config,
)


class TestGetClientConfig:
    """Test suite for get_client_config."""

    def test_falls_back_to_bundled_config(self, mocker: MockerFixture) -> None:
        """Test the bundled config is used when no override is present."""
        mocker.patch.dict("os.environ", {}, clear=True)
        mocker.patch("backend.oauth_client.Path.exists", return_value=False)

        assert get_client_config() == _BUNDLED_CLIENT_CONFIG

    def test_env_override_takes_priority(self, mocker: MockerFixture) -> None:
        """Test env var override is preferred over the bundled config."""
        mocker.patch.dict(
            "os.environ",
            {
                "GOOGLE_OAUTH_CLIENT_ID": "env-client-id",
                "GOOGLE_OAUTH_CLIENT_SECRET": "env-client-secret",
            },
            clear=True,
        )

        config = get_client_config()

        assert config["installed"]["client_id"] == "env-client-id"
        assert config["installed"]["client_secret"] == "env-client-secret"

    def test_partial_env_override_is_ignored(self, mocker: MockerFixture) -> None:
        """Test a client_id set without its matching secret is not used."""
        mocker.patch.dict(
            "os.environ", {"GOOGLE_OAUTH_CLIENT_ID": "env-client-id"}, clear=True
        )
        mocker.patch("backend.oauth_client.Path.exists", return_value=False)

        assert get_client_config() == _BUNDLED_CLIENT_CONFIG

    def test_file_override_used_when_no_env_vars(self, mocker: MockerFixture) -> None:
        """Test a local credentials.json override is used over the bundled config."""
        mocker.patch.dict("os.environ", {}, clear=True)
        file_config = {"installed": {"client_id": "file-client-id"}}
        mocker.patch("backend.oauth_client.Path.exists", return_value=True)
        mocker.patch("backend.oauth_client.json.load", return_value=file_config)
        mocker.patch("builtins.open", mocker.mock_open())

        assert get_client_config() == file_config

"""Unit tests for the non-loopback host warning check."""

import pytest
from pytest_mock import MockerFixture

from backend.host_check import get_non_loopback_host_warning


class TestGetNonLoopbackHostWarning:
    """Test suite for get_non_loopback_host_warning."""

    def test_no_warning_when_fastapi_url_unset(self, mocker: MockerFixture) -> None:
        """Test the default FASTAPI_URL (localhost) produces no warning."""
        mocker.patch.dict("os.environ", {}, clear=True)

        assert get_non_loopback_host_warning() is None

    @pytest.mark.parametrize(
        "loopback_url",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
        ],
    )
    def test_no_warning_for_loopback_hosts(
        self, mocker: MockerFixture, loopback_url: str
    ) -> None:
        """Test loopback FASTAPI_URL values produce no warning."""
        mocker.patch.dict("os.environ", {"FASTAPI_URL": loopback_url}, clear=True)

        assert get_non_loopback_host_warning() is None

    @pytest.mark.parametrize(
        "public_url,expected_hostname",
        [
            ("http://0.0.0.0:8000", "0.0.0.0"),
            ("http://192.168.1.42:8000", "192.168.1.42"),
            ("http://papers.example.com:8000", "papers.example.com"),
        ],
    )
    def test_warns_for_non_loopback_hosts(
        self, mocker: MockerFixture, public_url: str, expected_hostname: str
    ) -> None:
        """Test non-loopback FASTAPI_URL values produce a warning naming the host."""
        mocker.patch.dict("os.environ", {"FASTAPI_URL": public_url}, clear=True)

        warning = get_non_loopback_host_warning()

        assert warning is not None
        assert expected_hostname in warning
        assert "no authentication" in warning

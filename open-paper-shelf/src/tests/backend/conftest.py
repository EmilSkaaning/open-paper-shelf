"""Pytest configuration and fixtures for the backend tests."""

import sys
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

# Add the src directory to Python's search path dynamically
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.main import app  # noqa: E402


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Fixture providing a FastAPI TestClient for the application.

    Yields:
        TestClient: The test client instance.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_creds() -> MagicMock:
    """Provides a MagicMock standing in for Google OAuth Credentials.

    Returns:
        MagicMock: A mock credentials object.
    """
    return MagicMock()


@pytest.fixture
def mock_build(mocker: MockerFixture) -> MagicMock:
    """Mocks backend.drive.build, the Google Drive API service factory.

    Args:
        mocker (MockerFixture): The pytest-mock fixture.

    Returns:
        MagicMock: The mock replacing backend.drive.build.
    """
    return mocker.patch("backend.drive.build")

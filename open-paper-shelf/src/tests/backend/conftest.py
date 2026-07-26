"""Pytest configuration and fixtures for the backend tests."""

import sys
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

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

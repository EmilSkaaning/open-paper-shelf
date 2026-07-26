"""Tests for the main backend endpoints."""

from fastapi.testclient import TestClient


class TestMainEndpoints:
    """Test suite for the main FastAPI application endpoints."""

    def test_read_root_success(self, client: TestClient) -> None:
        """Test the root endpoint returns the expected welcome message.

        Args:
            client: The FastAPI test client fixture.
        """
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to Open Paper Shelf API"}

import sys
from pathlib import Path
from fastapi.testclient import TestClient

# Add the src directory to Python's search path dynamically
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.main import app  # noqa: E402

client = TestClient(app)


def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Open Paper Shelf API"}

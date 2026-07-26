"""Main entry point for the FastAPI backend application."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.drive import PAPERS_DIR

app = FastAPI(title="Open Paper Shelf API")

# Ensure the local papers directory exists before mounting
PAPERS_DIR.mkdir(exist_ok=True)
# WARNING: Mounted without authentication. Do not expose to public networks as-is.
app.mount("/papers", StaticFiles(directory=str(PAPERS_DIR)), name="papers")


class WelcomeResponse(BaseModel):
    """Pydantic model for the root endpoint response."""

    message: str


@app.get("/", response_model=WelcomeResponse)
def read_root() -> WelcomeResponse:
    """Handles GET requests to the root endpoint.

    Returns:
        WelcomeResponse: A welcome message wrapped in a Pydantic model.
    """
    return WelcomeResponse(message="Welcome to Open Paper Shelf API")

"""Main entry point for the FastAPI backend application."""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Open Paper Shelf API")


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

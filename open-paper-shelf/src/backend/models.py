"""Data models for the backend application."""

from typing import List, Literal

from pydantic import BaseModel, Field


class PaperMetadata(BaseModel):
    """Metadata for a paper in the library.

    Attributes:
        title: The title of the paper.
        tags: List of tag strings associated with the paper.
        notes: Personal notes or summary for the paper.
        citation: Citation text for the paper.
        status: Reading status of the paper.
    """

    title: str
    tags: List[str] = Field(default_factory=list)
    notes: str = ""
    citation: str = ""
    status: Literal["Unread", "Reading", "Read", "TODO"] = "Unread"

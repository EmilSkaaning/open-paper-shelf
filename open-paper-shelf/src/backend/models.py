"""Data models for the backend application."""

from typing import List, Literal

from pydantic import BaseModel, Field

ReadingStatus = Literal["Unread", "Reading", "Read", "TODO"]
"""A paper's reading status, shared by `PaperMetadata` and `PaperIndexEntry`."""


class PaperMetadata(BaseModel):
    """Metadata for a paper in the library.

    Attributes:
        title: The title of the paper.
        abstract: Generated or user-written abstract/TL;DR for the paper.
        tags: List of tag strings associated with the paper.
        notes: Personal notes or summary for the paper.
        citation: Citation text for the paper.
        status: Reading status of the paper.
        embedding: 384-dim sentence embedding of the paper's text, used for
            duplicate detection. An empty list means no embedding has been
            generated yet.
    """

    model_config = {"frozen": True}

    title: str
    abstract: str = ""
    tags: List[str] = Field(default_factory=list)
    notes: str = ""
    citation: str = ""
    status: ReadingStatus = "Unread"
    embedding: List[float] = Field(default_factory=list)


class PaperIndexEntry(BaseModel):
    """Entry in the library's id-mapping.json for a single paper.

    Attributes:
        title: The title of the paper.
        pdf_file_id: The Google Drive file ID of the paper's PDF.
        meta_file_id: The Google Drive file ID of the paper's metadata JSON.
        folder_id: The Google Drive folder ID containing the paper's files.
        tags: List of tag strings associated with the paper, kept in sync
            with the paper's meta.json so the sidebar can filter by tag
            without fetching every paper's metadata.
        status: Reading status of the paper, kept in sync with the paper's
            meta.json so the sidebar can filter/display by status without
            fetching every paper's metadata.
        embedding: 384-dim sentence embedding of the paper, kept in sync
            with the paper's meta.json so duplicate detection can compare
            against every paper in the library without fetching each one's
            metadata individually. An empty list means no embedding has
            been generated yet. Note this grows id-mapping.json roughly
            linearly with library size once every paper has an embedding.
    """

    model_config = {"frozen": True}

    title: str
    pdf_file_id: str
    meta_file_id: str
    folder_id: str
    tags: List[str] = Field(default_factory=list)
    status: ReadingStatus = "Unread"
    embedding: List[float] = Field(default_factory=list)


class LibraryIndex(BaseModel):
    """Represents the id-mapping.json mapping unique paper IDs to their metadata.

    Attributes:
        papers: Mapping of unique paper ID to its PaperIndexEntry.
    """

    model_config = {"frozen": True}

    papers: dict[str, PaperIndexEntry] = Field(default_factory=dict)

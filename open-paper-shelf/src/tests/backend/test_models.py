"""Unit tests for backend Pydantic models."""

from typing import Literal

import pytest
from pydantic import ValidationError

from backend.models import PaperIndexEntry, PaperMetadata


class TestPaperMetadata:
    """Test suite for the PaperMetadata Pydantic model."""

    def test_paper_metadata_defaults(self) -> None:
        """Test default values of PaperMetadata when only title is provided."""
        meta = PaperMetadata(title="Test Paper")
        assert meta.title == "Test Paper"
        assert meta.abstract == ""
        assert meta.tags == []
        assert meta.notes == ""
        assert meta.citation == ""
        assert meta.status == "Unread"
        assert meta.embedding == []

    def test_paper_metadata_custom_values(self) -> None:
        """Test PaperMetadata initialization with custom values for all fields."""
        meta = PaperMetadata(
            title="Attention Is All You Need",
            abstract="Introduces the Transformer architecture.",
            tags=["AI", "Transformer"],
            notes="Seminal paper on transformers.",
            citation="Vaswani et al., 2017",
            status="Read",
            embedding=[0.1, 0.2, 0.3],
        )
        assert meta.title == "Attention Is All You Need"
        assert meta.abstract == "Introduces the Transformer architecture."
        assert meta.tags == ["AI", "Transformer"]
        assert meta.notes == "Seminal paper on transformers."
        assert meta.citation == "Vaswani et al., 2017"
        assert meta.status == "Read"
        assert meta.embedding == [0.1, 0.2, 0.3]

    @pytest.mark.parametrize("status", ["Unread", "Reading", "Read", "TODO"])
    def test_paper_metadata_valid_statuses(
        self, status: Literal["Unread", "Reading", "Read", "TODO"]
    ) -> None:
        """Test that all allowed status values are accepted by PaperMetadata.

        Args:
            status: The valid status string to test.
        """
        meta = PaperMetadata(title="Test Paper", status=status)
        assert meta.status == status

    def test_paper_metadata_invalid_status(self) -> None:
        """Test that an invalid status raises a ValidationError."""
        with pytest.raises(ValidationError):
            PaperMetadata(title="Test Paper", status="InvalidStatus")  # type: ignore[arg-type]

    def test_paper_metadata_missing_required_title(self) -> None:
        """Test that initializing PaperMetadata without a title raises ValidationError."""
        with pytest.raises(ValidationError):
            PaperMetadata()  # type: ignore[call-arg]


class TestPaperIndexEntry:
    """Test suite for the PaperIndexEntry Pydantic model."""

    def test_defaults_when_tags_and_status_omitted(self) -> None:
        """Test tags/status/embedding default to empty/Unread for entries
        created before these fields existed (e.g. parsed from an old
        id-mapping.json)."""
        entry = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        assert entry.tags == []
        assert entry.status == "Unread"
        assert entry.embedding == []

    def test_custom_tags_and_status(self) -> None:
        """Test tags/status/embedding are stored as given when provided."""
        entry = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
            tags=["ai", "nlp"],
            status="Reading",
            embedding=[0.4, 0.5, 0.6],
        )
        assert entry.tags == ["ai", "nlp"]
        assert entry.status == "Reading"
        assert entry.embedding == [0.4, 0.5, 0.6]

    def test_invalid_status_rejected(self) -> None:
        """Test an out-of-range status raises ValidationError, matching
        PaperMetadata's status validation."""
        with pytest.raises(ValidationError):
            PaperIndexEntry(
                title="A Paper",
                pdf_file_id="pdf1",
                meta_file_id="meta1",
                folder_id="folder1",
                status="Archived",  # type: ignore[arg-type]
            )

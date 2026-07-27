"""Unit tests for backend Pydantic models."""

from typing import Literal

import pytest
from pydantic import ValidationError

from backend.models import PaperMetadata


class TestPaperMetadata:
    """Test suite for the PaperMetadata Pydantic model."""

    def test_paper_metadata_defaults(self) -> None:
        """Test default values of PaperMetadata when only title is provided."""
        meta = PaperMetadata(title="Test Paper")
        assert meta.title == "Test Paper"
        assert meta.tags == []
        assert meta.notes == ""
        assert meta.citation == ""
        assert meta.status == "Unread"

    def test_paper_metadata_custom_values(self) -> None:
        """Test PaperMetadata initialization with custom values for all fields."""
        meta = PaperMetadata(
            title="Attention Is All You Need",
            tags=["AI", "Transformer"],
            notes="Seminal paper on transformers.",
            citation="Vaswani et al., 2017",
            status="Read",
        )
        assert meta.title == "Attention Is All You Need"
        assert meta.tags == ["AI", "Transformer"]
        assert meta.notes == "Seminal paper on transformers."
        assert meta.citation == "Vaswani et al., 2017"
        assert meta.status == "Read"

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

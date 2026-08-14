"""Tests for the main backend endpoints."""

from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest_mock import MockerFixture

from backend.drive import CredentialsLoadResult
from backend.main import save_edited_pdf_route
from backend.models import LibraryIndex, PaperIndexEntry


class TestMainEndpoints:
    """Test suite for the main FastAPI application endpoints."""

    def test_read_root_success(self, client: TestClient) -> None:
        """Test the root endpoint returns the expected welcome message.

        Args:
            client: The FastAPI test client fixture.
        """
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to Paper Butler API"}


def _real_pdf_bytes() -> bytes:
    """Builds real, parseable PDF bytes via pypdf (not a mock).

    Returns:
        bytes: The serialized PDF's raw bytes.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestSaveEditedPdfRoute:
    """Test suite for POST /papers/{lib_id}/{pid}/edited."""

    def _seed_local_index(self, papers_dir: Path, lib_id: str, pid: str) -> None:
        """Writes a local id-mapping.json cache with a single paper entry.

        Args:
            papers_dir: The stand-in PAPERS_DIR root.
            lib_id: The library folder ID.
            pid: The paper ID.
        """
        entry = PaperIndexEntry(
            title="A Paper",
            pdf_file_id="pdf1",
            meta_file_id="meta1",
            folder_id="folder1",
        )
        index = LibraryIndex(papers={pid: entry})
        lib_dir = papers_dir / lib_id
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "id-mapping.json").write_text(
            index.model_dump_json(indent=2), encoding="utf-8"
        )

    def test_returns_401_when_not_authenticated(
        self, client: TestClient, mocker: MockerFixture
    ) -> None:
        """Test an unauthenticated request is rejected before touching disk."""
        mocker.patch(
            "backend.main.load_credentials_from_file",
            return_value=CredentialsLoadResult(credentials=None, revoked=False),
        )

        response = client.post(
            "/papers/" + "a" * 20 + "/" + "b" * 32 + "/edited",
            content=_real_pdf_bytes(),
            headers={"Content-Type": "application/pdf"},
        )

        assert response.status_code == 401

    def test_returns_400_for_unsafe_lib_id(
        self, client: TestClient, mocker: MockerFixture
    ) -> None:
        """Test an unsafe lib_id (containing characters outside the safe
        Drive-ID charset) is rejected with 400, not a 500 from an unguarded
        filesystem access."""
        mocker.patch(
            "backend.main.load_credentials_from_file",
            return_value=CredentialsLoadResult(credentials=MagicMock(), revoked=False),
        )

        response = client.post(
            "/papers/not a safe id/" + "b" * 32 + "/edited",
            content=_real_pdf_bytes(),
            headers={"Content-Type": "application/pdf"},
        )

        assert response.status_code == 400

    def test_returns_422_for_invalid_pdf_bytes(
        self, client: TestClient, mocker: MockerFixture
    ) -> None:
        """Test non-PDF bytes are rejected with 422."""
        mocker.patch(
            "backend.main.load_credentials_from_file",
            return_value=CredentialsLoadResult(credentials=MagicMock(), revoked=False),
        )

        response = client.post(
            "/papers/" + "a" * 20 + "/" + "b" * 32 + "/edited",
            content=b"not a pdf",
            headers={"Content-Type": "application/pdf"},
        )

        assert response.status_code == 422

    def test_rejects_oversized_upload_without_reading_full_body(
        self, mocker: MockerFixture
    ) -> None:
        """Test an oversized upload is rejected as soon as the streamed size
        exceeds the cap, without the route ever consuming the remaining
        chunks of the request stream.

        This is a regression test for the route buffering the entire
        request body via FastAPI's ``Body(bytes)`` parameter before
        checking its size, which meant an oversized payload was always
        fully read into memory regardless of the configured cap. Calling
        the route coroutine directly with a stub `Request.stream()`
        isolates that consumption behavior from the TestClient's own
        client-side request buffering.
        """
        mocker.patch(
            "backend.main.load_credentials_from_file",
            return_value=CredentialsLoadResult(credentials=MagicMock(), revoked=False),
        )
        mocker.patch("backend.main.MAX_EDITED_PDF_BYTES", 20 * 1024 * 1024)

        chunk = b"a" * (6 * 1024 * 1024)
        chunks_yielded = 0

        async def stream() -> AsyncGenerator[bytes, None]:
            nonlocal chunks_yielded
            for _ in range(10):
                chunks_yielded += 1
                yield chunk

        request = MagicMock()
        request.stream = stream

        with pytest.raises(HTTPException) as exc_info:
            anyio.run(save_edited_pdf_route, "a" * 20, "b" * 32, request)

        assert exc_info.value.status_code == 413
        assert chunks_yielded < 10

    def test_saves_and_returns_edited_file_id(
        self, client: TestClient, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a valid save returns 200 with the new Drive file id."""
        mocker.patch(
            "backend.main.load_credentials_from_file",
            return_value=CredentialsLoadResult(credentials=MagicMock(), revoked=False),
        )
        mocker.patch("backend.pdf_upload.PAPERS_DIR", tmp_path)
        lib_id = "a" * 20
        pid = "b" * 32
        self._seed_local_index(tmp_path, lib_id, pid)
        mocker.patch(
            "backend.pdf_upload.upload_file_to_folder", return_value="edited-id"
        )
        mocker.patch(
            "backend.pdf_upload.get_papers_folder", return_value="papers-folder-id"
        )
        mocker.patch("backend.pdf_upload.upload_library_index")

        response = client.post(
            f"/papers/{lib_id}/{pid}/edited",
            content=_real_pdf_bytes(),
            headers={"Content-Type": "application/pdf"},
        )

        assert response.status_code == 200
        assert response.json() == {"edited_pdf_file_id": "edited-id"}

"""Main entry point for the FastAPI backend application."""

import logging
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.drive import PAPERS_DIR, load_credentials_from_file
from backend.pdf_upload import InvalidIdError, InvalidPdfError, MAX_EDITED_PDF_BYTES
from backend.pdf_upload import save_edited_pdf as _save_edited_pdf

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Open Paper Shelf API")

# Ensure the local papers directory exists before mounting
PAPERS_DIR.mkdir(exist_ok=True)


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


class EditedPdfSavedResponse(BaseModel):
    """Pydantic model for a successful edited-PDF save."""

    edited_pdf_file_id: str


# WARNING: Mounted without authentication, like the /papers static mount
# above. Do not expose to public networks as-is.
@app.post("/papers/{lib_id}/{pid}/edited", response_model=EditedPdfSavedResponse)
def save_edited_pdf_route(
    lib_id: str, pid: str, data: bytes = Body(..., media_type="application/pdf")
) -> EditedPdfSavedResponse:
    """Persists a browser-auto-saved, annotated PDF for one paper.

    Called by the pdf.js viewer's own JavaScript whenever the user's
    highlight edits change, so annotations sync to Google Drive without a
    manual download/re-upload round trip.

    Args:
        lib_id: The Google Drive folder ID of the library.
        pid: The paper's unique ID.
        data: The raw PDF bytes produced by pdf.js's saveDocument().

    Returns:
        EditedPdfSavedResponse: The Drive file ID of the persisted edit.

    Raises:
        HTTPException: 400 for a malformed lib_id/pid, 401 if not
            authenticated with Google, 404 if the paper is unknown, 413 if
            `data` exceeds MAX_EDITED_PDF_BYTES, 422 if `data` is not a valid
            PDF, 502 on a Google Drive failure.
    """
    creds = load_credentials_from_file()
    if creds is None:
        raise HTTPException(
            status_code=401, detail="Not authenticated with Google Drive."
        )
    if len(data) > MAX_EDITED_PDF_BYTES:
        raise HTTPException(
            status_code=413, detail="Edited PDF exceeds the maximum allowed size."
        )

    try:
        updated_entry = _save_edited_pdf(creds, lib_id, pid, data)
    except InvalidIdError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except InvalidPdfError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (FileNotFoundError, KeyError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "Failed to persist edited PDF for lib_id=%s pid=%s", lib_id, pid
        )
        raise HTTPException(
            status_code=502, detail="Failed to sync edited PDF to Google Drive."
        ) from e

    return EditedPdfSavedResponse(edited_pdf_file_id=updated_entry.edited_pdf_file_id)


# Mounted after the explicit /papers/{lib_id}/{pid}/edited route above:
# Starlette matches mounts/routes in registration order, and a mount claims
# the whole match for its prefix, so mounting /papers before that route would
# shadow it (POST would 405 from StaticFiles instead of reaching the route).
# WARNING: Mounted without authentication. Do not expose to public networks as-is.
app.mount("/papers", StaticFiles(directory=str(PAPERS_DIR)), name="papers")
app.mount("/pdfjs", StaticFiles(directory=str(STATIC_DIR / "pdfjs")), name="pdfjs")

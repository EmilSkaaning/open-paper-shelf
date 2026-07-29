"""Uploading new papers to Google Drive and the in-memory library index."""

import tempfile
import uuid
from pathlib import Path
from typing import Sequence

import streamlit as st
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field
from streamlit.runtime.uploaded_file_manager import UploadedFile

from backend.drive import (
    create_paper_folder,
    delete_paper_folder,
    upload_file_to_folder,
)
from backend.models import PaperIndexEntry, PaperMetadata
from frontend.constants import (
    JSON_MIME_TYPE,
    META_FILENAME,
    PDF_FILENAME,
    PDF_MIME_TYPE,
)
from frontend.text_utils import strip_pdf_suffix


class UploadedPaperName(BaseModel):
    """Validates an uploaded PDF's filename before it becomes a paper title.

    Attributes:
        name: The original filename, non-empty and reasonably bounded.
    """

    name: str = Field(min_length=1, max_length=255)


def _cleanup_orphaned_folder(creds: Credentials, folder_id: str) -> None:
    """Deletes a paper's Drive folder after a failed upload.

    Reports (rather than raises) a cleanup failure, so it doesn't mask the
    original upload error that triggered this cleanup.

    Args:
        creds (Credentials): The Google OAuth credentials.
        folder_id (str): The Drive folder ID to delete.
    """
    try:
        delete_paper_folder(creds, folder_id)
    except Exception as cleanup_error:
        st.error(
            f"Also failed to clean up orphaned Drive folder "
            f"{folder_id}: {cleanup_error}"
        )


def _upload_paper_files(
    creds: Credentials, paper_id: str, pdf_path: Path, title: str
) -> PaperIndexEntry:
    """Uploads a single paper's PDF and metadata into a new Drive folder.

    On any failure after the folder is created, attempts to delete the
    orphaned folder before re-raising.

    Args:
        creds (Credentials): The Google OAuth credentials.
        paper_id (str): The paper's unique ID, used as the Drive folder name.
        pdf_path (Path): Local path to the paper's PDF file.
        title (str): The paper's initial title.

    Returns:
        PaperIndexEntry: The new paper's index entry.
    """
    folder_id = create_paper_folder(creds, st.session_state.current_papers_id, paper_id)
    try:
        pdf_file_id = upload_file_to_folder(
            creds, folder_id, pdf_path, PDF_FILENAME, PDF_MIME_TYPE
        )
        meta = PaperMetadata(title=title)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_meta:
            tmp_meta.write(meta.model_dump_json(indent=2).encode("utf-8"))
            meta_tmp_path = Path(tmp_meta.name)

        try:
            meta_file_id = upload_file_to_folder(
                creds, folder_id, meta_tmp_path, META_FILENAME, JSON_MIME_TYPE
            )
        finally:
            meta_tmp_path.unlink(missing_ok=True)
    except Exception:
        _cleanup_orphaned_folder(creds, folder_id)
        raise

    return PaperIndexEntry(
        title=meta.title,
        pdf_file_id=pdf_file_id,
        meta_file_id=meta_file_id,
        folder_id=folder_id,
        tags=meta.tags,
        status=meta.status,
    )


def upload_papers(creds: Credentials, uploaded_files: Sequence[UploadedFile]) -> bool:
    """Uploads each file to Drive and records it in the in-memory index.

    Args:
        creds (Credentials): The Google OAuth credentials.
        uploaded_files (Sequence[UploadedFile]): The PDF files selected by the
            user via Streamlit's file uploader.

    Returns:
        True if every file uploaded successfully, False if any failed.
    """
    all_succeeded = True
    for uploaded_file in uploaded_files:
        try:
            validated_name = UploadedPaperName(name=uploaded_file.name).name
            title = strip_pdf_suffix(validated_name)
            paper_id = uuid.uuid4().hex
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)

            try:
                entry = _upload_paper_files(creds, paper_id, tmp_path, title)
            finally:
                tmp_path.unlink(missing_ok=True)

            st.session_state.index.papers[paper_id] = entry
        except Exception as e:
            st.error(f"Failed to upload {uploaded_file.name}: {e}")
            all_succeeded = False
    return all_succeeded

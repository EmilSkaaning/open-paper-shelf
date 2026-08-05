"""Uploading new papers to Google Drive and the in-memory library index."""

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

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

logger = logging.getLogger(__name__)


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


def _is_duplicate_title(title: str) -> bool:
    """Checks whether a paper with the given title already exists in the index.

    Comparison is case-insensitive, since Streamlit's uploader preserves the
    original filename casing and users may re-select the same file with a
    different case.

    Args:
        title (str): The candidate paper title to check.

    Returns:
        bool: True if an existing index entry has a matching title.
    """
    normalized = title.strip().casefold()
    return any(
        entry.title.strip().casefold() == normalized
        for entry in st.session_state.index.papers.values()
    )


def upload_papers(
    creds: Credentials,
    uploaded_files: Sequence[UploadedFile],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """Uploads each file to Drive and records it in the in-memory index.

    Args:
        creds (Credentials): The Google OAuth credentials.
        uploaded_files (Sequence[UploadedFile]): The PDF files selected by the
            user via Streamlit's file uploader.
        on_progress (Optional[Callable[[int, int, str], None]]): Optional
            callback invoked after each file is processed (whether it
            succeeded or failed), receiving the 1-based file index, the
            total file count, and that file's name.

    Skipped duplicates (files whose title matches an existing index entry)
    are recorded in `st.session_state.duplicate_uploads_skipped` (a list of
    filenames), reset at the start of each call. This function does not
    render any UI feedback for skipped duplicates itself — callers own
    presenting that list to the user.

    Returns:
        True if every file uploaded successfully, False if any failed.
    """
    total = len(uploaded_files)
    all_succeeded = True
    st.session_state.duplicate_uploads_skipped = []
    for i, uploaded_file in enumerate(uploaded_files):
        try:
            validated_name = UploadedPaperName(name=uploaded_file.name).name
            title = strip_pdf_suffix(validated_name)
            if _is_duplicate_title(title):
                st.session_state.duplicate_uploads_skipped.append(uploaded_file.name)
                continue
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
        finally:
            if on_progress is not None:
                try:
                    on_progress(i + 1, total, uploaded_file.name)
                except Exception:
                    logger.exception(
                        "on_progress callback failed for %s", uploaded_file.name
                    )
    return all_succeeded

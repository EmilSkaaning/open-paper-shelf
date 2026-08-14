"""Uploading new papers to Google Drive and the in-memory library index."""

import logging
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

import streamlit as st
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field
from streamlit.runtime.uploaded_file_manager import UploadedFile

from backend.drive import (
    BatchFolderResult,
    create_paper_folders_batch,
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

# Bounds how many papers' PDF/metadata uploads run concurrently. These media
# uploads can't go through Drive's batch endpoint (it doesn't support media
# payloads), so this caps the thread pool used to run them in parallel
# instead of one full round-trip pair at a time.
MAX_CONCURRENT_UPLOADS = 4


class UploadedPaperName(BaseModel):
    """Validates an uploaded PDF's filename before it becomes a paper title.

    Attributes:
        name: The original filename, non-empty and reasonably bounded.
    """

    name: str = Field(min_length=1, max_length=255)


@dataclass(frozen=True)
class _SkippedUpload:
    """A file resolved during validation/dedup, before any Drive I/O.

    Attributes:
        filename: The uploaded file's original name.
        succeeded: Whether the file requires no further action (a duplicate
            skip) as opposed to a validation failure already reported to
            the user.
    """

    filename: str
    succeeded: bool


@dataclass(frozen=True)
class _PendingUpload:
    """A file queued for Drive folder creation and upload.

    Attributes:
        filename: The uploaded file's original name.
        paper_id: The freshly generated unique ID for this paper.
        title: The paper's initial title, derived from the filename.
        tmp_pdf_path: Local path to the paper's PDF, written to a temp file.
    """

    filename: str
    paper_id: str
    title: str
    tmp_pdf_path: Path


_UploadItem = Union[_SkippedUpload, _PendingUpload]


@dataclass(frozen=True)
class _UploadOutcome:
    """The result of uploading one paper's PDF and metadata to Drive.

    Attributes:
        entry: The new paper's index entry, if the upload succeeded.
        error_message: The upload failure's message, if it failed.
        cleanup_error_message: The orphaned-folder cleanup failure's
            message, if cleanup was attempted after a failure and itself
            failed.
    """

    entry: Optional[PaperIndexEntry]
    error_message: Optional[str]
    cleanup_error_message: Optional[str]

    @property
    def succeeded(self) -> bool:
        return self.entry is not None


def _cleanup_orphaned_folder(creds: Credentials, folder_id: str) -> Optional[str]:
    """Deletes a paper's Drive folder after a failed upload.

    Returns (rather than raises or reports) a cleanup failure, so callers
    running off the main thread can defer reporting it without masking the
    original upload error that triggered this cleanup.

    Args:
        creds (Credentials): The Google OAuth credentials.
        folder_id (str): The Drive folder ID to delete.

    Returns:
        Optional[str]: An error message if cleanup itself failed, else None.
    """
    try:
        delete_paper_folder(creds, folder_id)
        return None
    except Exception as cleanup_error:
        return (
            f"Also failed to clean up orphaned Drive folder "
            f"{folder_id}: {cleanup_error}"
        )


def _upload_paper_files(
    creds: Credentials, folder_id: str, pdf_path: Path, title: str
) -> PaperIndexEntry:
    """Uploads a single paper's PDF and metadata into an existing Drive folder.

    Args:
        creds (Credentials): The Google OAuth credentials.
        folder_id (str): The Drive folder ID to upload into.
        pdf_path (Path): Local path to the paper's PDF file.
        title (str): The paper's initial title.

    Returns:
        PaperIndexEntry: The new paper's index entry.
    """
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

    return PaperIndexEntry(
        title=meta.title,
        pdf_file_id=pdf_file_id,
        meta_file_id=meta_file_id,
        folder_id=folder_id,
        tags=meta.tags,
        status=meta.status,
    )


def _finish_upload(
    creds: Credentials,
    item: _PendingUpload,
    folder_id: Optional[str],
    folder_error: Optional[Exception],
) -> _UploadOutcome:
    """Uploads one paper's files, given its already-created (or failed) folder.

    Runs off the main thread as part of a bounded thread pool, so it must
    not touch `st.session_state` or call `st.*` UI functions directly (both
    are tied to Streamlit's main script-run context) — it only returns a
    result for the main thread to apply.

    On any upload failure after the folder was created, attempts to delete
    the orphaned folder. If the folder itself was never created (an error
    is already present, or no folder ID was returned), there is nothing to
    clean up.

    Args:
        creds (Credentials): The Google OAuth credentials.
        item (_PendingUpload): The paper's queued upload details.
        folder_id (Optional[str]): The paper's Drive folder ID, if creation
            succeeded.
        folder_error (Optional[Exception]): The error from creating the
            paper's folder, if creation failed.

    Returns:
        _UploadOutcome: The paper's index entry on success, or the upload
        (and any cleanup) failure messages on failure.
    """
    try:
        if folder_error is not None:
            raise folder_error
        if folder_id is None:
            raise RuntimeError(f"Drive folder was not created for {item.filename}")
        entry = _upload_paper_files(creds, folder_id, item.tmp_pdf_path, item.title)
        return _UploadOutcome(
            entry=entry, error_message=None, cleanup_error_message=None
        )
    except Exception as e:
        cleanup_error_message = (
            _cleanup_orphaned_folder(creds, folder_id)
            if folder_id is not None
            else None
        )
        return _UploadOutcome(
            entry=None,
            error_message=str(e),
            cleanup_error_message=cleanup_error_message,
        )
    finally:
        item.tmp_pdf_path.unlink(missing_ok=True)


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


def _stage_uploads(
    uploaded_files: Sequence[UploadedFile],
) -> list[_UploadItem]:
    """Validates and deduplicates each file, queuing the rest for upload.

    Runs sequentially (no Drive I/O yet) so that two identical filenames in
    the same batch are still deduplicated against each other, not just
    against papers already in the index — `staged_titles` tracks titles
    queued earlier in this same call, since their index entries don't exist
    yet (upload hasn't happened).

    Args:
        uploaded_files (Sequence[UploadedFile]): The PDF files selected by
            the user via Streamlit's file uploader.

    Returns:
        list[_UploadItem]: One item per input file, in the same order,
        either already resolved (skipped/invalid) or queued for upload.
    """
    items: list[_UploadItem] = []
    staged_titles: set[str] = set()
    for uploaded_file in uploaded_files:
        tmp_path: Optional[Path] = None
        try:
            validated_name = UploadedPaperName(name=uploaded_file.name).name
            title = strip_pdf_suffix(validated_name)
            normalized_title = title.strip().casefold()
            if _is_duplicate_title(title) or normalized_title in staged_titles:
                st.session_state.duplicate_uploads_skipped.append(uploaded_file.name)
                items.append(_SkippedUpload(uploaded_file.name, succeeded=True))
                continue

            staged_titles.add(normalized_title)
            paper_id = uuid.uuid4().hex
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(uploaded_file.getvalue())
            items.append(_PendingUpload(uploaded_file.name, paper_id, title, tmp_path))
        except Exception as e:
            st.error(f"Failed to upload {uploaded_file.name}: {e}")
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            items.append(_SkippedUpload(uploaded_file.name, succeeded=False))
    return items


def upload_papers(
    creds: Credentials,
    uploaded_files: Sequence[UploadedFile],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """Uploads each file to Drive and records it in the in-memory index.

    Reduces round-trips for multi-file uploads in two ways: every pending
    paper's Drive folder is created in one batched call (Drive's batch
    endpoint supports metadata-only requests), and the PDF/metadata media
    uploads themselves — which can't be batched — run concurrently across
    papers via a bounded thread pool, rather than one full pair of
    round-trips at a time.

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
    st.session_state.duplicate_uploads_skipped = []
    items = _stage_uploads(uploaded_files)

    pending_items = [item for item in items if isinstance(item, _PendingUpload)]
    try:
        folder_result = (
            create_paper_folders_batch(
                creds,
                st.session_state.current_papers_id,
                [item.paper_id for item in pending_items],
            )
            if pending_items
            else BatchFolderResult(folder_ids={}, errors={})
        )
    except Exception as e:
        folder_result = BatchFolderResult(
            folder_ids={}, errors={item.paper_id: e for item in pending_items}
        )

    all_succeeded = True
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_UPLOADS) as executor:
        futures = {
            item: executor.submit(
                _finish_upload,
                creds,
                item,
                folder_result.folder_ids.get(item.paper_id),
                folder_result.errors.get(item.paper_id),
            )
            for item in pending_items
        }

        for i, (uploaded_file, item) in enumerate(zip(uploaded_files, items)):
            if isinstance(item, _PendingUpload):
                outcome = futures[item].result()
                if outcome.cleanup_error_message is not None:
                    st.error(outcome.cleanup_error_message)
                if outcome.succeeded:
                    assert outcome.entry is not None
                    st.session_state.index.papers[item.paper_id] = outcome.entry
                else:
                    st.error(
                        f"Failed to upload {item.filename}: {outcome.error_message}"
                    )
                    all_succeeded = False
            elif not item.succeeded:
                all_succeeded = False

            if on_progress is not None:
                try:
                    on_progress(i + 1, total, uploaded_file.name)
                except Exception:
                    logger.exception(
                        "on_progress callback failed for %s", uploaded_file.name
                    )

    return all_succeeded

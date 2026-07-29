"""Library-level session state: opening, syncing, and deleting papers."""

import json
import shutil
from pathlib import Path
from typing import Sequence

import streamlit as st
from google.oauth2.credentials import Credentials

from backend.drive import (
    PAPERS_DIR,
    delete_paper_folder,
    download_file,
    get_library_index_file,
    upload_library_index,
)
from backend.models import LibraryIndex, PaperIndexEntry


def init_library_state(
    creds: Credentials, lib_id: str, papers_id: str, lib_name: str
) -> None:
    """Resets session state for a newly opened or newly created library.

    Also clears any pending request to force the manual library-selection
    screen, so a later single-library session goes back to auto-opening.

    Args:
        creds (Credentials): The Google OAuth credentials (unused directly,
            kept for a consistent signature with other library-scoped calls).
        lib_id (str): The Google Drive file ID of the library folder.
        papers_id (str): The Google Drive file ID of the library's papers folder.
        lib_name (str): The library's human-readable display name, shown in
            the sidebar in place of the opaque Drive file ID.
    """
    st.session_state.current_lib_id = lib_id
    st.session_state.current_lib_name = lib_name
    st.session_state.current_papers_id = papers_id
    st.session_state.local_lib_dir = PAPERS_DIR / lib_id
    st.session_state.local_lib_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.local_index_path = (
        st.session_state.local_lib_dir / "id-mapping.json"
    )
    st.session_state.selected_paper = None
    st.session_state.pop("manual_library_selection", None)


def sync_library_index(creds: Credentials) -> None:
    """Syncs the local library index cache with Drive if it's out of date.

    Downloads the remote id-mapping.json when it's missing locally or its
    modifiedTime differs from the last synced value, then loads it into
    st.session_state.index. On a download failure, falls back to the
    existing local cache if there is one, or an empty LibraryIndex
    otherwise. Falls back to an empty LibraryIndex on a parse failure.

    Args:
        creds (Credentials): The Google OAuth credentials.
    """
    papers_id = st.session_state.current_papers_id
    local_path = st.session_state.local_index_path

    remote_info = get_library_index_file(creds, papers_id)
    if not remote_info:
        st.session_state.index = LibraryIndex()
        st.session_state.last_sync_time = None
        return

    remote_time = remote_info.get("modifiedTime")
    if not local_path.exists() or st.session_state.get("last_sync_time") != remote_time:
        try:
            download_file(creds, remote_info["id"], local_path)
            st.session_state.last_sync_time = remote_time
        except Exception as e:
            st.error(f"Failed to sync library index: {e}")
            if not local_path.exists():
                st.session_state.index = LibraryIndex()
                return

    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            st.session_state.index = LibraryIndex(**data)
        except Exception as e:
            st.error(f"Failed to parse library index: {e}")
            st.session_state.index = LibraryIndex()
    else:
        st.session_state.index = LibraryIndex()


def delete_selected_papers(
    creds: Credentials,
    pids: Sequence[str],
    index: LibraryIndex,
    papers_id: str,
    local_lib_dir: Path,
) -> bool:
    """Deletes multiple papers from Drive, the library index, and disk.

    Each paper's Drive folder is removed individually - a failure there
    leaves that paper untouched and reports an error, but doesn't stop the
    rest of the batch. The index is then updated once for every paper that
    was actually deleted. If uploading the updated index fails, every
    removed entry is restored locally so a future sync doesn't merge it
    back in from the unchanged remote index as a broken entry.

    Args:
        creds: The Google OAuth credentials.
        pids: The paper IDs to delete.
        index: The current library index; mutated in place.
        papers_id: The Google Drive folder ID of the library's papers folder.
        local_lib_dir: The local cache directory for this library.

    Returns:
        bool: True only if every requested paper was deleted from Drive and
        (when there was anything to upload) the index upload succeeded.
        False if any individual paper's Drive deletion failed, or if the
        index upload failed.
    """
    all_succeeded = True
    removed: dict[str, PaperIndexEntry] = {}
    for pid in pids:
        entry = index.papers.get(pid)
        if entry is None:
            continue
        try:
            delete_paper_folder(creds, entry.folder_id)
        except Exception as e:
            st.error(f"Failed to delete paper '{entry.title}': {e}")
            all_succeeded = False
            continue
        removed[pid] = index.papers.pop(pid)

    if not removed:
        return all_succeeded

    try:
        upload_library_index(creds, papers_id, index, deleted_pids=set(removed))
    except Exception as e:
        for pid, entry in removed.items():
            index.papers[pid] = entry
        st.error(f"Failed to sync deletion: {e}")
        return False

    for pid in removed:
        if st.session_state.selected_paper == pid:
            st.session_state.selected_paper = None
        shutil.rmtree(local_lib_dir / pid, ignore_errors=True)

    return all_succeeded

import html
import json
import re
import shutil
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional, Sequence, cast, Literal

import streamlit as st
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field, ValidationError
from streamlit.runtime.uploaded_file_manager import UploadedFile
import os
from st_keyup import st_keyup

import sys

src_path = Path(__file__).resolve().parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backend.drive import (  # noqa: E402
    add_oauth_flow,
    create_library,
    create_paper_folder,
    delete_paper_folder,
    download_file,
    get_library_index_file,
    get_oauth_flow,
    get_or_create_root_folder,
    get_papers_folder,
    list_libraries,
    load_credentials_from_file,
    save_credentials,
    upload_file_to_folder,
    upload_library_index,
    OAUTH_FLOWS,
    PAPERS_DIR,
)
from backend.models import LibraryIndex, PaperIndexEntry, PaperMetadata  # noqa: E402


def get_initial_sidebar_state() -> Literal["auto", "expanded"]:
    """Determines whether the sidebar should start expanded.

    Returns:
        Literal["auto", "expanded"]: "expanded" once a library is already
        open in session state, so its controls are immediately usable
        without an extra click; otherwise Streamlit's default "auto".
    """
    if st.session_state.get("current_lib_id"):
        return "expanded"
    return "auto"


st.set_page_config(
    layout="wide",
    page_title="Open Paper Shelf",
    initial_sidebar_state=get_initial_sidebar_state(),
)


STATUS_ICONS: dict[str, str] = {
    "Unread": "📄",
    "Reading": "📖",
    "Read": "✅",
    "TODO": "📌",
}


class OAuthCallbackParams(BaseModel):
    """Validated OAuth redirect query parameters.

    Attributes:
        code: The authorization code returned by Google.
        state: The CSRF state string echoed back by Google, used to look up
            the matching cached OAuth flow.
    """

    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


def authenticate_user() -> Optional[Credentials]:
    """Handles the Google OAuth flow, returning credentials once authenticated.

    On first call, redirects the user to Google's consent screen. On the
    OAuth redirect back, validates and exchanges the authorization code for
    credentials, caches them locally, and clears OAuth-related session state.

    Returns:
        Optional[Credentials]: The user's Google OAuth credentials, or None
        if the user is not yet authenticated.
    """
    creds = load_credentials_from_file()
    if creds:
        return creds

    query_params = st.query_params
    if "code" in query_params:
        try:
            callback = OAuthCallbackParams(
                code=query_params.get("code") or "",
                state=query_params.get("state") or "",
            )
        except ValidationError:
            st.error("Authentication failed: State mismatch (possible CSRF attempt).")
            return None
        flow = OAUTH_FLOWS.get(callback.state)
        if flow is None:
            st.error("Authentication failed: State mismatch (possible CSRF attempt).")
            return None
        try:
            flow.fetch_token(code=callback.code)
            creds = cast(Credentials, flow.credentials)
            save_credentials(creds)
            OAUTH_FLOWS.pop(callback.state, None)
            st.query_params.clear()
            st.session_state.pop("auth_flow", None)
            st.session_state.pop("oauth_state", None)
            st.success("Successfully authenticated!")
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")
            return None

    if "auth_flow" not in st.session_state:
        try:
            flow = get_oauth_flow()
        except FileNotFoundError:
            st.error(
                "credentials.json not found. Please provide valid Google Drive credentials."
            )
            return None
        st.session_state.auth_flow = flow
        auth_url, state = flow.authorization_url(
            prompt="consent", access_type="offline"
        )
        add_oauth_flow(state, flow)
        st.session_state.auth_url = auth_url
        st.session_state.oauth_state = state

    st.warning("Please authenticate to access your Google Drive.")
    st.link_button("Login with Google", st.session_state.auth_url)
    return None


class UploadedPaperName(BaseModel):
    """Validates an uploaded PDF's filename before it becomes a paper title.

    Attributes:
        name: The original filename, non-empty and reasonably bounded.
    """

    name: str = Field(min_length=1, max_length=255)


def strip_pdf_suffix(name: str) -> str:
    """Removes a trailing .pdf extension from a paper title, if present.

    Args:
        name: The candidate title, typically derived from an uploaded
            filename or user-edited text.

    Returns:
        str: `name` with a trailing ".pdf" (any case) suffix removed.
        Falls back to the original `name` if stripping it would leave an
        empty string (e.g. a file literally named ".pdf").
    """
    stripped = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return stripped if stripped else name


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
                folder_id = create_paper_folder(
                    creds,
                    st.session_state.current_papers_id,
                    paper_id,
                )
                try:
                    pdf_file_id = upload_file_to_folder(
                        creds,
                        folder_id,
                        tmp_path,
                        "paper.pdf",
                        "application/pdf",
                    )
                    meta = PaperMetadata(title=title)

                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".json"
                    ) as tmp_meta:
                        tmp_meta.write(meta.model_dump_json(indent=2).encode("utf-8"))
                        meta_tmp_path = Path(tmp_meta.name)

                    try:
                        meta_file_id = upload_file_to_folder(
                            creds,
                            folder_id,
                            meta_tmp_path,
                            "meta.json",
                            "application/json",
                        )

                        st.session_state.index.papers[paper_id] = PaperIndexEntry(
                            title=meta.title,
                            pdf_file_id=pdf_file_id,
                            meta_file_id=meta_file_id,
                            folder_id=folder_id,
                            tags=meta.tags,
                            status=meta.status,
                        )
                    finally:
                        meta_tmp_path.unlink(missing_ok=True)
                except Exception:
                    try:
                        delete_paper_folder(creds, folder_id)
                    except Exception as cleanup_error:
                        st.error(
                            f"Also failed to clean up orphaned Drive folder "
                            f"{folder_id}: {cleanup_error}"
                        )
                    raise
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:
            st.error(f"Failed to upload {uploaded_file.name}: {e}")
            all_succeeded = False
    return all_succeeded


def sync_paper_metadata(
    creds: Credentials, paper_info: PaperIndexEntry, local_meta_path: Path
) -> bool:
    """Refreshes the local metadata cache for a paper from Drive.

    Args:
        creds (Credentials): The Google OAuth credentials.
        paper_info (PaperIndexEntry): The paper's index entry, used to locate
            its metadata file on Drive.
        local_meta_path (Path): The local path to refresh with the latest
            metadata.

    Returns:
        True if metadata is safe to edit (freshly downloaded, or a
        pre-existing local cache survives a failed download). False if
        there's no reliable copy of the real metadata, so editing would
        risk overwriting it with defaults.
    """
    had_local_copy = local_meta_path.exists()
    try:
        download_file(creds, paper_info.meta_file_id, local_meta_path)
        return True
    except Exception as e:
        st.error(f"Failed to fetch metadata: {e}")
        return had_local_copy


def init_library_state(creds: Credentials, lib_id: str, papers_id: str) -> None:
    """Resets session state for a newly opened or newly created library.

    Args:
        creds (Credentials): The Google OAuth credentials (unused directly,
            kept for a consistent signature with other library-scoped calls).
        lib_id (str): The Google Drive file ID of the library folder.
        papers_id (str): The Google Drive file ID of the library's papers folder.
    """
    st.session_state.current_lib_id = lib_id
    st.session_state.current_papers_id = papers_id
    st.session_state.local_lib_dir = PAPERS_DIR / lib_id
    st.session_state.local_lib_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.local_index_path = (
        st.session_state.local_lib_dir / "id-mapping.json"
    )
    st.session_state.selected_paper = None


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


def filter_papers(
    papers: dict[str, PaperIndexEntry],
    search_query: str,
    status_filter: Sequence[str],
    tags_filter: Sequence[str],
) -> list[tuple[str, PaperIndexEntry]]:
    """Filters and sorts the library's papers for sidebar display.

    Args:
        papers: Mapping of paper ID to its index entry, as stored in
            LibraryIndex.papers.
        search_query: Lowercased search text; a paper matches if its title
            contains this text.
        status_filter: Statuses to restrict results to. Empty means no
            status filtering.
        tags_filter: Tags to restrict results to; a paper matches if it has
            at least one of these tags. Empty means no tag filtering.

    Returns:
        list[tuple[str, PaperIndexEntry]]: Matching (paper_id, entry) pairs,
        sorted by title. Entries whose key isn't a 32-character hex paper ID
        (e.g. a legacy/malformed index entry) are always skipped.
    """
    matches: list[tuple[str, PaperIndexEntry]] = []
    for pid, p in papers.items():
        if not re.match(r"^[a-f0-9]{32}$", pid):
            continue
        if search_query not in p.title.lower():
            continue
        if status_filter and p.status not in status_filter:
            continue
        if tags_filter and not any(tag in p.tags for tag in tags_filter):
            continue
        matches.append((pid, p))
    return sorted(matches, key=lambda x: x[1].title)


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


def main() -> None:
    """The main entry point for the Streamlit frontend application.

    Authenticates the user, displays the library selection UI, and handles
    all interactions including paper uploads, search, and metadata editing.

    Args:
        None

    Returns:
        None
    """
    creds = authenticate_user()
    if not creds:
        return

    st.title("📚 Open Paper Shelf")

    if "root_id" not in st.session_state:
        st.session_state.root_id = get_or_create_root_folder(creds)

    root_id = st.session_state.root_id

    # Library Selection Screen
    if "current_lib_id" not in st.session_state:
        st.subheader("Select or Create a Library")
        libraries = list_libraries(creds, root_id)

        col1, col2 = st.columns(2)
        with col1:
            if libraries:
                lib_options = {lib["id"]: lib["name"] for lib in libraries}
                selected_lib = st.selectbox(
                    "Existing Libraries",
                    options=list(lib_options.keys()),
                    format_func=lambda x: lib_options[x],
                )
                if st.button("Open Library"):
                    papers_id = get_papers_folder(creds, selected_lib)
                    init_library_state(creds, selected_lib, papers_id)
                    st.rerun()
            else:
                st.info("No existing libraries found.")

        with col2:
            new_lib_name = st.text_input("New Library Name")
            if st.button("Create Library") and new_lib_name:
                lib_info = create_library(creds, root_id, new_lib_name)
                init_library_state(creds, lib_info["lib_id"], lib_info["papers_id"])
                upload_library_index(creds, lib_info["papers_id"], LibraryIndex())
                st.success(f"Library '{new_lib_name}' created!")
                st.rerun()
        return

    # Library View
    if "index" not in st.session_state:
        with st.spinner("Syncing library..."):
            sync_library_index(creds)

    with st.sidebar:

        def switch_lib() -> None:
            """Clears the current library's session state to return to library selection."""
            for k in ["current_lib_id", "current_papers_id", "index", "last_sync_time"]:
                st.session_state.pop(k, None)

        st.caption(f"📁 Library ID: {st.session_state.current_lib_id}")
        st.button("🔙 Switch Library", on_click=switch_lib, use_container_width=True)

        if "uploader_key" not in st.session_state:
            st.session_state.uploader_key = 0

        with st.expander("📤 Upload Paper", expanded=False):
            with st.container(height=150):
                uploaded_files = st.file_uploader(
                    "Choose PDF files",
                    type="pdf",
                    accept_multiple_files=True,
                    key=str(st.session_state.uploader_key),
                )
            if uploaded_files:
                if st.button("Upload"):
                    with st.spinner("Uploading to Google Drive..."):
                        try:
                            all_succeeded = upload_papers(creds, uploaded_files)
                        finally:
                            upload_library_index(
                                creds,
                                st.session_state.current_papers_id,
                                st.session_state.index,
                            )
                            st.session_state.last_sync_time = None
                        st.session_state.uploader_key += 1
                        if all_succeeded:
                            st.success("Uploaded successfully!")
                            st.rerun()
                        else:
                            st.warning(
                                "Some files failed to upload. See the errors above; "
                                "re-select the failed files to retry."
                            )

        st.header("Library Papers")
        search_box = st_keyup(
            "Search", placeholder="Search papers...", key="search_box"
        )
        search_query = (search_box or "").lower()

        icon_col, status_col, tags_col = st.columns([1, 2, 2])

        with status_col:
            status_filter = st.multiselect(
                "Status",
                options=["Unread", "Reading", "Read", "TODO"],
                key="status_filter",
            )
        with tags_col:
            all_tags = sorted(
                {tag for p in st.session_state.index.papers.values() for tag in p.tags}
            )
            tags_filter = st.multiselect("Tags", options=all_tags, key="tags_filter")

        filtered_papers = filter_papers(
            st.session_state.index.papers, search_query, status_filter, tags_filter
        )

        with icon_col:
            if st.button("🗑️", key="trash_icon", help="Delete selected papers"):
                checked_pids = [
                    pid
                    for pid, _ in filtered_papers
                    if st.session_state.get(f"chk_{pid}")
                ]
                if checked_pids:
                    st.session_state.confirm_delete_pids = checked_pids
                else:
                    st.warning("No papers selected.")

        if st.session_state.get("confirm_delete_pids"):
            pids_to_delete = st.session_state.confirm_delete_pids
            st.warning(f"Delete {len(pids_to_delete)} paper(s)? This cannot be undone.")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                if st.button("Confirm", key="confirm_delete_btn"):
                    delete_succeeded = delete_selected_papers(
                        creds,
                        pids_to_delete,
                        st.session_state.index,
                        st.session_state.current_papers_id,
                        st.session_state.local_lib_dir,
                    )
                    st.session_state.confirm_delete_pids = None
                    if delete_succeeded:
                        st.rerun()
            with cancel_col:
                if st.button("Cancel", key="cancel_delete_btn"):
                    st.session_state.confirm_delete_pids = None
                    st.rerun()

        with st.container(height=400):
            for pid, p in filtered_papers:
                row_check, row_button = st.columns([1, 4])
                with row_check:
                    st.checkbox(
                        "Select", key=f"chk_{pid}", label_visibility="collapsed"
                    )
                with row_button:
                    display_name = f"{STATUS_ICONS.get(p.status, '📄')} {p.title}"
                    if pid == st.session_state.selected_paper:
                        display_name = f"**{display_name}**"
                    if st.button(
                        display_name, key=f"btn_{pid}", use_container_width=True
                    ):
                        st.session_state.selected_paper = pid
                        st.rerun()

    # Main area
    if st.session_state.selected_paper:
        pid = st.session_state.selected_paper
        if not re.match(r"^[a-f0-9]{32}$", pid):
            st.error("Invalid paper ID format.")
            st.stop()
        paper_info = st.session_state.index.papers[pid]

        local_paper_dir = st.session_state.local_lib_dir / pid
        local_paper_dir.mkdir(parents=True, exist_ok=True)
        local_pdf_path = local_paper_dir / "paper.pdf"
        local_meta_path = local_paper_dir / "meta.json"

        # Download files if missing
        pdf_available = local_pdf_path.exists()
        with st.spinner("Loading paper..."):
            if not pdf_available:
                try:
                    download_file(creds, paper_info.pdf_file_id, local_pdf_path)
                    pdf_available = True
                except Exception as e:
                    st.error(f"Failed to load PDF: {e}")

            # Always sync metadata on load to avoid stale caches across devices
            metadata_available = sync_paper_metadata(creds, paper_info, local_meta_path)

        meta = PaperMetadata(title=paper_info.title)
        if local_meta_path.exists():
            try:
                data = json.loads(local_meta_path.read_text(encoding="utf-8"))
                meta = PaperMetadata(**data)
            except ValidationError as e:
                st.warning("Metadata invalid, recovering valid fields.")
                data = json.loads(local_meta_path.read_text(encoding="utf-8"))
                invalid_fields = [
                    err.get("loc")[0] for err in e.errors() if err.get("loc")
                ]
                for field in invalid_fields:
                    if field in data:
                        del data[field]
                data["title"] = data.get("title", paper_info.title)
                try:
                    meta = PaperMetadata(**data)
                except Exception:
                    meta = PaperMetadata(title=paper_info.title)
            except Exception as e:
                st.error(f"Could not load metadata: {e}")

        col_pdf, col_meta = st.columns([2, 1])
        with col_pdf:
            if pdf_available:
                base_url = os.environ.get("FASTAPI_URL", "http://localhost:8000")
                quoted_lib_id = urllib.parse.quote(st.session_state.current_lib_id)
                quoted_pid = urllib.parse.quote(pid)
                fastapi_url = f"{base_url.rstrip('/')}/papers/{quoted_lib_id}/{quoted_pid}/paper.pdf"
                pdf_display = f'<iframe src="{html.escape(fastapi_url)}" width="100%" height="750" style="border:none;" type="application/pdf"></iframe>'
                st.markdown(pdf_display, unsafe_allow_html=True)
            else:
                st.warning("PDF could not be loaded from Drive.")

        with col_meta:
            st.subheader("Metadata")
            if not metadata_available:
                st.warning(
                    "Could not load the latest metadata from Drive. Editing is "
                    "disabled to avoid overwriting your saved data."
                )
            with st.form(key=f"meta_form_{pid}"):
                new_title = st.text_input("Title", value=meta.title)
                tags_str = st.text_input(
                    "Tags (comma separated)", value=", ".join(meta.tags)
                )
                status = st.selectbox(
                    "Status",
                    options=["Unread", "Reading", "Read", "TODO"],
                    index=["Unread", "Reading", "Read", "TODO"].index(meta.status),
                )
                citation = st.text_input("Citation", value=meta.citation)
                notes = st.text_area("Notes", value=meta.notes, height=200)

                if st.form_submit_button(
                    "Save Changes", disabled=not metadata_available
                ):
                    meta.title = strip_pdf_suffix(new_title)
                    meta.tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    meta.status = cast(
                        Literal["Unread", "Reading", "Read", "TODO"], status
                    )
                    meta.citation = citation
                    meta.notes = notes

                    local_meta_path.write_text(
                        meta.model_dump_json(indent=2), encoding="utf-8"
                    )
                    with st.spinner("Saving metadata to Drive..."):
                        upload_file_to_folder(
                            creds,
                            paper_info.folder_id,
                            local_meta_path,
                            "meta.json",
                            "application/json",
                        )

                        paper_info.title = meta.title
                        paper_info.tags = meta.tags
                        paper_info.status = meta.status
                        st.session_state.index.papers[pid] = paper_info
                        upload_library_index(
                            creds,
                            st.session_state.current_papers_id,
                            st.session_state.index,
                        )

                    st.success("Metadata saved!")
    else:
        st.info("Select a paper from the sidebar to view it.")


if __name__ == "__main__":
    main()

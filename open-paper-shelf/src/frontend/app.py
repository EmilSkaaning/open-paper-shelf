import html
import json
import re
import shutil
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Optional, cast, Literal

import streamlit as st
from pydantic import ValidationError
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
    PAPERS_DIR,
)
from backend.models import LibraryIndex, PaperIndexEntry, PaperMetadata  # noqa: E402

st.set_page_config(layout="wide", page_title="Open Paper Shelf")


def authenticate_user() -> Optional[Any]:
    creds = load_credentials_from_file()
    if creds:
        return creds

    if "auth_flow" not in st.session_state:
        flow = get_oauth_flow()
        st.session_state.auth_flow = flow
        add_oauth_flow("main_auth", flow)
        auth_url, state = flow.authorization_url(prompt="consent")
        st.session_state.auth_url = auth_url
        st.session_state.oauth_state = state

    query_params = st.query_params
    if "code" in query_params:
        if "state" not in query_params or query_params["state"] != st.session_state.get(
            "oauth_state"
        ):
            st.error("Authentication failed: State mismatch (possible CSRF attempt).")
            return None
        code = query_params["code"]
        flow = st.session_state.auth_flow
        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            save_credentials(creds)
            st.query_params.clear()
            st.success("Successfully authenticated!")
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")
            return None

    st.warning("Please authenticate to access your Google Drive.")
    st.link_button("Login with Google", st.session_state.auth_url)
    return None


def init_library_state(creds, lib_id: str, papers_id: str):
    st.session_state.current_lib_id = lib_id
    st.session_state.current_papers_id = papers_id
    st.session_state.local_lib_dir = PAPERS_DIR / lib_id
    st.session_state.local_lib_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.local_index_path = (
        st.session_state.local_lib_dir / "id-mapping.json"
    )
    st.session_state.selected_paper = None


def sync_library_index(creds):
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
            return

    if local_path.exists():
        try:
            data = json.loads(local_path.read_text())
            st.session_state.index = LibraryIndex(**data)
        except Exception as e:
            st.error(f"Failed to parse library index: {e}")


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
            for k in ["current_lib_id", "current_papers_id", "index", "last_sync_time"]:
                st.session_state.pop(k, None)

        st.button("🔙 Switch Library", on_click=switch_lib, use_container_width=True)

        st.header("Upload Paper")
        uploaded_files = st.file_uploader(
            "Choose PDF files", type="pdf", accept_multiple_files=True
        )
        if uploaded_files:
            if st.button("Upload"):
                with st.spinner("Uploading to Google Drive..."):
                    try:
                        for uploaded_file in uploaded_files:
                            try:
                                paper_id = uuid.uuid4().hex
                                with tempfile.NamedTemporaryFile(
                                    delete=False, suffix=".pdf"
                                ) as tmp:
                                    tmp.write(uploaded_file.getvalue())
                                    tmp_path = Path(tmp.name)

                                try:
                                    folder_id = create_paper_folder(
                                        creds,
                                        st.session_state.current_papers_id,
                                        paper_id,
                                    )
                                    pdf_file_id = upload_file_to_folder(
                                        creds,
                                        folder_id,
                                        tmp_path,
                                        "paper.pdf",
                                        "application/pdf",
                                    )
                                    meta = PaperMetadata(title=uploaded_file.name)

                                    with tempfile.NamedTemporaryFile(
                                        delete=False, suffix=".json"
                                    ) as tmp_meta:
                                        tmp_meta.write(
                                            meta.model_dump_json(indent=2).encode(
                                                "utf-8"
                                            )
                                        )
                                        meta_tmp_path = Path(tmp_meta.name)

                                    try:
                                        meta_file_id = upload_file_to_folder(
                                            creds,
                                            folder_id,
                                            meta_tmp_path,
                                            "meta.json",
                                            "application/json",
                                        )

                                        st.session_state.index.papers[paper_id] = (
                                            PaperIndexEntry(
                                                title=meta.title,
                                                pdf_file_id=pdf_file_id,
                                                meta_file_id=meta_file_id,
                                                folder_id=folder_id,
                                            )
                                        )
                                    finally:
                                        meta_tmp_path.unlink(missing_ok=True)
                                finally:
                                    tmp_path.unlink(missing_ok=True)
                            except Exception as e:
                                st.error(f"Failed to upload {uploaded_file.name}: {e}")
                    finally:
                        upload_library_index(
                            creds,
                            st.session_state.current_papers_id,
                            st.session_state.index,
                        )
                        st.session_state.last_sync_time = None
                    st.success("Uploaded successfully!")
                    st.rerun()

        st.header("Library Papers")
        search_box = st_keyup(
            "Search", placeholder="Search papers...", key="search_box"
        )
        search_query = (search_box or "").lower()

        filtered_papers = []
        for pid, p in st.session_state.index.papers.items():
            if not re.match(r"^[a-f0-9]{32}$", pid):
                continue
            if search_query in p.title.lower():
                filtered_papers.append((pid, p))

        for pid, p in sorted(filtered_papers, key=lambda x: x[1].title):
            col1, col2 = st.columns([4, 1])
            with col1:
                display_name = f"📄 {p.title}"
                if pid == st.session_state.selected_paper:
                    display_name = f"**{display_name}**"
                if st.button(display_name, key=f"btn_{pid}", use_container_width=True):
                    st.session_state.selected_paper = pid
                    st.rerun()
            with col2:
                if st.button("🗑️", key=f"del_{pid}", help="Delete paper"):
                    with st.spinner("Deleting..."):
                        delete_paper_folder(creds, p.folder_id)
                        st.session_state.index.papers.pop(pid)
                        upload_library_index(
                            creds,
                            st.session_state.current_papers_id,
                            st.session_state.index,
                        )
                        if st.session_state.selected_paper == pid:
                            st.session_state.selected_paper = None
                        shutil.rmtree(
                            st.session_state.local_lib_dir / pid, ignore_errors=True
                        )
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
        with st.spinner("Loading paper..."):
            if not local_pdf_path.exists():
                download_file(creds, paper_info.pdf_file_id, local_pdf_path)

            # Always sync metadata on load to avoid stale caches across devices
            try:
                download_file(creds, paper_info.meta_file_id, local_meta_path)
            except Exception as e:
                st.error(f"Failed to fetch metadata: {e}")

        meta = PaperMetadata(title=paper_info.title)
        if local_meta_path.exists():
            try:
                data = json.loads(local_meta_path.read_text(encoding="utf-8"))
                meta = PaperMetadata(**data)
            except ValidationError:
                st.warning("Metadata invalid, using default fallback.")
                data = json.loads(local_meta_path.read_text(encoding="utf-8"))
                data["title"] = data.get("title", paper_info.title)
                # Ignore validation for fallback rendering if possible, but Pydantic requires it
                try:
                    meta = PaperMetadata(**data)
                except Exception:
                    meta = PaperMetadata(title=paper_info.title)
            except Exception as e:
                st.error(f"Could not load metadata: {e}")

        col_pdf, col_meta = st.columns([2, 1])
        with col_pdf:
            base_url = os.environ.get("FASTAPI_URL", "http://localhost:8000")
            quoted_lib_id = urllib.parse.quote(st.session_state.current_lib_id)
            quoted_pid = urllib.parse.quote(pid)
            fastapi_url = (
                f"{base_url.rstrip('/')}/papers/{quoted_lib_id}/{quoted_pid}/paper.pdf"
            )
            pdf_display = f'<iframe src="{html.escape(fastapi_url)}" width="100%" height="750" style="border:none;" type="application/pdf"></iframe>'
            st.markdown(pdf_display, unsafe_allow_html=True)

        with col_meta:
            st.subheader("Metadata")
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

                if st.form_submit_button("Save Changes"):
                    meta.title = new_title
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

                        # Update index if title changed
                        if paper_info.title != new_title:
                            paper_info.title = new_title
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

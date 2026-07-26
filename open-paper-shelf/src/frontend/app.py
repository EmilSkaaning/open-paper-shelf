"""Main Streamlit application for Open Paper Shelf."""

import os
import sys
import urllib.parse
import tempfile
import re
import shutil
from pathlib import Path
from typing import Optional, Any

import streamlit as st
from st_keyup import st_keyup
from google.oauth2.credentials import Credentials


def get_safe_filename(name: str) -> str:
    """Returns a filesystem-safe filename by removing invalid characters."""
    base = Path(name).name
    return re.sub(r'[<>:"/\\|?*]', "_", base)


# Ensure the src directory is in the path to import backend
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.append(str(src_path))

from backend.drive import (  # noqa: E402
    PAPERS_DIR,
    get_oauth_flow,
    load_credentials_from_file,
    save_credentials,
    get_or_create_library_folder,
    OAUTH_FLOWS,
    add_oauth_flow,
    list_pdfs_in_library,
    download_pdf,
    upload_pdf,
    delete_pdf,
)


def authenticate_user() -> Optional[Credentials]:
    """Handles the Google OAuth flow within Streamlit.

    Checks session state, local files, and URL query parameters for valid
    authentication.

    Returns:
        Credentials if authenticated, else None.
    """
    # 1. Check if we already have valid credentials in session state
    if "credentials" in st.session_state:
        return st.session_state.credentials

    # 2. Check if we have credentials saved locally
    creds: Optional[Credentials] = load_credentials_from_file()
    if creds:
        st.session_state.credentials = creds
        return creds

    # 3. Check if we are returning from Google Auth with a code in the URL
    code: Optional[str] = st.query_params.get("code")
    state: Optional[str] = st.query_params.get("state")

    if code and state:
        try:
            # Retrieve the exact Flow object that generated the authorization URL
            flow = OAUTH_FLOWS.pop(state, None)

            if not flow:
                st.error("Authentication session lost. Please try logging in again.")
                st.query_params.clear()
                return None

            flow.fetch_token(code=code)
            creds = flow.credentials

            # Save for future use locally and in session
            save_credentials(creds)
            st.session_state.credentials = creds

            # Clean up the URL
            st.query_params.pop("code", None)
            st.query_params.pop("state", None)

            return creds
        except Exception as e:
            st.error(f"Failed to authenticate: {e}")
            return None

    return None


@st.dialog("Overwrite existing files?")
def bulk_overwrite_dialog(
    creds: Credentials,
    folder_id: str,
    conflicts: list[tuple[Any, str, list[str]]],
) -> None:
    """Displays a dialog to overwrite multiple existing Google Drive PDFs.

    Args:
        creds: The authenticated Google credentials.
        folder_id: The Google Drive folder ID to upload to.
        conflicts: A list of tuples containing (UploadedFile, safe_name, existing_ids).
    """
    st.write("The following files already exist in your Google Drive library:")
    for _, name, _ in conflicts:
        st.write(f"- {name}")
    st.write("Do you want to overwrite all of them?")
    if st.button("Yes, overwrite all"):
        for uf, name, existing_ids in conflicts:
            try:
                with st.spinner(f"Deleting old versions of {name}..."):
                    for file_id in existing_ids:
                        delete_pdf(creds, file_id)
                        shutil.rmtree(
                            st.session_state.papers_dir / file_id, ignore_errors=True
                        )
                with st.spinner(f"Uploading new version of {name}..."):
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".pdf"
                    ) as tmp_file:
                        tmp_file.write(uf.getbuffer())
                        temp_file_path = Path(tmp_file.name)
                    try:
                        upload_pdf(creds, folder_id, temp_file_path, display_name=name)
                    finally:
                        temp_file_path.unlink(missing_ok=True)
            except Exception as e:
                st.error(f"Failed to overwrite {name}: {e}")

        st.session_state.drive_pdfs = list_pdfs_in_library(creds, folder_id)
        st.session_state.pending_conflicts = None
        st.session_state.uploader_key += 1
        st.success("Overwritten successfully!")
        st.rerun()
    if st.button("Cancel"):
        st.session_state.pending_conflicts = None
        st.session_state.uploader_key += 1
        st.rerun()


def main() -> None:
    """Main function to run the Streamlit app."""
    st.set_page_config(page_title="Open Paper Shelf", page_icon="📚", layout="wide")

    creds: Optional[Credentials] = authenticate_user()

    if not creds:
        st.title("Open Paper Shelf")
        st.write("Welcome to your Google Drive-backed paper library!")
        st.info("Please connect your Google account to continue.")
        try:
            flow = get_oauth_flow()
            # Generate the URL the user will click to authenticate
            auth_url, state = flow.authorization_url(
                access_type="offline", prompt="consent"
            )

            # Save the flow so we have the PKCE code_verifier when they return
            add_oauth_flow(state, flow)

            st.link_button("Connect with Google", auth_url)
        except FileNotFoundError as e:
            st.error(str(e))
        return

    # --- Initialization / Syncing ---
    if "folder_id" not in st.session_state:
        with st.spinner("Initializing library and syncing papers..."):
            st.session_state.folder_id = get_or_create_library_folder(creds)

            # Sync files
            pdfs = list_pdfs_in_library(creds, st.session_state.folder_id)
            st.session_state.drive_pdfs = pdfs

            # Create local papers dir
            PAPERS_DIR.mkdir(exist_ok=True)
            st.session_state.papers_dir = PAPERS_DIR

            # Do NOT download PDFs synchronously on load, to prevent blocking UI.

    papers_dir = st.session_state.papers_dir
    folder_id = st.session_state.folder_id

    # Check for pending upload conflicts
    if st.session_state.get("pending_conflicts"):
        bulk_overwrite_dialog(creds, folder_id, st.session_state.pending_conflicts)

    # --- UI Layout ---
    left_col, right_col = st.columns([1, 3])

    with left_col.container(border=True, height=800):
        st.subheader("Papers")

        # State initialization
        if "uploader_key" not in st.session_state:
            st.session_state.uploader_key = 0

        if "selected_paper" not in st.session_state:
            st.session_state.selected_paper = None

        sorted_pdfs = sorted(
            st.session_state.drive_pdfs, key=lambda x: x["name"].lower()
        )
        checked_papers = [
            pdf
            for pdf in sorted_pdfs
            if st.session_state.get(f"chk_{pdf['id']}", False)
        ]

        # Upload area
        uploaded_files = st.file_uploader(
            "Upload PDF(s)",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"uploader_{st.session_state.uploader_key}",
        )
        if uploaded_files:
            conflicts = []
            new_uploads = []
            for uf in uploaded_files:
                safe_name = get_safe_filename(uf.name)
                existing_ids = [
                    p["id"]
                    for p in st.session_state.drive_pdfs
                    if p["name"] == safe_name
                ]
                if existing_ids:
                    conflicts.append((uf, safe_name, existing_ids))
                else:
                    new_uploads.append((uf, safe_name))

            # Process non-conflicting files immediately
            if new_uploads:
                for uf, safe_name in new_uploads:
                    try:
                        with st.spinner(f"Uploading {safe_name}..."):
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix=".pdf"
                            ) as tmp_file:
                                tmp_file.write(uf.getbuffer())
                                temp_file_path = Path(tmp_file.name)
                            try:
                                upload_pdf(
                                    creds,
                                    folder_id,
                                    temp_file_path,
                                    display_name=safe_name,
                                )
                            finally:
                                temp_file_path.unlink(missing_ok=True)
                    except Exception as e:
                        st.error(f"Failed to upload {safe_name}: {e}")

            if conflicts:
                st.session_state.pending_conflicts = conflicts
                st.rerun()
            else:
                st.session_state.drive_pdfs = list_pdfs_in_library(creds, folder_id)
                st.session_state.uploader_key += 1
                st.success("Uploaded successfully!")
                st.rerun()

        # Icon bar
        icon_cols = st.columns([1.5, 8.5])
        with icon_cols[0]:
            if st.button(
                "🗑️",
                help="Delete selected papers",
                disabled=not bool(checked_papers),
                type="primary" if checked_papers else "secondary",
            ):
                for pdf in checked_papers:
                    try:
                        with st.spinner(f"Deleting {pdf['name']}..."):
                            delete_pdf(creds, pdf["id"])
                            shutil.rmtree(
                                st.session_state.papers_dir / pdf["id"],
                                ignore_errors=True,
                            )
                        # Reset the checkbox state
                        st.session_state[f"chk_{pdf['id']}"] = False
                        if st.session_state.selected_paper == pdf["id"]:
                            st.session_state.selected_paper = None
                    except Exception as e:
                        st.error(f"Failed to delete {pdf['name']}: {e}")

                st.session_state.drive_pdfs = list_pdfs_in_library(creds, folder_id)
                st.success("Deleted successfully!")
                st.rerun()

        # Search box
        search_query = st_keyup(
            "Search",
            placeholder="Search by title...",
            label_visibility="collapsed",
            debounce=200,
        )
        search_query = search_query or ""

        # List PDFs
        filtered_pdfs = [
            p for p in sorted_pdfs if search_query.lower() in p["name"].lower()
        ]

        if filtered_pdfs:
            for pdf in filtered_pdfs:
                name = pdf["name"]
                pdf_id = pdf["id"]
                display_name = name[:-4] if name.lower().endswith(".pdf") else name
                col1, col2 = st.columns([0.15, 0.85])
                with col1:
                    st.checkbox(
                        "Select", key=f"chk_{pdf_id}", label_visibility="collapsed"
                    )
                with col2:
                    is_selected = st.session_state.selected_paper == pdf_id
                    if st.button(
                        display_name,
                        key=f"btn_{pdf_id}",
                        use_container_width=True,
                        type="secondary" if is_selected else "tertiary",
                    ):
                        st.session_state.selected_paper = pdf_id
                        st.rerun()
        else:
            st.write("No papers found.")

        selected_paper = st.session_state.selected_paper

    with right_col.container(border=True, height=800):
        if selected_paper:
            # Find the selected paper by ID
            selected_pdf = next(
                (p for p in st.session_state.drive_pdfs if p["id"] == selected_paper),
                None,
            )
            if selected_pdf:
                safe_paper_name = get_safe_filename(selected_pdf["name"])
                paper_folder = papers_dir / selected_pdf["id"]
                local_pdf_path = paper_folder / safe_paper_name

                if not local_pdf_path.exists():
                    paper_folder.mkdir(exist_ok=True)
                    try:
                        with st.spinner(f"Downloading {safe_paper_name} from Drive..."):
                            download_pdf(creds, selected_pdf["id"], local_pdf_path)
                    except Exception as e:
                        st.error(f"Failed to download {safe_paper_name}: {e}")

                if local_pdf_path.exists():
                    base_url = os.environ.get("FASTAPI_URL", "http://localhost:8000")
                    fastapi_url = f"{base_url.rstrip('/')}/papers/{selected_pdf['id']}/{urllib.parse.quote(safe_paper_name)}"
                    pdf_display = f'<iframe src="{fastapi_url}" width="100%" height="750" style="border:none;" type="application/pdf"></iframe>'
                    st.markdown(pdf_display, unsafe_allow_html=True)
            else:
                st.error("Selected paper not found in library data.")
        else:
            st.info(
                "Select a paper from the list to view it here.",
                icon=":material/arrow_back:",
            )


if __name__ == "__main__":
    main()

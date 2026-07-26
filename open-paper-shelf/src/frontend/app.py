"""Main Streamlit application for Open Paper Shelf."""

import os
import sys
import urllib.parse
import tempfile
from pathlib import Path
from typing import Optional

import streamlit as st
from google.oauth2.credentials import Credentials

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


@st.dialog("Overwrite existing file?")
def overwrite_dialog(
    creds: Credentials,
    folder_id: str,
    file_path: Path,
    display_name: str,
    existing_ids: list[str],
) -> None:
    st.write(f"The file {display_name} already exists in your Google Drive library.")
    st.write("Do you want to overwrite it?")
    if st.button("Yes, overwrite it"):
        with st.spinner("Deleting old version(s)..."):
            for file_id in existing_ids:
                delete_pdf(creds, file_id)
        with st.spinner("Uploading new version..."):
            upload_pdf(creds, folder_id, file_path, display_name=display_name)
            st.session_state.drive_pdfs = list_pdfs_in_library(creds, folder_id)
            st.session_state.uploader_key += 1
            st.success("Overwritten successfully!")
            file_path.unlink(missing_ok=True)
            st.rerun()
    if st.button("Cancel"):
        st.session_state.uploader_key += 1
        file_path.unlink(missing_ok=True)
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

    # --- UI Layout ---
    left_col, right_col = st.columns([1, 3])

    with left_col.container(border=True, height=800):
        st.subheader("Papers")

        # Upload new paper
        if "uploader_key" not in st.session_state:
            st.session_state.uploader_key = 0

        with st.popover("Upload PDF"):
            uploaded_file = st.file_uploader(
                "Choose a PDF",
                type=["pdf"],
                label_visibility="collapsed",
                key=f"uploader_{st.session_state.uploader_key}",
            )
            if uploaded_file is not None:
                safe_name = Path(uploaded_file.name).name

                # Store the buffer in a temporary file outside of papers_dir
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".pdf"
                ) as tmp_file:
                    tmp_file.write(uploaded_file.getbuffer())
                    temp_file_path = Path(tmp_file.name)

                # Check if file with same name exists in drive
                existing_ids = [
                    p["id"]
                    for p in st.session_state.drive_pdfs
                    if p["name"] == safe_name
                ]

                if existing_ids:
                    overwrite_dialog(
                        creds, folder_id, temp_file_path, safe_name, existing_ids
                    )
                else:
                    with st.spinner(f"Uploading {safe_name}..."):
                        upload_pdf(
                            creds, folder_id, temp_file_path, display_name=safe_name
                        )
                        st.success("Uploaded!")
                        # Refresh list and clear uploader
                        st.session_state.drive_pdfs = list_pdfs_in_library(
                            creds, folder_id
                        )
                        st.session_state.uploader_key += 1
                        temp_file_path.unlink(missing_ok=True)
                        st.rerun()

        # Search box
        search_query = st.text_input(
            "Search", placeholder="Search by title...", label_visibility="collapsed"
        )

        # List PDFs
        pdf_names = [p["name"] for p in st.session_state.drive_pdfs]
        filtered_names = [n for n in pdf_names if search_query.lower() in n.lower()]

        if filtered_names:
            selected_paper = st.radio(
                "Select paper", filtered_names, label_visibility="collapsed"
            )
        else:
            st.write("No papers found.")
            selected_paper = None

    with right_col.container(border=True, height=800):
        if selected_paper:
            # Find the ID of the selected paper
            selected_pdf = next(
                (p for p in st.session_state.drive_pdfs if p["name"] == selected_paper),
                None,
            )
            if selected_pdf:
                safe_paper_name = Path(selected_paper).name
                paper_folder = papers_dir / selected_pdf["id"]
                local_pdf_path = paper_folder / safe_paper_name

                if not local_pdf_path.exists():
                    paper_folder.mkdir(exist_ok=True)
                    with st.spinner(f"Downloading {safe_paper_name} from Drive..."):
                        download_pdf(creds, selected_pdf["id"], local_pdf_path)

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

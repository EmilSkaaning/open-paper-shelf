"""Main Streamlit application for Open Paper Shelf."""

import sys
from pathlib import Path
from typing import Optional

import streamlit as st
from google.oauth2.credentials import Credentials

# Ensure the src directory is in the path to import backend
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.append(str(src_path))

from backend.drive import (  # noqa: E402
    get_oauth_flow,
    load_credentials_from_file,
    save_credentials,
    get_or_create_library_folder,
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
    if code:
        try:
            flow = get_oauth_flow()
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Save for future use locally and in session
            save_credentials(creds)
            st.session_state.credentials = creds

            # Clean up the URL by removing the code parameter
            st.query_params.clear()

            return creds
        except Exception as e:
            st.error(f"Failed to authenticate: {e}")
            return None

    return None


def main() -> None:
    """Main function to run the Streamlit app."""
    st.set_page_config(page_title="Open Paper Shelf", page_icon="📚")
    st.title("Open Paper Shelf")
    st.write("Welcome to your Google Drive-backed paper library!")

    creds: Optional[Credentials] = authenticate_user()

    if not creds:
        st.info("Please connect your Google account to continue.")
        try:
            flow = get_oauth_flow()
            # Generate the URL the user will click to authenticate
            auth_url, _ = flow.authorization_url(prompt="consent")
            st.link_button("Connect with Google", auth_url)
        except FileNotFoundError as e:
            st.error(str(e))
        return

    st.success("Successfully connected to Google Drive!")

    if st.button("Initialize / Check Library Folder"):
        with st.spinner("Checking for folder..."):
            try:
                folder_id: str = get_or_create_library_folder(creds)
                st.success(f"Library folder is ready! Folder ID: {folder_id}")
            except Exception as e:
                st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()

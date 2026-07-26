"""Module for interacting with Google Drive.

This module provides functionality to authenticate with Google Drive
using OAuth 2.0 Web Flow and to ensure the library folder exists.
"""

from pathlib import Path
from typing import Optional, List, Dict, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io

# Dictionary to cache Flow objects across Streamlit reruns.
# Keys are the OAuth 'state' strings.
OAUTH_FLOWS: Dict[str, Flow] = {}
MAX_OAUTH_FLOWS: int = 100

# Scopes needed for Google Drive API
SCOPES: List[str] = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME: str = "open-paper-shelf-lib"
FOLDER_MIME_TYPE: str = "application/vnd.google-apps.folder"

# Streamlit's default port for local development
REDIRECT_URI: str = "http://localhost:8501/"

# Resolve project root to reliably find credentials and tokens
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"


def add_oauth_flow(state: str, flow: Flow) -> None:
    """Adds an OAuth flow to the cache, evicting the oldest if at capacity.

    Args:
        state (str): The OAuth state string.
        flow (Flow): The configured OAuth 2.0 flow.
    """
    if len(OAUTH_FLOWS) >= MAX_OAUTH_FLOWS:
        # Remove the oldest entry (Python 3.7+ dicts preserve insertion order)
        OAUTH_FLOWS.pop(next(iter(OAUTH_FLOWS)))
    OAUTH_FLOWS[state] = flow


def get_oauth_flow() -> Flow:
    """Gets the Google OAuth 2.0 flow for web applications.

    Returns:
        Flow: The configured OAuth 2.0 flow.

    Raises:
        FileNotFoundError: If the credentials.json file is not found.
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            "credentials.json not found. Please download it from "
            "Google Cloud Console (Web application type) and place it in the project root. "
            "Ensure the authorized redirect URI includes 'http://localhost:8501/'."
        )
    return Flow.from_client_secrets_file(
        str(CREDENTIALS_PATH), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )


def load_credentials_from_file() -> Optional[Credentials]:
    """Attempts to load and refresh credentials from a local token.json file.

    Returns:
        Optional[Credentials]: Valid credentials if available, otherwise None.
    """
    creds: Optional[Credentials] = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())
        else:
            creds = None

    return creds


def save_credentials(creds: Credentials) -> None:
    """Saves the credentials to token.json for future use.

    Args:
        creds: The authenticated Google credentials to save.
    """
    with open(TOKEN_PATH, "w") as token:
        token.write(creds.to_json())


def get_or_create_library_folder(creds: Credentials) -> str:
    """Checks for the library folder in Google Drive and creates it if missing.

    Args:
        creds: The authenticated Google credentials.

    Returns:
        str: The Google Drive folder ID of the library folder.
    """
    service: Any = build("drive", "v3", credentials=creds)

    query: str = f"name = '{FOLDER_NAME}' and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
    results: Dict[str, Any] = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    items: List[Dict[str, str]] = results.get("files", [])

    if not items:
        folder_metadata: Dict[str, str] = {
            "name": FOLDER_NAME,
            "mimeType": FOLDER_MIME_TYPE,
        }
        folder: Dict[str, Any] = (
            service.files().create(body=folder_metadata, fields="id").execute()
        )
        return str(folder.get("id"))
    else:
        return str(items[0].get("id"))


def list_pdfs_in_library(creds: Credentials, folder_id: str) -> List[Dict[str, str]]:
    """Lists all PDF files in the specified Google Drive folder.

    Args:
        creds: The authenticated Google credentials.
        folder_id: The ID of the Google Drive folder.

    Returns:
        A list of dictionaries, each containing 'id' and 'name' of a PDF file.
    """
    service: Any = build("drive", "v3", credentials=creds)
    query: str = (
        f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    )
    results: Dict[str, Any] = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    return results.get("files", [])


def download_pdf(creds: Credentials, file_id: str, dest_path: Path) -> None:
    """Downloads a PDF file from Google Drive to the local filesystem.

    Args:
        creds: The authenticated Google credentials.
        file_id: The Google Drive file ID.
        dest_path: The local path where the PDF will be saved.
    """
    service: Any = build("drive", "v3", credentials=creds)
    request: Any = service.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done: bool = False
        while not done:
            status, done = downloader.next_chunk()


def upload_pdf(creds: Credentials, folder_id: str, file_path: Path) -> str:
    """Uploads a local PDF file to the specified Google Drive folder.

    Args:
        creds: The authenticated Google credentials.
        folder_id: The ID of the Google Drive folder.
        file_path: The local path to the PDF file.

    Returns:
        The Google Drive file ID of the newly uploaded file.
    """
    service: Any = build("drive", "v3", credentials=creds)
    file_metadata: Dict[str, Any] = {"name": file_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(file_path), mimetype="application/pdf", resumable=True)
    file: Dict[str, Any] = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id")
        .execute()
    )
    return str(file.get("id"))

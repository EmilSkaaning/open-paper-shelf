from typing import Optional, List, Dict, Any
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pydantic import ValidationError
import json
import tempfile
import uuid

from backend.models import LibraryIndex

# googleapiclient.discovery.build() returns a dynamically-generated Resource
# object with no static type; the client library builds its API surface at
# runtime from Google's service discovery documents.
DriveService = Any
# The Drive API's request/response JSON bodies (e.g. file/folder metadata)
# are loosely-typed by the API itself, so no narrower static type applies.
DriveMetadata = Dict[str, Any]

SCOPES: List[str] = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME: str = "open-paper-shelf-lib"
FOLDER_MIME_TYPE: str = "application/vnd.google-apps.folder"
REDIRECT_URI: str = "http://localhost:8501/"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"
PAPERS_DIR = PROJECT_ROOT / "papers"

# Caches Flow objects across the OAuth redirect, keyed by the 'state' string.
# A module-level global (rather than st.session_state) because Streamlit's
# session state is not guaranteed to survive the cross-domain redirect to and
# from Google's consent screen.
OAUTH_FLOWS: Dict[str, Flow] = {}
MAX_OAUTH_FLOWS: int = 100


def add_oauth_flow(state: str, flow: Flow) -> None:
    """Caches an OAuth flow keyed by its state string, evicting the oldest if full.

    Args:
        state: The OAuth state string returned by flow.authorization_url().
        flow: The Flow object to cache.
    """
    if len(OAUTH_FLOWS) >= MAX_OAUTH_FLOWS:
        try:
            OAUTH_FLOWS.pop(next(iter(OAUTH_FLOWS)))
        except (StopIteration, KeyError):
            # Another thread already evicted the same (or the only
            # remaining) entry between our capacity check and this pop.
            pass
    OAUTH_FLOWS[state] = flow


def get_oauth_flow() -> Flow:
    """Builds a new Google OAuth Flow from the local client secrets file.

    Returns:
        Flow: A new OAuth Flow configured with this app's scopes and redirect URI.

    Raises:
        FileNotFoundError: If credentials.json does not exist at CREDENTIALS_PATH.
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError("credentials.json not found.")
    return Flow.from_client_secrets_file(
        str(CREDENTIALS_PATH), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )


def load_credentials_from_file() -> Optional[Credentials]:
    """Loads and, if needed, refreshes cached OAuth credentials from disk.

    Returns:
        Optional[Credentials]: Valid credentials loaded from TOKEN_PATH, or None
        if no token file exists or the token is invalid and cannot be refreshed.
    """
    creds: Optional[Credentials] = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())
        else:
            creds = None
    return creds


def save_credentials(creds: Credentials) -> None:
    """Persists OAuth credentials to the local token file.

    Args:
        creds (Credentials): The Google OAuth credentials to save.
    """
    with open(TOKEN_PATH, "w") as token:
        token.write(creds.to_json())


def _escape_drive_query_value(value: str) -> str:
    """Escapes a string for safe interpolation into a Drive API query clause.

    Per the Drive API's search query syntax, both backslashes and single
    quotes must be backslash-escaped. Backslashes are escaped first so that
    the escaping added for a quote is not itself re-escaped.

    Args:
        value (str): The raw string to embed in a `name = '...'` clause.

    Returns:
        str: The value with backslashes and single quotes escaped.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _get_or_create_folder(
    service: DriveService, name: str, parent_id: Optional[str] = None
) -> str:
    """Gets an existing Google Drive folder ID or creates a new folder.

    Args:
        service (DriveService): The Google Drive API v3 resource service.
        name (str): The name of the folder to find or create.
        parent_id (Optional[str], optional): The ID of the parent folder. Defaults to None.

    Returns:
        str: The Google Drive file ID of the folder.
    """
    escaped_name = _escape_drive_query_value(name)
    query = f"name = '{escaped_name}' and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = (
        service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    )
    items = results.get("files", [])
    if not items:
        folder_metadata: DriveMetadata = {"name": name, "mimeType": FOLDER_MIME_TYPE}
        if parent_id:
            folder_metadata["parents"] = [parent_id]
        folder = service.files().create(body=folder_metadata, fields="id").execute()
        return str(folder.get("id"))
    return str(items[0].get("id"))


def get_or_create_root_folder(creds: Credentials) -> str:
    """Gets or creates this app's root library folder in the user's Google Drive.

    Args:
        creds (Credentials): The Google OAuth credentials.

    Returns:
        str: The Google Drive file ID of the root folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    return _get_or_create_folder(service, FOLDER_NAME)


def list_libraries(creds: Credentials, root_id: str) -> List[Dict[str, str]]:
    """Lists the library folders directly under the root folder.

    Args:
        creds (Credentials): The Google OAuth credentials.
        root_id (str): The Google Drive file ID of the root folder.

    Returns:
        List[Dict[str, str]]: The `id`/`name` metadata of each library folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    query = f"'{root_id}' in parents and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
    libraries: List[Dict[str, str]] = []
    page_token = None
    while True:
        results = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
            )
            .execute()
        )
        libraries.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return libraries


def create_library(creds: Credentials, root_id: str, lib_name: str) -> Dict[str, str]:
    """Creates a new library folder (with a nested papers folder) under root_id.

    A short random suffix is appended to lib_name to avoid collisions.

    Args:
        creds (Credentials): The Google OAuth credentials.
        root_id (str): The Google Drive file ID of the root folder.
        lib_name (str): The desired display name for the new library.

    Returns:
        Dict[str, str]: A mapping with keys `lib_id`, `lib_name` (the unique
        name actually used), and `papers_id`.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    unique_name = f"{lib_name}_{uuid.uuid4().hex[:8]}"
    lib_id = _get_or_create_folder(service, unique_name, root_id)
    papers_id = _get_or_create_folder(service, "papers", lib_id)
    return {"lib_id": lib_id, "lib_name": unique_name, "papers_id": papers_id}


def get_papers_folder(creds: Credentials, lib_id: str) -> str:
    """Gets or creates the papers folder nested inside a library folder.

    Args:
        creds (Credentials): The Google OAuth credentials.
        lib_id (str): The Google Drive file ID of the library folder.

    Returns:
        str: The Google Drive file ID of the papers folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    return _get_or_create_folder(service, "papers", lib_id)


def get_library_index_file(
    creds: Credentials, papers_folder_id: str
) -> Optional[DriveMetadata]:
    """Looks up the id-mapping.json index file inside a papers folder.

    Args:
        creds (Credentials): The Google OAuth credentials.
        papers_folder_id (str): The Google Drive file ID of the papers folder.

    Returns:
        Optional[DriveMetadata]: The `id`/`modifiedTime` metadata of the index
        file, or None if no index file exists yet.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    query = f"name = 'id-mapping.json' and '{papers_folder_id}' in parents and trashed = false"
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, modifiedTime)")
        .execute()
    )
    items = results.get("files", [])
    if items:
        return items[0]
    return None


def upload_library_index(
    creds: Credentials,
    papers_folder_id: str,
    index: LibraryIndex,
    deleted_pids: set[str] | None = None,
) -> None:
    """Uploads the library index file (id-mapping.json) to Google Drive.

    Args:
        creds (Credentials): Google OAuth credentials.
        papers_folder_id (str): The Google Drive folder ID where the index should be uploaded.
        index (LibraryIndex): The library index data model to serialize and upload.
        deleted_pids (set[str] | None): A set of paper IDs that were deleted locally and should not be merged back from the remote index.

    Returns:
        None

    Raises:
        HttpError: If fetching the existing remote index fails for a reason
            other than the file not existing (HTTP 404).
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    file_info = get_library_index_file(creds, papers_folder_id)

    if file_info:
        try:
            request = service.files().get_media(fileId=file_info["id"])
            remote_bytes = request.execute()
            remote_data = json.loads(remote_bytes.decode("utf-8"))
            remote_index = LibraryIndex(**remote_data)
            pids_to_ignore = deleted_pids or set()
            for pid, p in remote_index.papers.items():
                if pid not in index.papers and pid not in pids_to_ignore:
                    index.papers[pid] = p
        except HttpError as e:
            if e.resp.status != 404:
                raise
        except (json.JSONDecodeError, ValidationError):
            pass

    with tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".json", encoding="utf-8"
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(index.model_dump_json(indent=2))

    try:
        media = MediaFileUpload(
            str(tmp_path), mimetype="application/json", resumable=True
        )
        if file_info:
            service.files().update(fileId=file_info["id"], media_body=media).execute()
        else:
            file_metadata = {"name": "id-mapping.json", "parents": [papers_folder_id]}
            service.files().create(
                body=file_metadata, media_body=media, fields="id"
            ).execute()
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def create_paper_folder(
    creds: Credentials, papers_folder_id: str, paper_id: str
) -> str:
    """Gets or creates a per-paper folder nested inside the papers folder.

    Args:
        creds (Credentials): The Google OAuth credentials.
        papers_folder_id (str): The Google Drive file ID of the papers folder.
        paper_id (str): The unique ID of the paper, used as the folder name.

    Returns:
        str: The Google Drive file ID of the paper's folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    return _get_or_create_folder(service, paper_id, papers_folder_id)


def upload_file_to_folder(
    creds: Credentials, folder_id: str, file_path: Path, filename: str, mime_type: str
) -> str:
    """Uploads a file to a specific Google Drive folder.

    If a file with the given name already exists in the folder, it is updated.

    Args:
        creds (Credentials): The Google OAuth credentials.
        folder_id (str): The ID of the parent folder in Google Drive.
        file_path (Path): The local path of the file to upload.
        filename (str): The destination name for the file in Google Drive.
        mime_type (str): The MIME type of the uploaded file.

    Returns:
        str: The Google Drive file ID of the uploaded file.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    escaped_filename = _escape_drive_query_value(filename)
    query = (
        f"name = '{escaped_filename}' and '{folder_id}' in parents and trashed = false"
    )
    existing = (
        service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    )
    files = existing.get("files", [])
    media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)

    if files:
        file_id = str(files[0]["id"])
        service.files().update(fileId=file_id, media_body=media).execute()
        return file_id
    else:
        file_metadata = {"name": filename, "parents": [folder_id]}
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        return str(file.get("id"))


def download_file(creds: Credentials, file_id: str, dest_path: Path) -> None:
    """Downloads a Google Drive file to a local path atomically.

    Streams the file to a temporary file in dest_path's parent directory and
    then renames it into place, so dest_path is never left partially written.

    Args:
        creds (Credentials): The Google OAuth credentials.
        file_id (str): The Google Drive file ID to download.
        dest_path (Path): The local destination path for the downloaded file.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    request = service.files().get_media(fileId=file_id)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest_path.parent, delete=False
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            downloader = MediaIoBaseDownload(tmp_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        tmp_path.replace(dest_path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def delete_paper_folder(creds: Credentials, folder_id: str) -> None:
    """Deletes a paper's folder (and its contents) from Google Drive.

    Args:
        creds (Credentials): The Google OAuth credentials.
        folder_id (str): The Google Drive file ID of the paper's folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    service.files().delete(fileId=folder_id).execute()

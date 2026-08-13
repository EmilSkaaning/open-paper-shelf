from typing import Callable, NamedTuple, Optional, List, Dict, Any
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pydantic import ValidationError
import json
import logging
import tempfile
import time
import uuid

from backend.models import LibraryIndex
from backend.oauth_client import get_client_config

logger = logging.getLogger(__name__)

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
    """Builds a new Google OAuth Flow from the resolved OAuth client config.

    Uses a self-hoster's override (env vars or a local credentials.json) if
    present, otherwise falls back to the OAuth client bundled with the app.

    Returns:
        Flow: A new OAuth Flow configured with this app's scopes and redirect URI.
    """
    return Flow.from_client_config(
        get_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
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


def _list_children(service: DriveService, folder_id: str) -> List[DriveMetadata]:
    """Lists the direct, non-trashed children of a Drive folder.

    Args:
        service (DriveService): An authenticated Drive API client.
        folder_id (str): The Google Drive file ID of the parent folder.

    Returns:
        List[DriveMetadata]: The `id`/`mimeType` metadata of each child.
    """
    query = f"'{folder_id}' in parents and trashed = false"
    children: List[DriveMetadata] = []
    page_token = None
    while True:
        results = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, mimeType)",
                pageToken=page_token,
            )
            .execute()
        )
        children.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return children


def _collect_ids_recursive(service: DriveService, folder_id: str) -> List[str]:
    """Collects a folder's own ID plus every descendant's ID, depth-first.

    Args:
        service (DriveService): An authenticated Drive API client.
        folder_id (str): The Google Drive file ID of the folder to walk.

    Returns:
        List[str]: Descendant file IDs first, followed by `folder_id` last.
    """
    ids: List[str] = []
    for child in _list_children(service, folder_id):
        if child.get("mimeType") == FOLDER_MIME_TYPE:
            ids.extend(_collect_ids_recursive(service, child["id"]))
        else:
            ids.append(child["id"])
    ids.append(folder_id)
    return ids


# Drive's batch endpoint accepts at most 100 sub-requests per HTTP call.
DRIVE_BATCH_CHUNK_SIZE = 100

# Drive's API is documented to return transient 5xx errors under normal
# operation, which should be retried rather than treated as fatal.
_TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
DRIVE_BATCH_TRASH_MAX_RETRIES: int = 3
DRIVE_BATCH_TRASH_RETRY_DELAY_SECONDS: float = 2.0


class DriveTransientError(Exception):
    """Raised when trashing Drive files keeps failing with transient (5xx)
    errors after all retries are exhausted, so callers can show a
    user-friendly message instead of a raw HttpError traceback."""


def _is_transient_http_error(exc: HttpError) -> bool:
    """Returns whether `exc` looks like a transient Drive failure worth
    retrying (a 5xx status), as opposed to a permanent one (e.g. a 403
    lack of authorization) that retrying can't fix.

    Args:
        exc (HttpError): The per-item error returned by a batch request.

    Returns:
        bool: True if `exc`'s HTTP status is one of the retryable 5xx codes.
    """
    return exc.resp.status in _TRANSIENT_HTTP_STATUSES


def _batch_trash(
    service: DriveService,
    file_ids: List[str],
    max_retries: int = DRIVE_BATCH_TRASH_MAX_RETRIES,
    delay_seconds: float = DRIVE_BATCH_TRASH_RETRY_DELAY_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Trashes a list of Drive file/folder IDs via batched HTTP requests.

    Batching turns what would otherwise be one HTTP round-trip per file
    into `ceil(len(file_ids) / DRIVE_BATCH_CHUNK_SIZE)` round-trips, which
    matters for libraries with many papers. Items that fail with a
    transient (5xx) error are retried with a fixed delay between attempts;
    items that fail with a non-transient error (e.g. a 403 lack of
    authorization) are surfaced immediately without retrying.

    Args:
        service (DriveService): An authenticated Drive API client.
        file_ids (List[str]): The Google Drive file IDs to trash.
        max_retries (int): Maximum attempts for a batch of file IDs still
            failing with transient errors before giving up.
        delay_seconds (float): Delay passed to `sleep_fn` between retries.
        sleep_fn (Callable[[float], None]): Called between retries;
            injected so tests never sleep for real.

    Raises:
        HttpError: Re-raises the first non-transient per-item error
            encountered, if any item in `file_ids` failed for a reason
            that retrying can't fix.
        DriveTransientError: If items are still failing with transient
            errors after `max_retries` attempts.
    """
    pending_ids = file_ids
    last_errors: Dict[str, HttpError] = {}

    for attempt in range(max_retries):
        errors: Dict[str, HttpError] = {}

        def _record_error(
            request_id: str, _response: DriveMetadata, exception: Optional[HttpError]
        ) -> None:
            if exception is not None:
                errors[request_id] = exception

        for start in range(0, len(pending_ids), DRIVE_BATCH_CHUNK_SIZE):
            batch = service.new_batch_http_request(callback=_record_error)
            for file_id in pending_ids[start : start + DRIVE_BATCH_CHUNK_SIZE]:
                batch.add(
                    service.files().update(fileId=file_id, body={"trashed": True}),
                    request_id=file_id,
                )
            batch.execute()

        if not errors:
            return

        if not all(_is_transient_http_error(exc) for exc in errors.values()):
            raise next(
                exc for exc in errors.values() if not _is_transient_http_error(exc)
            )

        last_errors = errors
        pending_ids = list(errors.keys())
        if attempt < max_retries - 1:
            sleep_fn(delay_seconds)

    raise DriveTransientError(
        f"Google Drive is temporarily unavailable; failed to trash "
        f"{len(pending_ids)} item(s) after {max_retries} attempts."
    ) from next(iter(last_errors.values()))


def delete_library(creds: Credentials, lib_id: str) -> None:
    """Trashes a library's folder (and its contents) in Google Drive.

    Walks the folder tree to collect every descendant's ID, then trashes
    them all (plus the folder itself) via batched HTTP requests rather
    than a single top-level call: a plain trash/delete on a folder is
    rejected by Drive with a 403 `appNotAuthorizedToChild` error unless
    the app's `drive.file` scope covers every nested child (e.g. items
    added to the folder outside the app), so each item is trashed
    individually to surface only genuinely inaccessible items as failures.

    Args:
        creds (Credentials): The Google OAuth credentials.
        lib_id (str): The Google Drive file ID of the library folder.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    file_ids = _collect_ids_recursive(service, lib_id)
    _batch_trash(service, file_ids)


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


def _merge_remote_papers(
    service: DriveService,
    file_info: DriveMetadata,
    index: LibraryIndex,
    deleted_pids: set[str] | None,
    own_pid_updates: set[str] | None = None,
) -> None:
    """Merges remote-only papers and backend-owned fields into `index`.

    A paper only present in the remote index (e.g. uploaded from another
    device) is added to `index.papers` in place, unless it's in
    `deleted_pids` (deleted locally in this same operation, so it must not
    be resurrected from the stale remote copy).

    For a paper present in both, `edited_pdf_file_id` is taken from the
    remote copy whenever it differs, unless the paper's pid is in
    `own_pid_updates` - the caller just authoritatively set that field
    locally (e.g. `save_edited_pdf`'s own write, made moments before this
    function fetched a remote copy that doesn't have it yet), so the local
    value must win instead of being reverted to the stale remote one. For
    every other caller (e.g. the frontend saving metadata, whose in-memory
    index never touches this field), the remote copy is the only place a
    concurrent backend autosave's Drive reference could live, so it must
    win. A corrupted or unreadable remote index is logged and skipped
    rather than blocking the upload.

    Args:
        service (DriveService): The Google Drive API v3 resource service.
        file_info (DriveMetadata): The existing remote index file's `id`/
            `modifiedTime` metadata, as returned by `get_library_index_file`.
        index (LibraryIndex): The local library index; entries are added to
            or updated in its `papers` dict in place.
        deleted_pids (set[str] | None): Paper IDs deleted locally that must
            not be merged back from the remote index.
        own_pid_updates (set[str] | None): Paper IDs whose
            `edited_pdf_file_id` the caller just authoritatively set locally
            and which must not be overwritten by the remote copy.

    Raises:
        HttpError: If fetching the remote index fails for a reason other
            than the file not existing (HTTP 404).
    """
    try:
        request = service.files().get_media(fileId=file_info["id"])
        remote_bytes = request.execute()
        remote_data = json.loads(remote_bytes.decode("utf-8"))
        remote_index = LibraryIndex(**remote_data)
        pids_to_ignore = deleted_pids or set()
        pids_owned_locally = own_pid_updates or set()
        for pid, p in remote_index.papers.items():
            if pid in pids_to_ignore:
                continue
            local_entry = index.papers.get(pid)
            if local_entry is None:
                index.papers[pid] = p
            elif (
                pid not in pids_owned_locally
                and p.edited_pdf_file_id != local_entry.edited_pdf_file_id
            ):
                index.papers[pid] = local_entry.model_copy(
                    update={"edited_pdf_file_id": p.edited_pdf_file_id}
                )
    except HttpError as e:
        if e.resp.status != 404:
            raise
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning(
            "Skipping corrupted remote index for file %s: %s", file_info["id"], e
        )


def _write_index_file(
    service: DriveService,
    papers_folder_id: str,
    index: LibraryIndex,
    file_info: DriveMetadata,
) -> None:
    """Serializes `index` to a temp file and uploads it as id-mapping.json.

    Creates the remote file if `file_info` is empty (no existing index),
    otherwise updates the existing file it identifies.

    Args:
        service (DriveService): The Google Drive API v3 resource service.
        papers_folder_id (str): The Google Drive folder ID to create the
            index file in, if it doesn't already exist.
        index (LibraryIndex): The library index data model to serialize.
        file_info (DriveMetadata): The existing remote index file's `id`
            metadata, or empty if no index file exists yet.
    """
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


def upload_library_index(
    creds: Credentials,
    papers_folder_id: str,
    index: LibraryIndex,
    deleted_pids: set[str] | None = None,
    own_pid_updates: set[str] | None = None,
) -> None:
    """Uploads the library index file (id-mapping.json) to Google Drive.

    Args:
        creds (Credentials): Google OAuth credentials.
        papers_folder_id (str): The Google Drive folder ID where the index should be uploaded.
        index (LibraryIndex): The library index data model to serialize and upload.
        deleted_pids (set[str] | None): A set of paper IDs that were deleted locally and should not be merged back from the remote index.
        own_pid_updates (set[str] | None): Paper IDs whose `edited_pdf_file_id`
            the caller just authoritatively set on `index` (e.g. the
            backend's own PDF-autosave write) and which must win over
            whatever the remote copy still has for that field.

    Returns:
        None

    Raises:
        HttpError: If fetching the existing remote index fails for a reason
            other than the file not existing (HTTP 404).
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    file_info = get_library_index_file(creds, papers_folder_id)

    if file_info:
        _merge_remote_papers(service, file_info, index, deleted_pids, own_pid_updates)

    _write_index_file(service, papers_folder_id, index, file_info or {})


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


class BatchFolderResult(NamedTuple):
    """Per-paper outcome of a batched folder-creation call.

    Attributes:
        folder_ids: Maps each paper ID that was created successfully to its
            new Drive folder ID.
        errors: Maps each paper ID that failed to create to the error Drive
            returned for it.
    """

    folder_ids: Dict[str, str]
    errors: Dict[str, Exception]


def create_paper_folders_batch(
    creds: Credentials, papers_folder_id: str, paper_ids: List[str]
) -> BatchFolderResult:
    """Creates multiple per-paper folders via batched HTTP requests.

    Unlike `create_paper_folder`, this always creates a new folder rather
    than getting-or-creating one, since callers use it only for paper IDs
    they just generated (freshly minted UUIDs that cannot already have a
    folder) — skipping the existence check lets the create calls be
    metadata-only and therefore batchable, turning what would otherwise be
    one HTTP round-trip per paper into
    `ceil(len(paper_ids) / DRIVE_BATCH_CHUNK_SIZE)` round-trips.

    A failure creating one paper's folder does not prevent the others in
    the same call from being created; each paper's outcome (its new folder
    ID, or the error Drive returned for it) is reported independently via
    the returned `BatchFolderResult`.

    Args:
        creds (Credentials): The Google OAuth credentials.
        papers_folder_id (str): The Google Drive file ID of the papers
            folder each new folder is created inside.
        paper_ids (List[str]): The unique paper IDs to create folders for,
            used as each folder's name.

    Returns:
        BatchFolderResult: The per-paper folder IDs and errors.
    """
    service: DriveService = build("drive", "v3", credentials=creds)
    folder_ids: Dict[str, str] = {}
    errors: Dict[str, Exception] = {}

    def _record_result(
        request_id: str, response: DriveMetadata, exception: Optional[HttpError]
    ) -> None:
        if exception is not None:
            errors[request_id] = exception
        else:
            folder_ids[request_id] = str(response["id"])

    for start in range(0, len(paper_ids), DRIVE_BATCH_CHUNK_SIZE):
        chunk = paper_ids[start : start + DRIVE_BATCH_CHUNK_SIZE]
        batch = service.new_batch_http_request(callback=_record_result)
        for paper_id in chunk:
            folder_metadata = {
                "name": paper_id,
                "mimeType": FOLDER_MIME_TYPE,
                "parents": [papers_folder_id],
            }
            batch.add(
                service.files().create(body=folder_metadata, fields="id"),
                request_id=paper_id,
            )
        try:
            batch.execute()
        except Exception as e:
            for paper_id in chunk:
                errors.setdefault(paper_id, e)

    return BatchFolderResult(folder_ids=folder_ids, errors=errors)


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

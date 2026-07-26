# Metadata UI Implementation Plan

**Goal:** Implement a scalable metadata storage and UI for PDF papers in Open Paper Shelf, syncing a separate JSON file per paper to Google Drive.

**Architecture:** A new Pydantic model `PaperMetadata` represents the metadata. Google Drive operations fetch, upload, and download `*_meta.json` files alongside PDFs. The Streamlit UI fetches all metadata on startup, caches it locally, and displays it in a side-pane next to the PDF for editing.

**Tech Stack:** Python, FastAPI (Pydantic), Streamlit, Google Drive API, Pytest.

## Global Constraints

- Type Hints: Add or update type hints for any new or modified functions/classes. All variables and functions must be fully type-hinted.
- Docstrings: Google docstring format strictly.
- Unit Test Coverage: 100% unit test coverage for all new and existing backend functions.
- Run tests using `uv run poe test` or `uv run pytest`.
- Format and lint with `uv run ruff format .` and `uv run ruff check .`
- Type checking with `uv run pyrefly check`.
- 1 logical change per commit.

---

### Task 1: Data Model

**Files:**
- Create: `open-paper-shelf/src/backend/models.py`
- Create: `open-paper-shelf/tests/backend/test_models.py`

**Interfaces:**
- Consumes: `pydantic.BaseModel`
- Produces: `PaperMetadata` model class.

- [ ] **Step 1: Write the failing test**

```python
# open-paper-shelf/tests/backend/test_models.py
from backend.models import PaperMetadata

def test_paper_metadata_defaults() -> None:
    """Test the default values of PaperMetadata."""
    meta = PaperMetadata(title="Test Paper")
    assert meta.title == "Test Paper"
    assert meta.tags == []
    assert meta.notes == ""
    assert meta.citation == ""
    assert meta.status == "Unread"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest open-paper-shelf/tests/backend/test_models.py -v`
Expected: FAIL with ModuleNotFoundError or ImportError

- [ ] **Step 3: Write minimal implementation**

```python
# open-paper-shelf/src/backend/models.py
"""Data models for the backend application."""

from typing import List, Literal
from pydantic import BaseModel

class PaperMetadata(BaseModel):
    """Metadata for a paper in the library."""

    title: str
    tags: List[str] = []
    notes: str = ""
    citation: str = ""
    status: Literal["Unread", "Reading", "Read", "TODO"] = "Unread"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest open-paper-shelf/tests/backend/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add open-paper-shelf/src/backend/models.py open-paper-shelf/tests/backend/test_models.py
git commit -m "feat(backend): add PaperMetadata pydantic model"
```

---

### Task 2: Drive Storage Functions

**Files:**
- Modify: `open-paper-shelf/src/backend/drive.py`
- Create: `open-paper-shelf/tests/backend/test_drive_metadata.py`

**Interfaces:**
- Consumes: Google Drive API `build("drive", "v3", ...)`
- Produces:
  - `list_metadata_in_library(creds: Credentials, folder_id: str) -> List[Dict[str, str]]`
  - `download_metadata(creds: Credentials, file_id: str, dest_path: Path) -> None`
  - `upload_metadata(creds: Credentials, folder_id: str, file_path: Path, display_name: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# open-paper-shelf/tests/backend/test_drive_metadata.py
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from backend.drive import list_metadata_in_library, download_metadata, upload_metadata

@pytest.fixture
def mock_creds() -> MagicMock:
    return MagicMock()

@patch("backend.drive.build")
def test_list_metadata_in_library(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    """Test listing metadata files."""
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "meta1", "name": "123_meta.json"}]
    }

    result = list_metadata_in_library(mock_creds, "folder_123")
    assert len(result) == 1
    assert result[0]["id"] == "meta1"

@patch("backend.drive.build")
@patch("backend.drive.MediaIoBaseDownload")
def test_download_metadata(mock_download: MagicMock, mock_build: MagicMock, mock_creds: MagicMock, tmp_path: Path) -> None:
    """Test downloading a metadata file."""
    dest = tmp_path / "123_meta.json"
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    mock_downloader = MagicMock()
    mock_download.return_value = mock_downloader
    mock_downloader.next_chunk.return_value = (None, True)

    download_metadata(mock_creds, "meta1", dest)
    assert dest.exists()

@patch("backend.drive.build")
@patch("backend.drive.MediaFileUpload")
def test_upload_metadata(mock_media: MagicMock, mock_build: MagicMock, mock_creds: MagicMock, tmp_path: Path) -> None:
    """Test uploading a metadata file."""
    src = tmp_path / "123_meta.json"
    src.write_text("{}")

    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().create().execute.return_value = {"id": "new_meta_id"}

    result = upload_metadata(mock_creds, "folder_123", src, "123_meta.json")
    assert result == "new_meta_id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest open-paper-shelf/tests/backend/test_drive_metadata.py -v`
Expected: FAIL with ImportError (functions not found in drive.py)

- [ ] **Step 3: Write minimal implementation**

Modify `open-paper-shelf/src/backend/drive.py`. Add the following to the bottom of the file:

```python
def list_metadata_in_library(creds: Credentials, folder_id: str) -> List[Dict[str, str]]:
    """Lists all metadata JSON files in the specified Google Drive folder.

    Args:
        creds: The authenticated Google credentials.
        folder_id: The ID of the Google Drive folder.

    Returns:
        A list of dictionaries, each containing 'id' and 'name' of a JSON file.
    """
    service: Any = build("drive", "v3", credentials=creds)
    query: str = (
        f"'{folder_id}' in parents and name contains '_meta.json' and trashed = false"
    )

    all_files = []
    page_token = None

    while True:
        results: Dict[str, Any] = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
            )
            .execute()
        )
        all_files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return all_files


def download_metadata(creds: Credentials, file_id: str, dest_path: Path) -> None:
    """Downloads a metadata JSON file from Google Drive to the local filesystem.

    Args:
        creds: The authenticated Google credentials.
        file_id: The Google Drive file ID.
        dest_path: The local path where the JSON will be saved.
    """
    service: Any = build("drive", "v3", credentials=creds)
    request: Any = service.files().get_media(fileId=file_id)

    with tempfile.NamedTemporaryFile(dir=dest_path.parent, delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)
        downloader = MediaIoBaseDownload(tmp_file, request)
        done: bool = False
        while not done:
            status, done = downloader.next_chunk()

    try:
        tmp_path.rename(dest_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def upload_metadata(
    creds: Credentials,
    folder_id: str,
    file_path: Path,
    display_name: str,
) -> str:
    """Uploads a local metadata JSON file to Google Drive.

    Args:
        creds: The authenticated Google credentials.
        folder_id: The ID of the Google Drive folder.
        file_path: The local path to the JSON file.
        display_name: The custom name for the file in Google Drive.

    Returns:
        The Google Drive file ID of the newly uploaded file.
    """
    service: Any = build("drive", "v3", credentials=creds)

    # Check if it already exists to overwrite
    query: str = f"name = '{display_name}' and '{folder_id}' in parents and trashed = false"
    existing = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = existing.get("files", [])

    media = MediaFileUpload(str(file_path), mimetype="application/json", resumable=True)

    if files:
        # Update existing
        file_id: str = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        return file_id
    else:
        # Create new
        file_metadata: Dict[str, Any] = {"name": display_name, "parents": [folder_id]}
        file: Dict[str, Any] = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        return str(file.get("id"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest open-paper-shelf/tests/backend/test_drive_metadata.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add open-paper-shelf/src/backend/drive.py open-paper-shelf/tests/backend/test_drive_metadata.py
git commit -m "feat(backend): implement drive metadata storage functions"
```

---

### Task 3: App Startup Sync

**Files:**
- Modify: `open-paper-shelf/src/frontend/app.py`

**Interfaces:**
- Consumes: `list_metadata_in_library`, `download_metadata` from `backend.drive`
- Produces: `st.session_state.metadata` dictionary mapping `paper_id` -> `dict`

- [ ] **Step 1: Write minimal implementation**

In `open-paper-shelf/src/frontend/app.py`, first add imports:
```python
import json
from backend.models import PaperMetadata
from backend.drive import (
    # ... existing imports
    list_metadata_in_library,
    download_metadata,
    upload_metadata,
)
```

In the `# --- Initialization / Syncing ---` block, inside the `if "folder_id" not in st.session_state:` condition (around line 177), add the metadata syncing:

```python
            # Create local metadata dir
            metadata_dir = PAPERS_DIR / "metadata"
            metadata_dir.mkdir(exist_ok=True, parents=True)
            st.session_state.metadata_dir = metadata_dir
            st.session_state.metadata = {}

            # Sync metadata
            metadata_files = list_metadata_in_library(creds, st.session_state.folder_id)
            for meta_file in metadata_files:
                name = meta_file["name"]
                if name.endswith("_meta.json"):
                    paper_id = name.replace("_meta.json", "")
                    local_meta_path = metadata_dir / name
                    if not local_meta_path.exists():
                        download_metadata(creds, meta_file["id"], local_meta_path)

                    if local_meta_path.exists():
                        try:
                            with open(local_meta_path, "r", encoding="utf-8") as f:
                                st.session_state.metadata[paper_id] = json.load(f)
                        except Exception as e:
                            st.error(f"Failed to load metadata for {paper_id}: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add open-paper-shelf/src/frontend/app.py
git commit -m "feat(frontend): sync metadata files on startup"
```

---

### Task 4: UI Metadata Sidebar

**Files:**
- Modify: `open-paper-shelf/src/frontend/app.py`

**Interfaces:**
- Consumes: `st.session_state.metadata`, `st.session_state.selected_paper`
- Produces: Interactive form pushing changes to `upload_metadata`

- [ ] **Step 1: Write minimal implementation**

In `open-paper-shelf/src/frontend/app.py`, locate the `with right_col.container(border=True, height=800):` block.

Replace the contents of `if selected_pdf:` (around line 343) with the split layout:

```python
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

                pdf_col, meta_col = st.columns([3, 1])

                with pdf_col:
                    if local_pdf_path.exists():
                        base_url = os.environ.get("FASTAPI_URL", "http://localhost:8000")
                        fastapi_url = f"{base_url.rstrip('/')}/papers/{selected_pdf['id']}/{urllib.parse.quote(safe_paper_name)}"
                        pdf_display = f'<iframe src="{fastapi_url}" width="100%" height="750" style="border:none;" type="application/pdf"></iframe>'
                        st.markdown(pdf_display, unsafe_allow_html=True)

                with meta_col:
                    with st.expander("Metadata", expanded=True):
                        # Load existing or create default
                        existing_data = st.session_state.metadata.get(selected_paper, {})
                        if not existing_data:
                            existing_data = PaperMetadata(title=selected_pdf["name"]).model_dump()

                        meta = PaperMetadata(**existing_data)

                        with st.form(key=f"meta_form_{selected_paper}"):
                            new_title = st.text_input("Title", value=meta.title)
                            tags_str = st.text_input("Tags (comma separated)", value=", ".join(meta.tags))

                            status_options = ["Unread", "Reading", "Read", "TODO"]
                            current_index = status_options.index(meta.status) if meta.status in status_options else 0
                            new_status = st.selectbox("Status", options=status_options, index=current_index)

                            new_citation = st.text_input("Citation", value=meta.citation)
                            new_notes = st.text_area("Notes", value=meta.notes, height=200)

                            if st.form_submit_button("Save Changes"):
                                updated_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                                updated_meta = PaperMetadata(
                                    title=new_title,
                                    tags=updated_tags,
                                    status=new_status, # type: ignore
                                    citation=new_citation,
                                    notes=new_notes
                                )

                                # Update session state
                                st.session_state.metadata[selected_paper] = updated_meta.model_dump()

                                # Save locally
                                meta_filename = f"{selected_paper}_meta.json"
                                local_meta_path = st.session_state.metadata_dir / meta_filename
                                with open(local_meta_path, "w", encoding="utf-8") as f:
                                    json.dump(updated_meta.model_dump(), f, indent=2)

                                # Upload to drive
                                try:
                                    with st.spinner("Saving metadata to Drive..."):
                                        upload_metadata(creds, folder_id, local_meta_path, meta_filename)
                                    st.success("Metadata saved!")
                                except Exception as e:
                                    st.error(f"Failed to save metadata to Drive: {e}")
```

- [ ] **Step 2: Commit**

```bash
uv run ruff format .
uv run ruff check --fix .
git add open-paper-shelf/src/frontend/app.py
git commit -m "feat(frontend): add metadata sidebar and edit form"
```

# Open Paper Shelf: Metadata Feature Design

## Goal
Implement a scalable metadata storage and UI for PDF papers in Open Paper Shelf, allowing users to view and edit title, tags, notes, citations, and status directly in the web app, with data synced to Google Drive.

## Architecture

### 1. Data Model
A new Pydantic model `PaperMetadata` will be defined in `backend/models.py`:
- `title` (str)
- `tags` (list[str])
- `notes` (str)
- `citation` (str)
- `status` (Literal["Unread", "Reading", "Read", "TODO"])

### 2. Storage & Sync (`backend/drive.py`)
- **Format:** Each paper's metadata is stored as `[paper_id]_meta.json` in the `open-paper-shelf-lib` Google Drive folder alongside the PDFs.
- **Sync at Startup:** During app initialization, the backend lists all `*_meta.json` files and downloads them to a local `papers/metadata/` cache folder.
- **State Management:** The JSON files are parsed and read into `st.session_state.metadata` (a dictionary keyed by `paper_id`) so the UI can instantly display metadata on click.
- **Save Operations:** When metadata is edited, it updates the local file and `session_state`, and uploads the updated JSON file back to Google Drive (creating it if it doesn't exist, overwriting if it does).

### 3. User Interface (`frontend/app.py`)
- The UI is restructured to display a metadata sidebar next to the PDF when a paper is selected.
- The `right_col` (where the PDF lives) is split into two internal columns: `pdf_col, meta_col = st.columns([3, 1])`.
- The `meta_col` contains a collapsible `st.expander("Metadata", expanded=True)`.
- The expander contains a Streamlit `st.form` or individually `on_change`-triggered widgets for editing:
  - Title: `st.text_input`
  - Tags: `st_tags` or `st.text_input` (comma-separated for simplicity)
  - Notes: `st.text_area`
  - Citation: `st.text_input`
  - Status: `st.selectbox`
- A "Save Changes" button ensures updates are pushed to Google Drive and synced locally.

## Testing & Verification
- Unit tests will be added for the new data models and drive interaction functions (`list_metadata_in_library`, `upload_metadata`, `download_metadata`).
- Manual UI testing to ensure that changing metadata updates Google Drive and persists across app reloads.

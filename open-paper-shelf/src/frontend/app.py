import html
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Literal, cast

import streamlit as st
from pydantic import ValidationError
from st_keyup import st_keyup

import sys

src_path = Path(__file__).resolve().parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backend.drive import (  # noqa: E402
    create_library,
    download_file,
    get_or_create_root_folder,
    get_papers_folder,
    list_libraries,
    upload_file_to_folder,
    upload_library_index,
)
from backend.models import LibraryIndex, PaperMetadata  # noqa: E402
from frontend.auth import authenticate_user  # noqa: E402
from frontend.constants import (  # noqa: E402
    DEFAULT_FASTAPI_URL,
    GENERATE_METADATA_HELP,
    JSON_MIME_TYPE,
    LABEL_TO_STATUS,
    META_FILENAME,
    PAPER_ID_PATTERN,
    PDF_FILENAME,
    STATUS_ICONS,
    STATUS_LABELS,
)
from frontend.library import (  # noqa: E402
    delete_selected_papers,
    init_library_state,
    sync_library_index,
)
from frontend.library_filters import (  # noqa: E402
    filter_papers,
    get_all_tags,
    get_duplicate_pids,
    get_missing_metadata_pids,
)
from frontend.metadata_generation import (  # noqa: E402
    generate_metadata_for_paper,
    generate_metadata_for_selected,
    sync_paper_metadata,
)
from frontend.text_utils import strip_pdf_suffix  # noqa: E402
from frontend.uploads import upload_papers  # noqa: E402

# The functions/constants below aren't called directly by this module's own
# code anymore (they moved to frontend/auth.py, uploads.py, library.py,
# library_filters.py, and metadata_generation.py) but the test suite patches
# and reads them via `app.<name>` (e.g. `mocker.patch.object(app, "...")`),
# so they're re-exported here rather than dropped as unused imports.
from backend.drive import (  # noqa: E402,F401
    OAUTH_FLOWS as OAUTH_FLOWS,
    PAPERS_DIR as PAPERS_DIR,
    add_oauth_flow as add_oauth_flow,
    create_paper_folder as create_paper_folder,
    delete_paper_folder as delete_paper_folder,
    get_library_index_file as get_library_index_file,
    get_oauth_flow as get_oauth_flow,
    load_credentials_from_file as load_credentials_from_file,
    save_credentials as save_credentials,
)
from backend.huggingface_client import (  # noqa: E402,F401
    DEFAULT_EMBEDDING_MODEL as DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GENERATION_MODEL as DEFAULT_GENERATION_MODEL,
    HFTokenMissingError as HFTokenMissingError,
    embed_text as embed_text,
    extract_pdf_text as extract_pdf_text,
    find_similar_papers as find_similar_papers,
    generate_paper_metadata as generate_paper_metadata,
)
from frontend.constants import (  # noqa: E402,F401
    BULK_GENERATE_DELAY_SECONDS as BULK_GENERATE_DELAY_SECONDS,
)
from frontend.metadata_generation import (  # noqa: E402,F401
    persist_generated_metadata as persist_generated_metadata,
)


st.set_page_config(
    layout="wide",
    page_title="Open Paper Shelf",
    initial_sidebar_state="expanded",
)


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
        libraries = list_libraries(creds, root_id)

        if len(libraries) == 1 and not st.session_state.get("manual_library_selection"):
            only_lib = libraries[0]
            papers_id = get_papers_folder(creds, only_lib["id"])
            init_library_state(creds, only_lib["id"], papers_id, only_lib["name"])
            st.rerun()
            return

        st.subheader("Select or Create a Library")

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
                    init_library_state(
                        creds, selected_lib, papers_id, lib_options[selected_lib]
                    )
                    st.rerun()
            else:
                st.info("No existing libraries found.")

        with col2:
            new_lib_name = st.text_input("New Library Name")
            if st.button("Create Library") and new_lib_name:
                lib_info = create_library(creds, root_id, new_lib_name)
                init_library_state(
                    creds,
                    lib_info["lib_id"],
                    lib_info["papers_id"],
                    lib_info["lib_name"],
                )
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
            """Clears the current library's session state to return to library selection.

            Also forces the manual selection screen to show even if only one
            library exists, since the user explicitly asked to switch.
            """
            st.session_state.manual_library_selection = True
            for k in [
                "current_lib_id",
                "current_lib_name",
                "current_papers_id",
                "index",
                "last_sync_time",
                "confirm_delete_pids",
                "confirm_generate_pids",
            ]:
                st.session_state.pop(k, None)

        # Expander headers have no built-in alignment option, so center
        # them to match the full-width "Switch Library" button above them.
        st.markdown(
            "<style>[data-testid='stExpander'] summary "
            "{ justify-content: center; }</style>",
            unsafe_allow_html=True,
        )

        lib_name = st.session_state.get(
            "current_lib_name", st.session_state.current_lib_id
        )
        st.caption(f"📁 Library: {lib_name}")
        st.button("Switch Library", on_click=switch_lib, use_container_width=True)

        if "uploader_key" not in st.session_state:
            st.session_state.uploader_key = 0

        with st.expander("Upload Paper(s)", expanded=False):
            with st.container(height=150):
                uploaded_files = st.file_uploader(
                    "Choose PDF files",
                    type="pdf",
                    accept_multiple_files=True,
                    key=str(st.session_state.uploader_key),
                )
            if uploaded_files:
                if st.button("Upload"):
                    file_count = len(uploaded_files)
                    progress = st.progress(0.0, text=f"Uploading (0/{file_count})...")

                    def on_progress(i: int, total: int, filename: str) -> None:
                        """Advances the upload progress bar after each file.

                        Args:
                            i (int): The 1-based index of the file just processed.
                            total (int): The total number of files in the batch.
                            filename (str): The name of the file just processed.
                        """
                        progress.progress(
                            i / total, text=f"Uploading ({i}/{total}): {filename}"
                        )

                    try:
                        all_succeeded = upload_papers(
                            creds, uploaded_files, on_progress=on_progress
                        )
                    finally:
                        try:
                            upload_library_index(
                                creds,
                                st.session_state.current_papers_id,
                                st.session_state.index,
                            )
                        finally:
                            progress.empty()
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

        with st.expander("Library Papers", expanded=True):
            # A paper's checkbox stays checked in session state even while
            # it's hidden by a search/status/tag filter, so scan every
            # known paper (not just the currently filtered ones) to decide
            # whether the bin icon should read as "armed".
            checked_pids = [
                pid
                for pid in st.session_state.index.papers
                if st.session_state.get(f"chk_{pid}")
            ]
            # Narrow columns with no gap keep the two icons adjacent instead
            # of centered in two full-width halves; the trailing column
            # just absorbs the remaining space. Native Streamlit has no way
            # to give one specific button a custom color (`type=` only
            # offers theme-wide presets), so the delete and generate icons
            # share the same "primary" red when active and are told apart
            # by their emoji and tooltip instead.
            icon_col1, icon_col2, icon_col3, _icon_spacer = st.columns(
                [1, 1, 1, 7], gap=None
            )
            with icon_col1:
                if st.button(
                    "🗑️",
                    key="trash_icon",
                    help="Delete selected papers",
                    type="primary" if checked_pids else "secondary",
                ):
                    if checked_pids:
                        st.session_state.confirm_delete_pids = checked_pids
                    else:
                        st.warning("No papers selected.")
            with icon_col2:
                if st.button(
                    "✨",
                    key="bulk_generate_icon",
                    help=GENERATE_METADATA_HELP,
                    type="primary" if checked_pids else "secondary",
                ):
                    if checked_pids:
                        st.session_state.confirm_generate_pids = checked_pids
                    else:
                        st.warning("No papers selected.")
            with icon_col3:
                if st.button(
                    "🪄",
                    key="generate_missing_icon",
                    help="Generate metadata for every paper that doesn't have any yet",
                ):
                    missing_pids = list(
                        get_missing_metadata_pids(st.session_state.index)
                    )
                    if missing_pids:
                        st.session_state.confirm_generate_pids = missing_pids
                    else:
                        st.info("Every paper already has metadata.")

            search_box = st_keyup(
                "Search", placeholder="Search papers...", key="search_box"
            )
            search_query = (search_box or "").lower()

            status_col, tags_col = st.columns([1, 1])

            with status_col:
                status_filter_labels = st.multiselect(
                    "Status",
                    options=list(STATUS_LABELS.values()),
                    key="status_filter",
                )
                status_filter = [
                    LABEL_TO_STATUS[label] for label in status_filter_labels
                ]
            with tags_col:
                all_tags = get_all_tags(st.session_state.index)
                # A previously selected tag may no longer exist (its last
                # paper was deleted or retagged since the last rerun). Drop
                # it from the persisted selection before the widget reads
                # it so a stale value never lingers against the current
                # options.
                if "tags_filter" in st.session_state:
                    st.session_state.tags_filter = [
                        tag for tag in st.session_state.tags_filter if tag in all_tags
                    ]
                tags_filter = st.multiselect(
                    "Tags", options=all_tags, key="tags_filter"
                )

            if st.session_state.get("confirm_delete_pids"):
                pids_to_delete = st.session_state.confirm_delete_pids
                st.warning(
                    f"Delete {len(pids_to_delete)} paper(s)? This cannot be undone."
                )
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

            if st.session_state.get("confirm_generate_pids"):
                pids_to_generate = st.session_state.confirm_generate_pids
                st.warning(
                    f"Generate metadata for {len(pids_to_generate)} paper(s)? "
                    "Any existing metadata will be overwritten."
                )
                confirm_gen_col, cancel_gen_col = st.columns(2)
                with confirm_gen_col:
                    if st.button("Confirm", key="confirm_generate_btn"):
                        generate_metadata_for_selected(
                            creds,
                            pids_to_generate,
                            st.session_state.index,
                            st.session_state.current_papers_id,
                            st.session_state.local_lib_dir,
                        )
                        st.session_state.confirm_generate_pids = None
                        st.rerun()
                with cancel_gen_col:
                    if st.button("Cancel", key="cancel_generate_btn"):
                        st.session_state.confirm_generate_pids = None
                        st.rerun()

            # Re-filter after the block above so a partial batch-delete
            # failure (which skips st.rerun() to keep its error visible)
            # never renders a now-deleted paper's row - clicking one would
            # otherwise select a pid missing from st.session_state.index.papers
            # and crash the main-area lookup with a KeyError.
            filtered_papers = filter_papers(
                st.session_state.index.papers, search_query, status_filter, tags_filter
            )

            duplicate_pids = get_duplicate_pids(st.session_state.index)

            with st.container(height=400):
                for pid, p in filtered_papers:
                    row_check, row_button = st.columns([1, 8])
                    with row_check:
                        st.checkbox(
                            "Select", key=f"chk_{pid}", label_visibility="collapsed"
                        )
                    with row_button:
                        display_name = f"{STATUS_ICONS.get(p.status, '📄')} {p.title}"
                        if pid in duplicate_pids:
                            display_name = f"⚠️ {display_name}"
                        if pid == st.session_state.selected_paper:
                            display_name = f"**{display_name}**"
                        if st.button(
                            display_name, key=f"btn_{pid}", use_container_width=True
                        ):
                            st.session_state.selected_paper = pid
                            st.session_state.confirm_delete_pids = None
                            st.rerun()

    # Main area
    if st.session_state.selected_paper:
        pid = st.session_state.selected_paper
        if not re.match(PAPER_ID_PATTERN, pid):
            st.error("Invalid paper ID format.")
            st.stop()
        paper_info = st.session_state.index.papers[pid]

        local_paper_dir = st.session_state.local_lib_dir / pid
        local_paper_dir.mkdir(parents=True, exist_ok=True)
        local_pdf_path = local_paper_dir / PDF_FILENAME
        local_meta_path = local_paper_dir / META_FILENAME

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
                base_url = os.environ.get("FASTAPI_URL", DEFAULT_FASTAPI_URL)
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

            if st.button(
                "✨ Generate metadata",
                key=f"generate_btn_{pid}",
                disabled=not pdf_available,
                help=GENERATE_METADATA_HELP,
            ):
                has_unsaved_edits = (
                    st.session_state.get(f"title_{pid}", meta.title) != meta.title
                    or st.session_state.get(f"abstract_{pid}", meta.abstract)
                    != meta.abstract
                    or st.session_state.get(f"tags_{pid}", ", ".join(meta.tags))
                    != ", ".join(meta.tags)
                )
                if meta.abstract or meta.tags or has_unsaved_edits:
                    st.session_state[f"confirm_regenerate_{pid}"] = True
                else:
                    with st.spinner("Generating metadata with Hugging Face..."):
                        if generate_metadata_for_paper(pid, local_pdf_path):
                            st.rerun()

            if st.session_state.get(f"confirm_regenerate_{pid}"):
                st.warning(
                    "This paper already has generated metadata or unsaved "
                    "edits. Regenerate and overwrite them?"
                )
                regen_col, cancel_regen_col = st.columns(2)
                with regen_col:
                    if st.button("Regenerate", key=f"confirm_regenerate_btn_{pid}"):
                        st.session_state.pop(f"confirm_regenerate_{pid}", None)
                        with st.spinner("Generating metadata with Hugging Face..."):
                            if generate_metadata_for_paper(pid, local_pdf_path):
                                st.rerun()
                with cancel_regen_col:
                    if st.button("Cancel", key=f"cancel_regenerate_btn_{pid}"):
                        st.session_state.pop(f"confirm_regenerate_{pid}", None)
                        st.rerun()

            for _, dupe_title, dupe_score in st.session_state.get(f"dupes_{pid}", []):
                st.warning(f"Similar to '{dupe_title}' — {dupe_score:.0%} match")

            draft = st.session_state.get(f"generated_{pid}", {})
            with st.form(key=f"meta_form_{pid}"):
                new_title = st.text_input(
                    "Title", value=draft.get("title", meta.title), key=f"title_{pid}"
                )
                new_abstract = st.text_area(
                    "Abstract / TL;DR",
                    value=draft.get("abstract", meta.abstract),
                    height=100,
                    key=f"abstract_{pid}",
                )
                tags_str = st.text_input(
                    "Tags (comma separated)",
                    value=", ".join(draft.get("tags", meta.tags)),
                    key=f"tags_{pid}",
                )
                status_label = st.selectbox(
                    "Status",
                    options=list(STATUS_LABELS.values()),
                    index=list(STATUS_LABELS.keys()).index(meta.status),
                    key=f"status_{pid}",
                )
                status = LABEL_TO_STATUS.get(status_label, meta.status)
                citation = st.text_input(
                    "Citation", value=meta.citation, key=f"citation_{pid}"
                )
                notes = st.text_area(
                    "Notes", value=meta.notes, height=200, key=f"notes_{pid}"
                )

                if st.form_submit_button(
                    "Save Changes", disabled=not metadata_available
                ):
                    meta = meta.model_copy(
                        update={
                            "title": strip_pdf_suffix(new_title or ""),
                            "abstract": new_abstract or "",
                            "tags": [
                                t.strip() for t in tags_str.split(",") if t.strip()
                            ],
                            "status": cast(
                                Literal["Unread", "Reading", "Read", "TODO"], status
                            ),
                            "citation": citation,
                            "notes": notes,
                            "embedding": draft.get("embedding", meta.embedding),
                        }
                    )

                    local_meta_path.write_text(
                        meta.model_dump_json(indent=2), encoding="utf-8"
                    )
                    with st.spinner("Saving metadata to Drive..."):
                        upload_file_to_folder(
                            creds,
                            paper_info.folder_id,
                            local_meta_path,
                            META_FILENAME,
                            JSON_MIME_TYPE,
                        )

                        paper_info = paper_info.model_copy(
                            update={
                                "title": meta.title,
                                "tags": meta.tags,
                                "status": meta.status,
                                "embedding": meta.embedding,
                            }
                        )
                        st.session_state.index.papers[pid] = paper_info
                        upload_library_index(
                            creds,
                            st.session_state.current_papers_id,
                            st.session_state.index,
                        )

                    st.session_state.pop(f"generated_{pid}", None)
                    st.session_state.pop(f"dupes_{pid}", None)
                    st.success("Metadata saved!")
                    st.rerun()
    else:
        st.info("Select a paper from the sidebar to view it.")


if __name__ == "__main__":
    main()

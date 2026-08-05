import html
import json
import logging
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
    delete_library,
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
    EDITED_PDF_FILENAME,
    GENERATE_METADATA_HELP,
    JSON_MIME_TYPE,
    LABEL_TO_STATUS,
    MAX_DUPLICATE_NAMES_TO_LIST,
    META_FILENAME,
    PAPER_ID_PATTERN,
    PDF_FILENAME,
    SIMILAR_FILTER_LABEL,
    STATUS_ICONS,
    STATUS_LABELS,
)
from frontend.library import (  # noqa: E402
    add_tags_to_selected,
    delete_selected_papers,
    init_library_state,
    remove_tags_from_selected,
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


logger = logging.getLogger(__name__)

st.set_page_config(
    layout="wide",
    page_title="Open Paper Shelf",
    initial_sidebar_state="expanded",
)

BULK_ACTION_STATE_KEYS = (
    "confirm_delete_pids",
    "confirm_generate_pids",
    "show_add_tag_pids",
    "show_remove_tag_pids",
)
"""Session-state keys backing the icon bar's mutually exclusive staged
actions - only one of delete/generate/add-tag/remove-tag can be staged at
a time, so arming one clears the others rather than stacking their forms."""


def _stage_bulk_action(state_key: str, pids: list[str]) -> None:
    """Stages a bulk action for the icon bar, clearing any other staged action.

    Args:
        state_key: The `st.session_state` key to stage `pids` under - one of
            `BULK_ACTION_STATE_KEYS`.
        pids: The paper IDs the staged action applies to.
    """
    for key in BULK_ACTION_STATE_KEYS:
        if key != state_key:
            st.session_state.pop(key, None)
    st.session_state[state_key] = pids


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
                if (
                    st.session_state.get("confirm_delete_lib_id") is not None
                    and st.session_state.confirm_delete_lib_id != selected_lib
                ):
                    st.session_state.confirm_delete_lib_id = None

                if st.button("Delete Library"):
                    st.session_state.confirm_delete_lib_id = selected_lib

                if st.session_state.get("confirm_delete_lib_id") in lib_options:
                    lib_id_to_delete = st.session_state.confirm_delete_lib_id
                    st.warning(
                        f"Delete library '{lib_options[lib_id_to_delete]}' and "
                        "all its papers? This will move the library and all "
                        "its papers to your Google Drive trash."
                    )
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        if st.button("Confirm", key="confirm_delete_lib_btn"):
                            try:
                                delete_library(creds, lib_id_to_delete)
                            except Exception:
                                logger.exception("Failed to delete library")
                                st.error("Failed to delete library. Please try again.")
                            else:
                                st.session_state.confirm_delete_lib_id = None
                                st.rerun()
                    with cancel_col:
                        if st.button("Cancel", key="cancel_delete_lib_btn"):
                            st.session_state.confirm_delete_lib_id = None
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
                "show_add_tag_pids",
                "show_remove_tag_pids",
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

        upload_flash = st.session_state.pop("upload_flash", None)
        with st.expander("Upload Paper(s)", expanded=upload_flash is not None):
            if upload_flash is not None:
                duplicates_skipped = upload_flash["duplicates_skipped"]
                if len(duplicates_skipped) > MAX_DUPLICATE_NAMES_TO_LIST:
                    st.warning(
                        f"Skipped {len(duplicates_skipped)} duplicate files "
                        "already in this library."
                    )
                else:
                    for skipped_name in duplicates_skipped:
                        st.warning(
                            f"Skipped {skipped_name}: a paper with that title "
                            "already exists in this library."
                        )
                if upload_flash["all_succeeded"]:
                    st.success(upload_flash["message"])
                else:
                    st.warning(upload_flash["message"])

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
                    duplicates_skipped = st.session_state.get(
                        "duplicate_uploads_skipped", []
                    )
                    if all_succeeded:
                        message = "Uploaded successfully!"
                        if duplicates_skipped:
                            message = (
                                "Uploaded successfully! (Duplicate files "
                                "were skipped — see warnings above.)"
                            )
                        st.session_state.upload_flash = {
                            "duplicates_skipped": duplicates_skipped,
                            "all_succeeded": True,
                            "message": message,
                        }
                        st.rerun()
                    else:
                        if len(duplicates_skipped) > MAX_DUPLICATE_NAMES_TO_LIST:
                            st.warning(
                                f"Skipped {len(duplicates_skipped)} duplicate "
                                "files already in this library."
                            )
                        else:
                            for skipped_name in duplicates_skipped:
                                st.warning(
                                    f"Skipped {skipped_name}: a paper with "
                                    "that title already exists in this "
                                    "library."
                                )
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
            # just absorbs the remaining space. All three icons use a fixed
            # "secondary" type; no per-button background-color styling.
            #
            # The [1, 1, 1, 7] ratio is only a *starting* width: Streamlit
            # columns are flex items that shrink with their container, so
            # dragging the sidebar's resize handle narrower than a few
            # hundred px would shrink these three icon columns past the
            # button's natural size, making the buttons overlap and the
            # emoji glyphs spill outside their bounds. Pin the icon columns
            # to a fixed size (flex: 0 0 auto) so only the trailing spacer
            # column shrinks/grows, and clip+center each button's content so
            # its emoji can never render outside the button box.
            #
            # At very narrow sidebar widths the row itself wraps (the three
            # fixed-size columns no longer fit next to the shrunk spacer
            # column), stacking icons onto a second row. A bottom margin on
            # every button keeps that wrapped row from touching the one
            # above it, in addition to the existing horizontal gap.
            st.markdown(
                "<style>"
                "div[data-testid='stColumn']:has(.st-key-trash_icon),"
                "div[data-testid='stColumn']:has(.st-key-bulk_generate_icon),"
                "div[data-testid='stColumn']:has(.st-key-generate_missing_icon),"
                "div[data-testid='stColumn']:has(.st-key-add_tag_icon),"
                "div[data-testid='stColumn']:has(.st-key-remove_tag_icon)"
                "{ flex: 0 0 auto; width: auto; min-width: 2.5rem; }"
                ".st-key-trash_icon button,"
                ".st-key-bulk_generate_icon button,"
                ".st-key-generate_missing_icon button,"
                ".st-key-add_tag_icon button,"
                ".st-key-remove_tag_icon button"
                "{ width: 2.5rem; height: 2.5rem; min-width: 2.5rem; padding: 0;"
                " display: flex; align-items: center; justify-content: center;"
                " overflow: hidden; line-height: 1; margin-bottom: 0.4rem; }"
                ".st-key-trash_icon button, .st-key-bulk_generate_icon button,"
                ".st-key-generate_missing_icon button, .st-key-add_tag_icon button"
                "{ margin-right: 0.4rem; }"
                "</style>",
                unsafe_allow_html=True,
            )
            (
                icon_col1,
                icon_col2,
                icon_col3,
                icon_col4,
                icon_col5,
                _icon_spacer,
            ) = st.columns([1, 1, 1, 1, 1, 5], gap=None)
            with icon_col1:
                if st.button(
                    "🗑️",
                    key="trash_icon",
                    help="Delete selected papers",
                    type="secondary",
                ):
                    if checked_pids:
                        _stage_bulk_action("confirm_delete_pids", checked_pids)
                    else:
                        st.warning("No papers selected.")
            with icon_col2:
                if st.button(
                    "✨",
                    key="bulk_generate_icon",
                    help=GENERATE_METADATA_HELP,
                    type="secondary",
                ):
                    if checked_pids:
                        _stage_bulk_action("confirm_generate_pids", checked_pids)
                    else:
                        st.warning("No papers selected.")
            with icon_col3:
                if st.button(
                    "🪄",
                    key="generate_missing_icon",
                    help="Generate metadata for every paper that doesn't have any yet",
                    type="secondary",
                ):
                    missing_pids = list(
                        get_missing_metadata_pids(st.session_state.index)
                    )
                    if missing_pids:
                        _stage_bulk_action("confirm_generate_pids", missing_pids)
                    else:
                        st.info("Every paper already has metadata.")
            with icon_col4:
                if st.button(
                    "🏷️",
                    key="add_tag_icon",
                    help="Add a tag to selected papers",
                    type="secondary",
                ):
                    if checked_pids:
                        _stage_bulk_action("show_add_tag_pids", checked_pids)
                    else:
                        st.warning("No papers selected.")
            with icon_col5:
                if st.button(
                    "🚫",
                    key="remove_tag_icon",
                    help="Remove a tag from selected papers",
                    type="secondary",
                ):
                    if checked_pids:
                        _stage_bulk_action("show_remove_tag_pids", checked_pids)
                    else:
                        st.warning("No papers selected.")

            search_box = st_keyup(
                "Search", placeholder="Search papers...", key="search_box"
            )
            search_query = (search_box or "").lower()

            status_col, tags_col = st.columns([1, 1])

            with status_col:
                status_filter_labels = st.multiselect(
                    "Status",
                    options=list(STATUS_LABELS.values()) + [SIMILAR_FILTER_LABEL],
                    key="status_filter",
                )
                status_filter = [
                    LABEL_TO_STATUS[label]
                    for label in status_filter_labels
                    if label in LABEL_TO_STATUS
                ]
                include_similar_filter = SIMILAR_FILTER_LABEL in status_filter_labels
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

            if st.session_state.get("show_add_tag_pids"):
                pids_to_tag = st.session_state.show_add_tag_pids
                new_tags_str = st.text_input(
                    f"Add tag(s) to {len(pids_to_tag)} paper(s), comma separated",
                    key="add_tag_input",
                )
                add_tag_col, cancel_add_tag_col = st.columns(2)
                with add_tag_col:
                    if st.button("Add", key="confirm_add_tag_btn"):
                        new_tags = [
                            t.strip() for t in new_tags_str.split(",") if t.strip()
                        ]
                        if new_tags:
                            add_tags_to_selected(
                                creds,
                                pids_to_tag,
                                st.session_state.index,
                                st.session_state.current_papers_id,
                                st.session_state.local_lib_dir,
                                new_tags,
                            )
                        st.session_state.show_add_tag_pids = None
                        st.rerun()
                with cancel_add_tag_col:
                    if st.button("Cancel", key="cancel_add_tag_btn"):
                        st.session_state.show_add_tag_pids = None
                        st.rerun()

            if st.session_state.get("show_remove_tag_pids"):
                pids_to_untag = st.session_state.show_remove_tag_pids
                tags_in_selection = sorted(
                    {
                        tag
                        for pid in pids_to_untag
                        if pid in st.session_state.index.papers
                        for tag in st.session_state.index.papers[pid].tags
                    }
                )
                if not tags_in_selection:
                    st.info("None of the selected papers have any tags.")
                    st.session_state.show_remove_tag_pids = None
                else:
                    tags_to_remove = st.multiselect(
                        f"Remove tag(s) from {len(pids_to_untag)} paper(s)",
                        options=tags_in_selection,
                        key="remove_tag_select",
                    )
                    remove_tag_col, cancel_remove_tag_col = st.columns(2)
                    with remove_tag_col:
                        if st.button("Remove", key="confirm_remove_tag_btn"):
                            if tags_to_remove:
                                remove_tags_from_selected(
                                    creds,
                                    pids_to_untag,
                                    st.session_state.index,
                                    st.session_state.current_papers_id,
                                    st.session_state.local_lib_dir,
                                    tags_to_remove,
                                )
                            st.session_state.show_remove_tag_pids = None
                            st.rerun()
                    with cancel_remove_tag_col:
                        if st.button("Cancel", key="cancel_remove_tag_btn"):
                            st.session_state.show_remove_tag_pids = None
                            st.rerun()

            # Re-filter after the block above so a partial batch-delete
            # failure (which skips st.rerun() to keep its error visible)
            # never renders a now-deleted paper's row - clicking one would
            # otherwise select a pid missing from st.session_state.index.papers
            # and crash the main-area lookup with a KeyError.
            duplicate_pids = get_duplicate_pids(st.session_state.index)

            filtered_papers = filter_papers(
                st.session_state.index.papers,
                search_query,
                status_filter,
                tags_filter,
                duplicate_pids=duplicate_pids,
                include_similar=include_similar_filter,
            )

            with st.container(height=400):
                if not filtered_papers:
                    if not st.session_state.index.papers:
                        st.info(
                            "Your library is empty. Upload PDFs above to get started!"
                        )
                    else:
                        st.info("No papers match your search and filters.")

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
                    logger.exception(
                        "Failed to recover valid fields from %s", local_meta_path
                    )
                    st.warning(
                        "Could not recover this paper's saved notes/citation/"
                        "status - showing a blank metadata form instead."
                    )
                    meta = PaperMetadata(title=paper_info.title)
            except Exception as e:
                st.error(f"Could not load metadata: {e}")

        col_pdf, col_meta = st.columns([3.5, 1])
        with col_pdf:
            if pdf_available:
                local_edited_path = local_paper_dir / EDITED_PDF_FILENAME
                edited_available = local_edited_path.exists()
                if paper_info.edited_pdf_file_id and not edited_available:
                    try:
                        download_file(
                            creds, paper_info.edited_pdf_file_id, local_edited_path
                        )
                        edited_available = True
                    except Exception as e:
                        st.error(f"Failed to load edited PDF: {e}")

                view_filename = (
                    EDITED_PDF_FILENAME if edited_available else PDF_FILENAME
                )

                base_url = os.environ.get("FASTAPI_URL", DEFAULT_FASTAPI_URL)
                quoted_lib_id = urllib.parse.quote(st.session_state.current_lib_id)
                quoted_pid = urllib.parse.quote(pid)
                pdf_file_url = (
                    f"{base_url.rstrip('/')}/papers/{quoted_lib_id}/{quoted_pid}"
                    f"/{view_filename}"
                )
                if view_filename == EDITED_PDF_FILENAME:
                    # upload_file_to_folder reuses the same Drive file id on
                    # re-upload, so the URL alone wouldn't change after a
                    # further round of annotation - bust the browser's cache
                    # with the local file's mtime.
                    pdf_file_url += f"?v={local_edited_path.stat().st_mtime_ns}"

                viewer_query = urllib.parse.urlencode(
                    {
                        "file": pdf_file_url,
                        "libId": st.session_state.current_lib_id,
                        "pid": pid,
                    }
                )
                viewer_url = (
                    f"{base_url.rstrip('/')}/pdfjs/web/viewer.html?{viewer_query}"
                )
                pdf_display = (
                    f'<iframe src="{html.escape(viewer_url)}" width="100%" '
                    'height="750" style="border:none;"></iframe>'
                )
                st.markdown(pdf_display, unsafe_allow_html=True)

                st.caption(
                    "Use the toolbar's highlight tool to annotate the PDF - "
                    "your edits save to Drive automatically as you work."
                )
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

            draft = st.session_state.get(f"generated_{pid}", {})
            dupe_embedding = draft.get("embedding") or paper_info.embedding
            if dupe_embedding:
                for _, dupe_title, dupe_score in find_similar_papers(
                    dupe_embedding, st.session_state.index, exclude_pid=pid
                ):
                    st.warning(f"Similar to '{dupe_title}' — {dupe_score:.0%} match")

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
                    st.success("Metadata saved!")
                    st.rerun()
    else:
        st.info("Select a paper from the sidebar to view it.")


if __name__ == "__main__":
    main()

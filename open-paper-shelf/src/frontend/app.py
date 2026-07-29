import html
import json
import re
import shutil
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence, cast, Literal

import streamlit as st
from google.oauth2.credentials import Credentials
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field, ValidationError
from streamlit.runtime.uploaded_file_manager import UploadedFile
import os
from st_keyup import st_keyup

import sys

src_path = Path(__file__).resolve().parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backend.drive import (  # noqa: E402
    add_oauth_flow,
    create_library,
    create_paper_folder,
    delete_paper_folder,
    download_file,
    get_library_index_file,
    get_oauth_flow,
    get_or_create_root_folder,
    get_papers_folder,
    list_libraries,
    load_credentials_from_file,
    save_credentials,
    upload_file_to_folder,
    upload_library_index,
    OAUTH_FLOWS,
    PAPERS_DIR,
)
from backend.models import LibraryIndex, PaperIndexEntry, PaperMetadata  # noqa: E402
from backend.huggingface_client import (  # noqa: E402
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GENERATION_MODEL,
    HFTokenMissingError,
    embed_text,
    extract_pdf_text,
    find_similar_papers,
    generate_paper_metadata,
)


st.set_page_config(
    layout="wide",
    page_title="Open Paper Shelf",
    initial_sidebar_state="expanded",
)

GENERATE_METADATA_HELP = (
    f"Generates a title, abstract, and tags with {DEFAULT_GENERATION_MODEL}, "
    f"and a similarity-detection embedding with {DEFAULT_EMBEDDING_MODEL}."
)

BULK_GENERATE_DELAY_SECONDS: float = 1.5


STATUS_ICONS: dict[str, str] = {
    "Unread": "📄",
    "Reading": "📖",
    "Read": "✅",
    "TODO": "📌",
}

STATUS_LABELS: dict[str, str] = {
    status: f"{icon} {status}" for status, icon in STATUS_ICONS.items()
}
LABEL_TO_STATUS: dict[str, str] = {
    label: status for status, label in STATUS_LABELS.items()
}


class OAuthCallbackParams(BaseModel):
    """Validated OAuth redirect query parameters.

    Attributes:
        code: The authorization code returned by Google.
        state: The CSRF state string echoed back by Google, used to look up
            the matching cached OAuth flow.
    """

    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


def authenticate_user() -> Optional[Credentials]:
    """Handles the Google OAuth flow, returning credentials once authenticated.

    On first call, redirects the user to Google's consent screen. On the
    OAuth redirect back, validates and exchanges the authorization code for
    credentials, caches them locally, and clears OAuth-related session state.

    Returns:
        Optional[Credentials]: The user's Google OAuth credentials, or None
        if the user is not yet authenticated.
    """
    creds = load_credentials_from_file()
    if creds:
        return creds

    query_params = st.query_params
    if "code" in query_params:
        try:
            callback = OAuthCallbackParams(
                code=query_params.get("code") or "",
                state=query_params.get("state") or "",
            )
        except ValidationError:
            st.error("Authentication failed: State mismatch (possible CSRF attempt).")
            return None
        flow = OAUTH_FLOWS.get(callback.state)
        if flow is None:
            st.error("Authentication failed: State mismatch (possible CSRF attempt).")
            return None
        try:
            flow.fetch_token(code=callback.code)
            creds = cast(Credentials, flow.credentials)
            save_credentials(creds)
            OAUTH_FLOWS.pop(callback.state, None)
            st.query_params.clear()
            st.session_state.pop("auth_flow", None)
            st.session_state.pop("oauth_state", None)
            st.success("Successfully authenticated!")
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")
            return None

    if "auth_flow" not in st.session_state:
        try:
            flow = get_oauth_flow()
        except FileNotFoundError:
            st.error(
                "credentials.json not found. Please provide valid Google Drive credentials."
            )
            return None
        st.session_state.auth_flow = flow
        auth_url, state = flow.authorization_url(
            prompt="consent", access_type="offline"
        )
        add_oauth_flow(state, flow)
        st.session_state.auth_url = auth_url
        st.session_state.oauth_state = state

    st.warning("Please authenticate to access your Google Drive.")
    st.link_button("Login with Google", st.session_state.auth_url)
    return None


class UploadedPaperName(BaseModel):
    """Validates an uploaded PDF's filename before it becomes a paper title.

    Attributes:
        name: The original filename, non-empty and reasonably bounded.
    """

    name: str = Field(min_length=1, max_length=255)


def strip_pdf_suffix(name: str) -> str:
    """Removes a trailing .pdf extension from a paper title, if present.

    Args:
        name: The candidate title, typically derived from an uploaded
            filename or user-edited text.

    Returns:
        str: `name` with a trailing ".pdf" (any case) suffix removed.
        Falls back to the original `name` if stripping it would leave an
        empty string (e.g. a file literally named ".pdf").
    """
    stripped = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return stripped if stripped else name


def upload_papers(creds: Credentials, uploaded_files: Sequence[UploadedFile]) -> bool:
    """Uploads each file to Drive and records it in the in-memory index.

    Args:
        creds (Credentials): The Google OAuth credentials.
        uploaded_files (Sequence[UploadedFile]): The PDF files selected by the
            user via Streamlit's file uploader.

    Returns:
        True if every file uploaded successfully, False if any failed.
    """
    all_succeeded = True
    for uploaded_file in uploaded_files:
        try:
            validated_name = UploadedPaperName(name=uploaded_file.name).name
            title = strip_pdf_suffix(validated_name)
            paper_id = uuid.uuid4().hex
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)

            try:
                folder_id = create_paper_folder(
                    creds,
                    st.session_state.current_papers_id,
                    paper_id,
                )
                try:
                    pdf_file_id = upload_file_to_folder(
                        creds,
                        folder_id,
                        tmp_path,
                        "paper.pdf",
                        "application/pdf",
                    )
                    meta = PaperMetadata(title=title)

                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".json"
                    ) as tmp_meta:
                        tmp_meta.write(meta.model_dump_json(indent=2).encode("utf-8"))
                        meta_tmp_path = Path(tmp_meta.name)

                    try:
                        meta_file_id = upload_file_to_folder(
                            creds,
                            folder_id,
                            meta_tmp_path,
                            "meta.json",
                            "application/json",
                        )

                        st.session_state.index.papers[paper_id] = PaperIndexEntry(
                            title=meta.title,
                            pdf_file_id=pdf_file_id,
                            meta_file_id=meta_file_id,
                            folder_id=folder_id,
                            tags=meta.tags,
                            status=meta.status,
                        )
                    finally:
                        meta_tmp_path.unlink(missing_ok=True)
                except Exception:
                    try:
                        delete_paper_folder(creds, folder_id)
                    except Exception as cleanup_error:
                        st.error(
                            f"Also failed to clean up orphaned Drive folder "
                            f"{folder_id}: {cleanup_error}"
                        )
                    raise
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:
            st.error(f"Failed to upload {uploaded_file.name}: {e}")
            all_succeeded = False
    return all_succeeded


def sync_paper_metadata(
    creds: Credentials, paper_info: PaperIndexEntry, local_meta_path: Path
) -> bool:
    """Refreshes the local metadata cache for a paper from Drive.

    Args:
        creds (Credentials): The Google OAuth credentials.
        paper_info (PaperIndexEntry): The paper's index entry, used to locate
            its metadata file on Drive.
        local_meta_path (Path): The local path to refresh with the latest
            metadata.

    Returns:
        True if metadata is safe to edit (freshly downloaded, or a
        pre-existing local cache survives a failed download). False if
        there's no reliable copy of the real metadata, so editing would
        risk overwriting it with defaults.
    """
    had_local_copy = local_meta_path.exists()
    try:
        download_file(creds, paper_info.meta_file_id, local_meta_path)
        return True
    except Exception as e:
        st.error(f"Failed to fetch metadata: {e}")
        return had_local_copy


def generate_metadata_for_paper(pid: str, local_pdf_path: Path) -> bool:
    """Generates a draft title/abstract/tags/embedding for a paper via Hugging Face.

    Extracts text from the paper's local PDF and calls the Hugging Face
    generation and embedding functions, staging the result (and any
    duplicate matches found in the current library index) in
    `st.session_state` for the metadata form to prefill. Does not write to
    local disk, Drive, or the library index - the user must still click
    "Save Changes" to persist the draft, and does not rerun, so callers can
    batch several papers before triggering a single rerun. Reports errors via
    `st.error`/`st.warning` rather than raising, since this is invoked from a
    best-effort UI action.

    The title/abstract/tags call and the embedding call are each already-
    paid-for Hugging Face requests, so they're staged independently: if
    title/abstract/tags succeed but the embedding call then fails (e.g. a
    402 quota error), the text draft is still staged and saveable rather
    than being discarded along with the failed embedding.

    Args:
        pid (str): The paper's unique ID.
        local_pdf_path (Path): Local path to the paper's downloaded PDF.

    Returns:
        bool: True if a draft (full or text-only) was staged, False if
        the PDF couldn't be read, generation was skipped, or the
        title/abstract/tags call itself failed (in which case an
        error/warning has already been shown).

    Sets:
        `st.session_state["hf_quota_exceeded"]`: True if either HF call
        failed because Hugging Face returned 402 Payment Required (monthly
        included credits used up), False otherwise. Callers that generate
        for multiple papers in a loop should check this after each call and
        stop the batch rather than retrying more calls doomed to fail the
        same way.
    """
    try:
        pdf_text = extract_pdf_text(local_pdf_path)
    except ValueError as e:
        st.error(str(e))
        return False
    if not pdf_text.strip():
        st.warning(
            "Could not extract text from this PDF (it may be scanned/"
            "image-only); metadata generation skipped."
        )
        return False

    st.session_state["hf_quota_exceeded"] = False
    try:
        existing_tags = get_all_tags(st.session_state.index)
        generated = generate_paper_metadata(pdf_text, existing_tags=existing_tags)
    except HFTokenMissingError as e:
        st.error(str(e))
        return False
    except HfHubHTTPError as e:
        if getattr(e.response, "status_code", None) == 402:
            st.session_state["hf_quota_exceeded"] = True
            st.error(
                "Hugging Face returned 402 Payment Required — your monthly "
                "included credits are used up. Purchase pre-paid credits or "
                "upgrade to PRO, then try again."
            )
        else:
            st.error(f"Metadata generation failed: {e}")
        return False
    except Exception as e:
        st.error(f"Metadata generation failed: {e}")
        return False

    # Stage the text draft now, before the embedding is even attempted -
    # this call already cost real HF credits, so a failure below must not
    # throw it away. Seed "embedding" from any existing embedding rather
    # than blanking it, so a failed re-generation doesn't lose a
    # previously-computed one if the user saves this draft as-is.
    existing_entry = st.session_state.index.papers.get(pid)
    existing_embedding = existing_entry.embedding if existing_entry else []
    st.session_state[f"generated_{pid}"] = {
        "title": generated.title,
        "abstract": generated.abstract,
        "tags": generated.tags,
        "embedding": existing_embedding,
    }
    # The form's widgets already have keys (title_{pid}, etc.) from their
    # first render, so passing value=... on later reruns has no effect -
    # Streamlit serves the widget's session_state entry instead. Write the
    # draft into those same keys directly so the form actually refreshes.
    st.session_state[f"title_{pid}"] = generated.title
    st.session_state[f"abstract_{pid}"] = generated.abstract
    st.session_state[f"tags_{pid}"] = ", ".join(generated.tags)

    try:
        embedding = embed_text(pdf_text)
    except HFTokenMissingError as e:
        st.error(str(e))
        return True
    except HfHubHTTPError as e:
        if getattr(e.response, "status_code", None) == 402:
            st.session_state["hf_quota_exceeded"] = True
            st.warning(
                "Title/abstract/tags generated, but the similarity-detection "
                "embedding failed: Hugging Face returned 402 Payment "
                "Required. You can still save this draft; duplicate "
                "detection won't be refreshed for it until you regenerate "
                "later."
            )
        else:
            st.warning(f"Title/abstract/tags generated, but embedding failed: {e}")
        return True
    except Exception as e:
        st.warning(f"Title/abstract/tags generated, but embedding failed: {e}")
        return True

    st.session_state[f"generated_{pid}"]["embedding"] = embedding
    st.session_state[f"dupes_{pid}"] = find_similar_papers(
        embedding, st.session_state.index, exclude_pid=pid
    )
    return True


def persist_generated_metadata(
    creds: Credentials,
    pid: str,
    index: LibraryIndex,
    local_meta_path: Path,
) -> bool:
    """Writes a paper's staged Hugging Face draft to local disk and Drive.

    Merges the draft staged under `st.session_state[f"generated_{pid}"]`
    (title/abstract/tags/embedding) onto the paper's existing metadata -
    loaded from `local_meta_path` if present, so previously-saved
    notes/citation/status survive - writes the result back to
    `local_meta_path`, uploads it to the paper's Drive folder, and updates
    its `PaperIndexEntry` in `index` in place. Does not call
    `upload_library_index`; batch callers persisting several papers should
    do that once after the whole batch, not once per paper.

    Args:
        creds (Credentials): The Google OAuth credentials.
        pid (str): The paper's unique ID.
        index (LibraryIndex): The library index; the paper's entry is
            mutated in place.
        local_meta_path (Path): Local path to the paper's meta.json cache.

    Returns:
        bool: True if a staged draft was found and persisted, False if
        there was nothing staged for this pid (no-op).
    """
    draft = st.session_state.get(f"generated_{pid}")
    if not draft:
        return False

    paper_info = index.papers[pid]
    meta = PaperMetadata(title=paper_info.title)
    if local_meta_path.exists():
        try:
            data = json.loads(local_meta_path.read_text(encoding="utf-8"))
            meta = PaperMetadata(**data)
        except Exception:
            meta = PaperMetadata(title=paper_info.title)

    meta = meta.model_copy(
        update={
            "title": strip_pdf_suffix(draft["title"]) or meta.title,
            "abstract": draft["abstract"],
            "tags": draft["tags"],
            "embedding": draft["embedding"],
        }
    )

    local_meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    upload_file_to_folder(
        creds, paper_info.folder_id, local_meta_path, "meta.json", "application/json"
    )

    paper_info = paper_info.model_copy(
        update={"title": meta.title, "tags": meta.tags, "embedding": meta.embedding}
    )
    index.papers[pid] = paper_info

    st.session_state.pop(f"generated_{pid}", None)
    st.session_state.pop(f"dupes_{pid}", None)
    return True


def generate_metadata_for_selected(
    creds: Credentials,
    pids: Sequence[str],
    index: LibraryIndex,
    papers_id: str,
    local_lib_dir: Path,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Generates and saves draft metadata for multiple papers.

    Shows a progress bar across the batch. Each paper's local PDF (and
    existing metadata cache) is downloaded first if not already cached
    (e.g. because the paper was never opened), mirroring the single-paper
    view's lazy download. Each paper's generated draft is written to its
    local meta.json, uploaded to its Drive folder, and applied to `index`
    as soon as it's generated - via `persist_generated_metadata` - so a
    paper is never left as an in-memory-only draft nobody will open again.
    The whole-library index is uploaded once, after the batch, rather than
    once per paper. A per-paper failure (download or persistence) is
    reported and the batch continues with the rest, a small delay is
    inserted between papers to avoid bursting Hugging Face's inference API,
    and the batch stops immediately (rather than continuing to burn through
    guaranteed failures) if a paper fails with a 402 Payment Required quota
    error.

    Args:
        creds (Credentials): The Google OAuth credentials.
        pids (Sequence[str]): The paper IDs to generate metadata for.
        index (LibraryIndex): The current library index; mutated in place
            as each paper's draft is persisted.
        papers_id (str): The Google Drive folder ID of the library's papers
            folder, used to upload the updated index once after the batch.
        local_lib_dir (Path): The local cache directory for the current
            library.
        sleep_fn (Callable[[float], None]): Called between papers to pace
            requests. Injected so tests never sleep for real.
    """
    total = len(pids)
    progress = st.progress(0.0, text=f"Generating metadata (0/{total})...")
    any_persisted = False
    for i, pid in enumerate(pids):
        entry = index.papers.get(pid)
        if entry is None:
            continue
        local_paper_dir = local_lib_dir / pid
        local_paper_dir.mkdir(parents=True, exist_ok=True)
        local_pdf_path = local_paper_dir / "paper.pdf"
        local_meta_path = local_paper_dir / "meta.json"
        if not local_pdf_path.exists():
            try:
                download_file(creds, entry.pdf_file_id, local_pdf_path)
            except Exception as e:
                st.error(f"Failed to load PDF for '{entry.title}': {e}")
                progress.progress(
                    (i + 1) / total, text=f"Generating metadata ({i + 1}/{total})..."
                )
                continue
        sync_paper_metadata(creds, entry, local_meta_path)
        if generate_metadata_for_paper(pid, local_pdf_path):
            try:
                if persist_generated_metadata(creds, pid, index, local_meta_path):
                    any_persisted = True
            except Exception as e:
                st.error(
                    f"Generated metadata for '{entry.title}' but failed to save it: {e}"
                )
        if st.session_state.get("hf_quota_exceeded"):
            st.warning(
                f"Stopped after {i + 1}/{total} paper(s): Hugging Face "
                "credits are exhausted for now. Wait a bit, or generate one "
                "paper at a time."
            )
            break
        progress.progress(
            (i + 1) / total, text=f"Generating metadata ({i + 1}/{total})..."
        )
        if i + 1 < total:
            sleep_fn(BULK_GENERATE_DELAY_SECONDS)
    progress.empty()

    if any_persisted:
        try:
            upload_library_index(creds, papers_id, index)
        except Exception as e:
            st.error(f"Generated metadata was saved, but syncing the index failed: {e}")


def init_library_state(
    creds: Credentials, lib_id: str, papers_id: str, lib_name: str
) -> None:
    """Resets session state for a newly opened or newly created library.

    Also clears any pending request to force the manual library-selection
    screen, so a later single-library session goes back to auto-opening.

    Args:
        creds (Credentials): The Google OAuth credentials (unused directly,
            kept for a consistent signature with other library-scoped calls).
        lib_id (str): The Google Drive file ID of the library folder.
        papers_id (str): The Google Drive file ID of the library's papers folder.
        lib_name (str): The library's human-readable display name, shown in
            the sidebar in place of the opaque Drive file ID.
    """
    st.session_state.current_lib_id = lib_id
    st.session_state.current_lib_name = lib_name
    st.session_state.current_papers_id = papers_id
    st.session_state.local_lib_dir = PAPERS_DIR / lib_id
    st.session_state.local_lib_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.local_index_path = (
        st.session_state.local_lib_dir / "id-mapping.json"
    )
    st.session_state.selected_paper = None
    st.session_state.pop("manual_library_selection", None)


def sync_library_index(creds: Credentials) -> None:
    """Syncs the local library index cache with Drive if it's out of date.

    Downloads the remote id-mapping.json when it's missing locally or its
    modifiedTime differs from the last synced value, then loads it into
    st.session_state.index. On a download failure, falls back to the
    existing local cache if there is one, or an empty LibraryIndex
    otherwise. Falls back to an empty LibraryIndex on a parse failure.

    Args:
        creds (Credentials): The Google OAuth credentials.
    """
    papers_id = st.session_state.current_papers_id
    local_path = st.session_state.local_index_path

    remote_info = get_library_index_file(creds, papers_id)
    if not remote_info:
        st.session_state.index = LibraryIndex()
        st.session_state.last_sync_time = None
        return

    remote_time = remote_info.get("modifiedTime")
    if not local_path.exists() or st.session_state.get("last_sync_time") != remote_time:
        try:
            download_file(creds, remote_info["id"], local_path)
            st.session_state.last_sync_time = remote_time
        except Exception as e:
            st.error(f"Failed to sync library index: {e}")
            if not local_path.exists():
                st.session_state.index = LibraryIndex()
                return

    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            st.session_state.index = LibraryIndex(**data)
        except Exception as e:
            st.error(f"Failed to parse library index: {e}")
            st.session_state.index = LibraryIndex()
    else:
        st.session_state.index = LibraryIndex()


def get_all_tags(index: LibraryIndex) -> list[str]:
    """Returns every distinct tag used across the library, sorted alphabetically.

    Args:
        index: The library index to scan.

    Returns:
        list[str]: The sorted, deduplicated tags used by any paper in `index`.
    """
    return sorted({tag for p in index.papers.values() for tag in p.tags})


def get_duplicate_pids(index: LibraryIndex) -> set[str]:
    """Finds every paper whose embedding matches another paper in the index.

    Computed fresh from the persisted embeddings already in `index` (rather
    than from the ephemeral `dupes_{pid}` session-state key that's only
    populated right after generation), so the result is available for any
    paper regardless of when its embedding was generated or whether the
    user has navigated away and back.

    The underlying pairwise comparison is O(N^2) in the number of papers,
    but this is called on every Streamlit rerun (i.e. on every click or
    keystroke anywhere in the app), so the result is cached in
    `st.session_state` under a signature of every paper's embedding
    content. `st.session_state.index` is typically the same object mutated
    in place across reruns, so caching by object identity wouldn't detect
    embedding changes - the signature is recomputed each call (an O(N)
    operation) and only triggers the expensive O(N^2) scan when it
    actually differs from the last cached signature.

    Args:
        index: The library index to scan.

    Returns:
        set[str]: The paper IDs with at least one similar-embedding match
        elsewhere in the index, per `find_similar_papers`'s default
        threshold.
    """
    signature = tuple(
        sorted(
            (pid, tuple(entry.embedding))
            for pid, entry in index.papers.items()
            if entry.embedding
        )
    )
    cached = st.session_state.get("_duplicate_pids_cache")
    if cached is not None and cached[0] == signature:
        return cached[1]

    result = {
        pid
        for pid, entry in index.papers.items()
        if entry.embedding
        and find_similar_papers(entry.embedding, index, exclude_pid=pid)
    }
    st.session_state["_duplicate_pids_cache"] = (signature, result)
    return result


def get_missing_metadata_pids(index: LibraryIndex) -> set[str]:
    """Finds every paper with no generated tags or embedding yet.

    Args:
        index: The library index to scan.

    Returns:
        set[str]: The paper IDs that have never had metadata generated.
    """
    return {
        pid
        for pid, entry in index.papers.items()
        if not entry.tags and not entry.embedding
    }


def filter_papers(
    papers: dict[str, PaperIndexEntry],
    search_query: str,
    status_filter: Sequence[str],
    tags_filter: Sequence[str],
) -> list[tuple[str, PaperIndexEntry]]:
    """Filters and sorts the library's papers for sidebar display.

    Args:
        papers: Mapping of paper ID to its index entry, as stored in
            LibraryIndex.papers.
        search_query: Lowercased search text; a paper matches if its title
            contains this text.
        status_filter: Statuses to restrict results to. Empty means no
            status filtering.
        tags_filter: Tags to restrict results to; a paper matches if it has
            at least one of these tags. Empty means no tag filtering.

    Returns:
        list[tuple[str, PaperIndexEntry]]: Matching (paper_id, entry) pairs,
        sorted by title. Entries whose key isn't a 32-character hex paper ID
        (e.g. a legacy/malformed index entry) are always skipped.
    """
    matches: list[tuple[str, PaperIndexEntry]] = []
    for pid, p in papers.items():
        if not re.match(r"^[a-f0-9]{32}$", pid):
            continue
        if search_query not in p.title.lower():
            continue
        if status_filter and p.status not in status_filter:
            continue
        if tags_filter and not any(tag in p.tags for tag in tags_filter):
            continue
        matches.append((pid, p))
    return sorted(matches, key=lambda x: x[1].title)


def delete_selected_papers(
    creds: Credentials,
    pids: Sequence[str],
    index: LibraryIndex,
    papers_id: str,
    local_lib_dir: Path,
) -> bool:
    """Deletes multiple papers from Drive, the library index, and disk.

    Each paper's Drive folder is removed individually - a failure there
    leaves that paper untouched and reports an error, but doesn't stop the
    rest of the batch. The index is then updated once for every paper that
    was actually deleted. If uploading the updated index fails, every
    removed entry is restored locally so a future sync doesn't merge it
    back in from the unchanged remote index as a broken entry.

    Args:
        creds: The Google OAuth credentials.
        pids: The paper IDs to delete.
        index: The current library index; mutated in place.
        papers_id: The Google Drive folder ID of the library's papers folder.
        local_lib_dir: The local cache directory for this library.

    Returns:
        bool: True only if every requested paper was deleted from Drive and
        (when there was anything to upload) the index upload succeeded.
        False if any individual paper's Drive deletion failed, or if the
        index upload failed.
    """
    all_succeeded = True
    removed: dict[str, PaperIndexEntry] = {}
    for pid in pids:
        entry = index.papers.get(pid)
        if entry is None:
            continue
        try:
            delete_paper_folder(creds, entry.folder_id)
        except Exception as e:
            st.error(f"Failed to delete paper '{entry.title}': {e}")
            all_succeeded = False
            continue
        removed[pid] = index.papers.pop(pid)

    if not removed:
        return all_succeeded

    try:
        upload_library_index(creds, papers_id, index, deleted_pids=set(removed))
    except Exception as e:
        for pid, entry in removed.items():
            index.papers[pid] = entry
        st.error(f"Failed to sync deletion: {e}")
        return False

    for pid in removed:
        if st.session_state.selected_paper == pid:
            st.session_state.selected_paper = None
        shutil.rmtree(local_lib_dir / pid, ignore_errors=True)

    return all_succeeded


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
                    with st.spinner("Uploading to Google Drive..."):
                        try:
                            all_succeeded = upload_papers(creds, uploaded_files)
                        finally:
                            upload_library_index(
                                creds,
                                st.session_state.current_papers_id,
                                st.session_state.index,
                            )
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
        if not re.match(r"^[a-f0-9]{32}$", pid):
            st.error("Invalid paper ID format.")
            st.stop()
        paper_info = st.session_state.index.papers[pid]

        local_paper_dir = st.session_state.local_lib_dir / pid
        local_paper_dir.mkdir(parents=True, exist_ok=True)
        local_pdf_path = local_paper_dir / "paper.pdf"
        local_meta_path = local_paper_dir / "meta.json"

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
                base_url = os.environ.get("FASTAPI_URL", "http://localhost:8000")
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
                            "meta.json",
                            "application/json",
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

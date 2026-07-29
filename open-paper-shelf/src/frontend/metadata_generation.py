"""Generating, staging, and persisting Hugging Face-drafted paper metadata."""

import json
import time
from pathlib import Path
from typing import Callable, Sequence

import streamlit as st
from google.oauth2.credentials import Credentials
from huggingface_hub.errors import HfHubHTTPError

from backend.drive import download_file, upload_file_to_folder, upload_library_index
from backend.huggingface_client import (
    HFTokenMissingError,
    embed_text,
    extract_pdf_text,
    find_similar_papers,
    generate_paper_metadata,
)
from backend.models import LibraryIndex, PaperIndexEntry, PaperMetadata
from frontend.constants import BULK_GENERATE_DELAY_SECONDS
from frontend.library_filters import get_all_tags
from frontend.text_utils import strip_pdf_suffix


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

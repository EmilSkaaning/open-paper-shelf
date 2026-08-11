"""Bulk PDF packaging for the "Download marked PDFs" icon-bar action."""

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from google.oauth2.credentials import Credentials

from backend.drive import download_file
from backend.models import LibraryIndex
from frontend.constants import EDITED_PDF_FILENAME, PDF_FILENAME

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


@dataclass(frozen=True)
class ZipBuildResult:
    """Result of zipping the marked papers' PDFs for download.

    Attributes:
        data: The zip archive bytes, ready for st.download_button.
        included: Titles of papers successfully added to the archive.
        skipped: Titles (or pids, if the pid was unknown) of papers whose
            PDF could not be fetched or added.
    """

    data: bytes
    included: list[str]
    skipped: list[str]


def zip_marked_pdfs(
    creds: Credentials,
    pids: Sequence[str],
    index: LibraryIndex,
    local_lib_dir: Path,
) -> ZipBuildResult:
    """Builds a zip of PDFs for the given paper IDs.

    For each pid, ensures the PDF (edited copy preferred, else raw) is
    present in the local cache - downloading it from Drive first if
    missing, mirroring the download-if-missing pattern used by the paper
    viewer in app.py - then adds it to an in-memory zip. A paper whose PDF
    can't be fetched is skipped (recorded in `skipped`) rather than
    failing the whole batch.

    Args:
        creds: The Google OAuth credentials.
        pids: The paper IDs to include.
        index: The current library index (read-only here).
        local_lib_dir: The local cache directory for this library.

    Returns:
        ZipBuildResult: the zip bytes plus per-paper included/skipped titles.
    """
    included: list[str] = []
    skipped: list[str] = []
    used_names: set[str] = set()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for pid in pids:
            entry = index.papers.get(pid)
            if entry is None:
                skipped.append(pid)
                continue

            local_paper_dir = local_lib_dir / pid
            if entry.edited_pdf_file_id:
                filename = EDITED_PDF_FILENAME
                file_id = entry.edited_pdf_file_id
            else:
                filename = PDF_FILENAME
                file_id = entry.pdf_file_id
            local_path = local_paper_dir / filename

            if not local_path.exists():
                try:
                    local_paper_dir.mkdir(parents=True, exist_ok=True)
                    download_file(creds, file_id, local_path)
                except Exception:
                    logger.exception(
                        "Failed to download PDF for paper %r (pid=%s)", entry.title, pid
                    )
                    skipped.append(entry.title)
                    continue

            arcname = _unique_arcname(entry.title, pid, used_names)
            zf.write(local_path, arcname=arcname)
            included.append(entry.title)

    return ZipBuildResult(data=buffer.getvalue(), included=included, skipped=skipped)


def zip_download_filename(library_name: str, now: datetime | None = None) -> str:
    """Builds the download filename for a marked-PDFs zip.

    The full `yyyy-mm-dd-HHMMSS` timestamp (not just the date) makes the
    filename close to unique across repeated downloads of the same
    library on the same day.

    Args:
        library_name: The current library's display name.
        now: The timestamp to stamp the filename with; defaults to now.

    Returns:
        str: `<yyyy-mm-dd-HHMMSS>-<sanitized library name>.zip`.
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", library_name).strip() or library_name
    return f"{stamp}-{sanitized}.zip"


def _unique_arcname(title: str, pid: str, used_names: set[str]) -> str:
    """Builds a collision-free in-zip filename for a paper's PDF.

    Args:
        title: The paper's title, used as the base filename.
        pid: The paper's ID, used as a disambiguating suffix on collision.
        used_names: Names already claimed in the current archive; mutated
            in place to record the returned name.

    Returns:
        str: A sanitized `<name>.pdf` filename unique within `used_names`.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or pid
    name = f"{sanitized}.pdf"
    if name in used_names:
        name = f"{sanitized}_{pid[:8]}.pdf"
    used_names.add(name)
    return name

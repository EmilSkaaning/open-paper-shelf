"""Checks whether the backend is configured for a non-loopback host.

Kept separate from `backend.main` (which instantiates the FastAPI app and
mounts `StaticFiles` as import-time side effects) so the frontend can import
just this pure check without pulling those in.
"""

import os
from urllib.parse import urlparse

# Mirrors frontend.constants.DEFAULT_FASTAPI_URL - kept as a literal here
# rather than imported, since backend must not depend on frontend.
DEFAULT_FASTAPI_URL = "http://localhost:8000"

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def get_non_loopback_host_warning() -> str | None:
    """Checks whether the backend is reachable via a non-loopback host.

    The `FASTAPI_URL` environment variable (defaulting to
    `DEFAULT_FASTAPI_URL`) is the address the Streamlit frontend and its
    browser-side pdf.js viewer use to reach the backend's unauthenticated
    `/papers` mount. Overriding it to a LAN or public hostname makes those
    PDFs reachable to anyone else on that network, since the endpoint has
    no authentication (see docs/ARCHITECTURE.md's security note).

    Returns:
        A human-readable warning if `FASTAPI_URL` resolves to a
        non-loopback host, otherwise None.
    """
    fastapi_url = os.environ.get("FASTAPI_URL", DEFAULT_FASTAPI_URL)
    hostname = urlparse(fastapi_url).hostname
    if hostname is None or hostname in LOOPBACK_HOSTNAMES:
        return None
    return (
        f"Backend is configured for a non-loopback host ({hostname!r}). "
        "The /papers PDF endpoint has no authentication, so anyone on that "
        "network can access your papers. See the security note in "
        "docs/ARCHITECTURE.md before deploying beyond localhost."
    )

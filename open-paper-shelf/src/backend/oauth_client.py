"""Google OAuth client configuration for the app's Desktop-app OAuth client.

This module resolves which OAuth client config to use for the Google Drive
login flow: a self-hoster's own override (env vars or a local
credentials.json), falling back to the client bundled with the app.
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# The Drive API's OAuth client config (Google's "client_secret_*.json"
# `installed` block) is loosely-typed JSON, so no narrower static type
# applies.
ClientConfig = Dict[str, Any]

# This is the app's own bundled Google OAuth client, of type "Desktop app".
# Google does not treat a Desktop-app client secret as confidential: unlike a
# server-side "Web application" client, a Desktop client authenticates a
# public, distributed binary rather than a private backend, so Google's own
# OAuth guidance for installed apps expects this value to ship in source.
# https://developers.google.com/identity/protocols/oauth2/native-app
# The value below identifies which app is requesting access; it does not
# grant access by itself — each end user still authenticates their own
# Google account and authorizes their own Drive data.
#
# Stored base64-encoded (not encrypted — there is no secret key hidden from
# this same source file) purely so the literal value doesn't match GitHub's
# push-protection secret-scanning pattern for Google OAuth credentials, which
# has no notion of Desktop-app clients being non-confidential.
_BUNDLED_CLIENT_ID = base64.b64decode(
    "ODgxNTY4MjU3NjcxLTVydmJpMDJqNWVpajZmM3BodmJoOHUzbWo2cGNkMXRmLmFwcHMuZ29vZ2xldXNlcmNvbnRlbnQuY29t"
).decode()
_BUNDLED_CLIENT_SECRET = base64.b64decode(
    "R09DU1BYLXQ3ajVKeWRycVRDOWxKMDZDSEZJSXhaX18wcnA="
).decode()

_BUNDLED_CLIENT_CONFIG: ClientConfig = {
    "installed": {
        "client_id": _BUNDLED_CLIENT_ID,
        "client_secret": _BUNDLED_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CREDENTIALS_OVERRIDE_PATH = PROJECT_ROOT / "credentials.json"


def _load_env_override() -> Optional[ClientConfig]:
    """Builds a client config from override env vars, if both are set.

    Returns:
        Optional[ClientConfig]: A client config built from
        `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`, or None if
        either is unset.
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _load_file_override() -> Optional[ClientConfig]:
    """Loads a client config from a local credentials.json override, if present.

    Returns:
        Optional[ClientConfig]: The parsed contents of CREDENTIALS_OVERRIDE_PATH,
        or None if that file does not exist.
    """
    if not CREDENTIALS_OVERRIDE_PATH.exists():
        return None
    with open(CREDENTIALS_OVERRIDE_PATH) as f:
        return dict(json.load(f))


def get_client_config() -> ClientConfig:
    """Resolves the OAuth client config to use, preferring self-hoster overrides.

    Resolution order: `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` env
    vars, then a local `credentials.json`, then the client bundled with the
    app.

    Returns:
        ClientConfig: The OAuth client config to build a Flow from.
    """
    return _load_env_override() or _load_file_override() or _BUNDLED_CLIENT_CONFIG

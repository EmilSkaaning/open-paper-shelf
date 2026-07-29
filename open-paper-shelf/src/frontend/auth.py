"""Google OAuth authentication flow for the Streamlit frontend."""

from typing import Optional, cast

import streamlit as st
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field, ValidationError

from backend.drive import (
    OAUTH_FLOWS,
    add_oauth_flow,
    get_oauth_flow,
    load_credentials_from_file,
    save_credentials,
)


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

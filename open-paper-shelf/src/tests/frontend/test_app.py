import json
from unittest.mock import MagicMock, patch

import pytest

import frontend.app as app
from backend.models import LibraryIndex


class FakeSessionState(dict):
    """Minimal attribute-accessible dict standing in for st.session_state."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key)


class StopRerun(BaseException):
    """Raised by our mocked st.rerun() to mimic Streamlit halting the script.

    Subclasses BaseException (not Exception), matching Streamlit's real
    RerunException, so it isn't swallowed by an `except Exception` in the
    code under test.
    """


@pytest.fixture(autouse=True)
def clear_oauth_flows():
    app.OAUTH_FLOWS.clear()
    yield
    app.OAUTH_FLOWS.clear()


@pytest.fixture
def fake_st(monkeypatch):
    mock_st = MagicMock()
    mock_st.session_state = FakeSessionState()
    mock_st.query_params = {}
    mock_st.rerun.side_effect = StopRerun
    monkeypatch.setattr(app, "st", mock_st)
    return mock_st


# --- sync_library_index -----------------------------------------------------


def test_sync_library_index_no_remote_file(fake_st):
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.local_index_path = None

    with patch.object(app, "get_library_index_file", return_value=None):
        app.sync_library_index(creds=MagicMock())

    assert fake_st.session_state.index == LibraryIndex()
    assert fake_st.session_state.last_sync_time is None


def test_sync_library_index_download_failure_falls_back_to_empty_index(
    fake_st, tmp_path
):
    local_path = tmp_path / "id-mapping.json"
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.local_index_path = local_path

    with (
        patch.object(
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        ),
        patch.object(app, "download_file", side_effect=RuntimeError("network blip")),
    ):
        app.sync_library_index(creds=MagicMock())

    assert fake_st.session_state.index == LibraryIndex()
    fake_st.error.assert_called_once()


def test_sync_library_index_corrupted_local_file_falls_back_to_empty_index(
    fake_st, tmp_path
):
    local_path = tmp_path / "id-mapping.json"
    local_path.write_text("not valid json")
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.local_index_path = local_path
    fake_st.session_state.last_sync_time = "t1"

    with (
        patch.object(
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        ),
        patch.object(app, "download_file") as mock_download,
    ):
        app.sync_library_index(creds=MagicMock())
        mock_download.assert_not_called()

    assert fake_st.session_state.index == LibraryIndex()
    fake_st.error.assert_called_once()


def test_sync_library_index_happy_path_parses_downloaded_index(fake_st, tmp_path):
    local_path = tmp_path / "id-mapping.json"
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.local_index_path = local_path

    valid_data = {
        "papers": {
            "abc123": {
                "title": "A Paper",
                "pdf_file_id": "pdf1",
                "meta_file_id": "meta1",
                "folder_id": "folder1",
            }
        }
    }

    def fake_download(creds, file_id, dest_path):
        dest_path.write_text(json.dumps(valid_data))

    with (
        patch.object(
            app,
            "get_library_index_file",
            return_value={"id": "idx", "modifiedTime": "t1"},
        ),
        patch.object(app, "download_file", side_effect=fake_download),
    ):
        app.sync_library_index(creds=MagicMock())

    assert fake_st.session_state.index == LibraryIndex(**valid_data)
    assert fake_st.session_state.last_sync_time == "t1"
    fake_st.error.assert_not_called()


# --- upload_papers -----------------------------------------------------------


def make_uploaded_file(name: str, content: bytes = b"pdf-bytes") -> MagicMock:
    f = MagicMock()
    f.name = name
    f.getvalue.return_value = content
    return f


def test_upload_papers_all_succeed(fake_st):
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.index = LibraryIndex()
    files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]

    with (
        patch.object(app, "create_paper_folder", side_effect=["folder1", "folder2"]),
        patch.object(
            app,
            "upload_file_to_folder",
            side_effect=["pdf1", "meta1", "pdf2", "meta2"],
        ),
    ):
        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

    assert result is True
    assert len(fake_st.session_state.index.papers) == 2
    fake_st.error.assert_not_called()


def test_upload_papers_partial_failure_reports_error_and_keeps_successful_ones(
    fake_st,
):
    """Regression test: a failed file must not be silently dropped, and the
    caller must be told (via the return value) that not everything succeeded,
    so it doesn't unconditionally show success/rerun past a real error."""
    fake_st.session_state.current_papers_id = "papers_123"
    fake_st.session_state.index = LibraryIndex()
    files = [make_uploaded_file("a.pdf"), make_uploaded_file("b.pdf")]

    with (
        patch.object(
            app, "create_paper_folder", side_effect=["folder1", RuntimeError("boom")]
        ),
        patch.object(app, "upload_file_to_folder", side_effect=["pdf1", "meta1"]),
    ):
        result = app.upload_papers(creds=MagicMock(), uploaded_files=files)

    assert result is False
    assert len(fake_st.session_state.index.papers) == 1
    fake_st.error.assert_called_once()
    assert "b.pdf" in fake_st.error.call_args[0][0]


# --- authenticate_user (OAuth return path) ----------------------------------


def test_authenticate_user_uses_cached_flow_when_session_state_is_lost(fake_st):
    """Regression test: even if st.session_state was reset across the Google
    redirect, the flow cached in the global OAUTH_FLOWS dict (keyed by state)
    must still be found and used, so login succeeds."""
    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_flow.credentials = mock_creds
    app.OAUTH_FLOWS["state1"] = mock_flow

    fake_st.query_params = {"code": "abc123", "state": "state1"}
    # session_state starts empty, simulating a lost session across the redirect.

    with (
        patch.object(app, "load_credentials_from_file", return_value=None),
        patch.object(app, "save_credentials") as mock_save_creds,
        pytest.raises(StopRerun),
    ):
        app.authenticate_user()

    mock_flow.fetch_token.assert_called_once_with(code="abc123")
    mock_save_creds.assert_called_once_with(mock_creds)
    fake_st.error.assert_not_called()
    assert "state1" not in app.OAUTH_FLOWS


def test_authenticate_user_rejects_unknown_state(fake_st):
    """An unknown/forged state (not in OAUTH_FLOWS) must be rejected as a
    possible CSRF attempt, never call fetch_token."""
    fake_st.query_params = {"code": "abc123", "state": "forged-state"}

    with patch.object(app, "load_credentials_from_file", return_value=None):
        result = app.authenticate_user()

    assert result is None
    fake_st.error.assert_called_once()
    assert "State mismatch" in fake_st.error.call_args[0][0]

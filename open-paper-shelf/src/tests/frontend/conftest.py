"""Pytest configuration and fixtures for the frontend tests."""

from typing import Any, Generator
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

import frontend.app as app
import frontend.auth as auth
import frontend.library as library
import frontend.library_filters as library_filters
import frontend.metadata_generation as metadata_generation
import frontend.uploads as uploads


class FakeSessionState(dict):
    """Minimal attribute-accessible dict standing in for st.session_state."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def __delattr__(self, key: str) -> None:
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


def _columns_side_effect(spec: object, **_kwargs: object) -> tuple[MagicMock, ...]:
    """Builds the right number of column mocks for a given st.columns() spec.

    Args:
        spec: The column spec passed to st.columns() — an int count or a
            sequence of relative widths.
        _kwargs: Unused; absorbs st.columns() keyword arguments like `gap`.

    Returns:
        tuple[MagicMock, ...]: One fresh MagicMock per requested column.
    """
    count = spec if isinstance(spec, int) else len(spec)  # type: ignore[arg-type]
    return tuple(MagicMock() for _ in range(count))


def make_uploaded_file(name: str, content: bytes = b"pdf-bytes") -> MagicMock:
    """Builds a mock standing in for a Streamlit UploadedFile.

    Args:
        name: The filename the mock should report.
        content: The raw bytes `.getvalue()` should return.

    Returns:
        MagicMock: A mock exposing `.name` and `.getvalue()`.
    """
    uploaded_file = MagicMock()
    uploaded_file.name = name
    uploaded_file.getvalue.return_value = content
    return uploaded_file


@pytest.fixture
def stop_rerun() -> type[BaseException]:
    """Provides the StopRerun class used by fake_st's mocked rerun().

    Test modules must obtain the class through this fixture rather than
    importing it directly - importing conftest.py as a regular submodule
    creates a distinct class object from the one pytest loads internally
    for fixture resolution, which would break isinstance/except matching.

    Returns:
        type[BaseException]: The StopRerun exception class.
    """
    return StopRerun


@pytest.fixture(autouse=True)
def clear_oauth_flows() -> Generator[None, None, None]:
    """Clears the module-level OAuth flow cache before and after each test."""
    app.OAUTH_FLOWS.clear()
    yield
    app.OAUTH_FLOWS.clear()


@pytest.fixture
def fake_st(mocker: MockerFixture) -> MagicMock:
    """Replaces frontend.app's `st` module reference with a scriptable mock.

    Args:
        mocker (MockerFixture): The pytest-mock fixture.

    Returns:
        MagicMock: The mock standing in for streamlit, with a real
        FakeSessionState for session_state, an empty query_params dict, a
        `multiselect()` defaulting to an empty selection (so unrelated
        filters don't unexpectedly exclude everything), and a `rerun()`
        that raises StopRerun (mirroring Streamlit's real halt behavior on
        rerun).
    """
    mock_st = MagicMock()
    mock_st.session_state = FakeSessionState()
    mock_st.query_params = {}
    mock_st.button.return_value = False
    mock_st.multiselect.return_value = []
    mock_st.rerun.side_effect = StopRerun
    mock_st.columns.side_effect = _columns_side_effect
    mocker.patch.object(app, "st", mock_st)
    # Each of these modules independently does `import streamlit as st`, so
    # patching app.st alone doesn't reach their calls - every module that
    # calls st.* internally needs the same fake patched onto its own binding.
    mocker.patch.object(auth, "st", mock_st)
    mocker.patch.object(uploads, "st", mock_st)
    mocker.patch.object(metadata_generation, "st", mock_st)
    mocker.patch.object(library, "st", mock_st)
    mocker.patch.object(library_filters, "st", mock_st)
    return mock_st

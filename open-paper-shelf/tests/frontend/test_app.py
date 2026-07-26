"""Unit tests for Streamlit app startup metadata sync."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


class MockSessionState(dict):
    """Mock Streamlit session state object supporting both dict and attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'SessionState' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(f"'SessionState' object has no attribute '{name}'")


class TestAppMetadataSync:
    """Test suite for app startup metadata syncing logic."""

    @patch("frontend.app.authenticate_user")
    @patch("frontend.app.get_or_create_library_folder")
    @patch("frontend.app.list_pdfs_in_library")
    @patch("frontend.app.list_metadata_in_library")
    @patch("frontend.app.download_metadata")
    @patch("frontend.app.st")
    def test_startup_syncs_metadata(
        self,
        mock_st: MagicMock,
        mock_download_metadata: MagicMock,
        mock_list_metadata: MagicMock,
        mock_list_pdfs: MagicMock,
        mock_get_folder: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test metadata is downloaded and loaded into st.session_state on startup.

        Args:
            mock_st: Mock for streamlit module.
            mock_download_metadata: Mock for download_metadata.
            mock_list_metadata: Mock for list_metadata_in_library.
            mock_list_pdfs: Mock for list_pdfs_in_library.
            mock_get_folder: Mock for get_or_create_library_folder.
            mock_auth: Mock for authenticate_user.
            tmp_path: Temporary directory fixture.
        """
        mock_creds = MagicMock()
        mock_auth.return_value = mock_creds
        mock_get_folder.return_value = "folder_123"
        mock_list_pdfs.return_value = []

        # Mock Drive metadata files
        mock_list_metadata.return_value = [
            {"id": "meta_1", "name": "paper1_meta.json"},
            {"id": "meta_2", "name": "paper2_meta.json"},
            {"id": "other_file", "name": "readme.txt"},
        ]

        session_state = MockSessionState()
        mock_st.session_state = session_state
        mock_st.columns.return_value = [MagicMock(), MagicMock()]

        # When download_metadata is called, write test JSON file
        def fake_download(_creds: Any, file_id: str, dest_path: Path) -> None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if file_id == "meta_1":
                dest_path.write_text(json.dumps({"title": "Paper One"}))
            elif file_id == "meta_2":
                dest_path.write_text(json.dumps({"title": "Paper Two"}))

        mock_download_metadata.side_effect = fake_download

        with patch("frontend.app.PAPERS_DIR", tmp_path):
            from frontend.app import main

            main()

        assert session_state.folder_id == "folder_123"
        assert "metadata" in session_state
        assert session_state.metadata == {
            "paper1": {"title": "Paper One"},
            "paper2": {"title": "Paper Two"},
        }
        assert mock_download_metadata.call_count == 2

    @patch("frontend.app.authenticate_user")
    @patch("frontend.app.get_or_create_library_folder")
    @patch("frontend.app.list_pdfs_in_library")
    @patch("frontend.app.list_metadata_in_library")
    @patch("frontend.app.download_metadata")
    @patch("frontend.app.st")
    def test_startup_uses_existing_local_metadata(
        self,
        mock_st: MagicMock,
        mock_download_metadata: MagicMock,
        mock_list_metadata: MagicMock,
        mock_list_pdfs: MagicMock,
        mock_get_folder: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test existing local metadata file is not re-downloaded on startup.

        Args:
            mock_st: Mock for streamlit module.
            mock_download_metadata: Mock for download_metadata.
            mock_list_metadata: Mock for list_metadata_in_library.
            mock_list_pdfs: Mock for list_pdfs_in_library.
            mock_get_folder: Mock for get_or_create_library_folder.
            mock_auth: Mock for authenticate_user.
            tmp_path: Temporary directory fixture.
        """
        mock_creds = MagicMock()
        mock_auth.return_value = mock_creds
        mock_get_folder.return_value = "folder_123"
        mock_list_pdfs.return_value = []

        mock_list_metadata.return_value = [
            {"id": "meta_1", "name": "paper1_meta.json"},
        ]

        # Pre-create local metadata file
        meta_dir = tmp_path / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "paper1_meta.json").write_text(
            json.dumps({"title": "Cached Paper"})
        )

        session_state = MockSessionState()
        mock_st.session_state = session_state
        mock_st.columns.return_value = [MagicMock(), MagicMock()]

        with patch("frontend.app.PAPERS_DIR", tmp_path):
            from frontend.app import main

            main()

        assert mock_download_metadata.call_count == 0
        assert session_state.metadata == {"paper1": {"title": "Cached Paper"}}

    @patch("frontend.app.authenticate_user")
    @patch("frontend.app.get_or_create_library_folder")
    @patch("frontend.app.list_pdfs_in_library")
    @patch("frontend.app.list_metadata_in_library")
    @patch("frontend.app.download_metadata")
    @patch("frontend.app.st")
    def test_startup_handles_corrupt_metadata_json(
        self,
        mock_st: MagicMock,
        mock_download_metadata: MagicMock,
        mock_list_metadata: MagicMock,
        mock_list_pdfs: MagicMock,
        mock_get_folder: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test error is reported when metadata JSON fails to parse.

        Args:
            mock_st: Mock for streamlit module.
            mock_download_metadata: Mock for download_metadata.
            mock_list_metadata: Mock for list_metadata_in_library.
            mock_list_pdfs: Mock for list_pdfs_in_library.
            mock_get_folder: Mock for get_or_create_library_folder.
            mock_auth: Mock for authenticate_user.
            tmp_path: Temporary directory fixture.
        """
        mock_creds = MagicMock()
        mock_auth.return_value = mock_creds
        mock_get_folder.return_value = "folder_123"
        mock_list_pdfs.return_value = []

        mock_list_metadata.return_value = [
            {"id": "meta_corrupt", "name": "corrupt_meta.json"},
        ]

        meta_dir = tmp_path / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "corrupt_meta.json").write_text("invalid json content")

        session_state = MockSessionState()
        mock_st.session_state = session_state
        mock_st.columns.return_value = [MagicMock(), MagicMock()]

        with patch("frontend.app.PAPERS_DIR", tmp_path):
            from frontend.app import main

            main()

        mock_st.error.assert_called_once()
        assert "Failed to load metadata for corrupt" in mock_st.error.call_args[0][0]

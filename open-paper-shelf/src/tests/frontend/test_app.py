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
        mock_st.button.return_value = False

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
        mock_st.button.return_value = False

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
        mock_st.button.return_value = False

        with patch("frontend.app.PAPERS_DIR", tmp_path):
            from frontend.app import main

            main()

        mock_st.error.assert_called_once()
        assert "Failed to load metadata for corrupt" in mock_st.error.call_args[0][0]

    @patch("frontend.app.upload_metadata")
    @patch("frontend.app.download_pdf")
    @patch("frontend.app.authenticate_user")
    @patch("frontend.app.get_or_create_library_folder")
    @patch("frontend.app.list_pdfs_in_library")
    @patch("frontend.app.list_metadata_in_library")
    @patch("frontend.app.st")
    def test_selected_paper_renders_metadata_form_and_saves(
        self,
        mock_st: MagicMock,
        mock_list_metadata: MagicMock,
        mock_list_pdfs: MagicMock,
        mock_get_folder: MagicMock,
        mock_auth: MagicMock,
        _mock_download_pdf: MagicMock,
        mock_upload_metadata: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that selecting a paper renders metadata edit form and form submission updates state and uploads.

        Args:
            mock_st: Mock for streamlit module.
            mock_list_metadata: Mock for list_metadata_in_library.
            mock_list_pdfs: Mock for list_pdfs_in_library.
            mock_get_folder: Mock for get_or_create_library_folder.
            mock_auth: Mock for authenticate_user.
            _mock_download_pdf: Mock for download_pdf.
            mock_upload_metadata: Mock for upload_metadata.
            tmp_path: Temporary directory fixture.
        """
        mock_creds = MagicMock()
        mock_auth.return_value = mock_creds
        mock_get_folder.return_value = "folder_123"
        mock_list_pdfs.return_value = [{"id": "paper123", "name": "sample.pdf"}]
        mock_list_metadata.return_value = []

        session_state = MockSessionState(
            {
                "folder_id": "folder_123",
                "drive_pdfs": [{"id": "paper123", "name": "sample.pdf"}],
                "papers_dir": tmp_path,
                "metadata_dir": tmp_path / "metadata",
                "metadata": {
                    "paper123": {
                        "title": "Old Title",
                        "tags": ["ml"],
                        "status": "Reading",
                        "citation": "cite123",
                        "notes": "Old notes",
                    }
                },
                "selected_paper": "paper123",
            }
        )
        (tmp_path / "metadata").mkdir(parents=True, exist_ok=True)
        (tmp_path / "paper123").mkdir(parents=True, exist_ok=True)
        (tmp_path / "paper123" / "sample.pdf").write_bytes(b"pdf content")

        mock_st.session_state = session_state
        mock_st.columns.return_value = [MagicMock(), MagicMock()]
        mock_st.button.return_value = False
        mock_st.text_input.side_effect = lambda _label, **kwargs: kwargs.get(
            "value", ""
        )
        mock_st.selectbox.side_effect = lambda _label, **kwargs: (
            kwargs.get("options", [])[kwargs.get("index", 0)]
            if kwargs.get("options")
            else ""
        )
        mock_st.text_area.side_effect = lambda _label, **kwargs: kwargs.get("value", "")
        mock_st.form_submit_button.return_value = True

        with patch("frontend.app.PAPERS_DIR", tmp_path):
            from frontend.app import main

            main()

        assert "paper123" in session_state.metadata
        mock_upload_metadata.assert_called_once()
        local_file = tmp_path / "metadata" / "paper123_meta.json"
        assert local_file.exists()
        saved_data = json.loads(local_file.read_text())
        assert saved_data["title"] == "Old Title"
        mock_st.success.assert_called_with("Metadata saved!")

"""Unit tests for Google Drive metadata storage functions."""

from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from backend.drive import (
    download_metadata,
    list_metadata_in_library,
    upload_metadata,
)


@pytest.fixture
def mock_creds() -> MagicMock:
    """Fixture providing a mock Google credentials object.

    Returns:
        MagicMock: A mock credentials instance.
    """
    return MagicMock()


class TestListMetadataInLibrary:
    """Test suite for list_metadata_in_library function."""

    @patch("backend.drive.build")
    def test_list_metadata_single_page(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test listing metadata files when results fit on a single page.

        Args:
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
        """
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "meta1", "name": "123_meta.json"}]
        }

        result: List[Dict[str, str]] = list_metadata_in_library(
            mock_creds, "folder_123"
        )
        assert len(result) == 1
        assert result[0]["id"] == "meta1"
        assert result[0]["name"] == "123_meta.json"
        mock_service.files().list.assert_called_with(
            q="'folder_123' in parents and name contains '_meta.json' and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id, name)",
            pageToken=None,
            pageSize=1000,
        )

    @patch("backend.drive.build")
    def test_list_metadata_pagination(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test listing metadata files across multiple paginated responses.

        Args:
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
        """
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.side_effect = [
            {
                "files": [{"id": "meta1", "name": "1_meta.json"}],
                "nextPageToken": "token2",
            },
            {
                "files": [{"id": "meta2", "name": "2_meta.json"}],
            },
        ]

        result: List[Dict[str, str]] = list_metadata_in_library(
            mock_creds, "folder_123"
        )
        assert len(result) == 2
        assert result[0]["id"] == "meta1"
        assert result[1]["id"] == "meta2"

    @patch("backend.drive.build")
    def test_list_metadata_empty(
        self, mock_build: MagicMock, mock_creds: MagicMock
    ) -> None:
        """Test listing metadata when no files match the query.

        Args:
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
        """
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {}

        result: List[Dict[str, str]] = list_metadata_in_library(
            mock_creds, "folder_123"
        )
        assert result == []


class TestDownloadMetadata:
    """Test suite for download_metadata function."""

    @patch("backend.drive.build")
    @patch("backend.drive.MediaIoBaseDownload")
    def test_download_metadata_success(
        self,
        mock_download: MagicMock,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test successfully downloading a metadata file.

        Args:
            mock_download: Mock for MediaIoBaseDownload.
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
            tmp_path: Pytest temporary path fixture.
        """
        dest: Path = tmp_path / "123_meta.json"
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_downloader = MagicMock()
        mock_download.return_value = mock_downloader
        mock_downloader.next_chunk.return_value = (None, True)

        download_metadata(mock_creds, "meta1", dest)
        assert dest.exists()

    @patch("backend.drive.build")
    @patch("backend.drive.MediaIoBaseDownload")
    def test_download_metadata_failure_cleanup(
        self,
        mock_download: MagicMock,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that temporary file is cleaned up if download fails.

        Args:
            mock_download: Mock for MediaIoBaseDownload.
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
            tmp_path: Pytest temporary path fixture.
        """
        dest: Path = tmp_path / "failed_meta.json"
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_downloader = MagicMock()
        mock_download.return_value = mock_downloader
        mock_downloader.next_chunk.side_effect = RuntimeError("Download error")

        with pytest.raises(RuntimeError, match="Download error"):
            download_metadata(mock_creds, "meta_fail", dest)

        assert not dest.exists()
        # Verify no remaining temporary files in dest.parent
        tmp_files = list(tmp_path.glob("*tmp*"))
        assert len(tmp_files) == 0


class TestUploadMetadata:
    """Test suite for upload_metadata function."""

    @patch("backend.drive.build")
    @patch("backend.drive.MediaFileUpload")
    def test_upload_metadata_create_new(
        self,
        mock_media: MagicMock,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test uploading metadata when file does not exist (create new).

        Args:
            mock_media: Mock for MediaFileUpload.
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
            tmp_path: Pytest temporary path fixture.
        """
        src: Path = tmp_path / "123_meta.json"
        src.write_text("{}")

        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": []
        }
        mock_service.files.return_value.create.return_value.execute.return_value = {
            "id": "new_meta_id"
        }

        result: str = upload_metadata(mock_creds, "folder_123", src, "123_meta.json")
        assert result == "new_meta_id"
        mock_service.files.return_value.create.assert_called_once()

    @patch("backend.drive.build")
    @patch("backend.drive.MediaFileUpload")
    def test_upload_metadata_update_existing(
        self,
        mock_media: MagicMock,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test uploading metadata when file already exists (update existing).

        Args:
            mock_media: Mock for MediaFileUpload.
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
            tmp_path: Pytest temporary path fixture.
        """
        src: Path = tmp_path / "123_meta.json"
        src.write_text("{}")

        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "existing_meta_id"}]
        }

        result: str = upload_metadata(mock_creds, "folder_123", src, "123_meta.json")
        assert result == "existing_meta_id"
        mock_service.files.return_value.update.assert_called_once()

    @patch("backend.drive.build")
    @patch("backend.drive.MediaFileUpload")
    def test_upload_metadata_escapes_single_quotes(
        self,
        mock_media: MagicMock,
        mock_build: MagicMock,
        mock_creds: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that single quotes in display_name are properly escaped in Drive query.

        Args:
            mock_media: Mock for MediaFileUpload.
            mock_build: Mock for googleapiclient.discovery.build.
            mock_creds: Mock credentials fixture.
            tmp_path: Pytest temporary path fixture.
        """
        src: Path = tmp_path / "author's_meta.json"
        src.write_text("{}")

        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_list = mock_service.files.return_value.list
        mock_list.return_value.execute.return_value = {"files": []}
        mock_service.files.return_value.create.return_value.execute.return_value = {
            "id": "new_meta_id"
        }

        display_name: str = "author's_meta.json"
        upload_metadata(mock_creds, "folder_123", src, display_name)

        call_kwargs = mock_list.call_args[1]
        assert "name = 'author\\'s_meta.json'" in call_kwargs["q"]

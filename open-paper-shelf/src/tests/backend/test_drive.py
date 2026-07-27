import pytest
from unittest.mock import MagicMock, patch
from backend.drive import (
    get_or_create_root_folder,
    list_libraries,
    create_library,
    get_papers_folder,
    get_library_index_file,
    upload_library_index,
    create_paper_folder,
    upload_file_to_folder,
    download_file,
    delete_paper_folder,
)
from backend.models import LibraryIndex


@pytest.fixture
def mock_creds():
    return MagicMock()


@pytest.fixture
def mock_build():
    with patch("backend.drive.build") as mock:
        yield mock


def test_get_or_create_root_folder(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": [{"id": "root_123"}]}

    assert get_or_create_root_folder(mock_creds) == "root_123"


def test_list_libraries(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "lib1", "name": "My Lib"}]
    }

    libs = list_libraries(mock_creds, "root_123")
    assert len(libs) == 1
    assert libs[0]["id"] == "lib1"


def test_create_library(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.side_effect = [
        {"id": "new_lib"},
        {"id": "new_papers"},
    ]

    res = create_library(mock_creds, "root_123", "TestLib")
    assert res["lib_id"] == "new_lib"
    assert res["papers_id"] == "new_papers"
    assert "TestLib" in res["lib_name"]


def test_upload_file_to_folder(mock_build, mock_creds, tmp_path):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {"id": "uploaded_123"}

    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"content")

    with patch("backend.drive.MediaFileUpload"):
        res = upload_file_to_folder(
            mock_creds, "folder_123", file_path, "paper.pdf", "application/pdf"
        )
        assert res == "uploaded_123"


def test_download_file(mock_build, mock_creds, tmp_path):
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    dest_path = tmp_path / "paper.pdf"
    with patch("backend.drive.MediaIoBaseDownload") as mock_downloader:
        mock_downloader.return_value.next_chunk.return_value = (None, True)
        download_file(mock_creds, "file_123", dest_path)

    assert dest_path.exists()


def test_get_papers_folder(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": [{"id": "papers_123"}]}
    assert get_papers_folder(mock_creds, "lib_123") == "papers_123"


def test_get_library_index_file(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "idx", "modifiedTime": "time"}]
    }
    res = get_library_index_file(mock_creds, "papers_123")
    assert res is not None
    assert res["id"] == "idx"


def test_upload_library_index(mock_build, mock_creds, tmp_path):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": [{"id": "idx"}]}

    with patch("backend.drive.MediaFileUpload"):
        upload_library_index(mock_creds, "papers_123", LibraryIndex())
    mock_service.files().update.assert_called_once()


def test_create_paper_folder(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {"id": "p_folder"}
    assert create_paper_folder(mock_creds, "papers_123", "p_uuid") == "p_folder"


def test_delete_paper_folder(mock_build, mock_creds):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    delete_paper_folder(mock_creds, "folder_123")
    mock_service.files().delete.assert_called_once_with(fileId="folder_123")

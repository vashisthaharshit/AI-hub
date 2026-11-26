"""Files dropped on a command card are saved inside the app's folder, safely."""

import pytest

from aihub.apps.files import UploadError, clean_name, save_upload


def test_saves_inside_the_repo(tmp_path):
    assert save_upload(tmp_path, "documents/cv", "My CV.pdf", b"pdf") == "documents/cv/My CV.pdf"
    assert (tmp_path / "documents" / "cv" / "My CV.pdf").read_bytes() == b"pdf"


def test_never_overwrites(tmp_path):
    save_upload(tmp_path, "docs", "cv.pdf", b"1")
    assert save_upload(tmp_path, "docs", "cv.pdf", b"2") == "docs/cv (2).pdf"
    assert (tmp_path / "docs" / "cv.pdf").read_bytes() == b"1"


@pytest.mark.parametrize("folder", ["../outside", "/etc", "C:/Windows", "docs/../../x", "..", "."])
def test_refuses_folders_outside_the_repo(tmp_path, folder):
    with pytest.raises(UploadError):
        save_upload(tmp_path, folder, "a.txt", b"x")


def test_cleans_names():
    assert clean_name("..\\..\\evil.pdf") == "evil.pdf"
    assert clean_name("a:b?.txt") == "a_b_.txt"
    with pytest.raises(UploadError):
        clean_name("   ")


def test_empty_and_default_folder(tmp_path):
    with pytest.raises(UploadError, match="empty"):
        save_upload(tmp_path, None, "a.txt", b"")
    assert save_upload(tmp_path, None, "a.txt", b"x") == "uploads/a.txt"

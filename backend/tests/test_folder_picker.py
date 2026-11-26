"""The folder picker: the dialog itself can't run in tests, so we check the
choices around it (fallbacks, cancel, one window at a time)."""

import asyncio

import pytest

from aihub import folder_picker as fp


def test_returns_the_chosen_folder(monkeypatch, tmp_path):
    seen = {}

    def fake(start, title):
        seen.update(start=start, title=title)
        return str(tmp_path)
    monkeypatch.setattr(fp, "_with_tkinter", fake)
    assert asyncio.run(fp.pick_folder(str(tmp_path), "Pick")) == str(tmp_path)
    assert seen == {"start": str(tmp_path), "title": "Pick"}


def test_missing_start_folder_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "_with_tkinter", lambda start, title: start)
    assert asyncio.run(fp.pick_folder(str(tmp_path / "nope"))) is None


def test_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(fp, "_with_tkinter", lambda start, title: None)
    assert asyncio.run(fp.pick_folder()) is None


def test_no_tkinter_on_windows_uses_powershell(monkeypatch):
    def no_tk(start, title):
        raise ImportError("no tkinter")
    monkeypatch.setattr(fp, "_with_tkinter", no_tk)
    monkeypatch.setattr(fp.sys, "platform", "win32")
    monkeypatch.setattr(fp, "_with_powershell", lambda start, title: "C:\\Chosen")
    assert asyncio.run(fp.pick_folder()) == "C:\\Chosen"


def test_dialog_error_is_explained(monkeypatch):
    def broken(start, title):
        raise RuntimeError("no display")
    monkeypatch.setattr(fp, "_with_tkinter", broken)
    with pytest.raises(fp.PickerError, match="Type the folder path instead"):
        asyncio.run(fp.pick_folder())

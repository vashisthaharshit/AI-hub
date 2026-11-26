"""Update repo: get GitHub's newest version, with a backup first (apps/update.py)."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from aihub import config
from aihub.apps import update
from aihub.apps.connect import AppError, connect
from aihub.db import Database
from aihub.models import AppConnect


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    upstream = tmp_path / "github"
    upstream.mkdir()
    git(upstream, "init", "-q")
    commit(upstream, {"README.md": "# demo\nUse /hello.\n", ".gitignore": "personal/\n",
                      ".claude/commands/hello.md": "---\ndescription: Say hi\n---\nSay hi\n"}, "first")
    clone = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(upstream), str(clone))
    db = Database(tmp_path / "aihub.db")
    app = asyncio.run(connect(AppConnect(path=str(clone)), db))
    return upstream, clone, db, app


def test_up_to_date(env):
    _, _, db, app = env
    found = asyncio.run(update.check(app))
    assert (found.behind, found.ahead, found.can_update) == (0, 0, False)
    result = asyncio.run(update.update(app, db))
    assert not result.updated and result.message == "Already up to date."


def test_update_backs_up_then_moves_forward(env):
    upstream, clone, db, app = env
    commit(upstream, {".claude/commands/bye.md": "---\ndescription: Say bye\n---\nBye\n"}, "add bye")
    (clone / "notes.txt").write_text("my notes", encoding="utf-8")          # new file of the user's
    (clone / "README.md").write_text("# demo\nUse /hello.\nmine\n", encoding="utf-8")  # their edit
    (clone / "personal").mkdir()
    (clone / "personal" / "cv.txt").write_text("cv", encoding="utf-8")       # ignored: never touched

    found = asyncio.run(update.check(app))
    assert found.behind == 1 and found.can_update and found.changes == ["add bye"]
    assert found.local_changes == 2

    result = asyncio.run(update.update(app, db))
    assert result.updated and result.backed_up_files == 2 and not result.conflicts
    backup = Path(result.backup)
    assert (backup / "files" / "notes.txt").read_text(encoding="utf-8") == "my notes"
    assert "mine" in (backup / "files" / "README.md").read_text(encoding="utf-8")
    assert "git reset --hard refs/aihub/backup-" in (backup / "README.txt").read_text(encoding="utf-8")
    # the user's work is still there, on top of the new version
    assert "mine" in (clone / "README.md").read_text(encoding="utf-8")
    assert (clone / "notes.txt").exists() and (clone / "personal" / "cv.txt").exists()
    assert git(clone, "rev-parse", "HEAD") == git(upstream, "rev-parse", "HEAD")
    assert git(clone, "for-each-ref", "refs/aihub/")  # the old version is kept
    # the folder was read again: the new command is a button now
    assert "bye" in [c.name for c in result.app.manifest.commands]


def test_own_commits_block_the_update(env):
    upstream, clone, db, app = env
    commit(upstream, {"a.txt": "a"}, "theirs")
    commit(clone, {"b.txt": "b"}, "mine")
    before = git(clone, "rev-parse", "HEAD")
    found = asyncio.run(update.check(app))
    assert found.ahead == 1 and not found.can_update
    with pytest.raises(AppError, match="aren't on GitHub"):
        asyncio.run(update.update(app, db))
    assert git(clone, "rev-parse", "HEAD") == before


def test_same_name_as_a_users_file(env):
    upstream, clone, db, app = env
    commit(upstream, {"notes.txt": "theirs"}, "add notes")
    (clone / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(AppError, match="same names"):
        asyncio.run(update.update(app, db))
    assert (clone / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_not_a_git_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    folder = tmp_path / "plain"
    (folder / ".claude" / "commands").mkdir(parents=True)
    (folder / ".claude" / "commands" / "x.md").write_text("x", encoding="utf-8")
    db = Database(tmp_path / "aihub.db")
    app = asyncio.run(connect(AppConnect(path=str(folder)), db))
    with pytest.raises(AppError, match="isn't a Git copy"):
        asyncio.run(update.check(app))

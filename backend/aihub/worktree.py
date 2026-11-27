"""Git worktrees: a safe, separate copy of the repo for every run.

A worktree is a second working folder attached to the same git repo, on its
own branch. The agent edits files there, so the user's main folder and
`main` branch are never touched. Each run's changes stay on a branch called
aihub/run-<id>, which makes them easy to compare, keep or throw away.

All diffs compare against the run's BASE COMMIT (where it started), not
HEAD. That way the diff stays correct even if the agent makes its own
commits, and after "keep" we can still diff base..branch.
"""

import asyncio
from pathlib import Path

from aihub import config
from aihub.models import ChangedFile


class GitError(RuntimeError):
    pass


async def _git_bytes(*args: str, cwd: Path) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(err.decode(errors="replace").strip() or f"git {' '.join(args)} failed")
    return out


async def _git(*args: str, cwd: Path) -> str:
    return (await _git_bytes(*args, cwd=cwd)).decode(errors="replace")


# ---------- creating ----------

async def repo_root(path: Path) -> Path:
    """Return the top folder of the git repo that contains `path`."""
    if not path.is_dir():
        raise GitError(f"Folder not found: {path}")
    try:
        top = await _git("rev-parse", "--show-toplevel", cwd=path)
    except GitError:
        raise GitError(f"Not a git repository: {path}") from None
    try:
        await _git("rev-parse", "--verify", "HEAD", cwd=path)
    except GitError:
        raise GitError("The repository has no commits yet. Make one commit first.") from None
    return Path(top.strip())


async def head_commit(repo: Path) -> str:
    return (await _git("rev-parse", "HEAD", cwd=repo)).strip()


async def create_worktree(repo: Path, run_id: str, base_commit: str) -> tuple[str, Path]:
    """Create a new branch + worktree for a run. Returns (branch, worktree path)."""
    branch = f"aihub/run-{run_id}"
    path = config.WORKTREES_DIR / run_id
    await _git("worktree", "add", "-b", branch, str(path), base_commit, cwd=repo)
    return branch, path


# ---------- reading changes ----------

async def _track_new_files(worktree: Path) -> None:
    # `git diff` ignores untracked (new) files. "Intent to add" (-N) makes git
    # aware of them without staging their content, so they show in diffs.
    await _git("add", "--intent-to-add", "--all", cwd=worktree)


async def diff(worktree: Path, base: str = "HEAD") -> str:
    """Everything the agent changed as unified diff text, including new files."""
    await _track_new_files(worktree)
    return await _git("diff", base, cwd=worktree)


async def diff_stats(worktree: Path, base: str) -> tuple[list[str], int, int]:
    """(changed file paths, lines added, lines removed) compared to `base`."""
    await _track_new_files(worktree)
    out = await _git("diff", base, "--numstat", "--no-renames", "-z", cwd=worktree)
    files, added, removed = [], 0, 0
    for entry in (e for e in out.split("\0") if e):
        # Each entry: "<added>\t<removed>\t<path>"; binary files show "-" for counts.
        parts = entry.split("\t", 2)
        if len(parts) != 3:
            continue
        a, r, path = parts
        files.append(path)
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
    return files, added, removed


# Files bigger than this are listed but their content is not sent to the UI.
MAX_FILE_BYTES = 1_000_000


async def changed_files(repo_dir: Path, base: str, target: str | None = None) -> list[ChangedFile]:
    """Each changed file with its old and new content, for a side-by-side diff.

    target=None compares `base` with the files on disk in `repo_dir` (a live
    worktree). target="<branch>" compares two commits (a kept run whose
    worktree folder is gone).

    A unified diff only has the changed lines. A side-by-side viewer like
    Monaco needs both full versions of each file, so we fetch them.
    """
    if target is None:
        await _track_new_files(repo_dir)
    range_args = [base, target] if target else [base]
    # -z: NUL-separated output, safe for paths with spaces or odd characters.
    # --no-renames: a rename shows as one delete + one add (simpler to display).
    out = await _git("diff", *range_args, "--name-status", "--no-renames", "-z", cwd=repo_dir)
    parts = [p for p in out.split("\0") if p]

    files = []
    for status, path in zip(parts[0::2], parts[1::2]):
        status = status[0]  # A (added), M (modified), D (deleted), T (type change)
        original = modified = None
        binary = too_large = False

        if status != "A":
            original, binary, too_large = _decode(await _git_bytes("show", f"{base}:{path}", cwd=repo_dir))
        if status != "D" and not binary and not too_large:
            if target:
                raw = await _git_bytes("show", f"{target}:{path}", cwd=repo_dir)
            else:
                raw = (repo_dir / path).read_bytes()
            modified, binary, too_large = _decode(raw)
        if binary or too_large:
            original = modified = None

        files.append(ChangedFile(
            path=path, status=status, original=original, modified=modified,
            binary=binary, too_large=too_large,
        ))
    return files


def _decode(raw: bytes) -> tuple[str | None, bool, bool]:
    """Returns (text, is_binary, is_too_large)."""
    if len(raw) > MAX_FILE_BYTES:
        return None, False, True
    if b"\0" in raw:
        return None, True, False
    try:
        return raw.decode("utf-8"), False, False
    except UnicodeDecodeError:
        return None, True, False


# ---------- keep / discard ----------

async def commit_all(worktree: Path, message: str) -> bool:
    """Commit every change in the worktree to its branch. False if nothing to commit."""
    await _git("add", "--all", cwd=worktree)
    if not (await _git("status", "--porcelain", cwd=worktree)).strip():
        return False
    identity: list[str] = []
    try:
        await _git("config", "user.email", cwd=worktree)
    except GitError:
        # No git identity configured: commit as AI-Hub instead of failing.
        identity = ["-c", "user.name=AI-Hub", "-c", "user.email=aihub@localhost"]
    await _git(*identity, "commit", "-m", message, cwd=worktree)
    return True


async def remove_worktree(repo: Path, worktree: Path) -> None:
    """Delete the worktree folder (the branch stays).

    On Windows `git worktree remove` can fail with "Directory not empty" when a
    file is read-only or still open for a moment (a test run, an agent's
    process, a cache folder). Then: delete the folder ourselves, retrying a
    few times, and let git forget it.
    """
    try:
        await _git("worktree", "remove", "--force", str(worktree), cwd=repo)
        return
    except GitError as exc:
        first_error = exc
    gone = await asyncio.to_thread(_delete_folder, worktree)
    try:
        await _git("worktree", "prune", cwd=repo)
    except GitError:
        pass
    if not gone:
        raise GitError(f"Couldn't delete the folder {worktree} (a program may still be using a file in it). "
                       f"You can delete it by hand later. ({first_error})")


def _delete_folder(path: Path, attempts: int = 6) -> bool:
    import os
    import shutil
    import stat
    import time

    def make_writable(func, target, _exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    for attempt in range(attempts):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, onerror=make_writable)
        except OSError:
            pass
        if not path.exists():
            return True
        time.sleep(0.4 * (attempt + 1))
    return not path.exists()


async def delete_branch(repo: Path, branch: str) -> None:
    await _git("branch", "-D", branch, cwd=repo)


async def prune(repo: Path) -> None:
    """Forget worktrees whose folders were deleted by hand."""
    await _git("worktree", "prune", cwd=repo)

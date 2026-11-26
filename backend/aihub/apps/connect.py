"""Connect an app: a GitHub link (cloned for the user) or a folder already here.

Safety: right after cloning we switch off `git push` for that clone. These
repos end up holding personal data (a CV, documents, a job tracker), and the
README of ai-job-search itself warns that forks are public. With the push URL
replaced, a stray `git push` from an agent fails instead of publishing it.
"""

import asyncio
import os
import re
import uuid
from pathlib import Path

from aihub import config
from aihub.apps.manifest import build_manifest
from aihub.db import Database, now_iso
from aihub.models import AppConnect, AppInfo

PUSH_DISABLED = "PUSH-DISABLED-BY-AIHUB"

_GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/.*)?(?:[?#].*)?$"
)
_SHORT = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)$")  # "owner/repo"
_NAME_OK = re.compile(r"^[\w.-]+$")


class AppError(RuntimeError):
    """A problem the user can fix; shown as-is in the UI."""


def parse_repo_url(url: str) -> tuple[str, str]:
    """Return (clone URL, folder name) for a link the user pasted.

    Accepts https://github.com/owner/repo, .../repo.git, .../repo/tree/main/x,
    github.com/owner/repo and plain owner/repo. Other https git hosts work
    too if the link ends in .git.
    """
    url = url.strip()
    match = _GITHUB.match(url) or _SHORT.match(url)
    if match:
        owner, repo = match.group("owner"), match.group("repo")
        return f"https://github.com/{owner}/{repo}.git", repo
    if url.startswith("https://") and url.rstrip("/").endswith(".git"):
        name = url.rstrip("/").rsplit("/", 1)[-1][: -len(".git")]
        if _NAME_OK.match(name):
            return url, name
    raise AppError("That doesn't look like a GitHub link. Example: https://github.com/owner/repo")


async def _git(*args: str, cwd: Path | None = None) -> str:
    # GIT_TERMINAL_PROMPT=0: for a private repo without saved sign-in, fail
    # with a message instead of waiting forever for a password nobody sees.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(cwd) if cwd else None, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AppError("Git is not installed. Install it from https://git-scm.com and try again.") from None
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise AppError(err.decode(errors="replace").strip() or f"git {args[0]} failed")
    return out.decode(errors="replace").strip()


async def _origin(folder: Path) -> str | None:
    try:
        return await _git("remote", "get-url", "origin", cwd=folder)
    except AppError:
        return None


def _same_repo(a: str | None, b: str) -> bool:
    def norm(u: str) -> str:
        return u.strip().rstrip("/").removesuffix(".git").lower()
    return a is not None and norm(a) == norm(b)


async def clone(clone_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _git("clone", "--", clone_url, str(dest))
    except AppError as exc:
        text = str(exc)
        if "not found" in text.lower() or "could not read username" in text.lower():
            raise AppError("Couldn't download the repo. Check the link; if it's private, "
                           "sign in to GitHub with Git on this machine first.") from None
        raise
    await disable_push(dest)


async def disable_push(folder: Path) -> None:
    """Make `git push` fail for this clone (fetch/pull still work)."""
    if await _origin(folder):
        await _git("remote", "set-url", "--push", "origin", PUSH_DISABLED, cwd=folder)


def default_folder() -> Path:
    return config.APPS_DIR


async def connect(req: AppConnect, db: Database) -> AppInfo:
    """Clone (or reuse) the repo, read it, and save it as an app."""
    if req.url and req.path:
        raise AppError("Send either a GitHub link (url) or a folder already on this computer (path), not both.")
    if req.path:
        dest = Path(req.path).expanduser()
        if not dest.is_dir():
            raise AppError(f"Folder not found: {dest}")
        url = await _origin(dest) if (dest / ".git").exists() else None
        if url == PUSH_DISABLED:
            url = None
    elif req.url:
        clone_url, name = parse_repo_url(req.url)
        parent = Path(req.folder).expanduser() if req.folder else default_folder()
        dest = parent / name
        url = clone_url
        if dest.exists() and any(dest.iterdir()):
            # Already downloaded (e.g. connected before, or cloned by hand): reuse it.
            if not _same_repo(await _origin(dest), clone_url):
                raise AppError(f"The folder {dest} already exists and holds something else. "
                               "Pick another folder.")
            await disable_push(dest)
        else:
            await clone(clone_url, dest)
    else:
        raise AppError("Paste a GitHub link, or choose a folder that's already on this computer.")

    dest = dest.resolve()
    manifest = build_manifest(dest)

    existing = db.get_app_by_path(str(dest))
    if existing:  # connecting the same folder again = rescan it
        db.update_app_manifest(existing.id, manifest)
        return db.get_app(existing.id)

    now = now_iso()
    app = AppInfo(id=uuid.uuid4().hex[:12], name=manifest.name, url=url, path=str(dest),
                  created_at=now, updated_at=now, manifest=manifest)
    db.insert_app(app)
    return app


def rescan(app_id: str, db: Database) -> AppInfo:
    app = db.get_app(app_id)
    if not app:
        raise KeyError(f"App '{app_id}' not found")
    if not Path(app.path).is_dir():
        raise AppError(f"The app's folder is gone: {app.path}")
    db.update_app_manifest(app_id, build_manifest(Path(app.path)))
    return db.get_app(app_id)

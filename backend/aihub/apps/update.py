"""Update an app: get the repo's newest version from GitHub, with a backup first.

  check   fetch, then say how far behind GitHub the copy is and what changed
          (commit titles), and whether the user has edits of their own
  update  1. backup: copy every file the user changed or added (git status,
             including untracked files like a CV) to ~/.aihub/backups/<app>/<time>/,
             and keep the old version under refs/aihub/backup-<time>, so
             `git reset --hard refs/aihub/backup-<time>` brings it back
          2. `git merge --ff-only --autostash`: only moves forward to GitHub's
             version, never makes a merge; the user's uncommitted edits are
             put aside and put back on top
          3. read the folder again (new commands, skills, setup questions)

Files the repo ignores (.gitignore), where these repos keep personal data,
are never touched by git, so they stay as they are.

If the copy has commits of its own that aren't on GitHub, nothing is changed:
that needs a real merge, which is a decision for the user, not for a button.
"""

import shutil
from datetime import datetime
from pathlib import Path

from aihub import config
from aihub.apps.connect import AppError, _git, _origin
from aihub.apps.install import install_skills
from aihub.apps.manifest import build_manifest
from aihub.db import Database
from aihub.models import AppInfo, AppUpdateCheck, AppUpdateResult

MAX_CHANGES = 10


def _repo(app: AppInfo) -> Path:
    repo = Path(app.path)
    if not repo.is_dir():
        raise AppError(f"The app's folder is gone: {app.path}")
    if not (repo / ".git").exists():
        raise AppError("This folder isn't a Git copy of a repo, so there's nothing to update from.")
    return repo


async def _upstream(repo: Path) -> str:
    """The GitHub branch this copy follows: its upstream, else origin's default branch."""
    try:
        return await _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=repo)
    except AppError:
        pass
    try:
        ref = await _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo)
        if ref:
            return ref
    except AppError:
        pass
    branch = await _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    return f"origin/{branch}"


async def _local_changes(repo: Path) -> list[str]:
    """Files the user changed or added (not deleted), relative to the repo."""
    out = await _git("status", "--porcelain", "-z", "--untracked-files=all", cwd=repo, strip=False)
    paths, entries, i = [], out.split("\0"), 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # renames/copies are followed by the old name
        if "D" not in code:
            paths.append(path)
    return paths


async def check(app: AppInfo) -> AppUpdateCheck:
    repo = _repo(app)
    if not await _origin(repo):
        raise AppError("This copy isn't linked to a repo on GitHub (no 'origin'), so there's nothing to update from.")
    try:
        await _git("fetch", "--quiet", "origin", cwd=repo)
    except AppError as exc:
        raise AppError(f"Couldn't reach GitHub to look for updates: {exc}") from None
    upstream = await _upstream(repo)
    behind = int(await _git("rev-list", "--count", f"HEAD..{upstream}", cwd=repo) or 0)
    ahead = int(await _git("rev-list", "--count", f"{upstream}..HEAD", cwd=repo) or 0)
    log = await _git("log", "--format=%s", f"-n{MAX_CHANGES}", f"HEAD..{upstream}", cwd=repo) if behind else ""
    local = await _local_changes(repo)
    return AppUpdateCheck(
        behind=behind, ahead=ahead, upstream=upstream,
        changes=[line for line in log.splitlines() if line.strip()],
        local_changes=len(local),
        can_update=behind > 0 and ahead == 0,
    )


async def backup(app: AppInfo, repo: Path, local: list[str]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = config.DATA_DIR / "backups" / f"{app.name}-{app.id}" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    for rel in local:
        src = repo / rel
        if src.is_file():
            dest = folder / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    head = await _git("rev-parse", "HEAD", cwd=repo)
    ref = f"refs/aihub/backup-{stamp}"
    await _git("update-ref", ref, head, cwd=repo)
    (folder / "README.txt").write_text(
        f"Backup made by AI-Hub before updating {app.name} ({app.path}).\n\n"
        f"Version before the update: {head}\n"
        f"files/ holds a copy of the {len(local)} file(s) you had changed or added.\n\n"
        f"To go back to the old version, in that folder run:\n"
        f"  git reset --hard {ref}\n"
        f"then copy your files back from files/ if needed.\n",
        encoding="utf-8",
    )
    return folder


async def update(app: AppInfo, db: Database) -> AppUpdateResult:
    repo = _repo(app)
    found = await check(app)
    if found.behind == 0:
        return AppUpdateResult(updated=False, message="Already up to date.", check=found, app=app)
    if found.ahead > 0:
        raise AppError(
            f"Your copy has {found.ahead} saved change(s) of its own (commits) that aren't on GitHub, "
            "so AI-Hub won't update it automatically. Nothing was changed.")

    local = await _local_changes(repo)
    folder = await backup(app, repo, local)
    try:
        await _git("merge", "--ff-only", "--autostash", found.upstream, cwd=repo)
    except AppError as exc:
        text = str(exc)
        if "would be overwritten" in text:
            raise AppError(
                "The update adds files with the same names as files you made in this folder. "
                f"Nothing was changed. Your files are backed up in {folder}; move or rename them, "
                f"then update again.\n\n{text}") from None
        raise AppError(f"The update didn't go through, nothing was changed. Backup: {folder}\n\n{text}") from None

    status = await _git("status", "--porcelain", cwd=repo)
    conflicts = [line[3:] for line in status.splitlines() if line[:2] in ("UU", "AA", "DU", "UD")]
    install_skills(repo)
    db.update_app_manifest(app.id, build_manifest(repo))
    message = f"Updated with {found.behind} change(s) from GitHub."
    if conflicts:
        message += (" Some of your own edits clash with the update; those files need a look. "
                    "Your versions are in the backup.")
    return AppUpdateResult(updated=True, message=message, check=found, backup=str(folder),
                           backed_up_files=len(local), conflicts=conflicts, app=db.get_app(app.id))

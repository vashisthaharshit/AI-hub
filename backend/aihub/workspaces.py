"""Workspaces: one per product (= one Git repo folder). All of a product's
chats live in it: Manual runs and Brain tasks. Apps stay outside workspaces.

The user picks (or creates) a workspace first; every chat started inside it
uses its repo, so nobody types a folder path again.

Chats made before workspaces existed are moved into one automatically at
startup (one workspace per repo folder they used).
"""

import uuid
from pathlib import Path

from aihub import worktree
from aihub.db import Database, now_iso
from aihub.models import Workspace, WorkspaceCreate, WorkspaceEdit

COLORS = ("sky", "violet", "emerald", "amber", "rose", "cyan", "fuchsia", "lime")


class WorkspaceError(RuntimeError):
    """Shown to the user as-is."""


def _next_color(db: Database) -> str:
    used = [w.color for w in db.list_workspaces()]
    return min(COLORS, key=lambda c: (used.count(c), COLORS.index(c)))


def ensure_for_repo(db: Database, repo: str, name: str | None = None, test_command: str | None = None) -> Workspace:
    """The workspace of this repo folder, made if there isn't one yet."""
    found = db.get_workspace_by_repo(repo)
    if found:
        return found
    now = now_iso()
    ws = Workspace(id=uuid.uuid4().hex[:12], name=(name or "").strip() or Path(repo).name or repo,
                   repo_path=repo, test_command=(test_command or "").strip() or None,
                   color=_next_color(db), created_at=now, updated_at=now)
    db.insert_workspace(ws)
    return db.get_workspace(ws.id)


async def create(db: Database, req: WorkspaceCreate) -> Workspace:
    try:
        repo = await worktree.repo_root(Path(req.repo_path).expanduser())
    except worktree.GitError as exc:
        raise WorkspaceError(
            f"That folder isn't a Git repository with at least one commit. ({exc})\n"
            "Tip: in that folder run `git init`, then `git add -A` and `git commit -m start`.") from None
    existing = db.get_workspace_by_repo(str(repo))
    if existing:
        return existing
    ws = ensure_for_repo(db, str(repo), req.name, req.test_command)
    if req.color in COLORS:
        ws.color = req.color
        db.update_workspace(ws)
    return db.get_workspace(ws.id)


def get(db: Database, ws_id: str) -> Workspace:
    ws = db.get_workspace(ws_id)
    if not ws:
        raise KeyError(f"Workspace '{ws_id}' not found")
    return ws


def edit(db: Database, ws_id: str, req: WorkspaceEdit) -> Workspace:
    ws = get(db, ws_id)
    if req.name is not None:
        if not req.name.strip():
            raise WorkspaceError("A workspace needs a name.")
        ws.name = req.name.strip()
    if req.test_command is not None:
        ws.test_command = req.test_command.strip() or None
    if req.color is not None:
        if req.color not in COLORS:
            raise WorkspaceError(f"Unknown color '{req.color}'.")
        ws.color = req.color
    db.update_workspace(ws)
    return db.get_workspace(ws_id)


def delete(db: Database, ws_id: str) -> None:
    """Only empty workspaces: its chats point to it. The repo folder is never touched."""
    ws = get(db, ws_id)
    if ws.manual_count or ws.brain_count or db.list_runs(1, workspace_id=ws_id, origin="brain"):
        raise WorkspaceError("Delete this workspace's chats first. (Your repo folder is never deleted.)")
    db.delete_workspace(ws_id)


def adopt_orphans(db: Database) -> int:
    """Put chats made before workspaces existed into one, by repo folder. Returns how many moved."""
    moved = 0
    for kind, item_id, repo in db.chats_without_workspace():
        ws = ensure_for_repo(db, repo)
        if kind == "run":
            db.update_run(item_id, workspace_id=ws.id)
        else:
            task = db.get_brain_task(item_id)
            if task:
                task.workspace_id = ws.id
                db.save_brain_task(task)
        moved += 1
    return moved

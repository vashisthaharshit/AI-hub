"""Workspaces (one per repo) and keeping Brain runs out of Manual."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from aihub import workspaces
from aihub.brain.tasks import Brain
from aihub.db import Database, now_iso
from aihub.models import BrainCreate, BrainTask, RunInfo, TriageResult, WorkspaceCreate, WorkspaceEdit
from aihub.workspaces import WorkspaceError


def make_repo(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# r", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
    return folder


def run(run_id, repo, **kw) -> RunInfo:
    return RunInfo(id=run_id, agent_id="claude-code", repo_path=str(repo), prompt="p", state="succeeded",
                   created_at=now_iso(), **kw)


def test_create_reuse_and_edit(tmp_path):
    db = Database(tmp_path / "a.db")
    repo = make_repo(tmp_path / "shop")
    ws = asyncio.run(workspaces.create(db, WorkspaceCreate(repo_path=str(repo))))
    assert ws.name == "shop" and ws.color == "sky"
    again = asyncio.run(workspaces.create(db, WorkspaceCreate(repo_path=str(repo), name="Other")))
    assert again.id == ws.id                                     # one workspace per repo
    other = asyncio.run(workspaces.create(db, WorkspaceCreate(repo_path=str(make_repo(tmp_path / "blog")))))
    assert other.color != ws.color                               # colors spread out
    ws = workspaces.edit(db, ws.id, WorkspaceEdit(name="My shop", test_command="pytest -q", color="violet"))
    assert (ws.name, ws.test_command, ws.color) == ("My shop", "pytest -q", "violet")
    with pytest.raises(WorkspaceError):
        workspaces.edit(db, ws.id, WorkspaceEdit(color="plaid"))


def test_not_a_repo(tmp_path):
    db = Database(tmp_path / "a.db")
    with pytest.raises(WorkspaceError, match="isn't a Git repository"):
        asyncio.run(workspaces.create(db, WorkspaceCreate(repo_path=str(tmp_path))))


def test_counts_and_delete_only_when_empty(tmp_path):
    db = Database(tmp_path / "a.db")
    repo = make_repo(tmp_path / "shop")
    ws = asyncio.run(workspaces.create(db, WorkspaceCreate(repo_path=str(repo))))
    db.insert_run(run("r1", repo, workspace_id=ws.id))
    db.insert_run(run("r2", repo, workspace_id=ws.id, origin="brain", brain_task_id="b1"))
    ws = workspaces.get(db, ws.id)
    assert ws.manual_count == 1 and ws.last_activity
    with pytest.raises(WorkspaceError, match="chats first"):
        workspaces.delete(db, ws.id)


def test_brain_runs_stay_out_of_manual_until_moved(tmp_path):
    db = Database(tmp_path / "a.db")
    repo = make_repo(tmp_path / "shop")
    ws = workspaces.ensure_for_repo(db, str(repo))
    db.insert_run(run("manual", repo, workspace_id=ws.id))
    db.insert_run(run("fromBrain", repo, workspace_id=ws.id, origin="brain", brain_task_id="b1"))
    assert [r.id for r in db.list_runs(workspace_id=ws.id, origin="manual")] == ["manual"]
    assert [r.id for r in db.list_runs(brain_task_id="b1")] == ["fromBrain"]
    db.update_run("fromBrain", origin="manual")                  # "Move to Manual"
    assert {r.id for r in db.list_runs(workspace_id=ws.id, origin="manual")} == {"manual", "fromBrain"}
    assert db.get_run("fromBrain").brain_task_id == "b1"          # still listed under its Brain task


def test_old_chats_are_put_into_workspaces(tmp_path):
    db = Database(tmp_path / "a.db")
    shop, blog = make_repo(tmp_path / "shop"), make_repo(tmp_path / "blog")
    db.insert_run(run("r1", shop))
    db.insert_run(run("r2", shop))
    now = now_iso()
    db.save_brain_task(BrainTask(id="b1", repo_path=str(blog), request="x", state="direct",
                                 triage=TriageResult(size="small", how="rules"), created_at=now, updated_at=now))
    assert workspaces.adopt_orphans(db) == 3
    names = {w.name: w for w in db.list_workspaces()}
    assert set(names) == {"shop", "blog"}
    assert db.get_run("r1").workspace_id == db.get_run("r2").workspace_id == names["shop"].id
    assert db.get_brain_task("b1").workspace_id == names["blog"].id
    assert workspaces.adopt_orphans(db) == 0                      # nothing left the second time


class NoAI:
    def settings(self):
        from aihub.models import SummarySettings
        return SummarySettings()


class Registry:
    async def scan(self, refresh=False, hidden=None):
        return []


def test_brain_task_in_a_workspace(tmp_path):
    db = Database(tmp_path / "a.db")
    repo = make_repo(tmp_path / "shop")
    ws = workspaces.edit(db, workspaces.ensure_for_repo(db, str(repo)).id, WorkspaceEdit(test_command="pytest -q"))
    task = asyncio.run(Brain(db, NoAI(), Registry()).create(
        BrainCreate(workspace_id=ws.id, request="fix the typo in README.md")))
    assert task.workspace_id == ws.id and Path(task.repo_path).resolve() == repo.resolve()
    assert task.test_command == "pytest -q"                       # the workspace's test command
    assert [t.id for t in db.list_brain_tasks(workspace_id=ws.id)] == [task.id]
    assert db.list_brain_tasks(workspace_id="other") == []

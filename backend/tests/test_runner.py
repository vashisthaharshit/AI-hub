"""End-to-end tests of runs, without any real agent.

A FakeAdapter writes files and emits events, so we can check the whole
pipeline: worktree -> events saved in order -> live stream -> checks ->
follow-ups -> approvals -> keep / discard.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from aihub import config, worktree
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.db import Database
from aihub.models import AgentEvent, ApprovalDecision, ApprovalRequest, RunCreate, RunResult
from aihub.runner import RunError, RunManager


class FakeAdapter(AgentAdapter):
    id = "fake"
    name = "Fake agent"
    supports_supervised = True
    supports_follow_up = True

    def __init__(self, hang: bool = False):
        self.hang = hang
        self.calls: list[tuple[str, RunOptions]] = []

    async def detect(self):
        return self.status("ready")

    async def test_connection(self):
        return await self.detect()

    async def run(self, prompt, workdir: Path, options: RunOptions | None = None):
        options = options or RunOptions()
        self.calls.append((prompt, options))
        yield AgentEvent(kind="session_started", data={"session_id": f"sess-{len(self.calls)}", "model": "fake"})
        # write_bytes, not write_text: on Windows write_text turns "\n" into "\r\n"
        (workdir / f"file{len(self.calls)}.txt").write_bytes(b"hi\n")
        yield AgentEvent(kind="tool_call", data={"id": "t1", "name": "Write", "input": {}})
        if self.hang:
            await asyncio.sleep(3600)
        yield AgentEvent(kind="result", data=RunResult(success=True, summary="done").model_dump())


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "worktrees")
    config.WORKTREES_DIR.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "README.md").write_bytes(b"x\n")
    git("add", ".")
    git("commit", "-m", "init")
    return repo


def make_manager(tmp_path, adapter) -> RunManager:
    return RunManager(Database(tmp_path / "test.db"), AgentRegistry([adapter]))


async def wait_turn(runs: RunManager, run_id: str, after: int = -1) -> list[str]:
    return [ev.kind async for _, ev in runs.stream(run_id, after)]


def test_full_run(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="say hi"))
        assert info.state == "running" and info.branch == f"aihub/run-{info.id}" and info.base_commit

        kinds = await wait_turn(runs, info.id)
        assert kinds == ["user_message", "session_started", "tool_call", "result", "checks"]

        final = runs.db.get_run(info.id)
        assert final.state == "succeeded" and final.result.summary == "done"
        assert final.session_id == "sess-1"
        assert final.metrics.files_changed == 1 and final.metrics.files == ["file1.txt"]
        assert final.metrics.tests_passed is None  # no test command given

        # The change is in the worktree, not in the user's repo.
        assert not (repo / "file1.txt").exists()

    asyncio.run(scenario())


def test_checks_run_the_test_command(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        passing = f'"{sys.executable}" -c "print(42)"'
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x", test_command=passing))
        await wait_turn(runs, info.id)
        metrics = runs.db.get_run(info.id).metrics
        assert metrics.tests_passed is True and "42" in metrics.test_output

        failing = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x", test_command=failing))
        await wait_turn(runs, info.id)
        metrics = runs.db.get_run(info.id).metrics
        assert metrics.tests_passed is False and metrics.test_exit_code == 3

    asyncio.run(scenario())


def test_follow_up_continues_session(repo, tmp_path):
    async def scenario():
        adapter = FakeAdapter()
        runs = make_manager(tmp_path, adapter)
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="first"))
        await wait_turn(runs, info.id)
        last = runs.db.last_seq(info.id)

        await runs.follow_up(info.id, "second")
        kinds = await wait_turn(runs, info.id, after=last)
        assert kinds[0] == "user_message"
        assert adapter.calls[1] == ("second", adapter.calls[1][1])
        assert adapter.calls[1][1].resume_session_id == "sess-1"  # resumed the first session

        # Numbering continues; both turns' files are in the metrics.
        seqs = [seq for seq, _ in runs.db.events(info.id)]
        assert seqs == list(range(len(seqs)))
        assert runs.db.get_run(info.id).metrics.files_changed == 2

    asyncio.run(scenario())


def test_follow_up_rejected_while_running(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter(hang=True))
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x"))
        await asyncio.sleep(0.1)
        with pytest.raises(RunError):
            await runs.follow_up(info.id, "again")
        runs.cancel(info.id)
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_supervised_approval_flow(repo, tmp_path):
    async def scenario():
        adapter = FakeAdapter(hang=True)
        runs = make_manager(tmp_path, adapter)
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x", supervised=True))
        await asyncio.sleep(0.1)

        env = adapter.calls[0][1].approval_env  # what the MCP bridge would receive
        assert env["AIHUB_RUN_ID"] == info.id and env["AIHUB_URL"].startswith("http://127.0.0.1")

        # A wrong token is refused.
        with pytest.raises(PermissionError):
            await runs.request_approval(ApprovalRequest(run_id=info.id, token="wrong", tool_name="Bash"))

        # The bridge asks; the request waits until the user decides.
        ask = asyncio.create_task(runs.request_approval(ApprovalRequest(
            run_id=info.id, token=env["AIHUB_TOKEN"], tool_name="Bash", input={"command": "rm -rf build"},
        )))
        await asyncio.sleep(0.05)
        _, request = runs.db.events(info.id)[-1]
        assert request.kind == "approval_request" and request.data["tool_name"] == "Bash"
        assert not ask.done()

        assert runs.decide(info.id, request.data["id"], ApprovalDecision(allow=False, message="not now"))
        assert await ask == {"behavior": "deny", "message": "not now"}
        assert runs.db.events(info.id)[-1][1].kind == "approval_resolved"

        # A pending request is denied automatically when the run ends.
        ask2 = asyncio.create_task(runs.request_approval(ApprovalRequest(
            run_id=info.id, token=env["AIHUB_TOKEN"], tool_name="Write")))
        await asyncio.sleep(0.05)
        runs.cancel(info.id)
        assert (await ask2)["behavior"] == "deny"

    asyncio.run(scenario())


def test_changed_files(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x"))
        await wait_turn(runs, info.id)
        wt = Path(runs.db.get_run(info.id).worktree_path)
        (wt / "README.md").write_bytes(b"changed\n")       # a modified file
        (wt / "logo.bin").write_bytes(b"\x00\x01binary")  # a binary file

        files = {f.path: f for f in await runs.changed_files(info.id)}
        assert files["file1.txt"].status == "A"
        assert files["file1.txt"].original is None and files["file1.txt"].modified == "hi\n"
        assert (files["README.md"].original, files["README.md"].modified) == ("x\n", "changed\n")
        assert files["logo.bin"].binary and files["logo.bin"].modified is None

    asyncio.run(scenario())


def test_keep_commits_to_branch_and_removes_worktree(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="Add file\nmore text"))
        await wait_turn(runs, info.id)
        wt = Path(runs.db.get_run(info.id).worktree_path)

        kept = await runs.keep(info.id)
        assert kept.outcome == "kept" and kept.worktree_path is None
        assert not wt.exists()

        log = subprocess.run(["git", "log", "-1", "--format=%s", kept.branch], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
        assert log == "AI-Hub: Add file"
        # The diff still works after the folder is gone (base..branch).
        assert [f.path for f in await runs.changed_files(info.id)] == ["file1.txt"]
        # The user's own checkout is untouched.
        assert not (repo / "file1.txt").exists()

        with pytest.raises(RunError):
            await runs.follow_up(info.id, "more")

    asyncio.run(scenario())


def test_discard_removes_worktree_and_branch(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x"))
        await wait_turn(runs, info.id)

        gone = await runs.discard(info.id)
        assert gone.outcome == "discarded"
        branches = subprocess.run(["git", "branch", "--list", gone.branch], cwd=repo,
                                  capture_output=True, text=True).stdout
        assert branches.strip() == ""
        assert await runs.changed_files(info.id) == []

    asyncio.run(scenario())


def test_cancel(repo, tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter(hang=True))
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="hang"))
        await asyncio.sleep(0.1)
        assert runs.cancel(info.id)
        await asyncio.sleep(0.1)
        final = runs.db.get_run(info.id)
        assert final.state == "cancelled"
        assert runs.db.events(info.id)[-1][1].data["message"] == "Cancelled by user."

    asyncio.run(scenario())


def test_not_a_repo(tmp_path):
    async def scenario():
        runs = make_manager(tmp_path, FakeAdapter())
        with pytest.raises(worktree.GitError):
            await runs.start(RunCreate(agent_id="fake", repo_path=str(tmp_path), prompt="x"))

    asyncio.run(scenario())


def test_old_database_is_migrated(tmp_path):
    """A database from the first version (fewer columns) still opens."""
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE runs (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, repo_path TEXT NOT NULL,
            prompt TEXT NOT NULL, state TEXT NOT NULL, branch TEXT, worktree_path TEXT,
            created_at TEXT NOT NULL, result_json TEXT);
        INSERT INTO runs VALUES ('r1', 'claude-code', '/x', 'old task', 'succeeded', NULL, NULL, '2026-01-01', NULL);
    """)
    conn.commit()
    conn.close()
    run = Database(path).get_run("r1")
    assert run.prompt == "old task" and run.supervised is False and run.outcome is None

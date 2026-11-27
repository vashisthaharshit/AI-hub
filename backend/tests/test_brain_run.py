"""Brain steps 3-7: running a plan (sequential and parallel steps, merging,
conflicts, retries with a stronger model, approvals), project memory and
learning. A scripted fake agent edits files in the real git worktrees."""

import asyncio
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aihub import config
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.brain import learning, router
from aihub.brain.executor import BrainRunner
from aihub.brain.memory import CodeIndex, Memory, parse_report, symbols_in
from aihub.brain.tasks import Brain
from aihub.db import Database, now_iso
from aihub.models import (
    AgentEvent, AgentStatus, BrainAnswer, BrainPlan, BrainTask, ModelOption, PlanStep, RunResult, StepAssignment,
    TriageResult,
)
from aihub.runner import RunManager

MODELS = [ModelOption(id="small", label="Small Mini"), ModelOption(id="mid", label="Mid"),
          ModelOption(id="big", label="Big Max")]


class Scripted(AgentAdapter):
    """Plays each step from a script: title -> list of attempts.
    An attempt: {"files": {path: text or None (delete)}, "report": "done" | "blocked: why", "notes": [...]}"""

    id = "fake"
    name = "Fake"
    supports_follow_up = True
    models = MODELS

    def __init__(self, script: dict[str, list[dict]]):
        self.script = script
        self.sessions: dict[str, str] = {}      # session id -> step title
        self.counts: dict[str, int] = {}
        self.prompts: list[tuple[str, RunOptions]] = []

    async def detect(self):
        return self.status("ready")

    async def test_connection(self):
        return await self.detect()

    async def run(self, prompt, workdir: Path, options: RunOptions | None = None):
        options = options or RunOptions()
        self.prompts.append((prompt, options))
        if options.resume_session_id:
            title = self.sessions[options.resume_session_id]
        elif "conflict markers" in prompt:
            title = "#conflict"
        elif "tests fail" in prompt:
            title = "#tests"
        else:
            title = re.search(r"Your step: (.+)", prompt).group(1).strip()
        n = self.counts.get(title, 0)
        self.counts[title] = n + 1
        attempts = self.script.get(title, [{}])
        attempt = attempts[min(n, len(attempts) - 1)]
        session = f"s-{title}-{n}"
        self.sessions[session] = title
        yield AgentEvent(kind="session_started", data={"session_id": session, "model": options.model})
        for rel, text in (attempt.get("files") or {}).items():
            path = workdir / rel
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode())
        notes = "".join(f"\n- {n}" for n in attempt.get("notes", []))
        report = attempt.get("report", "done")
        yield AgentEvent(kind="text", data={"text": f"Worked on {title}.\nSTEP RESULT: {report}\nNOTES:{notes}"})
        yield AgentEvent(kind="result", data=RunResult(success=True, summary="ok", input_tokens=100,
                                                       output_tokens=20).model_dump())


class Registry(AgentRegistry):
    async def scan(self, refresh=False, hidden=None):
        return [AgentStatus(id="fake", name="Fake", state="ready", models=MODELS)]


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_bytes(b"x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def setup(tmp_path, repo, script, steps, approval="all", test_command=None):
    db = Database(tmp_path / "a.db")
    adapter = Scripted(script)
    registry = Registry([adapter])
    runs = RunManager(db, registry)
    brain = Brain(db, summarizer=None, registry=registry)
    memory = Memory(db)
    runner = BrainRunner(db, runs, registry, brain, memory)
    plan = __import__("aihub.brain.planner", fromlist=["build_plan"]).build_plan(steps)
    now = now_iso()
    task = BrainTask(id="t1", workspace_id="ws", repo_path=str(repo), request="Build it", test_command=test_command,
                     approval=approval, state="planned", triage=TriageResult(size="big", how="rules"),
                     plan=plan, created_at=now, updated_at=now)
    router.route(task.plan, [AgentStatus(id="fake", name="Fake", state="ready", models=MODELS)], task.mode)
    db.save_brain_task(task)
    return db, adapter, runner


def step(sid, title, deps=(), files=(), difficulty="easy", kind="build"):
    return PlanStep(id=sid, title=title, type=kind, difficulty=difficulty, files=list(files), depends_on=list(deps),
                    brief=f"do {title}", done_when="it works")


async def until(runner, states, timeout=40):
    for _ in range(int(timeout * 20)):
        task = runner.brain.get("t1")
        if task.execution and task.execution.state in states:
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"still {runner.brain.get('t1').execution.state}: {runner.brain.get('t1').execution.message}")


OK = [sys.executable, "-c", "import sys; sys.exit(0)"]


def cmd(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_sequential_plan_then_keep(tmp_path, repo):
    script = {"API": [{"files": {"api.py": "def login():\n    return 1\n"}, "notes": ["login() lives in api.py"]}],
              "Tests": [{"files": {"test_api.py": "from api import login\n"}}],
              "Review all changes": [{}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "API"), step("s2", "Tests", deps=["s1"])],
                                test_command=cmd("import sys; sys.exit(0)"))

    async def go():
        await runner.start("t1")
        task = await until(runner, {"done", "failed", "waiting"})
        ex = task.execution
        assert ex.state == "done", ex.message
        assert [ex.steps[s].state for s in ("s1", "s2", "review")] == ["passed", "passed", "passed"]
        assert ex.baseline_tests is True and ex.final_tests is True
        main = Path(ex.worktree_path)
        assert (main / "api.py").exists() and (main / "test_api.py").exists()
        assert not (repo / "api.py").exists()                       # the user's folder is untouched
        files = {f.path for f in await runner.files("t1")}
        assert files == {"api.py", "test_api.py"}
        # the second step's brief says what the first step did, and the memory note
        second = next(p for p, _ in adapter.prompts if "Your step: Tests" in p)
        assert "[done] API" in second and "login() lives in api.py" in second
        task = await runner.keep("t1")
        assert task.execution.state == "kept" and "git merge aihub/brain-t1" in task.execution.message
        assert "api.py" in git(repo, "show", "--stat", "aihub/brain-t1")
        assert not main.exists()
        notes = runner.memory.notes(workspace_id="ws", folder=repo, scope="repo")
        assert [n.text for n in notes] == ["login() lives in api.py"] and notes[0].files == ["api.py"]
        assert notes[0].stale_files == ["api.py"]       # not merged into the user's folder yet
        stats = learning.load(db)
        assert stats[("build", "easy", "small")] == (2, 2)

    asyncio.run(go())


def test_parallel_steps_are_merged(tmp_path, repo):
    script = {"Backend": [{"files": {"backend.py": "b = 1\n"}}], "Frontend": [{"files": {"frontend.js": "f = 1\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "Backend", files=["backend.py"]),
                                                          step("s2", "Frontend", files=["frontend.js"])])

    async def go():
        await runner.start("t1")
        task = await until(runner, {"done", "failed", "waiting"})
        ex = task.execution
        assert ex.state == "done", ex.message
        main = Path(ex.worktree_path)
        assert (main / "backend.py").exists() and (main / "frontend.js").exists()
        assert all(ex.steps[s].merged for s in ("s1", "s2"))
        assert not any(Path(config.WORKTREES_DIR / f"brain-t1-{s}").exists() for s in ("s1", "s2"))
        assert "aihub/brain-t1-s1" not in git(repo, "branch")
        p1 = next(p for p, _ in adapter.prompts if "Your step: Backend" in p)
        assert "run at the same time" in p1 and "Frontend: frontend.js" in p1
        task = await runner.discard("t1")
        assert task.execution.state == "discarded" and "aihub/brain-t1" not in git(repo, "branch")

    asyncio.run(go())


def test_conflict_is_fixed_by_an_agent(tmp_path, repo):
    script = {"One": [{"files": {"shared.txt": "one\n"}}], "Two": [{"files": {"shared.txt": "two\n"}}],
              "#conflict": [{"files": {"shared.txt": "one and two\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "One"), step("s2", "Two")])

    async def go():
        await runner.start("t1")
        task = await until(runner, {"done", "failed", "waiting"})
        assert task.execution.state == "done", task.execution.message
        main = Path(task.execution.worktree_path)
        assert (main / "shared.txt").read_text() == "one and two\n"
        assert len(task.execution.fixup_runs) == 1
        assert any("conflict markers" in p for p, _ in adapter.prompts)

    asyncio.run(go())


def test_failed_step_retries_with_a_stronger_model(tmp_path, repo):
    script = {"API": [{"report": "blocked: no idea"}, {"files": {"api.py": "x = 1\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "API")])

    async def go():
        await runner.start("t1")
        task = await until(runner, {"done", "failed", "waiting"})
        prog = task.execution.steps["s1"]
        assert task.execution.state == "done" and prog.state == "passed" and prog.stepped_up and prog.attempts == 2
        retry_prompt, options = adapter.prompts[1]
        assert "isn't done yet" in retry_prompt and options.model == "mid" and options.resume_session_id
        stats = learning.load(db)
        assert stats[("build", "easy", "small")] == (0, 1) and stats[("build", "easy", "medium")] == (1, 1)

    asyncio.run(go())


def test_still_failing_asks_the_user_then_skip(tmp_path, repo):
    script = {"API": [{"report": "blocked: missing key"}], "Docs": [{"files": {"docs.md": "hi\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "API"), step("s2", "Docs", deps=["s1"])])

    async def go():
        await runner.start("t1")
        task = await until(runner, {"waiting", "failed"})
        assert task.execution.waiting_for == "failure" and "missing key" in task.execution.message
        await runner.answer("t1", BrainAnswer(action="skip"))
        task = await until(runner, {"done", "failed"})
        assert task.execution.steps["s1"].state == "skipped" and task.execution.steps["s2"].state == "passed"

    asyncio.run(go())


def test_tests_judge_a_step(tmp_path, repo):
    check = cmd("import os, sys; sys.exit(1 if os.path.exists('broken.flag') else 0)")
    script = {"API": [{"files": {"broken.flag": "x"}}, {"files": {"broken.flag": None, "api.py": "ok\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "API")], test_command=check)

    async def go():
        await runner.start("t1")
        task = await until(runner, {"done", "failed", "waiting"})
        assert task.execution.state == "done", task.execution.message
        assert task.execution.steps["s1"].attempts == 2 and task.execution.final_tests is True

    asyncio.run(go())


def test_approve_each_step_with_an_edited_brief(tmp_path, repo):
    db, adapter, runner = setup(tmp_path, repo, {"API": [{"files": {"a.py": "1\n"}}]}, [step("s1", "API")],
                                approval="each")

    async def go():
        await runner.start("t1")
        task = await until(runner, {"waiting"})
        assert task.execution.waiting_for == "approve" and task.execution.waiting_steps == ["s1"]
        await runner.answer("t1", BrainAnswer(action="continue", briefs={"s1": "use the new style"}))
        task = await until(runner, {"waiting"})      # then the review step
        assert task.execution.waiting_steps == ["review"]
        await runner.answer("t1", BrainAnswer(action="stop"))
        task = await until(runner, {"stopped"})
        assert "What to do: use the new style" in adapter.prompts[0][0]
        # continue later from where it stopped
        await runner.start("t1")
        task = await until(runner, {"waiting"})
        assert task.execution.waiting_steps == ["review"]

    asyncio.run(go())


def test_recover_marks_running_plans_stopped(tmp_path, repo):
    db, adapter, runner = setup(tmp_path, repo, {}, [step("s1", "API")])
    task = runner.brain.get("t1")
    from aihub.models import BrainExecution
    task.execution = BrainExecution(state="running")
    runner.brain._save(task)
    runner.recover()
    assert runner.brain.get("t1").execution.state == "stopped"


# ---------- memory ----------

def test_parse_report():
    status, reason, notes = parse_report("Did it.\nSTEP RESULT: blocked: no API key\nNOTES:\n- keys live in .env\n- x")
    assert (status, reason, notes) == ("blocked", "no API key", ["keys live in .env", "x"])
    assert parse_report("all good") == (None, None, [])


def test_notes_search_staleness_and_promotion(tmp_path):
    db = Database(tmp_path / "a.db")
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "auth.py").write_text("v1")
    mem = Memory(db)
    mem.add(workspace_id="ws", task_id="t1", text="Passwords are hashed with bcrypt in auth.py", files=[], folder=folder)
    mem.add(workspace_id="ws", task_id="t1", text="The footer colors come from theme.css", files=[], folder=folder)
    found = mem.search("change how passwords are hashed", workspace_id="ws", task_id="t1", folder=folder)
    assert found[0].files == ["auth.py"] and "bcrypt" in found[0].text
    assert mem.promote("t1", folder) == 2
    (folder / "auth.py").write_text("v2")
    notes = mem.notes(workspace_id="ws", folder=folder, scope="repo")
    assert next(n for n in notes if "bcrypt" in n.text).stale_files == ["auth.py"]
    assert mem.forget_task("t1") == 0          # already repo notes


def test_code_index(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "auth.py").write_text("class Auth:\n    def login(self):\n        pass\n")
    (tmp_path / "app.py").write_text("from auth import Auth\n\nAuth().login()\n")
    (tmp_path / "Login.tsx").write_text("export function LoginPage() {}\nexport const API_URL = '/x'\n")
    index = CodeIndex()
    text = index.find(tmp_path, "Auth")
    assert "auth.py:1 (class Auth)" in text and "app.py:1" in text
    assert "Login.tsx:1 (function LoginPage)" in index.find(tmp_path, "LoginPage")
    assert "Nothing named" in index.find(tmp_path, "nope")
    assert {s.name for s in symbols_in("x.ts", "interface User {}\nexport class Store {}")} == {"User", "Store"}


# ---------- learning ----------

def test_learning_adjusts_the_model_size(tmp_path):
    db = Database(tmp_path / "a.db")
    for passed in [1] * 9 + [0]:
        learning.record(db, workspace_id="ws", task_id="t", step_type="test", difficulty="medium", agent_id="fake",
                        model="small", tier="small", mode="balanced", passed=bool(passed), attempt=1)
    stats = learning.load(db)
    assert learning.adjust(1, "test", "medium", "balanced", stats)[0] == 0          # small passes 9/10: use it
    assert learning.adjust(1, "test", "medium", "best", stats)[0] == 1              # Best never steps down
    plan = BrainPlan(steps=[step("s1", "Tests", kind="test", difficulty="medium")], waves=[["s1"]])
    router.route(plan, [AgentStatus(id="fake", name="Fake", state="ready", models=MODELS)], "balanced", stats)
    assert plan.steps[0].assignment.model == "small" and "learned" in plan.steps[0].assignment.reason
    summary = learning.summary(db)
    assert summary.adjustments and summary.stats[0].total == 10


def test_step_up_choices():
    agents = [AgentStatus(id="fake", name="Fake", state="ready", models=MODELS),
              AgentStatus(id="other", name="Other", state="ready", models=[ModelOption(id="o-max", label="O Max")])]
    small = StepAssignment(agent_id="fake", agent_name="Fake", model="small", model_label="Small", reason="", manual=False)
    assert router.step_up(small, agents).model == "mid"
    big = small.model_copy(update={"model": "big"})
    up = router.step_up(big, agents)
    assert up.agent_id == "other" and "fresh pair of eyes" in up.reason
    assert router.strongest(agents).model in ("big", "o-max")


# ---------- memory tools reach the agents ----------

def test_claude_gets_the_memory_tools(tmp_path, monkeypatch):
    import json

    from aihub.agents.claude_code import ClaudeCodeAdapter, _write_mcp_config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = _write_mcp_config(None, {"AIHUB_MEMORY_TOKEN": "x"})
    servers = json.loads(path.read_text())["mcpServers"]
    assert list(servers) == ["aihub_memory"] and servers["aihub_memory"]["args"] == ["-m", "aihub.brain.memory_mcp"]
    cmd = ClaudeCodeAdapter().build_command("claude", RunOptions(memory_env={"A": "1"}), mcp_config=path)
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert {"Bash", "PowerShell", "mcp__aihub_memory"} <= set(allowed)
    assert "Bash(git push:*)" in cmd[cmd.index("--disallowedTools") + 1]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits" and "--permission-prompt-tool" not in cmd


def test_codex_gets_the_memory_tools():
    from aihub.agents.codex import CodexAdapter

    cmd = CodexAdapter().build_command("codex", RunOptions(memory_env={"AIHUB_URL": "http://127.0.0.1:8000"}))
    joined = " ".join(cmd)
    assert "mcp_servers.aihub_memory.command=" in joined and "aihub.brain.memory_mcp" in joined
    assert "mcp_servers.aihub_memory.env={AIHUB_URL='http://127.0.0.1:8000'}" in cmd
    assert cmd[-1] == "-"


def test_memory_tool_server_lists_its_tools(capsys):
    import json

    from aihub.brain import memory_mcp

    memory_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = json.loads(capsys.readouterr().out)["result"]["tools"]
    assert {t["name"] for t in tools} == {"find_code", "search_notes", "add_note", "task_status"}


def test_memory_tool_answers_for_a_running_step(tmp_path, repo):
    db, adapter, runner = setup(tmp_path, repo, {}, [step("s1", "API")])
    (repo / "auth.py").write_text("def login():\n    pass\n")
    git(repo, "add", ".")
    runner._tokens["tok"] = ("t1", "s1", str(repo))

    async def go():
        assert "auth.py:1 (function login)" in await runner.memory_tool("tok", "find_code", {"name": "login"})
        assert await runner.memory_tool("tok", "add_note", {"text": "login() has no rate limit yet", "files": ["auth.py"]}) == "Saved."
        assert "rate limit" in await runner.memory_tool("tok", "search_notes", {"query": "rate limit login"})
        assert "API [you]" in await runner.memory_tool("tok", "task_status", {})
        with pytest.raises(PermissionError):
            await runner.memory_tool("wrong", "find_code", {"name": "x"})

    asyncio.run(go())


def test_folder_is_deleted_even_when_git_cannot(tmp_path, repo, monkeypatch):
    from aihub import worktree

    folder = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "temp", str(folder))
    (folder / "cache").mkdir()
    (folder / "cache" / "x.pyc").write_bytes(b"x")
    real = worktree._git

    async def flaky(*args, cwd):
        if args[:2] == ("worktree", "remove"):
            raise worktree.GitError("error: failed to delete: Directory not empty")
        return await real(*args, cwd=cwd)

    monkeypatch.setattr(worktree, "_git", flaky)
    asyncio.run(worktree.remove_worktree(repo, folder))
    assert not folder.exists() and str(folder) not in git(repo, "worktree", "list")


def test_codex_memory_tools_are_auto_approved():
    from aihub.agents.codex import memory_server_args

    assert "mcp_servers.aihub_memory.default_tools_approval_mode='approve'" in memory_server_args({"A": "1"})


def test_continue_after_a_half_deleted_step_copy(tmp_path, repo, monkeypatch):
    """Windows: deleting a step's copy failed half-way (its .git link gone, the folder
    still there) and the plan stopped. Continue must finish it, and a step whose
    unsaved work was in a lost copy runs again."""
    script = {"Backend": [{"files": {"backend.py": "b = 1\n"}}], "Frontend": [{"files": {"frontend.js": "f = 1\n"}}]}
    db, adapter, runner = setup(tmp_path, repo, script, [step("s1", "Backend", files=["backend.py"]),
                                                          step("s2", "Frontend", files=["frontend.js"])])
    from aihub import worktree
    original = BrainRunner._remove_step_worktree
    calls = {"n": 0}

    async def half_delete(self, task, prog, delete_branch):
        calls["n"] += 1
        if calls["n"] == 1 and prog.worktree_path:
            (Path(prog.worktree_path) / ".git").unlink()                      # git's link is gone...
            (Path(task.execution.steps["s2"].worktree_path) / ".git").unlink()  # ...and s2's copy too, unsaved
            raise worktree.GitError("error: failed to delete: Directory not empty")
        return await original(self, task, prog, delete_branch)

    async def go():
        monkeypatch.setattr(BrainRunner, "_remove_step_worktree", half_delete)
        await runner.start("t1")
        task = await until(runner, {"failed", "done", "waiting"})
        assert task.execution.state == "failed" and "Click Continue" in task.execution.message.splitlines()[0]
        monkeypatch.setattr(BrainRunner, "_remove_step_worktree", original)
        await runner.start("t1")                                              # Continue
        task = await until(runner, {"done", "failed", "waiting"})
        assert task.execution.state == "done", task.execution.message
        main = Path(task.execution.worktree_path)
        assert (main / "backend.py").exists() and (main / "frontend.js").exists()
        assert adapter.counts["Backend"] == 1 and adapter.counts["Frontend"] == 2   # only the lost step ran again
        assert any("was lost before its work was saved" in e.text for e in task.execution.log)

    asyncio.run(go())

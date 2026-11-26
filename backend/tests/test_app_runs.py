"""Step 4: running an app's commands.

What's checked: the prompt built from a card's answers, the run's settings
(env, plugins, trust), money tools always asking, the outputs list, and that
app runs never run git on the app's folder.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.apps import running
from aihub.db import Database
from aihub.models import (
    AgentEvent, AppCommand, AppInfo, AppManifest, AppPlugin, AppSetup, ApprovalRequest, CommandForm, FieldChoice,
    FormField, RunResult,
)
from aihub.runner import RunError, RunManager


def form(argument: str, *fields: FormField) -> CommandForm:
    return CommandForm(fields=list(fields), argument=argument)


@pytest.fixture
def app(tmp_path) -> AppInfo:
    folder = tmp_path / "app"
    (folder / ".claude" / "commands").mkdir(parents=True)
    (folder / ".claude" / "commands" / "apply.md").write_text(
        "---\ndescription: Apply\n---\nThe job is $ARGUMENTS. First word: $1.\n", encoding="utf-8")
    manifest = AppManifest(
        name="app", tier=1,
        commands=[AppCommand(name="apply", title="Apply", source=".claude/commands/apply.md", invoke="/apply"),
                  AppCommand(name="trader-broker", title="Broker", source="x", invoke="/p:trader-broker",
                             plugin="p", money=True)],
        plugins=[AppPlugin(name="p", root=".")],
        setup=AppSetup(always_ask=["alpaca"], money_commands=["trader-broker"]),
    )
    return AppInfo(id="a1", name="app", path=str(folder), created_at="t", updated_at="t", manifest=manifest)


# ---------- the prompt ----------

class TestPrompt:
    def test_claude_gets_the_slash_command(self, app):
        f = form("{job} [--limit {limit}] {all}",
                 FormField(key="job", label="Job link", required=True),
                 FormField(key="limit", label="Limit", type="number"),
                 FormField(key="all", label="All", type="toggle", value="--all"))
        cmd = app.manifest.commands[0]
        assert running.build_prompt(app, cmd, f, {"job": "https://x.com/1", "all": "--all"}, "claude-code") \
            == "/apply https://x.com/1 --all"
        assert running.build_prompt(app, app.manifest.commands[1], None, {}, "claude-code") == "/p:trader-broker"

    def test_missing_required_answer(self, app):
        f = form("{job}", FormField(key="job", label="Job link", required=True))
        with pytest.raises(running.AppRunError, match="Job link"):
            running.build_prompt(app, app.manifest.commands[0], f, {"job": "  "}, "claude-code")

    def test_files_are_quoted(self, app):
        f = form("{docs}", FormField(key="docs", label="Docs", type="file", multiple=True))
        prompt = running.build_prompt(app, app.manifest.commands[0], f, {"docs": "documents/My CV.pdf\ndocuments/a.pdf"},
                                      "claude-code")
        assert prompt == '/apply "documents/My CV.pdf" documents/a.pdf'

    def test_codex_gets_the_instructions(self, app):
        f = form("{job}", FormField(key="job", label="Job"))
        prompt = running.build_prompt(app, app.manifest.commands[0], f, {"job": "acme engineer"}, "codex")
        assert "The job is acme engineer. First word: acme." in prompt and "`apply` command" in prompt


# ---------- settings and money ----------

def test_context(app, monkeypatch):
    monkeypatch.setattr(running, "uv_folder", lambda: "/uv/bin")
    ctx = running.build_context(app, {"ALPACA_API_KEY": "PK"}, trusted=True, command="apply")
    assert ctx.extra_env["ALPACA_API_KEY"] == "PK" and ctx.extra_env["PATH"].endswith("/uv/bin")
    assert ctx.plugin_dirs == [str(Path(app.path).resolve())]
    assert (ctx.trusted, ctx.ask_everything, ctx.always_ask) == (True, False, ["alpaca"])
    money = running.build_context(app, {}, trusted=True, command="trader-broker")
    assert (money.trusted, money.ask_everything) == (False, True)   # a money command: everything asks


@pytest.mark.parametrize("tool,expected", [
    ("mcp__alpaca__place_stock_order", True),
    ("mcp__plugin_day-trader-skill_alpaca__place_stock_order", True),
    ("mcp__alpacaish__x", False),
    ("Bash", False),
])
def test_money_tools(tool, expected):
    assert running.is_money_tool(tool, ["alpaca"]) is expected


def test_outputs(tmp_path):
    (tmp_path / "keep.txt").write_text("a")
    (tmp_path / "edit.txt").write_text("a")
    (tmp_path / ".git").mkdir()
    before = running.snapshot(tmp_path)
    (tmp_path / "edit.txt").write_text("changed!")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "cv.pdf").write_bytes(b"pdf")
    (tmp_path / ".git" / "index").write_text("x")   # ignored
    result = running.diff(before, running.snapshot(tmp_path))
    assert [(f["path"], f["status"]) for f in result["files"]] == [("out/cv.pdf", "new"), ("edit.txt", "changed")]
    assert result["total"] == 2


# ---------- the run manager ----------

class FakeAgent(AgentAdapter):
    id = "fake"
    name = "Fake"
    supports_supervised = True
    supports_follow_up = True

    def __init__(self):
        self.calls: list[tuple[str, RunOptions]] = []

    async def detect(self):
        return self.status("ready")

    async def test_connection(self):
        return await self.detect()

    async def run(self, prompt, workdir: Path, options: RunOptions | None = None):
        self.calls.append((prompt, options))
        yield AgentEvent(kind="session_started", data={"session_id": "s1"})
        (workdir / "result.md").write_bytes(b"done\n")
        yield AgentEvent(kind="result", data=RunResult(success=True, summary="ok").model_dump())


@pytest.fixture
def manager(tmp_path, app):
    db = Database(tmp_path / "t.db")
    db.insert_app(app)
    agent = FakeAgent()
    ctx = running.AppRunContext(extra_env={"KEY": "v"}, plugin_dirs=["/plug"], trusted=False, always_ask=["alpaca"])
    mgr = RunManager(db, AgentRegistry([agent]), app_context=lambda run: ctx)
    yield mgr, agent, db
    db.close()


def run_app(mgr, agent, app, prompt="/apply x"):
    async def go():
        run = await mgr.start_app(app.id, app.path, agent, prompt, "Apply")
        await mgr._tasks[run.id]
        return mgr.db.get_run(run.id)
    return asyncio.run(go())


class TestRunManager:
    def test_runs_in_the_app_folder_with_its_settings(self, manager, app):
        mgr, agent, db = manager
        run = run_app(mgr, agent, app)
        assert (run.kind, run.app_id, run.label, run.state) == ("app", "a1", "Apply", "succeeded")
        assert run.branch is None and run.worktree_path == app.path
        prompt, options = agent.calls[0]
        assert prompt == "/apply x" and options.extra_env == {"KEY": "v"} and options.plugin_dirs == ["/plug"]
        assert options.accept_edits and options.approval_env   # edits fine, other actions ask
        outputs = [e for _, e in db.events(run.id) if e.kind == "outputs"]
        assert outputs[0].data["files"] == [{"path": "result.md", "status": "new", "size": 5}]
        assert db.list_runs(kind="task") == [] and [r.id for r in db.list_runs(kind="app", app_id="a1")] == [run.id]

    def test_no_keep_discard_and_delete_keeps_the_folder(self, manager, app):
        mgr, agent, db = manager
        run = run_app(mgr, agent, app)
        for action in (mgr.keep, mgr.discard):
            with pytest.raises(RunError, match="nothing to keep or discard"):
                asyncio.run(action(run.id))
        asyncio.run(mgr.delete(run.id))
        assert db.get_run(run.id) is None and (Path(app.path) / "result.md").is_file()

    def test_follow_up_continues_in_place(self, manager, app):
        mgr, agent, db = manager
        run = run_app(mgr, agent, app)

        async def go():
            await mgr.follow_up(run.id, "Here is my answer")
            await mgr._tasks[run.id]
        asyncio.run(go())
        assert agent.calls[1][0] == "Here is my answer" and agent.calls[1][1].resume_session_id == "s1"


class TestApprovals:
    """request_approval with an app policy (set up by hand, as a run would)."""

    def ask(self, mgr, tool, policy):
        mgr._tokens["r1"] = "tok"
        mgr._policies["r1"] = policy
        mgr.db.insert_run(__import__("aihub.models", fromlist=["RunInfo"]).RunInfo(
            id="r1", agent_id="fake", repo_path=".", prompt="p", state="running", created_at="t", kind="app"))

        async def go():
            task = asyncio.create_task(mgr.request_approval(ApprovalRequest(run_id="r1", token="tok", tool_name=tool, input={})))
            await asyncio.sleep(0.05)
            if not task.done():   # waiting for the user: answer like the UI would
                pending = next(iter(mgr._approvals))
                mgr.decide("r1", pending, __import__("aihub.models", fromlist=["ApprovalDecision"]).ApprovalDecision(allow=False))
                return "asked", await task
            return "auto", await task
        return asyncio.run(go())

    def test_trusted_app_gets_an_automatic_yes(self, manager):
        how, answer = self.ask(manager[0], "Bash", running.AppRunContext(trusted=True, always_ask=["alpaca"]))
        assert how == "auto" and answer["behavior"] == "allow"

    def test_money_tools_always_ask_even_when_trusted(self, manager):
        how, _ = self.ask(manager[0], "mcp__alpaca__place_stock_order",
                          running.AppRunContext(trusted=True, always_ask=["alpaca"]))
        assert how == "asked"
        request = [e for _, e in manager[0].db.events("r1") if e.kind == "approval_request"][0]
        assert request.data["money"] is True

    def test_untrusted_app_asks(self, manager):
        how, _ = self.ask(manager[0], "Bash", running.AppRunContext(trusted=False))
        assert how == "asked"

    def test_money_command_asks_for_everything(self, manager):
        how, _ = self.ask(manager[0], "Bash", running.AppRunContext(trusted=True, ask_everything=True))
        assert how == "asked"

"""Agent switching (handoffs) and the summary model."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from aihub import handoff
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.db import Database
from aihub.models import AgentEvent, RunCreate, RunInfo, RunResult, SummarySettings
from aihub.runner import RunError, RunManager
from aihub.secrets_store import MemoryStore
from aihub.summarizer import Summarizer, SummaryError

from test_runner import FakeAdapter, repo, wait_turn  # noqa: F401 - repo is a fixture


class OtherAgent(FakeAdapter):
    id = "other"
    name = "Other agent"
    supports_supervised = False


class SummaryAgent(FakeAdapter):
    """Plays the summary model: answers any prompt with a fixed summary."""

    id = "summary-agent"
    name = "Summary agent"

    def __init__(self, reply="SHORT SUMMARY", fail=False):
        super().__init__()
        self.reply, self.fail = reply, fail

    async def run(self, prompt, workdir, options=None):
        self.calls.append((prompt, options))
        if self.fail:
            yield AgentEvent(kind="error", data={"message": "summary agent exploded"})
            yield AgentEvent(kind="result", data=RunResult(success=False).model_dump())
            return
        yield AgentEvent(kind="result", data=RunResult(success=True, summary=self.reply).model_dump())


def setup(tmp_path, *adapters, summary: SummarySettings | None = None):
    db = Database(tmp_path / "t.db")
    registry = AgentRegistry(list(adapters))
    summarizer = Summarizer(db, registry, MemoryStore())
    if summary:
        summarizer.save_settings(summary)
    return RunManager(db, registry, summarizer), summarizer


# ---------- the template, without any agent ----------

def ev(kind, **data):
    return AgentEvent(kind=kind, data=data)


def test_history_from_events():
    events = [
        ev("user_message", text="Add a /health endpoint", agent_id="claude-code", model="opus"),
        ev("tool_call", id="1", name="Write", input={"file_path": "app.py"}),
        ev("tool_call", id="2", name="Write", input={"file_path": "test_app.py"}),
        ev("tool_call", id="3", name="Bash", input={"command": "pytest"}),
        ev("result", success=True, summary="Added /health and a test."),
        ev("checks", files_changed=2, lines_added=20, lines_removed=0, files=["app.py", "test_app.py"],
           test_command="pytest -q", tests_passed=True, test_exit_code=0),
        ev("user_message", text="Also validate input", agent_id="claude-code"),
        ev("error", message="Something broke"),
        ev("result", success=False, summary=None),
    ]
    run = RunInfo(id="r", agent_id="claude-code", repo_path="/r", prompt="Add a /health endpoint",
                  state="succeeded", created_at="x")
    history = handoff.build_history(events, run)
    assert "### Turn 1: claude-code (opus)" in history
    assert "Result (finished): Added /health and a test." in history
    assert "Tools used: Write x2, Bash x1" in history
    assert "Files touched: app.py, test_app.py" in history
    assert "Tests (pytest -q): passed" in history
    assert "### Turn 2" in history and "Errors: Something broke" in history


def test_long_history_keeps_first_and_newest_turns(monkeypatch):
    monkeypatch.setattr(handoff, "MAX_HISTORY_CHARS", 400)
    events = []
    for i in range(10):
        events += [ev("user_message", text=f"request {i}"), ev("result", success=True, summary="x" * 80)]
    run = RunInfo(id="r", agent_id="a", repo_path="/r", prompt="p", state="succeeded", created_at="x")
    history = handoff.build_history(events, run)
    assert "request 0" in history and "request 9" in history   # first and newest kept
    assert "older turn(s) left out" in history and "request 3" not in history


# ---------- switching agents ----------

def test_switch_agent_uses_a_template_handoff(repo, tmp_path):
    async def scenario():
        first, other = FakeAdapter(), OtherAgent()
        runs, _ = setup(tmp_path, first, other)
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="Build the thing", model="m1"))
        await wait_turn(runs, info.id)

        switched = await runs.follow_up(info.id, "Now add docs", agent_id="other")
        assert switched.agent_id == "other" and switched.state == "running"
        assert switched.session_id is None and switched.model is None   # fresh session, own defaults
        await wait_turn(runs, info.id, after=runs.db.last_seq(info.id) - 100)

        prompt, options = other.calls[0]
        assert options.resume_session_id is None
        assert "Another AI agent (Fake agent)" in prompt
        assert "## Original task\nBuild the thing" in prompt
        assert "### Turn 1: fake (m1)" in prompt
        assert "file1.txt" in prompt                                     # changes so far
        assert prompt.rstrip().endswith("## Your task now\nNow add docs")

        user_msgs = [e for _, e in runs.db.events(info.id) if e.kind == "user_message"]
        last = user_msgs[-1].data
        assert last["text"] == "Now add docs" and last["agent_id"] == "other"
        assert last["handoff_kind"] == "template" and last["switched_from"] == "fake"
        assert last["handoff"] == prompt

        # The new agent's session is used for the next follow-up.
        final = runs.db.get_run(info.id)
        assert final.agent_id == "other" and final.session_id == "sess-1"
        await runs.follow_up(info.id, "one more")
        await wait_turn(runs, info.id, after=runs.db.last_seq(info.id))
        assert other.calls[1][1].resume_session_id == "sess-1"

    asyncio.run(scenario())


def test_switch_refused_when_supervised_and_new_agent_cannot(repo, tmp_path):
    async def scenario():
        runs, _ = setup(tmp_path, FakeAdapter(), OtherAgent())
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="x", supervised=True))
        await wait_turn(runs, info.id)
        with pytest.raises(RunError, match="supervised"):
            await runs.follow_up(info.id, "go", agent_id="other")
        assert runs.db.get_run(info.id).agent_id == "fake"   # nothing changed

    asyncio.run(scenario())


def test_switch_uses_the_summary_model(repo, tmp_path):
    async def scenario():
        summary_agent = SummaryAgent(reply="They built the thing; docs are missing.")
        other = OtherAgent()
        runs, _ = setup(tmp_path, FakeAdapter(), other, summary_agent,
                        summary=SummarySettings(provider="agent", agent_id="summary-agent", model="haiku", effort="low"))
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="Build the thing"))
        await wait_turn(runs, info.id)
        await runs.follow_up(info.id, "Add docs", agent_id="other")
        await wait_turn(runs, info.id, after=runs.db.last_seq(info.id) - 100)

        sum_prompt, sum_options = summary_agent.calls[0]
        assert "### Turn 1" in sum_prompt                        # it summarized the history
        assert (sum_options.model, sum_options.effort, sum_options.max_turns) == ("haiku", "low", 1)

        prompt = other.calls[0][0]
        assert "## Summary of the work so far\nThey built the thing; docs are missing." in prompt
        last = [e for _, e in runs.db.events(info.id) if e.kind == "user_message"][-1].data
        assert last["handoff_kind"] == "llm"
        assert last["handoff_note"] == "Summarized by Summary agent · haiku · effort low"

    asyncio.run(scenario())


def test_failed_summary_falls_back_to_template(repo, tmp_path):
    async def scenario():
        other = OtherAgent()
        runs, _ = setup(tmp_path, FakeAdapter(), other, SummaryAgent(fail=True),
                        summary=SummarySettings(provider="agent", agent_id="summary-agent"))
        info = await runs.start(RunCreate(agent_id="fake", repo_path=str(repo), prompt="Build"))
        await wait_turn(runs, info.id)
        await runs.follow_up(info.id, "Docs", agent_id="other")
        await wait_turn(runs, info.id, after=runs.db.last_seq(info.id) - 100)
        last = [e for _, e in runs.db.events(info.id) if e.kind == "user_message"][-1].data
        assert last["handoff_kind"] == "template"
        assert "summary agent exploded" in last["handoff_note"]
        assert "## What happened so far" in other.calls[0][0]   # the switch still happened

    asyncio.run(scenario())


# ---------- the summarizer with Groq / OpenRouter (fake local server) ----------

class FakeProvider(BaseHTTPRequestHandler):
    requests: list = []

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.requests.append((self.path, self.headers["Authorization"], body))
        if self.headers["Authorization"] != "Bearer good-key-123":
            return self._reply(401, {"error": {"message": "Invalid API Key"}})
        self._reply(200, {"choices": [{"message": {"role": "assistant", "content": " Summary from Groq. "}}]})

    def do_GET(self):  # noqa: N802
        self._reply(200, {"data": [{"id": "llama-b"}, {"id": "llama-a"}]})

    def log_message(self, *args):
        pass


@pytest.fixture
def provider(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("AIHUB_GROQ_BASE_URL", f"http://127.0.0.1:{server.server_port}/openai/v1")
    FakeProvider.requests = []
    yield
    server.shutdown()


def test_groq_summary_with_users_own_key(provider, tmp_path):
    _, summarizer = setup(tmp_path, FakeAdapter())
    settings = SummarySettings(provider="groq", model="llama-a")

    with pytest.raises(SummaryError, match="No Groq API key"):
        asyncio.run(summarizer.summarize("text", settings))

    summarizer.secrets.set("groq", "good-key-123")
    assert asyncio.run(summarizer.summarize("the history", settings)) == "Summary from Groq."
    path, auth, body = FakeProvider.requests[-1]
    assert path == "/openai/v1/chat/completions" and auth == "Bearer good-key-123"
    assert body["model"] == "llama-a" and body["messages"][1]["content"] == "the history"

    assert asyncio.run(summarizer.list_models("groq")) == ["llama-a", "llama-b"]

    summarizer.secrets.set("groq", "bad-key-456")
    with pytest.raises(SummaryError, match="rejected"):
        asyncio.run(summarizer.summarize("x", settings))


def test_settings_are_saved_without_keys(tmp_path):
    _, summarizer = setup(tmp_path, FakeAdapter())
    assert summarizer.settings().provider == "off"
    summarizer.save_settings(SummarySettings(provider="groq", model="llama-a"))
    assert summarizer.settings().model == "llama-a"
    summarizer.secrets.set("groq", "secret-value-1")
    saved = summarizer.db.get_setting("summary")
    assert "secret-value-1" not in json.dumps(saved)          # keys never land in the database


def test_test_button_reports_errors(tmp_path):
    _, summarizer = setup(tmp_path, FakeAdapter())
    result = asyncio.run(summarizer.test(SummarySettings(provider="agent", agent_id="missing")))
    assert result.ok is False and "Pick which agent" in result.error

"""Unit tests for turning Codex's `exec --json` lines into AgentEvents."""

import json

from aihub.agents.base import RunOptions
from aihub.agents.codex import CodexAdapter, CodexParser


def feed(parser: CodexParser, *messages) -> list:
    events = []
    for m in messages:
        events += parser.parse_line(json.dumps(m))
    return events


def test_session_and_message():
    events = feed(
        CodexParser(),
        {"type": "thread.started", "thread_id": "th_1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Done."}},
    )
    assert [e.kind for e in events] == ["session_started", "text"]
    assert events[0].data["session_id"] == "th_1"


def test_command_started_then_completed():
    events = feed(
        CodexParser(),
        {"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "ls", "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "ls",
                                            "aggregated_output": "a.py", "exit_code": 0, "status": "completed"}},
    )
    assert [e.kind for e in events] == ["tool_call", "tool_result"]  # no duplicate tool_call
    assert events[0].data == {"id": "c1", "name": "Command", "input": {"command": "ls"}}
    assert events[1].data["output"] == "a.py" and events[1].data["is_error"] is False


def test_failed_command_is_an_error():
    [_, result] = feed(CodexParser(), {"type": "item.completed", "item": {
        "id": "c2", "type": "command_execution", "command": "pytest", "aggregated_output": "1 failed",
        "exit_code": 1, "status": "failed"}})
    assert result.data["is_error"] is True


def test_file_change_only_completed():
    events = feed(CodexParser(), {"type": "item.completed", "item": {
        "id": "f1", "type": "file_change", "status": "completed",
        "changes": [{"path": "src/app.py", "kind": "update"}, {"path": "new.py", "kind": "add"}]}})
    assert [e.kind for e in events] == ["tool_call", "tool_result"]
    assert events[0].data["input"]["path"] == "src/app.py"
    assert events[1].data["output"] == "update src/app.py\nadd new.py"


def test_turn_completed_uses_last_message_as_summary():
    events = feed(
        CodexParser(),
        {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Added the endpoint."}},
        {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 80}},
    )
    result = events[-1]
    assert result.kind == "result"
    assert result.data["success"] is True and result.data["summary"] == "Added the endpoint."
    assert result.data["input_tokens"] == 1200 and result.data["cost_usd"] is None


def test_turn_failed_and_error():
    events = feed(CodexParser(), {"type": "turn.failed", "error": {"message": "401 Unauthorized"}},
                  {"type": "error", "message": "stream closed"})
    assert [e.kind for e in events] == ["error", "result", "error"]
    assert events[1].data["success"] is False


def test_unknown_items_are_kept_raw():
    events = feed(CodexParser(), {"type": "item.completed", "item": {"id": "r", "type": "reasoning", "text": "hmm"}},
                  {"type": "something.new"})
    assert [e.kind for e in events] == ["raw", "raw"]
    assert CodexParser().parse_line("not json")[0].kind == "raw"


def test_commands():
    adapter = CodexAdapter()
    cmd = adapter.build_command("codex", RunOptions())
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[-1] == "-" and "resume" not in cmd

    resumed = adapter.build_command("codex", RunOptions(resume_session_id="th_1"))
    assert resumed[-3:] == ["resume", "th_1", "-"]

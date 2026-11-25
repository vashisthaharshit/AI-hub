"""Unit tests for turning Claude Code's JSON lines into AgentEvents."""

import json

from aihub.agents.claude_code import parse_line


def line(obj) -> str:
    return json.dumps(obj)


def test_init_becomes_session_started():
    [event] = parse_line(line({"type": "system", "subtype": "init", "session_id": "s1", "model": "m", "cwd": "/x"}))
    assert event.kind == "session_started"
    assert event.data == {"session_id": "s1", "model": "m", "cwd": "/x"}


def test_assistant_text_and_tool_call():
    events = parse_line(line({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Reading the file"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}},
    ]}}))
    assert [e.kind for e in events] == ["text", "tool_call"]
    assert events[1].data["name"] == "Read"


def test_tool_result_list_content_is_flattened():
    [event] = parse_line(line({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "hello"}]},
    ]}}))
    assert event.kind == "tool_result"
    assert event.data == {"tool_use_id": "t1", "output": "hello", "is_error": False}


def test_result_counts_cached_tokens():
    [event] = parse_line(line({
        "type": "result", "subtype": "success", "is_error": False, "result": "Done",
        "total_cost_usd": 0.01, "duration_ms": 1200, "num_turns": 2,
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5},
    }))
    assert event.kind == "result"
    assert event.data["success"] is True
    assert event.data["input_tokens"] == 100
    assert event.data["output_tokens"] == 5


def test_error_result_is_not_success():
    [event] = parse_line(line({"type": "result", "subtype": "error_max_turns", "is_error": True}))
    assert event.data["success"] is False


def test_garbage_and_unknown_are_kept_as_raw():
    assert parse_line("not json")[0].kind == "raw"
    assert parse_line(line({"type": "something_new"}))[0].kind == "raw"

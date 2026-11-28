"""Antigravity CLI adapter: parsing `agy` stream-json and `agy models`.

The fixtures are real output from Antigravity CLI 1.5.19 on Windows
(tests/fixtures/antigravity), with the user name in paths replaced.
"""

import json
from pathlib import Path

from aihub.agents.antigravity import AntigravityAdapter, AntigravityParser, parse_models
from aihub.agents.base import RunOptions

FIXTURES = Path(__file__).parent / "fixtures" / "antigravity"


def parse_file(name: str) -> list:
    parser = AntigravityParser()
    events = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events += parser.parse_line(line)
    return events


def feed(*messages) -> list:
    parser = AntigravityParser()
    events = []
    for m in messages:
        events += parser.parse_line(json.dumps(m))
    return events


# ---------- a real first turn ----------

def test_real_run_kinds_in_order():
    events = parse_file("run1.jsonl")
    assert [e.kind for e in events] == [
        "session_started",
        "tool_call", "tool_result",   # write_file
        "tool_call", "tool_result",   # run_command dir
        "text",
        "result",
    ]
    assert events[0].data["session_id"] == "e7ee1f1a-f47b-4e17-a3da-cd5ff8b2b377"


def test_real_run_tools_are_named_like_other_agents():
    events = parse_file("run1.jsonl")
    edit, _, command, command_result = events[1:5]
    assert edit.data["name"] == "Edit files" and edit.data["input"]["path"].endswith("hello.txt")
    assert command.data == {"id": "step-4", "name": "Command", "input": {"command": "dir"}}
    assert command_result.data["tool_use_id"] == "step-4"
    assert "hello.txt" in command_result.data["output"] and command_result.data["is_error"] is False


def test_text_deltas_are_joined_into_one_message():
    text = next(e for e in parse_file("run1.jsonl") if e.kind == "text")
    assert text.data["text"].startswith("1. Created [hello.txt]")
    assert "listed `hello.txt` (2 bytes)" in text.data["text"]


def test_first_turn_tokens_match_the_cli_total():
    result = parse_file("run1.jsonl")[-1].data
    assert result["success"] is True
    # input 15570 + cache_read 17987 ; output 302 + thinking 103 (the CLI's own result says the same)
    assert result["input_tokens"] == 15570 + 17987
    assert result["output_tokens"] == 302 + 103


# ---------- a real follow-up (--conversation) ----------

def test_follow_up_counts_only_its_own_tokens():
    """result.usage after a follow-up is the whole conversation; we must not count turn 1 twice."""
    events = parse_file("run2.jsonl")
    result = events[-1].data
    cli_total = json.loads((FIXTURES / "run2.jsonl").read_text(encoding="utf-8").splitlines()[-1])["result"]["usage"]
    assert result["input_tokens"] == (10783 + 2294) + (0 + 8734)
    assert result["input_tokens"] < cli_total["input_tokens"] + cli_total["cache_read_tokens"]
    assert result["summary"].startswith("Deleted `")


def test_follow_up_ignores_system_message_steps():
    kinds = [e.kind for e in parse_file("run2.jsonl")]
    assert kinds == ["session_started", "tool_call", "tool_result", "text", "result"]


# ---------- failures ----------

def test_failed_command_is_an_error():
    [_, result] = feed(
        {"event": "step_update", "step_update": {"step_index": 3, "state": "DONE", "step_type": "tool",
                                                  "tool_info": {"name": "run_command", "parameters": {"CommandLine": "pytest"},
                                                                "output": "Command exited with code 1.\n\nCommand output:\nFAILED"}}},
    )
    assert result.data["is_error"] is True


def test_tool_error_object():
    [_, result] = feed(
        {"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool",
                                                  "tool_info": {"name": "view_file", "parameters": {"Path": "x"},
                                                                "error": {"type": "NOT_FOUND", "message": "no such file"}}}},
    )
    assert result.data["is_error"] is True and "no such file" in result.data["output"]


def test_error_status_gives_error_and_failed_result():
    events = feed({"event": "result", "result": {"status": "ERROR", "error": {"message": "authentication required"}}})
    assert [e.kind for e in events] == ["error", "result"]
    assert events[0].data["message"] == "authentication required"
    assert events[1].data["success"] is False


def test_unknown_lines_are_kept_as_raw():
    parser = AntigravityParser()
    assert parser.parse_line("not json")[0].kind == "raw"
    assert parser.parse_line(json.dumps({"event": "something_new"}))[0].kind == "raw"


# ---------- models ----------

def test_models_grouped_by_effort_and_noise_skipped():
    # The fixture also has PowerShell's stderr text mixed in (2>&1); it must be skipped.
    discovery, slugs = parse_models((FIXTURES / "models.txt").read_text(encoding="utf-8"))
    by_id = {m.id: m for m in discovery.models}
    assert list(by_id) == ["gemini-4-hydrogen", "gemini-3.8-flash", "claude-sonnet-4-6",
                           "claude-opus-4-6-thinking", "gpt-oss-120b"]
    hydrogen = by_id["gemini-4-hydrogen"]
    assert hydrogen.label == "Gemini 4 Hydrogen"
    assert hydrogen.efforts == ["low", "medium", "high", "xhigh"] and hydrogen.default_effort == "medium"
    assert by_id["claude-sonnet-4-6"].efforts == [] and by_id["claude-sonnet-4-6"].note
    assert "gemini-3.8-flash-high" in slugs and "agy" not in slugs


def test_no_models_is_a_discovery_error():
    discovery, _ = parse_models("")
    assert discovery.error and not discovery.models


# ---------- command line ----------

def adapter_with_models() -> AntigravityAdapter:
    adapter = AntigravityAdapter()
    _, adapter._slugs = parse_models((FIXTURES / "models.txt").read_text(encoding="utf-8"))
    return adapter


def test_command_for_a_new_run():
    cmd = adapter_with_models().build_command("agy", "fix the bug", RunOptions())
    assert cmd[:3] == ["agy", "-p=fix the bug", "--output-format"]
    assert "--dangerously-skip-permissions" in cmd and "--sandbox" in cmd
    assert "--model" not in cmd and "--conversation" not in cmd


def test_prompt_starting_with_a_dash_stays_a_prompt():
    cmd = AntigravityAdapter().build_command("agy", "-v is broken", RunOptions())
    assert cmd[1] == "-p=-v is broken"


def test_model_and_effort_become_the_real_slug():
    adapter = adapter_with_models()
    cmd = adapter.build_command("agy", "x", RunOptions(model="gemini-3.8-flash", effort="high"))
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-high" and "--effort" not in cmd
    cmd = adapter.build_command("agy", "x", RunOptions(model="gemini-4-hydrogen"))
    assert cmd[cmd.index("--model") + 1] == "gemini-4-hydrogen-medium"
    cmd = adapter.build_command("agy", "x", RunOptions(model="claude-sonnet-4-6"))
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"


def test_follow_up_resumes_the_conversation():
    cmd = AntigravityAdapter().build_command("agy", "now delete it", RunOptions(resume_session_id="c-1"))
    assert cmd[cmd.index("--conversation") + 1] == "c-1"


# ---------- Brain: project memory tools ----------

from aihub.agents.antigravity import MEMORY_SERVER, install_memory_config  # noqa: E402


def test_memory_config_added_then_removed(tmp_path):
    restore = install_memory_config(tmp_path, {"AIHUB_URL": "http://127.0.0.1:8000", "AIHUB_MEMORY_TOKEN": "t"})
    config = json.loads((tmp_path / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
    server = config["mcpServers"][MEMORY_SERVER]
    assert server["args"] == ["-m", "aihub.brain.memory_mcp"] and server["env"]["AIHUB_MEMORY_TOKEN"] == "t"
    restore()
    assert not (tmp_path / ".agents").exists()   # folder put back exactly as it was


def test_memory_config_keeps_the_repos_own_servers(tmp_path):
    folder = tmp_path / ".agents"
    (folder / "skills").mkdir(parents=True)
    original = b'{"mcpServers": {"github": {"command": "gh-mcp"}}}'
    (folder / "mcp_config.json").write_bytes(original)
    restore = install_memory_config(tmp_path, {"AIHUB_MEMORY_TOKEN": "t"})
    servers = json.loads((folder / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"github", MEMORY_SERVER}
    restore()
    assert (folder / "mcp_config.json").read_bytes() == original and (folder / "skills").is_dir()


def test_mcp_tool_calls_are_named_server_dot_tool():
    [call] = feed({"event": "step_update", "step_update": {
        "step_index": 7, "state": "ACTIVE", "step_type": "tool", "tool_name": "call_mcp_tool",
        "tool_info": {"name": "call_mcp_tool", "parameters": {
            "ServerName": "aihub_memory", "ToolName": "find_code", "Arguments": '{"name": "parse_models"}'}}}})
    assert call.data["name"] == "aihub_memory.find_code" and call.data["input"] == {"name": "parse_models"}

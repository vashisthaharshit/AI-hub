"""Tests the real ClaudeCodeAdapter against a fake `claude` program.

The fake prints the same kind of JSON lines Claude Code does, so we test
process handling (stdin, streaming, exit codes) without spending tokens.
Those tests are skipped on Windows, where a script can't stand in for claude.exe.
The command-building tests at the bottom run everywhere.
"""

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from aihub.agents.base import RunOptions
from aihub.agents.claude_code import ClaudeCodeAdapter

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX script as fake claude")

FAKE_CLAUDE = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, sys
    if "--version" in sys.argv:
        print("9.9.9 (Claude Code)"); sys.exit(0)
    prompt = sys.stdin.read()
    if "FAIL_AUTH" in prompt:
        print("Invalid API key. Please run /login", file=sys.stderr); sys.exit(1)
    for msg in [
        {{"type": "system", "subtype": "init", "session_id": "s", "model": "fake"}},
        {{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "got: " + prompt}}]}}}},
        {{"type": "result", "subtype": "success", "is_error": False, "result": "OK",
          "total_cost_usd": 0.001, "usage": {{"input_tokens": 3, "output_tokens": 1}}}},
    ]:
        print(json.dumps(msg), flush=True)
""")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return exe


@posix_only
def test_detect_and_run(fake_claude, tmp_path):
    adapter = ClaudeCodeAdapter()

    status = asyncio.run(adapter.detect())
    assert status.state == "installed" and status.version == "9.9.9 (Claude Code)"

    async def collect():
        return [e async for e in adapter.run("hello", tmp_path)]

    events = asyncio.run(collect())
    assert [e.kind for e in events] == ["session_started", "text", "result"]
    assert events[1].data["text"] == "got: hello"  # prompt arrived via stdin

    assert asyncio.run(adapter.test_connection()).state == "ready"


@posix_only
def test_auth_failure_detected(fake_claude, monkeypatch):
    adapter = ClaudeCodeAdapter()
    original = adapter.run

    def failing_run(prompt, workdir, options=None):
        return original("FAIL_AUTH", workdir, options)

    monkeypatch.setattr(adapter, "run", failing_run)
    status = asyncio.run(adapter.test_connection())
    assert status.state == "not_signed_in"


@posix_only
def test_not_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert asyncio.run(ClaudeCodeAdapter().detect()).state == "not_installed"


# ---------- command building: works on every OS ----------

def test_command_full_access():
    cmd = ClaudeCodeAdapter().build_command("claude", RunOptions())
    assert cmd[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert "--resume" not in cmd


def test_command_supervised_and_resume(tmp_path):
    cfg = tmp_path / "mcp.json"
    cmd = ClaudeCodeAdapter().build_command("claude", RunOptions(resume_session_id="abc"), mcp_config=cfg)
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__aihub__approve"
    assert cmd[cmd.index("--mcp-config") + 1] == str(cfg)
    assert cmd[cmd.index("--resume") + 1] == "abc"


def test_mcp_config_points_at_our_bridge(tmp_path, monkeypatch):
    import json

    from aihub import config
    from aihub.agents.claude_code import _write_mcp_config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = _write_mcp_config({"AIHUB_RUN_ID": "r1"})
    server = json.loads(path.read_text())["mcpServers"]["aihub"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "aihub.approval_mcp"]
    assert server["env"] == {"AIHUB_RUN_ID": "r1"}


def test_command_model():
    cmd = ClaudeCodeAdapter().build_command("claude", RunOptions(resume_session_id="abc", model="sonnet"))
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert "--model" not in ClaudeCodeAdapter().build_command("claude", RunOptions())
    assert [m.id for m in ClaudeCodeAdapter().models] == ["opus", "sonnet", "haiku"]

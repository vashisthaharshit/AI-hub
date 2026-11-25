"""Adapter for Claude Code, driven through its headless mode.

How it works:
    claude -p --output-format stream-json --verbose
starts Claude Code without its interactive screen. We send the prompt on
stdin and it prints one JSON object per line on stdout while it works
("JSON Lines"). We turn each line into our own AgentEvent.

Follow-ups:  --resume <session_id> continues the same conversation.

Supervised mode: Claude Code can hand every permission decision to an MCP
tool (--permission-prompt-tool). We give it a tiny MCP server
(aihub/approval_mcp.py) that forwards each request to AI-Hub and waits
for the user to click Allow or Deny. Note: this flag is not in Claude Code's
main docs; if it ever changes, the fallback is the official Claude Agent SDK
and its `can_use_tool` callback, which does the same job.
"""

import json
import shutil
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aihub import config
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.process import ProcessFailed, run_quick, status_from_test, stream_lines
from aihub.models import AgentEvent, AgentStatus, RunResult

MAX_TOOL_OUTPUT_CHARS = 20_000
APPROVAL_TOOL = "mcp__aihub__approve"  # mcp__<server name>__<tool name>
# How long Claude waits for an MCP tool (ms). The user may take a while to decide.
APPROVAL_TIMEOUT_MS = str(30 * 60 * 1000)


class ClaudeCodeAdapter(AgentAdapter):
    id = "claude-code"
    name = "Claude Code"
    binary = "claude"
    supports_supervised = True
    supports_follow_up = True

    def _find(self) -> str | None:
        # shutil.which searches PATH like a terminal does (and handles .exe/.cmd on Windows).
        return shutil.which(self.binary)

    # ---------- detection ----------

    async def detect(self) -> AgentStatus:
        path = self._find()
        if not path:
            return self.status("not_installed", help="Install Claude Code, then sign in with: claude auth login")
        try:
            version = await run_quick([path, "--version"], timeout=20)
        except Exception as exc:  # noqa: BLE001 - we report any failure to the UI
            return self.status("error", path=path, help=f"Found Claude Code but could not run it: {exc}")
        return self.status(
            "installed", path=path, version=version.strip() or None,
            help="Found. Use 'Test connection' to check that you are signed in.",
        )

    async def test_connection(self) -> AgentStatus:
        status = await self.detect()
        if status.state != "installed":
            return status
        # Run in an empty temp folder so the test can't touch any real files.
        with tempfile.TemporaryDirectory() as tmp:
            events = [e async for e in self.run("Reply with just the word OK.", Path(tmp), RunOptions(max_turns=1))]
        return status_from_test(status, events, login_hint="claude auth login")

    # ---------- running ----------

    def build_command(self, path: str, options: RunOptions, mcp_config: Path | None = None) -> list[str]:
        cmd = [path, "-p", "--output-format", "stream-json", "--verbose"]
        if mcp_config:
            # Supervised: normal permission rules; anything that needs a
            # permission prompt is sent to our approval tool instead.
            cmd += [
                "--permission-mode", "default",
                "--mcp-config", str(mcp_config),
                "--permission-prompt-tool", APPROVAL_TOOL,
            ]
        else:
            # Full access inside the worktree: file edits are allowed without asking.
            cmd += ["--permission-mode", "acceptEdits"]
        if options.resume_session_id:
            cmd += ["--resume", options.resume_session_id]
        if options.max_turns:
            cmd += ["--max-turns", str(options.max_turns)]
        return cmd

    async def run(
        self, prompt: str, workdir: Path, options: RunOptions | None = None
    ) -> AsyncIterator[AgentEvent]:
        options = options or RunOptions()
        path = self._find()
        if not path:
            yield AgentEvent(kind="error", data={"message": "Claude Code is not installed."})
            return

        mcp_config = _write_mcp_config(options.approval_env) if options.approval_env else None
        extra_env = {"MCP_TOOL_TIMEOUT": APPROVAL_TIMEOUT_MS} if mcp_config else None
        try:
            async for line in stream_lines(self.build_command(path, options, mcp_config), workdir, prompt, extra_env):
                for event in parse_line(line):
                    yield event
        except ProcessFailed as exc:
            yield AgentEvent(kind="error", data={"message": str(exc), "exit_code": exc.exit_code})
        finally:
            if mcp_config:
                mcp_config.unlink(missing_ok=True)


def _write_mcp_config(approval_env: dict[str, str]) -> Path:
    """Tell Claude Code how to start our approval MCP server.

    Written to a file (not passed inline as JSON) because quotes and braces
    in command-line arguments get mangled on Windows.
    """
    folder = config.DATA_DIR / "mcp"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps({
        "mcpServers": {
            "aihub": {
                # The same Python that runs AI-Hub, so the aihub package is importable.
                "command": sys.executable,
                "args": ["-m", "aihub.approval_mcp"],
                "env": approval_env,
            }
        }
    }), encoding="utf-8")
    return path


# ---------- parsing (pure functions: easy to unit-test) ----------

def parse_line(line: str) -> list[AgentEvent]:
    """Turn one line of Claude Code's stream-json output into AgentEvents."""
    try:
        msg: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        return [AgentEvent(kind="raw", data={"line": line})]

    msg_type = msg.get("type")

    if msg_type == "system" and msg.get("subtype") == "init":
        return [AgentEvent(kind="session_started", data={
            "session_id": msg.get("session_id"),
            "model": msg.get("model"),
            "cwd": msg.get("cwd"),
        })]

    if msg_type == "assistant":
        events = []
        for block in _content_blocks(msg):
            if block.get("type") == "text" and block.get("text"):
                events.append(AgentEvent(kind="text", data={"text": block["text"]}))
            elif block.get("type") == "tool_use":
                events.append(AgentEvent(kind="tool_call", data={
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input", {}),
                }))
        return events

    if msg_type == "user":
        # Tool results come back to the model as a "user" message.
        events = []
        for block in _content_blocks(msg):
            if block.get("type") == "tool_result":
                events.append(AgentEvent(kind="tool_result", data={
                    "tool_use_id": block.get("tool_use_id"),
                    "output": _stringify(block.get("content"))[:MAX_TOOL_OUTPUT_CHARS],
                    "is_error": bool(block.get("is_error")),
                }))
        return events

    if msg_type == "result":
        usage = msg.get("usage") or {}
        # Cached prompt tokens are still input tokens; count all of them.
        input_tokens = sum(
            usage.get(k) or 0
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        result = RunResult(
            success=msg.get("subtype") == "success" and not msg.get("is_error"),
            summary=msg.get("result"),
            cost_usd=msg.get("total_cost_usd"),
            duration_ms=msg.get("duration_ms"),
            input_tokens=input_tokens if usage else None,
            output_tokens=usage.get("output_tokens"),
            num_turns=msg.get("num_turns"),
        )
        return [AgentEvent(kind="result", data=result.model_dump())]

    # Unknown message types are kept as "raw" so no information is lost.
    return [AgentEvent(kind="raw", data=msg)]


def _content_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
        return "\n".join(p for p in parts if p)
    return json.dumps(content)


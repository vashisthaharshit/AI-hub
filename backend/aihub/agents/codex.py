"""Adapter for OpenAI Codex CLI, driven through `codex exec --json`.

How it works:
    codex exec --json --sandbox workspace-write -
runs Codex without its interactive screen; `-` means "read the prompt from
stdin". stdout becomes JSON Lines with events like:
    thread.started   -> the session id (used for follow-ups)
    item.started / item.completed  -> messages, commands, file edits...
    turn.completed   -> token usage
    turn.failed / error

Differences from Claude Code that the adapter hides from the rest of AI-Hub:
- Codex sends whole items (start + completed), not content blocks.
- It reports tokens but not cost, so cost_usd stays None.
- `codex exec` can't pause to ask for approval, so supervised mode isn't
  supported. Safety comes from its sandbox instead: `workspace-write` lets
  it change files only inside the worktree.

Follow-ups:  codex exec ... resume <session_id> -
"""

import json
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.process import ProcessFailed, run_quick, status_from_test, stream_lines
from aihub.models import AgentEvent, AgentStatus, RunResult

MAX_TOOL_OUTPUT_CHARS = 20_000


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "Codex"
    binary = "codex"
    supports_supervised = False
    supports_follow_up = True

    def _find(self) -> str | None:
        return shutil.which(self.binary)

    async def detect(self) -> AgentStatus:
        path = self._find()
        if not path:
            return self.status("not_installed", help="Install Codex CLI, then sign in with: codex login")
        try:
            version = await run_quick([path, "--version"], timeout=20)
        except Exception as exc:  # noqa: BLE001
            return self.status("error", path=path, help=f"Found Codex but could not run it: {exc}")
        return self.status(
            "installed", path=path, version=version.strip() or None,
            help="Found. Use 'Test connection' to check that you are signed in.",
        )

    async def test_connection(self) -> AgentStatus:
        status = await self.detect()
        if status.state != "installed":
            return status
        with tempfile.TemporaryDirectory() as tmp:
            events = [e async for e in self.run("Reply with just the word OK.", Path(tmp))]
        return status_from_test(status, events, login_hint="codex login")

    def build_command(self, path: str, options: RunOptions) -> list[str]:
        cmd = [
            path, "exec",
            "--json",
            "--sandbox", "workspace-write",  # may edit files only inside the working folder
            "--skip-git-repo-check",         # our test-connection folder isn't a git repo
        ]
        if options.resume_session_id:
            cmd += ["resume", options.resume_session_id]
        cmd.append("-")  # read the prompt from stdin
        return cmd

    async def run(
        self, prompt: str, workdir: Path, options: RunOptions | None = None
    ) -> AsyncIterator[AgentEvent]:
        options = options or RunOptions()
        path = self._find()
        if not path:
            yield AgentEvent(kind="error", data={"message": "Codex is not installed."})
            return

        parser = CodexParser()
        start = time.monotonic()
        try:
            async for line in stream_lines(self.build_command(path, options), workdir, prompt):
                for event in parser.parse_line(line):
                    if event.kind == "result":
                        # Codex doesn't report duration; we measure it ourselves.
                        event.data["duration_ms"] = int((time.monotonic() - start) * 1000)
                    yield event
        except ProcessFailed as exc:
            yield AgentEvent(kind="error", data={"message": str(exc), "exit_code": exc.exit_code})


class CodexParser:
    """Turns Codex JSON lines into AgentEvents.

    A class, not a plain function, because it needs a little memory:
    - which items already got a tool_call (so item.completed only adds the result)
    - the last agent message (used as the turn's summary)
    """

    def __init__(self):
        self._started: set[str] = set()
        self._last_message: str | None = None

    def parse_line(self, line: str) -> list[AgentEvent]:
        try:
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(kind="raw", data={"line": line})]

        msg_type = msg.get("type")

        if msg_type in ("thread.started", "thread.resumed"):
            return [AgentEvent(kind="session_started", data={"session_id": msg.get("thread_id"), "model": None})]

        if msg_type in ("turn.started", "item.updated"):
            return []  # lifecycle only, nothing to show

        if msg_type == "item.started":
            return self._item_started(msg.get("item") or {})

        if msg_type == "item.completed":
            return self._item_completed(msg.get("item") or {})

        if msg_type == "turn.completed":
            usage = msg.get("usage") or {}
            result = RunResult(
                success=True,
                summary=self._last_message,
                input_tokens=usage.get("input_tokens"),  # already includes cached tokens
                output_tokens=usage.get("output_tokens"),
            )
            return [AgentEvent(kind="result", data=result.model_dump())]

        if msg_type == "turn.failed":
            error = (msg.get("error") or {}).get("message") or "Turn failed"
            return [
                AgentEvent(kind="error", data={"message": error}),
                AgentEvent(kind="result", data=RunResult(success=False, summary=error).model_dump()),
            ]

        if msg_type == "error":
            return [AgentEvent(kind="error", data={"message": msg.get("message") or "Codex error"})]

        return [AgentEvent(kind="raw", data=msg)]

    # ---------- items ----------

    def _item_started(self, item: dict[str, Any]) -> list[AgentEvent]:
        call = _tool_call(item)
        if call is None:
            return []
        self._started.add(str(item.get("id")))
        return [call]

    def _item_completed(self, item: dict[str, Any]) -> list[AgentEvent]:
        item_type = item.get("type")

        if item_type in ("agent_message", "assistant_message"):
            text = item.get("text") or ""
            self._last_message = text or self._last_message
            return [AgentEvent(kind="text", data={"text": text})] if text else []

        if item_type == "error":
            return [AgentEvent(kind="error", data={"message": item.get("message") or "Codex error"})]

        call = _tool_call(item)
        if call is None:
            return [AgentEvent(kind="raw", data=item)]  # reasoning, todo_list, future types

        events = []
        if str(item.get("id")) not in self._started:
            events.append(call)  # some items only arrive as "completed"
        events.append(_tool_result(item))
        return events


def _tool_call(item: dict[str, Any]) -> AgentEvent | None:
    """Map a Codex tool-like item to our tool_call event (None if it isn't a tool)."""
    item_type = item.get("type")
    item_id = item.get("id")
    if item_type == "command_execution":
        name, tool_input = "Command", {"command": item.get("command", "")}
    elif item_type == "file_change":
        changes = item.get("changes") or []
        first = changes[0].get("path") if changes else ""
        name, tool_input = "Edit files", {"path": first, "changes": changes}
    elif item_type == "mcp_tool_call":
        name = f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}"
        tool_input = item.get("arguments") or {}
    elif item_type == "web_search":
        name, tool_input = "Web search", {"query": item.get("query", "")}
    else:
        return None
    return AgentEvent(kind="tool_call", data={"id": item_id, "name": name, "input": tool_input})


def _tool_result(item: dict[str, Any]) -> AgentEvent:
    item_type = item.get("type")
    status = item.get("status")
    if item_type == "command_execution":
        output = item.get("aggregated_output") or ""
        is_error = status == "failed" or (item.get("exit_code") not in (None, 0))
    elif item_type == "file_change":
        output = "\n".join(f"{c.get('kind', 'change')} {c.get('path', '')}" for c in item.get("changes") or [])
        is_error = status == "failed"
    else:
        result = item.get("result") or item.get("error") or ""
        output = result if isinstance(result, str) else json.dumps(result)
        is_error = status == "failed" or bool(item.get("error"))
    return AgentEvent(kind="tool_result", data={
        "tool_use_id": item.get("id"),
        "output": str(output)[:MAX_TOOL_OUTPUT_CHARS],
        "is_error": is_error,
    })

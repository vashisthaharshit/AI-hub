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
import sys
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.process import (
    JsonLineProcess, ProcessFailed, epoch_to_iso, run_quick, status_from_test, stream_lines,
)
from aihub.models import AccountInfo, AgentEvent, AgentStatus, Discovery, ModelOption, RunResult, UsageWindow

MAX_TOOL_OUTPUT_CHARS = 20_000


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "Codex"
    binary = "codex"
    supports_supervised = False
    supports_follow_up = True
    # No fallback list: Codex model names change often. discover() asks the
    # CLI; if that fails, only "Default model" is offered.
    models: list[ModelOption] = []

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

    async def discover(self) -> Discovery:
        path = self._find()
        if not path:
            return Discovery(models=self.models, error="Codex is not installed.")
        try:
            return await _discover(path)
        except Exception as exc:  # noqa: BLE001 - fall back to "Default model" only
            return Discovery(models=self.models, error=f"Couldn't read models from Codex: {exc}")

    def build_command(self, path: str, options: RunOptions) -> list[str]:
        cmd = [
            path, "exec",
            "--json",
            "--sandbox", "workspace-write",  # may edit files only inside the working folder
            "--skip-git-repo-check",         # our test-connection folder isn't a git repo
        ]
        if options.model:
            cmd += ["--model", options.model]
        if options.effort:
            # Config override: same as model_reasoning_effort in ~/.codex/config.toml
            cmd += ["-c", f"model_reasoning_effort={options.effort}"]
        if options.memory_env:
            cmd += memory_server_args(options.memory_env)
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
            async for line in stream_lines(self.build_command(path, options), workdir, prompt, options.extra_env):
                for event in parser.parse_line(line):
                    if event.kind == "result":
                        # Codex doesn't report duration; we measure it ourselves.
                        event.data["duration_ms"] = int((time.monotonic() - start) * 1000)
                    yield event
        except ProcessFailed as exc:
            yield AgentEvent(kind="error", data={"message": str(exc), "exit_code": exc.exit_code})


def memory_server_args(env: dict[str, str]) -> list[str]:
    """Brain steps: add AI-Hub's project memory tools (brain/memory_mcp.py) as an
    MCP server, with -c overrides (same as [mcp_servers.aihub_memory] in
    ~/.codex/config.toml). Values are TOML; '...' strings need no escaping,
    which matters for Windows paths."""
    def literal(text: str) -> str:
        return "'" + text.replace("'", "") + "'"
    table = ",".join(f"{key}={literal(value)}" for key, value in env.items())
    return [
        "-c", f"mcp_servers.aihub_memory.command={literal(sys.executable)}",
        "-c", 'mcp_servers.aihub_memory.args=["-m","aihub.brain.memory_mcp"]',
        "-c", f"mcp_servers.aihub_memory.env={{{table}}}",
        # Without this, `codex exec` can't ask and blocks the tools ("approval policy").
        "-c", "mcp_servers.aihub_memory.default_tools_approval_mode='approve'",
    ]


# ---------- discovery: models + account (codex app-server) ----------

async def _discover(path: str) -> Discovery:
    """`codex app-server` speaks JSON-RPC over stdin/stdout. Its documented
    methods give us the models (model/list), the account (account/read) and
    usage limits + credits (account/rateLimits/read). No tokens are used."""
    async with JsonLineProcess([path, "app-server"]) as p:
        next_id = 0

        async def call(method: str, params: dict | None = None) -> dict:
            nonlocal next_id
            next_id += 1
            request_id = next_id
            await p.send({"id": request_id, "method": method, "params": params or {}})
            reply = await p.wait_for(lambda m: m.get("id") == request_id)
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            return reply.get("result") or {}

        await call("initialize", {"clientInfo": {"name": "aihub", "title": "AI-Hub", "version": "0.2.0"}})
        await p.send({"method": "initialized"})

        raw_models: list[dict] = []
        cursor = None
        for _ in range(10):  # follow pagination, with a safety limit
            page = await call("model/list", {"cursor": cursor} if cursor else {})
            raw_models += page.get("data") or []
            cursor = page.get("nextCursor")
            if not cursor:
                break

        account = rate_limits = {}
        try:
            account = await call("account/read")
        except Exception:  # noqa: BLE001 - optional extras: models still work without them
            pass
        try:
            rate_limits = await call("account/rateLimits/read")
        except Exception:  # noqa: BLE001
            pass
    return parse_discovery(raw_models, account, rate_limits)


def parse_discovery(raw_models: list[dict], account: dict, rate_limits: dict) -> Discovery:
    models, default_label, default_model = [], None, None
    for m in raw_models:
        if not isinstance(m, dict) or m.get("hidden"):
            continue
        model_id = m.get("model") or m.get("id")
        if not model_id:
            continue
        label = m.get("displayName") or model_id
        if m.get("isDefault"):
            default_label = f"Default · {label}"
        note = None
        migration = (m.get("upgradeInfo") or {}).get("migrationMarkdown")
        if migration:
            note = migration.strip().splitlines()[0]  # e.g. "GPT-5.6 Sol retires on December 23, 2026."
        option = ModelOption(
            id=model_id,
            label=label,
            description=m.get("description") or None,
            efforts=[e.get("reasoningEffort") if isinstance(e, dict) else str(e)
                     for e in m.get("supportedReasoningEfforts") or []],
            default_effort=m.get("defaultReasoningEffort"),
            note=note,
        )
        models.append(option)
        if m.get("isDefault"):
            default_model = option

    info = AccountInfo(plan=((account.get("account") or {}).get("planType")))
    limits = rate_limits.get("rateLimits") or {}
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if isinstance(window, dict) and window.get("usedPercent") is not None:
            info.usage.append(UsageWindow(
                label=_window_label(window.get("windowDurationMins")),
                used_percent=float(window["usedPercent"]),
                resets_at=epoch_to_iso(window.get("resetsAt")),
            ))
    credits = limits.get("credits")
    if isinstance(credits, dict):
        info.has_credits = credits.get("hasCredits")
        info.unlimited_credits = credits.get("unlimited")
        info.credits_balance = credits.get("balance")
    return Discovery(
        models=models, default_label=default_label, account=info,
        default_efforts=default_model.efforts if default_model else [],
        default_effort=default_model.default_effort if default_model else None,
    )


def _window_label(minutes) -> str:
    if minutes == 300:
        return "5-hour limit"
    if minutes == 10080:
        return "Weekly limit"
    if isinstance(minutes, (int, float)) and minutes:
        hours = minutes / 60
        return f"{hours:g}-hour limit" if hours < 48 else f"{hours / 24:g}-day limit"
    return "Usage limit"




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

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aihub import config
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.process import (
    JsonLineProcess, ProcessFailed, epoch_to_iso, run_quick, status_from_test, stream_lines,
)
from aihub.models import AccountInfo, AgentEvent, AgentStatus, Discovery, ModelOption, RunResult, UsageWindow

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
    # Fallback only, if discover() fails. Aliases: Claude Code maps each to
    # the latest model of that family.
    models = [
        ModelOption(id="opus", label="Opus"),
        ModelOption(id="sonnet", label="Sonnet"),
        ModelOption(id="haiku", label="Haiku"),
    ]

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

    # ---------- discovery: models + account ----------

    async def discover(self) -> Discovery:
        """Ask Claude Code which models this account has.

        Claude Code started with `--input-format stream-json` accepts control
        requests on stdin. The official Agent SDK sends `initialize` first;
        the reply lists the account's models with display names and effort
        levels, plus the subscription type. No prompt is sent: no tokens used.
        """
        path = self._find()
        if not path:
            return Discovery(models=self.models, error="Claude Code is not installed.")
        cmd = [path, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        try:
            async with JsonLineProcess(cmd) as p:
                await p.send({"type": "control_request", "request_id": "aihub-init",
                              "request": {"subtype": "initialize"}})
                reply = await p.wait_for(
                    lambda m: m.get("type") == "control_response"
                    and (m.get("response") or {}).get("request_id") == "aihub-init"
                )
        except Exception as exc:  # noqa: BLE001 - fall back to the built-in list
            return Discovery(models=self.models, error=f"Couldn't read models from Claude Code: {exc}")
        return parse_initialize((reply.get("response") or {}).get("response") or {}, self.models)

    # ---------- running ----------

    def build_command(self, path: str, options: RunOptions, mcp_config: Path | None = None) -> list[str]:
        cmd = [path, "-p", "--output-format", "stream-json", "--verbose"]
        if mcp_config:
            # Supervised: normal permission rules; anything that needs a
            # permission prompt is sent to our approval tool instead.
            # App runs use acceptEdits: editing files in the app's folder is
            # its normal work; commands, web access and MCP tools still ask.
            cmd += [
                "--permission-mode", "acceptEdits" if options.accept_edits else "default",
                "--mcp-config", str(mcp_config),
                "--permission-prompt-tool", APPROVAL_TOOL,
            ]
        else:
            # Full access inside the worktree: file edits are allowed without asking.
            cmd += ["--permission-mode", "acceptEdits"]
        if options.resume_session_id:
            cmd += ["--resume", options.resume_session_id]
        if options.model:
            # Always explicit: a resumed session otherwise keeps its old model.
            cmd += ["--model", options.model]
        if options.effort:
            cmd += ["--effort", options.effort]
        if options.max_turns:
            cmd += ["--max-turns", str(options.max_turns)]
        for folder in options.plugin_dirs or []:
            # Load a plugin for this run only (nothing installed). Its skills
            # become /<plugin>:<skill>, its .mcp.json servers start with it.
            cmd += ["--plugin-dir", folder]
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
        extra_env = {**(options.extra_env or {}), **({"MCP_TOOL_TIMEOUT": APPROVAL_TIMEOUT_MS} if mcp_config else {})}
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

def parse_initialize(response: dict[str, Any], fallback: list[ModelOption]) -> Discovery:
    """Turn Claude Code's `initialize` reply into a Discovery."""
    models: list[ModelOption] = []
    default_label = None
    default_efforts: list[str] = []
    for m in response.get("models") or []:
        if not isinstance(m, dict) or not m.get("value"):
            continue
        if m["value"] == "default":
            # "Default (recommended)" is not a model of its own: it's what
            # running without --model does. Show it as the label of our
            # "default" choice instead of a separate entry.
            desc = m.get("description") or ""
            default_label = f"{m.get('displayName') or 'Default'}" + (f" · {desc}" if desc else "")
            default_efforts = list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort", True) else []
            continue
        models.append(ModelOption(
            id=m["value"],
            label=m.get("displayName") or m["value"],
            description=m.get("description") or None,
            efforts=list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort", True) else [],
        ))
    account = response.get("account") or {}
    return Discovery(
        models=models or fallback,
        default_label=default_label,
        default_efforts=default_efforts,
        account=AccountInfo(
            plan=account.get("subscriptionType") or None,
            # Claude Code reports these during runs (rate_limit_event), one
            # window at a time. Until then they show as "not reported yet".
            usage=[UsageWindow(label="5-hour limit"), UsageWindow(label="Weekly limit")],
        ) if account else None,
        error=None if models else "Claude Code didn't report any models; using the built-in list.",
    )


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

    if msg_type == "rate_limit_event":
        window = parse_rate_limit(msg.get("rate_limit_info") or {})
        return [AgentEvent(kind="usage", data=window.model_dump())] if window else []

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


_WINDOW_LABELS = {
    "five_hour": "5-hour limit",
    "seven_day": "Weekly limit",
    "seven_day_opus": "Weekly limit (Opus)",
    "seven_day_sonnet": "Weekly limit (Sonnet)",
    "overage": "Extra usage",
}
_STATUS = {"allowed": "ok", "allowed_warning": "warning", "rejected": "limit reached"}


def parse_rate_limit(info: dict[str, Any]) -> UsageWindow | None:
    """Claude Code sends a rate_limit_event during each run. It always says
    which window and when it resets; the % used is only included when you're
    near or at the limit."""
    kind = info.get("rateLimitType")
    if not kind:
        return None
    utilization = info.get("utilization")
    percent = None
    if isinstance(utilization, (int, float)):
        percent = utilization * 100 if utilization <= 1 else float(utilization)  # 0-1 fraction -> %
    return UsageWindow(
        label=_WINDOW_LABELS.get(kind, kind.replace("_", " ")),
        used_percent=percent,
        status=_STATUS.get(info.get("status"), info.get("status")),
        resets_at=epoch_to_iso(info.get("resetsAt")),
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


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


"""Adapter for Google's Antigravity CLI (`agy`), driven through print mode.

How it works:
    agy -p=<prompt> --output-format stream-json --dangerously-skip-permissions --sandbox
runs one turn without the interactive screen. stdout becomes JSON Lines,
each with an "event" field:
    init         -> conversation id (used for follow-ups), tools, permission mode
    step_update  -> one step of the turn, sent as ACTIVE (started / streaming)
                    and DONE. step_type is user_input, agent_response, tool,
                    checkpoint or system_message.
    result       -> status (SUCCESS, ERROR, ...), final response, token usage

Things the adapter hides from the rest of AI-Hub (all seen in a real probe):
- The prompt goes in as `-p=<prompt>` (the `=` form, so a prompt starting
  with "-" is never read as a flag). Windows caps a command line at about
  32k characters, so a very long prompt is written to a temp file and the
  agent is told to read it.
- Agent text arrives as small deltas; we join them and send one "text" event
  per message, like Codex.
- `result.usage` and `result.duration_seconds` add up the WHOLE conversation,
  so after a follow-up they include earlier turns. We add up this turn's own
  steps instead, and measure the time ourselves.
- Headless mode can't stop to ask for approval: an action it can't approve is
  silently skipped and the run still "succeeds". So we approve everything
  (--dangerously-skip-permissions) and rely on the worktree per run plus
  `--sandbox` (limits its terminal), the same idea as Codex's workspace-write.
  Supervised mode isn't supported.
- `agy models` lists one slug per model AND effort ("gemini-3.8-flash-high"),
  so discovery groups them into one model with effort levels, and
  build_command turns model + effort back into the real slug.

Follow-ups:  agy -p=<prompt> --conversation <id> ...

Brain steps (project memory tools): agy has no flag to add an MCP server for
one run, but it reads a workspace config at <folder>/.agents/mcp_config.json.
Every step runs in its own worktree, so for the length of the turn we add
AI-Hub's memory server to that file, then put the folder back exactly as it
was (delete the file, or restore the repo's own version) before AI-Hub
measures the changes. Tool permissions are covered by
--dangerously-skip-permissions.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.process import ProcessFailed, run_quick, status_from_test, stream_lines
from aihub.models import AccountInfo, AgentEvent, AgentStatus, Discovery, ModelOption, RunResult

MAX_TOOL_OUTPUT_CHARS = 20_000
# Windows' limit is 32,767 characters for the whole command line; keep room for the flags.
MAX_PROMPT_ARG_CHARS = 20_000
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
MEMORY_SERVER = "aihub_memory"   # same server name as for Claude Code and Codex
MCP_CONFIG = Path(".agents") / "mcp_config.json"
LOGIN_HINT = "agy   (run it once in a terminal to sign in with Google)"

# Tool names -> what the activity view shows, plus where to find the main argument.
COMMAND_TOOLS = {"run_command"}
EDIT_TOOLS = {"write_file", "write_to_file", "replace_file_content", "multi_replace_file_content",
              "sed_file", "notebook_edit"}


class AntigravityAdapter(AgentAdapter):
    id = "antigravity"
    name = "Antigravity"
    binary = "agy"
    supports_supervised = False
    supports_follow_up = True
    # No fallback list: model names change often and depend on the plan.
    models: list[ModelOption] = []

    def __init__(self):
        # Real slugs from the last discovery, e.g. {"gemini-3.8-flash-high", "claude-sonnet-4-6"}
        self._slugs: set[str] = set()

    def _find(self) -> str | None:
        return shutil.which(self.binary)

    async def detect(self) -> AgentStatus:
        path = self._find()
        if not path:
            return self.status("not_installed", help=(
                "Install Antigravity CLI (Windows PowerShell: irm https://antigravity.google/cli/install.ps1 | iex), "
                "then run agy once to sign in."))
        try:
            version = await run_quick([path, "--version"], timeout=20)
        except Exception as exc:  # noqa: BLE001
            return self.status("error", path=path, help=f"Found Antigravity but could not run it: {exc}")
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
        return status_from_test(status, events, login_hint=LOGIN_HINT)

    async def discover(self) -> Discovery:
        path = self._find()
        if not path:
            return Discovery(models=self.models, error="Antigravity is not installed.")
        try:
            # "Fetching available models..." goes to stderr; the list is on stdout.
            out = await run_quick([path, "models"], timeout=60)
        except Exception as exc:  # noqa: BLE001 - fall back to "Default model" only
            return Discovery(models=self.models, error=f"Couldn't read models from Antigravity: {exc}")
        discovery, slugs = parse_models(out)
        self._slugs = slugs
        return discovery

    def model_slug(self, model: str, effort: str | None) -> tuple[str, str | None]:
        """(slug for --model, effort for --effort or None).

        The listed slugs already contain the effort, so prefer "<model>-<effort>".
        If we don't know that slug (no discovery yet, or a model without
        effort variants), pass the model as-is and --effort separately.
        """
        if effort and f"{model}-{effort}" in self._slugs:
            return f"{model}-{effort}", None
        if model not in self._slugs:
            # A family name without an effort: pick its middle variant so the slug is real.
            for e in ("medium", "high", "low", "xhigh", "max"):
                if f"{model}-{e}" in self._slugs:
                    return f"{model}-{e}", None
        return model, effort

    def build_command(self, path: str, prompt: str, options: RunOptions) -> list[str]:
        cmd = [
            path,
            f"-p={prompt}",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",  # headless can't ask; see the module docstring
            "--sandbox",                       # limits what its terminal commands may do
        ]
        if options.model:
            slug, effort = self.model_slug(options.model, options.effort)
            cmd += ["--model", slug]
            if effort:
                cmd += ["--effort", effort]
        elif options.effort:
            cmd += ["--effort", options.effort]
        if options.resume_session_id:
            cmd += ["--conversation", options.resume_session_id]
        # Brain steps: memory tools come from .agents/mcp_config.json (see install_memory_config).
        return cmd

    async def run(
        self, prompt: str, workdir: Path, options: RunOptions | None = None
    ) -> AsyncIterator[AgentEvent]:
        options = options or RunOptions()
        path = self._find()
        if not path:
            yield AgentEvent(kind="error", data={"message": "Antigravity is not installed."})
            return

        prompt_file = None
        if len(prompt) > MAX_PROMPT_ARG_CHARS:
            fd, prompt_file = tempfile.mkstemp(prefix="aihub-prompt-", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt = (f"Your full task is in the file {prompt_file} (it was too long to pass directly). "
                      "Read the whole file first, then do exactly what it says.")

        parser = AntigravityParser()
        start = time.monotonic()
        restore = install_memory_config(workdir, options.memory_env) if options.memory_env else None
        try:
            # The prompt is in the command, so stdin just gets closed right away.
            async for line in stream_lines(self.build_command(path, prompt, options), workdir, "", options.extra_env):
                for event in parser.parse_line(line):
                    if event.kind == "result":
                        event.data["duration_ms"] = int((time.monotonic() - start) * 1000)
                    yield event
        except ProcessFailed as exc:
            yield AgentEvent(kind="error", data={"message": str(exc), "exit_code": exc.exit_code})
        finally:
            if restore:
                restore()
            if prompt_file:
                try:
                    os.remove(prompt_file)
                except OSError:
                    pass


# ---------- Brain steps: project memory tools ----------

def install_memory_config(workdir: Path, env: dict[str, str]) -> Callable[[], None]:
    """Add AI-Hub's memory server to <workdir>/.agents/mcp_config.json.

    Keeps any servers the repo already has in that file. Returns a function
    that puts the folder back exactly as it was.
    """
    path = workdir / MCP_CONFIG
    made_dir = not path.parent.exists()
    original = path.read_bytes() if path.exists() else None
    data: Any = {}
    if original:
        try:
            data = json.loads(original.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    servers[MEMORY_SERVER] = {"command": sys.executable, "args": ["-m", "aihub.brain.memory_mcp"], "env": env}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def restore() -> None:
        try:
            if original is None:
                path.unlink(missing_ok=True)
                if made_dir:
                    path.parent.rmdir()   # only if we made it and it's empty again
            else:
                path.write_bytes(original)
        except OSError:
            pass
    return restore


# ---------- discovery: `agy models` ----------

_EFFORT_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<effort>low|medium|high|xhigh|max)$")
_EFFORT_LABEL = re.compile(r"\s*\((low|medium|high|xhigh|max)\)\s*$", re.IGNORECASE)


def parse_models(text: str) -> tuple[Discovery, set[str]]:
    """Lines look like "gemini-3.8-flash-high<TAB>Gemini 3.8 Flash (High)".

    Slugs that differ only by an effort suffix become one model with efforts.
    Returns the Discovery and the set of real slugs.
    """
    families: dict[str, dict[str, Any]] = {}  # base id -> {"label", "efforts"}; keeps list order
    slugs: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "\t" not in line and " " not in line:
            continue
        slug, _, label = line.partition("\t") if "\t" in line else line.partition(" ")
        slug, label = slug.strip(), label.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._]*(-[a-z0-9._]+)+", slug) or not label:
            continue  # log noise, headers, PowerShell error text...
        slugs.add(slug)
        match = _EFFORT_SUFFIX.match(slug)
        if match and _EFFORT_LABEL.search(label):
            base, effort = match["base"], match["effort"]
            label = _EFFORT_LABEL.sub("", label)
        else:
            base, effort = slug, None
        family = families.setdefault(base, {"label": label, "efforts": []})
        if effort and effort not in family["efforts"]:
            family["efforts"].append(effort)

    models = []
    for base, family in families.items():
        efforts = sorted(family["efforts"], key=EFFORT_ORDER.index)
        default = "medium" if "medium" in efforts else (efforts[len(efforts) // 2] if efforts else None)
        note = "Uses your Antigravity (Google) quota" if not base.startswith("gemini") else None
        models.append(ModelOption(id=base, label=family["label"], efforts=efforts,
                                  default_effort=default, note=note))
    if not models:
        return Discovery(error="Antigravity listed no models. Run agy once to sign in, then Re-check."), slugs
    # Antigravity's CLI doesn't report quota outside its interactive /usage panel.
    return Discovery(models=models, account=AccountInfo()), slugs


# ---------- parsing the stream ----------

class AntigravityParser:
    """Turns `agy --output-format stream-json` lines into AgentEvents.

    Keeps a little state: text deltas per step, which tool steps already got a
    tool_call, and this turn's own token counts (result.usage is cumulative).
    """

    def __init__(self):
        self._text: dict[int, list[str]] = {}
        self._tool_started: set[int] = set()
        self._input_tokens = 0
        self._output_tokens = 0
        self._saw_usage = False
        self._last_message: str | None = None

    def parse_line(self, line: str) -> list[AgentEvent]:
        try:
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(kind="raw", data={"line": line})]
        if not isinstance(msg, dict):
            return [AgentEvent(kind="raw", data={"value": msg})]

        event = msg.get("event")
        if event == "init":
            init = msg.get("init") or {}
            return [AgentEvent(kind="session_started", data={
                "session_id": msg.get("conversation_id"), "model": init.get("model"),
            })]
        if event == "step_update":
            return self._step(msg.get("step_update") or {})
        if event == "result":
            return self._result(msg.get("result") or {})
        if event == "error":
            return [AgentEvent(kind="error", data={"message": _error_text(msg) or "Antigravity error"})]
        return [AgentEvent(kind="raw", data=msg)]

    # ---------- steps ----------

    def _step(self, step: dict[str, Any]) -> list[AgentEvent]:
        kind = step.get("step_type")
        index = step.get("step_index")
        done = step.get("state") == "DONE"
        self._count_usage(step.get("usage"))

        if kind == "agent_response":
            delta = step.get("text_delta")
            if delta:
                self._text.setdefault(index, []).append(delta)
            if not done:
                return []
            text = "".join(self._text.pop(index, [])).strip()
            if not text:
                return []  # a "thinking" step before a tool call has no text
            self._last_message = text
            return [AgentEvent(kind="text", data={"text": text})]

        if kind == "tool":
            info = step.get("tool_info") or {}
            name = info.get("name") or step.get("tool_name") or "tool"
            events = []
            if index not in self._tool_started:
                self._tool_started.add(index)
                events.append(_tool_call(index, name, info.get("parameters") or {}))
            if done:
                events.append(_tool_result(index, name, info))
            return events

        if step.get("subagent_info"):
            return [AgentEvent(kind="raw", data=step)]
        return []  # user_input, checkpoint, system_message: nothing to show

    def _count_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        self._saw_usage = True
        # input_tokens doesn't include cached tokens here; add them so every agent
        # reports "all input the model read" (Claude and Codex do the same).
        self._input_tokens += int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_tokens") or 0)
        self._output_tokens += int(usage.get("output_tokens") or 0) + int(usage.get("thinking_tokens") or 0)

    # ---------- end of turn ----------

    def _result(self, res: dict[str, Any]) -> list[AgentEvent]:
        status = str(res.get("status") or "").upper()
        response = (res.get("response") or "").strip() or self._last_message
        events = []
        success = status == "SUCCESS"
        if not success:
            message = _error_text(res) or f"Antigravity stopped ({status.lower() or 'unknown'})"
            events.append(AgentEvent(kind="error", data={"message": message}))
            response = response or message
        result = RunResult(
            success=success,
            summary=response,
            input_tokens=self._input_tokens if self._saw_usage else None,
            output_tokens=self._output_tokens if self._saw_usage else None,
        )
        events.append(AgentEvent(kind="result", data=result.model_dump()))
        return events


def _error_text(obj: dict[str, Any]) -> str:
    error = obj.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "")
    return str(error or obj.get("message") or "")


def _tool_call(index: Any, name: str, params: dict[str, Any]) -> AgentEvent:
    if name == "call_mcp_tool":
        # MCP tools are "lazy" by default: one built-in tool names the server and tool.
        server, tool = params.get("ServerName") or "mcp", params.get("ToolName") or "tool"
        arguments = params.get("Arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"arguments": arguments}
        label, tool_input = f"{server}.{tool}", arguments if isinstance(arguments, dict) else {}
    elif name in COMMAND_TOOLS:
        label, tool_input = "Command", {"command": params.get("CommandLine") or params.get("command", "")}
    elif name in EDIT_TOOLS:
        path = params.get("Path") or params.get("TargetFile") or params.get("path") or ""
        label, tool_input = "Edit files", {"path": path, **params}
    else:
        label, tool_input = name, params
    return AgentEvent(kind="tool_call", data={"id": f"step-{index}", "name": label, "input": tool_input})


_EXIT_CODE = re.compile(r"exited with code (-?\d+)")


def _tool_result(index: Any, name: str, info: dict[str, Any]) -> AgentEvent:
    error = info.get("error")
    output = info.get("output")
    if output is None:
        output = "done" if not error else ""
    if not isinstance(output, str):
        output = json.dumps(output)
    is_error = bool(error)
    if error:
        output = (output + "\n" if output else "") + (_error_text(info) or json.dumps(error))
    if name in COMMAND_TOOLS:
        match = _EXIT_CODE.search(output)
        if match and match.group(1) != "0":
            is_error = True
    return AgentEvent(kind="tool_result", data={
        "tool_use_id": f"step-{index}",
        "output": output[:MAX_TOOL_OUTPUT_CHARS],
        "is_error": is_error,
    })

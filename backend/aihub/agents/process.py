"""Running agent CLIs as child processes. Shared by every CLI-based adapter.

Every coding-agent CLI we support works the same way at this level:
start a process, send the prompt on stdin, read JSON Lines from stdout.
Only the meaning of each line differs, and that stays in each adapter.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from pathlib import Path

from aihub.models import AgentEvent, AgentStatus

# One JSON line can be huge (a big file read by a tool). asyncio's default
# limit is 64 KB per line, which would crash the reader, so raise it.
STREAM_LIMIT = 16 * 1024 * 1024


class ProcessFailed(RuntimeError):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(stderr or f"exited with code {exit_code}")


async def stream_lines(
    cmd: list[str],
    cwd: Path,
    stdin_text: str,
    extra_env: Mapping[str, str] | None = None,
) -> AsyncIterator[str]:
    """Start `cmd`, send `stdin_text`, yield each non-empty stdout line as it arrives.

    Raises ProcessFailed if the process exits with a non-zero code.
    """
    env = {**os.environ, **(extra_env or {})}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_LIMIT,
    )
    # Read stderr at the same time as stdout. If nobody reads it and its
    # buffer fills up, the agent blocks forever ("pipe deadlock").
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async for raw_line in proc.stdout:  # yields each line as soon as it arrives
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                yield line

        exit_code = await proc.wait()
        stderr = (await stderr_task).decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            raise ProcessFailed(exit_code, stderr)
    finally:
        # Runs on normal exit AND when the run is cancelled: never leave a
        # stray agent process running in the background.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()


async def run_quick(cmd: list[str], timeout: float) -> str:
    """Run a short command (like `--version`) and return its stdout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"'{' '.join(cmd)}' timed out after {timeout}s") from None
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip() or f"exit code {proc.returncode}")
    return out.decode(errors="replace")


def epoch_to_iso(value) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    if value > 10**11:  # milliseconds
        value /= 1000
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")


AUTH_HINTS = ("log in", "login", "/login", "auth", "api key", "unauthorized", "401", "credit")


def looks_like_auth_error(message: str) -> bool:
    lower = message.lower()
    return any(hint in lower for hint in AUTH_HINTS)


def status_from_test(status: AgentStatus, events: list[AgentEvent], login_hint: str) -> AgentStatus:
    """Turn the events of a tiny test prompt into ready / not_signed_in / error."""
    result = next((e for e in events if e.kind == "result"), None)
    if result and result.data.get("success"):
        return status.model_copy(update={"state": "ready", "help": None})
    message = next((str(e.data.get("message", "")) for e in events if e.kind == "error"), "")
    if not message and result:
        message = str(result.data.get("summary") or "")
    if looks_like_auth_error(message):
        return status.model_copy(update={"state": "not_signed_in", "help": f"Not signed in. Run: {login_hint}  ({message[:200]})"})
    return status.model_copy(update={"state": "error", "help": message[:300] or "Test prompt failed."})


class JsonLineProcess:
    """A two-way conversation with a CLI over JSON lines (stdin -> stdout).

    Used for short "ask and answer" exchanges, like asking Claude Code or
    Codex which models the account has. Example:

        async with JsonLineProcess([path, "app-server"]) as p:
            await p.send({"id": 1, "method": "model/list", "params": {}})
            reply = await p.wait_for(lambda m: m.get("id") == 1)
    """

    def __init__(self, cmd: list[str], timeout: float = 40):
        self.cmd = cmd
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.seen: list[dict] = []  # every message read, useful when debugging

    async def __aenter__(self) -> "JsonLineProcess":
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # not read, so it must not be a pipe (deadlock)
            limit=STREAM_LIMIT,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def send(self, message: dict) -> None:
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def wait_for(self, match) -> dict:
        """Read lines until `match(message)` is true. Raises TimeoutError or RuntimeError."""
        async def read():
            while True:
                raw = await self.proc.stdout.readline()
                if not raw:
                    raise RuntimeError("the program exited before answering")
                try:
                    message = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue  # not JSON (log noise): skip
                if not isinstance(message, dict):
                    continue
                self.seen.append(message)
                if match(message):
                    return message
        return await asyncio.wait_for(read(), self.timeout)

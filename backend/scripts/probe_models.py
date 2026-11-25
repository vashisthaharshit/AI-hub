"""Learning / discovery script: ask Claude Code and Codex which models YOUR account has.

Sends no prompt, so it costs no tokens. Run it and read what each CLI says:

    python scripts/probe_models.py

How it works:
- Claude Code: started in "stream-json input" mode, the same way the official
  Agent SDK starts it. We send one `initialize` control request; the reply
  includes the models this account can use (display names, effort levels).
- Codex: `codex app-server` speaks JSON-RPC over stdin/stdout. We send
  `initialize`, then `model/list` (models + reasoning efforts) and
  `account/read` (which account / plan is signed in).

Everything each CLI sends back is printed and also saved to probe_output.json,
so we can build the real feature on what your CLIs actually report.
"""

import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

TIMEOUT_S = 40


def start(cmd: list[str]) -> tuple[subprocess.Popen, queue.Queue]:
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    lines: queue.Queue = queue.Queue()

    def pump():  # read stdout in a thread so we can time out instead of hanging
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return proc, lines


def send(proc: subprocess.Popen, message: dict) -> None:
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def wait_for(lines: queue.Queue, match, label: str) -> dict | None:
    """Read JSON lines until match(msg) is true. Returns that message or None on timeout."""
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        if line is None:
            print(f"  [{label}] the program exited before answering")
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(f"  [{label}] non-JSON line: {line[:200]}")
            continue
        if match(msg):
            return msg
        print(f"  [{label}] other message: {line[:200]}")
    print(f"  [{label}] no answer within {TIMEOUT_S}s")
    return None


def stop(proc: subprocess.Popen) -> str:
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return (proc.stderr.read() or "").strip()


def probe_claude() -> dict:
    path = shutil.which("claude")
    if not path:
        return {"error": "claude not found on PATH"}
    print(f"\n=== Claude Code ({path}) ===")
    proc, lines = start([
        path, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ])
    send(proc, {"type": "control_request", "request_id": "probe-1", "request": {"subtype": "initialize"}})
    reply = wait_for(
        lines,
        lambda m: m.get("type") == "control_response"
        and (m.get("response") or {}).get("request_id") == "probe-1",
        "claude",
    )
    stderr = stop(proc)
    result = {"initialize_reply": reply, "stderr": stderr[-2000:]}
    response = ((reply or {}).get("response") or {}).get("response") or {}
    models = response.get("models")
    if models:
        print(f"  {len(models)} models reported:")
        for m in models:
            print(f"   - {m.get('displayName') or m.get('value')!s:40}  id={m.get('value')}"
                  f"  efforts={m.get('supportedEffortLevels')}")
    else:
        print("  No model list found in the reply. Top-level keys:", list(response.keys()))
        if stderr:
            print("  stderr:", stderr[-500:])
    return result


def probe_codex() -> dict:
    path = shutil.which("codex")
    if not path:
        return {"error": "codex not found on PATH"}
    print(f"\n=== Codex ({path}) ===")
    proc, lines = start([path, "app-server"])
    send(proc, {"id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "aihub_probe", "title": "AI-Hub probe", "version": "0.1.0"}}})
    init = wait_for(lines, lambda m: m.get("id") == 1, "codex initialize")
    send(proc, {"method": "initialized"})
    send(proc, {"id": 2, "method": "model/list", "params": {}})
    models_reply = wait_for(lines, lambda m: m.get("id") == 2, "codex model/list")
    send(proc, {"id": 3, "method": "account/read", "params": {}})
    account_reply = wait_for(lines, lambda m: m.get("id") == 3, "codex account/read")
    stderr = stop(proc)

    data = ((models_reply or {}).get("result") or {}).get("data")
    if data:
        print(f"  {len(data)} models reported:")
        for m in data:
            efforts = [e.get("reasoningEffort", e) if isinstance(e, dict) else e
                       for e in m.get("supportedReasoningEfforts") or []]
            print(f"   - {m.get('displayName') or m.get('model')!s:40}  id={m.get('model') or m.get('id')}"
                  f"  default={m.get('isDefault')}  efforts={efforts}")
    else:
        print("  No model list. Reply:", json.dumps(models_reply)[:500])
    print("  account/read:", json.dumps(account_reply)[:500])
    return {"initialize": init, "model_list": models_reply, "account_read": account_reply,
            "stderr": stderr[-2000:]}


def main() -> None:
    output = {"python": sys.version, "claude": probe_claude(), "codex": probe_codex()}
    out_path = Path(__file__).with_name("probe_output.json")
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nFull replies saved to {out_path}")


if __name__ == "__main__":
    main()

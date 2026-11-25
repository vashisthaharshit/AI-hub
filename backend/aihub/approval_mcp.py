"""A tiny MCP server that lets the AI-Hub user approve Claude Code's actions.

Who starts it: Claude Code, in supervised mode (see agents/claude_code.py),
using the config AI-Hub writes. Claude talks to it over stdin/stdout.

What MCP looks like on the wire: JSON-RPC 2.0, one JSON object per line.
We only need four methods:
    initialize                -> say hello, announce that we have tools
    notifications/initialized -> no reply needed
    tools/list                -> describe our one tool, "approve"
    tools/call                -> Claude asks: may I use tool X with input Y?

For tools/call we POST the question to AI-Hub (/api/internal/approvals) and
wait. AI-Hub shows Allow / Deny in the UI; when the user clicks, the HTTP
call returns and we answer Claude with:
    {"behavior": "allow", "updatedInput": {...}}   or
    {"behavior": "deny", "message": "..."}

Written with the standard library only, so you can read every step.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
TOOL = {
    "name": "approve",
    "description": "Ask the AI-Hub user whether a tool call may run.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}

_write_lock = threading.Lock()  # replies may come from several threads


def send(message: dict) -> None:
    with _write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def ask_aihub(tool_name: str, tool_input: dict) -> dict:
    """POST the request to AI-Hub and block until the user decides."""
    body = json.dumps({
        "run_id": os.environ["AIHUB_RUN_ID"],
        "token": os.environ["AIHUB_TOKEN"],
        "tool_name": tool_name,
        "input": tool_input,
    }).encode("utf-8")
    request = urllib.request.Request(
        os.environ["AIHUB_URL"].rstrip("/") + "/api/internal/approvals",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=None) as response:  # no timeout: wait for the user
            decision = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # If AI-Hub can't be reached, the safe answer is "no".
        return {"behavior": "deny", "message": f"AI-Hub approval failed: {exc}"}
    if decision.get("behavior") == "allow":
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": decision.get("message") or "Denied by the user in AI-Hub."}


def handle_call(request_id, params: dict) -> None:
    args = params.get("arguments") or {}
    decision = ask_aihub(str(args.get("tool_name", "unknown")), args.get("input") or {})
    send({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps(decision)}]},
    })


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        params = message.get("params") or {}
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aihub", "version": "0.1.0"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL]}})
    elif method == "tools/call":
        # Answer in a thread: waiting for the user must not block other messages.
        threading.Thread(target=handle_call, args=(request_id, message.get("params") or {}), daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown method {method}"}})
    # Notifications (no id) that we don't care about are ignored.


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()

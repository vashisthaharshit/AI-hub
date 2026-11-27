"""The project-memory tools, as a tiny MCP server agents start for Brain steps.

Like aihub/approval_mcp.py: JSON-RPC over stdin/stdout, standard library only.
Each tool call is forwarded to AI-Hub (/api/internal/memory) with the step's
secret token; AI-Hub answers from its database and code index.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
# Hints agents use to decide whether a tool needs approval: these only read
# the repo or AI-Hub's notes; add_note writes only to AI-Hub's notes.
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}

TOOLS = [
    {"name": "find_code",
     "description": "Where a function, class or variable is defined in this repo, and which files use it. "
                    "Faster and cheaper than searching the files yourself.",
     "annotations": READ_ONLY,
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "search_notes",
     "description": "What earlier steps of this task and past tasks learned about this repo: decisions, "
                    "gotchas, where things are. Check this before exploring.",
     "annotations": READ_ONLY,
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "add_note",
     "description": "Save one short fact later steps should know (a decision, a gotcha, where something lives).",
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]}},
    {"name": "task_status",
     "description": "The plan you are part of: which steps are done, who did them and what they changed.",
     "annotations": READ_ONLY,
     "inputSchema": {"type": "object", "properties": {}}},
]

_write_lock = threading.Lock()


def send(message: dict) -> None:
    with _write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def ask_aihub(tool: str, args: dict) -> str:
    body = json.dumps({"token": os.environ.get("AIHUB_MEMORY_TOKEN", ""), "tool": tool, "args": args}).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("AIHUB_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/internal/memory",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return str(json.loads(response.read()).get("text", ""))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"Project memory is not available right now ({exc}). Explore the repo instead."


def handle_call(request_id, params: dict) -> None:
    text = ask_aihub(str(params.get("name", "")), params.get("arguments") or {})
    send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        params = message.get("params") or {}
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aihub_memory", "version": "0.1.0"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        threading.Thread(target=handle_call, args=(request_id, message.get("params") or {}), daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown method {method}"}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                handle(json.loads(line))
            except json.JSONDecodeError:
                continue


if __name__ == "__main__":
    main()

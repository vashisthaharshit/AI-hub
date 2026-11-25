"""Talks to the approval MCP server exactly like Claude Code would:
start it as a process and exchange JSON-RPC lines over stdin/stdout.
A tiny local HTTP server plays the part of AI-Hub.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


class FakeAIHub(BaseHTTPRequestHandler):
    received: list = []
    answer = {"behavior": "allow"}

    def do_POST(self):  # noqa: N802 - name required by http.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAIHub.received.append((self.path, body))
        data = json.dumps(FakeAIHub.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def bridge():
    server = HTTPServer(("127.0.0.1", 0), FakeAIHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "PYTHONPATH": str(BACKEND),
           "AIHUB_URL": f"http://127.0.0.1:{server.server_port}", "AIHUB_RUN_ID": "r1", "AIHUB_TOKEN": "secret"}
    proc = subprocess.Popen([sys.executable, "-m", "aihub.approval_mcp"], env=env, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def call(message: dict) -> dict:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    FakeAIHub.received = []
    yield call
    proc.stdin.close()
    proc.wait(timeout=5)
    server.shutdown()


def test_handshake_and_tool_list(bridge):
    init = bridge({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
    assert init["result"]["capabilities"] == {"tools": {}}
    tools = bridge({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools["result"]["tools"][0]["name"] == "approve"


def test_allow_is_forwarded(bridge):
    FakeAIHub.answer = {"behavior": "allow", "message": None}
    reply = bridge({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "approve", "arguments": {"tool_name": "Write", "input": {"file_path": "a.py"}}}})
    decision = json.loads(reply["result"]["content"][0]["text"])
    assert decision == {"behavior": "allow", "updatedInput": {"file_path": "a.py"}}
    path, body = FakeAIHub.received[0]
    assert path == "/api/internal/approvals"
    assert body == {"run_id": "r1", "token": "secret", "tool_name": "Write", "input": {"file_path": "a.py"}}


def test_deny_is_forwarded(bridge):
    FakeAIHub.answer = {"behavior": "deny", "message": "not that file"}
    reply = bridge({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": "rm -rf /"}}}})
    assert json.loads(reply["result"]["content"][0]["text"]) == {"behavior": "deny", "message": "not that file"}


def test_unknown_method(bridge):
    assert bridge({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601

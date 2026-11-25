"""The FastAPI app: HTTP routes + one WebSocket per run, and the built UI.

This file is thin on purpose. It only translates HTTP <-> Python calls;
all the real logic lives in agents/, runner.py, worktree.py, checks.py and db.py.

Endpoints
  GET  /api/agents                     list agents and their status (cheap)
  POST /api/agents/{id}/test           send a tiny prompt to check sign-in
  GET  /api/runs                       recent runs
  POST /api/runs                       start a run
  GET  /api/runs/{id}                  one run
  POST /api/runs/{id}/messages         follow-up message (continues the session)
  POST /api/runs/{id}/cancel           stop a running agent
  GET  /api/runs/{id}/files            changed files, old + new content (diff viewer)
  GET  /api/runs/{id}/diff             changes as unified diff text
  POST /api/runs/{id}/keep             commit to the run's branch, remove the worktree
  POST /api/runs/{id}/discard          delete the worktree and the branch
  POST /api/runs/{id}/approvals/{aid}  supervised mode: Allow / Deny
  POST /api/internal/approvals         used by the approval MCP server, not the UI
  WS   /ws/runs/{id}?after=-1          past + live events of a run
  /                                    the React app (frontend/dist), if built
"""

import argparse
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aihub import config, worktree
from aihub.agents.registry import default_registry
from aihub.db import Database
from aihub.models import (
    AgentStatus, ApprovalDecision, ApprovalRequest, ChangedFile, FollowUp, RunCreate, RunInfo,
)
from aihub.runner import RunError, RunManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup...
    config.ensure_dirs()
    db = Database(config.DB_PATH)
    db.mark_interrupted_runs()
    app.state.db = db
    app.state.registry = default_registry()
    app.state.runs = RunManager(db, app.state.registry)
    yield
    # ...and once at shutdown: stop running agents, close the database.
    await app.state.runs.shutdown()
    db.close()


app = FastAPI(title="AI-Hub", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def runs() -> RunManager:
    return app.state.runs


async def _call(coro):
    """Turn our Python errors into HTTP errors with a readable message."""
    try:
        return await coro
    except KeyError as exc:
        raise HTTPException(404, exc.args[0] if exc.args else "Not found") from None
    except (RunError, worktree.GitError) as exc:
        raise HTTPException(400, str(exc)) from None


# ---------- agents ----------

@app.get("/api/agents", response_model=list[AgentStatus])
async def list_agents():
    return await app.state.registry.scan()


@app.post("/api/agents/{agent_id}/test", response_model=AgentStatus)
async def test_agent(agent_id: str):
    try:
        adapter = app.state.registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(404, exc.args[0]) from None
    return await adapter.test_connection()


# ---------- runs ----------

@app.get("/api/runs", response_model=list[RunInfo])
async def list_runs(limit: int = 50):
    return app.state.db.list_runs(limit)


@app.post("/api/runs", response_model=RunInfo, status_code=201)
async def create_run(req: RunCreate):
    return await _call(runs().start(req))


@app.get("/api/runs/{run_id}", response_model=RunInfo)
async def get_run(run_id: str):
    run = app.state.db.get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run '{run_id}' not found")
    return run


@app.post("/api/runs/{run_id}/messages", response_model=RunInfo)
async def follow_up(run_id: str, body: FollowUp):
    return await _call(runs().follow_up(run_id, body.prompt))


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    await get_run(run_id)
    return {"cancelled": runs().cancel(run_id)}


@app.get("/api/runs/{run_id}/files", response_model=list[ChangedFile])
async def get_changed_files(run_id: str):
    return await _call(runs().changed_files(run_id))


@app.get("/api/runs/{run_id}/diff")
async def get_diff(run_id: str):
    run = await get_run(run_id)
    if not run.worktree_path:
        raise HTTPException(400, "This run has no worktree")
    return {"diff": await _call(worktree.diff(Path(run.worktree_path), run.base_commit or "HEAD"))}


@app.post("/api/runs/{run_id}/keep", response_model=RunInfo)
async def keep_run(run_id: str):
    return await _call(runs().keep(run_id))


@app.post("/api/runs/{run_id}/discard", response_model=RunInfo)
async def discard_run(run_id: str):
    return await _call(runs().discard(run_id))


# ---------- supervised mode ----------

@app.post("/api/runs/{run_id}/approvals/{approval_id}")
async def decide_approval(run_id: str, approval_id: str, decision: ApprovalDecision):
    if not runs().decide(run_id, approval_id, decision):
        raise HTTPException(404, "No pending approval with that id")
    return {"ok": True}


@app.post("/api/internal/approvals")
async def request_approval(req: ApprovalRequest):
    """Long request: only returns once the user has decided in the UI."""
    try:
        return await runs().request_approval(req)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


# ---------- live events ----------

@app.websocket("/ws/runs/{run_id}")
async def run_events(ws: WebSocket, run_id: str, after: int = -1):
    """Sends {"type":"event", seq, kind, data} messages, then one {"type":"end", run}
    when the current turn finishes. `after` lets a reconnecting client skip
    events it already has."""
    await ws.accept()
    if not app.state.db.get_run(run_id):
        await ws.close(code=4404, reason="run not found")
        return
    try:
        async for seq, event in runs().stream(run_id, after):
            await ws.send_json({"type": "event", "seq": seq, **event.model_dump()})
        await ws.send_json({"type": "end", "run": app.state.db.get_run(run_id).model_dump()})
        await ws.close()
    except WebSocketDisconnect:
        pass  # the browser tab closed; the run itself keeps going


# ---------- the UI ----------
# Mounted last, so /api and /ws routes above take priority over static files.

if config.FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True), name="ui")
else:
    @app.get("/")
    async def ui_not_built():
        return {
            "message": "AI-Hub API is running, but the UI isn't built yet.",
            "fix": "cd frontend && npm install && npm run build, then restart aihub",
            "api_docs": "/docs",
        }


def run() -> None:
    """Entry point for the `aihub` command."""
    import uvicorn

    parser = argparse.ArgumentParser(prog="aihub", description="Start AI-Hub.")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()
    config.PORT = args.port

    url = config.base_url()
    if not config.FRONTEND_DIST.is_dir():
        print("\n  UI not built yet. Run:  cd frontend && npm install && npm run build\n")
    elif not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"\n  AI-Hub running at {url}   (Ctrl+C to stop)\n")

    # No --reload here on purpose: on Windows, uvicorn's reload mode switches
    # to an event loop that cannot start subprocesses, so agents would fail.
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    run()

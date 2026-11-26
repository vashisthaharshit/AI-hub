"""The FastAPI app: HTTP routes + one WebSocket per run, and the built UI.

This file is thin on purpose. It only translates HTTP <-> Python calls;
all the real logic lives in agents/, runner.py, worktree.py, checks.py and db.py.

Endpoints
  GET  /api/agents?refresh=false       agents, their models and account info (cached)
  POST /api/agents/{id}/test           send a tiny prompt to check sign-in
  POST /api/agents/{id}/models/unhide  show models hidden after a "needs credits" failure
  GET  /api/runs                       recent runs
  POST /api/runs                       start a run
  GET  /api/runs/{id}                  one run
  POST /api/runs/{id}/messages         follow-up message; with agent_id: switch agent (handoff)
  POST /api/runs/{id}/cancel           stop a running agent
  GET  /api/runs/{id}/files            changed files, old + new content (diff viewer)
  GET  /api/runs/{id}/diff             changes as unified diff text
  POST /api/runs/{id}/keep             commit to the run's branch, remove the worktree
  POST /api/runs/{id}/discard          delete the worktree and the branch
  DELETE /api/runs/{id}                remove from history (unkept work is discarded too)
  POST /api/runs/bulk-delete           same, for several runs {ids: [...]}
  POST /api/runs/{id}/approvals/{aid}  supervised mode: Allow / Deny
  POST /api/internal/approvals         used by the approval MCP server, not the UI
  GET  /api/settings                   summary model settings + which API keys are saved
  PUT  /api/settings/summary           save summary model settings
  POST /api/settings/summary/test      try settings on a tiny example
  PUT/DELETE /api/settings/keys/{p}    save / remove your own Groq or OpenRouter key (OS keychain)
  GET  /api/settings/models/{p}        models your key can use
  GET  /api/apps                       connected apps (Apps tab)
  GET  /api/apps/default-folder        where links are cloned unless the user picks another folder
  POST /api/apps                       connect {url, folder} or {path}: clone + read the repo
  GET  /api/apps/{id}                  one app with its manifest (commands, tools it needs...)
  POST /api/apps/{id}/rescan           read the folder again
  DELETE /api/apps/{id}                remove from the Apps tab (the folder is NOT deleted)
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
from aihub.apps import connect as apps
from aihub.agents.registry import default_registry
from aihub.db import Database
from aihub.models import (
    AgentStatus, AppConnect, AppInfo, ApprovalDecision, ApprovalRequest, BulkDelete, ChangedFile, FollowUp, KeyUpdate, RunCreate,
    RunInfo, SettingsView, SummarySettings, SummaryTest, UsageWindow,
)
from aihub.runner import RunError, RunManager
from aihub.secrets_store import UnavailableStore, default_store
from aihub.summarizer import HTTP_PROVIDERS, Summarizer, SummaryError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup...
    config.ensure_dirs()
    db = Database(config.DB_PATH)
    db.mark_interrupted_runs()
    app.state.db = db
    app.state.registry = default_registry()
    for agent_id, data in db.usage_events():  # replay saved usage: newest wins
        app.state.registry.update_usage(agent_id, UsageWindow(**data))
    app.state.secrets = default_store()
    app.state.summarizer = Summarizer(db, app.state.registry, app.state.secrets)
    app.state.runs = RunManager(db, app.state.registry, app.state.summarizer)
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
    except (RunError, worktree.GitError, apps.AppError) as exc:
        raise HTTPException(400, str(exc)) from None


# ---------- agents ----------

@app.get("/api/agents", response_model=list[AgentStatus])
async def list_agents(refresh: bool = False):
    """refresh=true asks each CLI again for its models and account info (slower)."""
    return await app.state.registry.scan(refresh, app.state.db.hidden_models())


@app.post("/api/agents/{agent_id}/test", response_model=AgentStatus)
async def test_agent(agent_id: str):
    registry = app.state.registry
    try:
        adapter = registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(404, exc.args[0]) from None
    registry.remember_test(await adapter.test_connection())
    return await registry.status_of(agent_id, hidden=app.state.db.hidden_models().get(agent_id, set()))


@app.post("/api/agents/{agent_id}/models/unhide", response_model=AgentStatus)
async def unhide_models(agent_id: str):
    """Show models again that were hidden after a 'needs credits' failure."""
    try:
        app.state.registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(404, exc.args[0]) from None
    app.state.db.unhide_models(agent_id)
    return await app.state.registry.status_of(agent_id, hidden=set())


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
    return await _call(runs().follow_up(run_id, body.prompt, body.model, body.effort, body.agent_id))


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


@app.post("/api/runs/bulk-delete")
async def bulk_delete(body: BulkDelete):
    deleted, failed = await runs().delete_many(body.ids)
    return {"deleted": deleted, "failed": failed}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    await _call(runs().delete(run_id))
    return {"deleted": True}


@app.post("/api/runs/{run_id}/keep", response_model=RunInfo)
async def keep_run(run_id: str):
    return await _call(runs().keep(run_id))


@app.post("/api/runs/{run_id}/discard", response_model=RunInfo)
async def discard_run(run_id: str):
    return await _call(runs().discard(run_id))


# ---------- settings: the summary model ----------

def _settings_view() -> SettingsView:
    secrets = app.state.secrets
    return SettingsView(
        summary=app.state.summarizer.settings(),
        keys={name: secrets.has(name) for name in HTTP_PROVIDERS},
        keyring_available=not isinstance(secrets, UnavailableStore),
    )


@app.get("/api/settings", response_model=SettingsView)
async def get_settings():
    return _settings_view()


@app.put("/api/settings/summary", response_model=SettingsView)
async def save_summary_settings(settings: SummarySettings):
    app.state.summarizer.save_settings(settings)
    return _settings_view()


@app.post("/api/settings/summary/test", response_model=SummaryTest)
async def test_summary_settings(settings: SummarySettings):
    """Try settings before saving them: summarizes a tiny example."""
    return await app.state.summarizer.test(settings)


@app.put("/api/settings/keys/{provider}", response_model=SettingsView)
async def save_key(provider: str, body: KeyUpdate):
    """Store the user's own API key in the OS keychain. It is never sent back."""
    if provider not in HTTP_PROVIDERS:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    try:
        app.state.secrets.set(provider, body.key.strip())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from None
    return _settings_view()


@app.delete("/api/settings/keys/{provider}", response_model=SettingsView)
async def delete_key(provider: str):
    if provider not in HTTP_PROVIDERS:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    app.state.secrets.delete(provider)
    return _settings_view()


@app.get("/api/settings/models/{provider}")
async def provider_models(provider: str):
    try:
        return {"models": await app.state.summarizer.list_models(provider)}
    except SummaryError as exc:
        raise HTTPException(400, str(exc)) from None


# ---------- apps ----------

@app.get("/api/apps", response_model=list[AppInfo])
async def list_apps():
    return app.state.db.list_apps()


@app.get("/api/apps/default-folder")
async def apps_default_folder():
    return {"folder": str(apps.default_folder())}


@app.post("/api/apps", response_model=AppInfo, status_code=201)
async def connect_app(req: AppConnect):
    """Can take a while: cloning downloads the whole repo."""
    return await _call(apps.connect(req, app.state.db))


@app.get("/api/apps/{app_id}", response_model=AppInfo)
async def get_app(app_id: str):
    found = app.state.db.get_app(app_id)
    if not found:
        raise HTTPException(404, f"App '{app_id}' not found")
    return found


@app.post("/api/apps/{app_id}/rescan", response_model=AppInfo)
async def rescan_app(app_id: str):
    async def do():
        return apps.rescan(app_id, app.state.db)
    return await _call(do())


@app.delete("/api/apps/{app_id}")
async def forget_app(app_id: str):
    """Only removes it from the list. The folder may hold the user's own
    files (CV, documents), so AI-Hub never deletes it."""
    await get_app(app_id)
    app.state.db.delete_app(app_id)
    return {"deleted": True}


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

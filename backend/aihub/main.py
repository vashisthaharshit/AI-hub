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
  POST /api/pick-folder                open the computer's "choose a folder" window {start, title}
  POST /api/apps                       connect {url, folder} or {path}: clone + read the repo
  GET  /api/apps/{id}                  one app with its manifest (commands, tools it needs...)
  POST /api/apps/{id}/rescan           read the folder again
  DELETE /api/apps/{id}                remove from the Apps tab (the folder is NOT deleted)
  GET  /api/apps/{id}/guide            the Guide tab: saved AI guide if still current, else the basic one
  POST /api/apps/{id}/guide?force=false  write the guide with the cheapest model (slow) and save it
  POST /api/apps/{id}/upload?folder=&name=   save a dropped file inside the app's folder (raw body)
  POST /api/apps/{id}/runs             run a command {command, values} or a typed request {message}
  GET  /api/apps/{id}/runs             this app's runs (newest first)
  PUT  /api/apps/{id}/trust            {trusted}: auto-yes to the agent's actions, except money tools
  POST /api/apps/{id}/open             open a file of the app {path, reveal}
  GET  /api/apps/{id}/setup            the setup form (keys, config files, connections) + current answers
  PUT  /api/apps/{id}/setup            save answers {values, confirm_unsafe}; writes config files / .mcp.json
  GET  /api/apps/{id}/tools            look again for the tools the app needs (fresh, not cached)
  POST /api/tools/{tool}/install       install a tool with winget (one at a time)
  GET  /api/tools/installs/{job}?after=-1  install progress + new log lines (poll this)
  WS   /ws/runs/{id}?after=-1          past + live events of a run
  /                                    the React app (frontend/dist), if built
"""

import argparse
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aihub import config, folder_picker, worktree
from aihub.apps import connect as apps
from aihub.apps import files as app_files
from aihub.apps import running as app_running
from aihub.apps import tools
from aihub.apps.guide import GuideError, Guides
from aihub.apps.setup_values import SetupError, SetupValues
from aihub.agents.registry import default_registry
from aihub.db import Database
from aihub.models import (
    AgentStatus, AppConnect, AppGuide, AppInfo, AppRequirement, AppRunCreate, AppTrust, OpenPath, SetupSave,
    SetupView, ApprovalDecision, InstallJob, ApprovalRequest, BulkDelete, ChangedFile, FollowUp, KeyUpdate, RunCreate,
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
    app.state.runs = RunManager(db, app.state.registry, app.state.summarizer, app_context=_app_context)
    app.state.installer = tools.Installer()
    app.state.guides = Guides(db, app.state.summarizer, app.state.registry)
    app.state.setup_values = SetupValues(db, app.state.secrets)
    yield
    # ...and once at shutdown: stop running agents, close the database.
    await app.state.runs.shutdown()
    db.close()


def _app_context(run: RunInfo) -> app_running.AppRunContext:
    """Settings for one turn of an app run, read fresh (changed settings apply)."""
    found = app.state.db.get_app(run.app_id or "")
    if not found:
        return app_running.AppRunContext()
    # Which command started this run: a money command makes every action ask.
    command = next((c.name for c in found.manifest.commands
                    if c.invoke and (run.prompt == c.invoke or run.prompt.startswith(c.invoke + " "))), None)
    values = app.state.setup_values
    return app_running.build_context(found, values.run_env(found), values.trusted(found.id), command)


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
    except (RunError, worktree.GitError, apps.AppError, tools.InstallError, GuideError, app_files.UploadError, folder_picker.PickerError, SetupError,
            app_running.AppRunError) as exc:
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
    return app.state.db.list_runs(limit)  # Manual mode only; app runs are listed per app


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


@app.post("/api/pick-folder")
async def pick_folder(body: dict | None = None):
    """Opens a real folder dialog on this computer and waits for the user.
    Returns {"path": "C:\\..."} or {"path": null} if they cancelled."""
    body = body or {}
    start = body.get("start") if isinstance(body.get("start"), str) else None
    title = body.get("title") if isinstance(body.get("title"), str) else "Choose a folder"
    return {"path": await _call(folder_picker.pick_folder(start, title[:100]))}


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


@app.get("/api/apps/{app_id}/guide", response_model=AppGuide)
async def get_guide(app_id: str):
    return app.state.guides.current(await get_app(app_id))


@app.post("/api/apps/{app_id}/guide", response_model=AppGuide)
async def write_guide(app_id: str, force: bool = False):
    """Can take 10-30 seconds. Returns the saved guide at once if it's still current."""
    return await _call(app.state.guides.write(await get_app(app_id), force))


@app.get("/api/apps/{app_id}/setup", response_model=SetupView)
async def get_setup(app_id: str):
    return app.state.setup_values.view(await get_app(app_id))


@app.put("/api/apps/{app_id}/setup", response_model=SetupView)
async def save_setup(app_id: str, body: SetupSave):
    """Secrets go to the OS keychain; config files and .mcp.json are written now."""
    found = await get_app(app_id)

    async def do():
        return app.state.setup_values.save(found, body)
    return await _call(do())


@app.post("/api/apps/{app_id}/runs", response_model=RunInfo, status_code=201)
async def run_app(app_id: str, body: AppRunCreate):
    found = await get_app(app_id)
    m = found.manifest
    agent_id = body.agent_id or app_running.AGENT_IDS.get(m.primary_agent or "claude", "claude-code")
    try:
        adapter = app.state.registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(404, exc.args[0]) from None

    if body.command:
        command = next((c for c in m.commands if c.name == body.command), None)
        if not command:
            raise HTTPException(404, f"This app has no command '{body.command}'")
        if (command.money or m.setup.always_ask) and not adapter.supports_supervised:
            raise HTTPException(400, f"{adapter.name} can't stop to ask you before orders or payments. "
                                     "Use Claude Code for this app.")
        form = app.state.guides.current(found).commands.get(command.name)
        label = (form.title if form and form.title else None) or command.title
        try:
            prompt = app_running.build_prompt(found, command, form, body.values, adapter.id)
        except app_running.AppRunError as exc:
            raise HTTPException(400, str(exc)) from None
    elif body.message and body.message.strip():
        if m.setup.always_ask and not adapter.supports_supervised:
            raise HTTPException(400, f"{adapter.name} can't stop to ask you before orders. Use Claude Code.")
        prompt, label = body.message.strip(), "Your request"
    else:
        raise HTTPException(400, "Pick a command or type what you want to do.")
    return await _call(runs().start_app(found.id, found.path, adapter, prompt, label, body.model, body.effort))


@app.get("/api/apps/{app_id}/runs", response_model=list[RunInfo])
async def list_app_runs(app_id: str, limit: int = 30):
    await get_app(app_id)
    return app.state.db.list_runs(limit, kind="app", app_id=app_id)


@app.put("/api/apps/{app_id}/trust")
async def set_app_trust(app_id: str, body: AppTrust):
    await get_app(app_id)
    app.state.setup_values.set_trusted(app_id, body.trusted)
    return {"trusted": body.trusted}


@app.post("/api/apps/{app_id}/open")
async def open_app_file(app_id: str, body: OpenPath):
    found = await get_app(app_id)

    async def do():
        app_running.open_path(found, body.path, body.reveal)
        return {"ok": True}
    return await _call(do())


@app.post("/api/apps/{app_id}/upload")
async def upload_file(app_id: str, request: Request, name: str, folder: str = "uploads"):
    """The file is the raw request body (no multipart, so no extra dependency).
    Returns {"path": "documents/cv/CV.pdf"}, relative to the app's folder."""
    found = await get_app(app_id)
    if int(request.headers.get("content-length") or 0) > app_files.MAX_BYTES:
        raise HTTPException(400, f"The file is too big (over {app_files.MAX_BYTES // (1024 * 1024)} MB).")
    data = await request.body()

    async def do():
        return {"path": app_files.save_upload(Path(found.path), folder, name, data)}
    return await _call(do())


@app.get("/api/apps/{app_id}/tools", response_model=list[AppRequirement])
async def check_app_tools(app_id: str):
    """Look for every tool again, e.g. after the user installed one by hand,
    and save the result so the app list shows it too."""
    found = await get_app(app_id)
    found.manifest.requirements = tools.recheck(found.manifest.requirements)
    app.state.db.update_app_manifest(app_id, found.manifest)
    return found.manifest.requirements


@app.post("/api/tools/{tool_id}/install", response_model=InstallJob, status_code=201)
async def install_tool(tool_id: str):
    """Only called when the user clicks Install. Returns right away; poll the job."""
    async def do():
        return app.state.installer.start(tool_id)
    return await _call(do())


@app.get("/api/tools/installs/{job_id}", response_model=InstallJob)
async def install_progress(job_id: str, after: int = -1):
    async def do():
        return app.state.installer.get(job_id, after)
    return await _call(do())


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

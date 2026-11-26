# Contributing to AI-Hub

Thanks for taking a look! This page explains how the code is organized, the rules that keep it
easy to change, and how to add a new agent.

## Development setup

```bash
# Backend (Python 3.10+)
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   |   source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                            # no real agent or tokens needed

# Frontend (Node 20+)
cd ../frontend
npm install
npm test                          # unit tests (vitest)
npm run typecheck                 # TypeScript
```

Run with hot reload, in two terminals:

```bash
cd backend && aihub --no-browser       # API on http://127.0.0.1:8000
cd frontend && npm run dev             # UI on http://127.0.0.1:5173 (proxies /api and /ws)
```

> **Windows:** don't run uvicorn with `--reload`. Its reload mode switches to an event loop that
> can't start subprocesses, so agents would fail to start.

## Project structure

```
AI-hub/
├── backend/                      Python (FastAPI)
│   ├── aihub/
│   │   ├── main.py               HTTP + WebSocket routes, serves the built UI (thin layer)
│   │   ├── config.py             paths, host/port, where the UI build is
│   │   ├── models.py             Pydantic data shapes used everywhere
│   │   ├── db.py                 SQLite: workspaces, runs, event log, Brain tasks, apps, settings, migrations
│   │   ├── workspaces.py         one workspace per product (repo folder); old chats adopted at startup
│   │   ├── runner.py             RunManager: turns, follow-ups, agent switching, approvals, keep/discard
│   │   ├── handoff.py            the briefing a new agent gets when you switch agents
│   │   ├── summarizer.py         the cheap "summary model" (own agent, or own Groq/OpenRouter key)
│   │   ├── secrets_store.py      API keys in the OS keychain (never in files)
│   │   ├── worktree.py           git: worktree per run, diffs vs the base commit, commit/remove
│   │   ├── checks.py             after each turn: files/lines changed, test command
│   │   ├── approval_mcp.py       tiny MCP server Claude Code asks for approvals (ask before changes)
│   │   ├── folder_picker.py      the computer's own "choose a folder" dialog
│   │   ├── agents/
│   │   │   ├── base.py           AgentAdapter contract + RunOptions
│   │   │   ├── process.py        shared: run a CLI, stream its JSON lines
│   │   │   ├── claude_code.py    Claude Code adapter
│   │   │   ├── codex.py          Codex adapter
│   │   │   └── registry.py       the list of agents + cached models/usage
│   │   ├── brain/                plan a task across agents (no AI where code will do)
│   │   │   ├── triage.py         small or big? rules first, a tiny AI check only when unsure
│   │   │   ├── digest.py         a short repo description for the planner, made by code
│   │   │   ├── planner.py        questions or a plan; checked and fixed by code
│   │   │   ├── router.py         agent + model + effort per step (Cheapest / Balanced / Best)
│   │   │   └── tasks.py          Brain tasks: triage -> questions -> plan -> edits
│   │   └── apps/                 GitHub repos made for agents, used through buttons
│   │       ├── connect.py        clone or reuse a repo (push disabled for safety)
│   │       ├── manifest.py       read commands, skills, plugins, requirements (no AI)
│   │       ├── guide.py          the Guide tab + per-command forms (cheapest model, cached)
│   │       ├── running.py        prompts, run settings, money safety, outputs
│   │       ├── setup.py / setup_values.py   the setup form: keys, config files, MCP servers
│   │       ├── tools.py / install.py         tools an app needs, winget install, skills for Claude Code
│   │       ├── update.py         "Update repo": backup, then fast-forward
│   │       └── files.py          uploaded files
│   ├── scripts/                  learning: raw agent output, what each CLI reports
│   └── tests/                    pytest
├── frontend/                     React + Vite + Tailwind (see frontend/README.md)
└── docs/images/                  screenshots for the README
```

## Design rules

1. **Layers only call downward:** `main.py` → `brain/`, `apps/`, `runner.py` → `agents/`, `worktree.py`, `checks.py`, `db.py`.
   `runner.py` and `brain/` never import FastAPI; adapters never touch the database.
2. **Agent-specific code lives only in its adapter.** Everything else uses the shared `AgentEvent` format.
3. **Every data shape is a Pydantic model** in `models.py`, mirrored in `frontend/src/api/types.ts`.
   Change both in the same commit.
4. **Never drop agent output.** Unknown lines become `raw` events.
5. **Agents only work in worktrees.** Nothing is merged into the user's branches automatically.
6. **The server binds to `127.0.0.1` only.** It can run agents that edit files.
7. **Secrets never go to files or the database.** Use `secrets_store.py`.
8. **Every feature comes with a test.**
9. **Use code before AI.** Anything that is the same every time (reading a repo, checking a plan, picking a
   model) is done by code: free, instant and testable. AI calls go to the cheapest model that can do the job.
10. **Apps stay generic.** A fix found while testing one repo must work for every similar repo.

## Adding a new agent

1. Create `backend/aihub/agents/<name>.py` with a class extending `AgentAdapter`:
   - `detect()`: is the CLI installed, which version (fast, no tokens)
   - `test_connection()`: a tiny prompt to confirm sign-in
   - `run()`: start the CLI headless and **yield `AgentEvent`s** as its output arrives
   - optional `discover()`: models, effort levels, account and usage info
2. Put the parsing in pure functions or a parser class, and unit-test them with sample output lines
   (see `tests/test_codex_parser.py`).
3. Add it to `default_registry()` in `agents/registry.py`.

That's it: the Agents panel, model pickers, runs, follow-ups and agent switching pick it up automatically.

## Pull requests

- Keep PRs focused; describe what changed and why.
- `pytest` and `npm run typecheck` must pass.
- For UI changes, add a screenshot.

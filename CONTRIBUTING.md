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
│   │   ├── db.py                 SQLite: runs, append-only event log, settings, small migrations
│   │   ├── runner.py             RunManager: turns, follow-ups, agent switching, approvals, keep/discard
│   │   ├── handoff.py            the briefing a new agent gets when you switch agents
│   │   ├── summarizer.py         the summary model (own agent, or own Groq/OpenRouter key)
│   │   ├── secrets_store.py      API keys in the OS keychain (never in files)
│   │   ├── worktree.py           git: worktree per run, diffs vs the base commit, commit/remove
│   │   ├── checks.py             after each turn: files/lines changed, test command
│   │   ├── approval_mcp.py       tiny MCP server Claude Code asks for approvals (supervised mode)
│   │   └── agents/
│   │       ├── base.py           AgentAdapter contract + RunOptions
│   │       ├── process.py        shared: run a CLI, stream its JSON lines, JSON-line conversations
│   │       ├── claude_code.py    Claude Code adapter
│   │       ├── codex.py          Codex adapter
│   │       └── registry.py       the list of agents + cached models/usage
│   ├── scripts/
│   │   ├── spike_claude.py       learning: watch Claude Code's raw event stream
│   │   └── probe_models.py       learning: what models/usage each CLI reports for your account
│   └── tests/                    pytest
├── frontend/                     React + Vite + Tailwind (see frontend/README.md)
└── docs/images/                  screenshots for the README
```

## Design rules

1. **Layers only call downward:** `main.py` → `runner.py` → `agents/`, `worktree.py`, `checks.py`, `db.py`.
   `runner.py` never imports FastAPI; adapters never touch the database.
2. **Agent-specific code lives only in its adapter.** Everything else uses the shared `AgentEvent` format.
3. **Every data shape is a Pydantic model** in `models.py`, mirrored in `frontend/src/api/types.ts`.
   Change both in the same commit.
4. **Never drop agent output.** Unknown lines become `raw` events.
5. **Agents only work in worktrees.** Nothing is merged into the user's branches automatically.
6. **The server binds to `127.0.0.1` only.** It can run agents that edit files.
7. **Secrets never go to files or the database.** Use `secrets_store.py`.
8. **Every feature comes with a test.**

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

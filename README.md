# AI-Hub

One control plane for AI coding agents (Claude Code, Codex, ...), with two modes:

- **Manual mode**: pick an agent, give it a task, watch it work (like T3 Code).
- **Brain mode** (later phases): AI-Hub plans the task, picks agents, checks results.

Current status: **Phase 1 (Manual mode) complete.**

- Agents: Claude Code and Codex, detected automatically, with a sign-in test
- Every run in its own git worktree + branch; your folder and `main` are never touched
- Live activity over WebSocket, every event saved (replayable)
- Supervised mode: approve each edit / command (Claude Code)
- Follow-up messages that continue the same agent session
- Checks after each turn: files and lines changed, and your test command's result
- Side-by-side diff (Monaco)
- Keep (commit to the run's branch) or Discard (delete worktree + branch)

## Quick start (Windows, PowerShell)

Needs Python 3.10+, Node.js 20+, git, and at least one agent CLI installed and signed in
(`claude auth login` and/or `codex login`).

```powershell
git clone <this repo> AI-hub
cd AI-hub

# 1. Backend (once)
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. UI (once, and again after UI changes)
cd ..\frontend
npm install
npm run build

# 3. Start (every time)
cd ..\backend
.\.venv\Scripts\Activate.ps1
aihub
```

`aihub` serves the UI and the API on **http://127.0.0.1:8000** and opens your browser.
Options: `aihub --port 8100`, `aihub --no-browser`.

macOS/Linux: use `source .venv/bin/activate` and `/` instead of `\`.

## Folder structure

```
AI-hub/
├── backend/                     Python (FastAPI)
│   ├── aihub/
│   │   ├── main.py              HTTP + WebSocket routes, serves the built UI (thin layer)
│   │   ├── config.py            paths, host/port, where the UI build is
│   │   ├── models.py            Pydantic data shapes used everywhere
│   │   ├── db.py                SQLite: runs + append-only event log, tiny migrations
│   │   ├── runner.py            RunManager: turns, follow-ups, approvals, keep/discard
│   │   ├── worktree.py          git: worktree per run, diffs vs base commit, commit/remove
│   │   ├── checks.py            measures each turn: files/lines changed, test command
│   │   ├── approval_mcp.py      tiny MCP server Claude Code asks for approvals (supervised)
│   │   └── agents/
│   │       ├── base.py          AgentAdapter contract + RunOptions
│   │       ├── process.py       shared: run a CLI, stream its JSON lines
│   │       ├── claude_code.py   Claude Code adapter (claude -p --output-format stream-json)
│   │       ├── codex.py         Codex adapter (codex exec --json)
│   │       └── registry.py      the list of known agents
│   ├── scripts/spike_claude.py  learning script: Claude Code's raw event stream
│   └── tests/                   pytest (no real agent or tokens needed)
└── frontend/                    React + Vite + Tailwind (see frontend/README.md)
```

## Rules to follow

1. **Layers only talk downward:** `main.py` → `runner.py` → `agents/`, `worktree.py`, `checks.py`, `db.py`.
   `runner.py` never imports FastAPI; adapters never touch the database.
2. **Agent-specific code lives only in its adapter.** Everything else uses `AgentEvent`.
   A new agent = a new file in `agents/` + one line in `registry.py`.
3. **Every shape is a Pydantic model in `models.py`**, mirrored in `frontend/src/api/types.ts`.
4. **Never drop agent output.** Unknown lines become `raw` events.
5. **Agents only work in worktrees.** Nothing is merged into your branches automatically.
6. **The server binds to 127.0.0.1 only.** It can run agents that edit files.
7. **Every new feature comes with a test.**

## Development (hot reload for UI work)

```powershell
# Terminal 1
cd backend; .\.venv\Scripts\Activate.ps1; aihub --no-browser
# Terminal 2
cd frontend; npm run dev        # http://127.0.0.1:5173, proxies to the backend
```

## Tests

```powershell
cd backend; pytest              # 35 tests (some skip on Windows)
cd frontend; npm test           # unit tests
cd frontend; npm run typecheck  # TypeScript
```

## Data

Stored in `%USERPROFILE%\.aihub`: the database (`aihub.db`) and run worktrees.
Kept runs live on as branches named `aihub/run-<id>` in your repo; merge with
`git merge aihub/run-<id>`.

## Phase 2 next

- [ ] Compare mode: same task to 2 agents side by side
- [ ] Evaluator: hard checks + LLM judge
- [ ] Benchmark suite of 20–30 tasks

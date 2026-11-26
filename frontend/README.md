# AI-Hub frontend

React + TypeScript + Vite + Tailwind, with Monaco for the diff view.

## Structure

```
frontend/
├── index.html            loads the fonts (Bricolage Grotesque, Figtree, JetBrains Mono)
├── package.json
├── vite.config.ts        dev server on :5173, proxies /api and /ws to the backend on :8000
├── tsconfig.json
└── src/
    ├── main.tsx          entry point
    ├── App.tsx           layout: Home, a workspace (Manual / Brain), Apps, Settings
    ├── index.css         Tailwind + the theme (colors, shell, motion)
    ├── api/
    │   ├── types.ts      TS copies of the backend's Pydantic models (keep in sync!)
    │   └── client.ts     every HTTP call + error handling
    ├── hooks/
    │   ├── useAgents.ts      load / re-check / test agents
    │   └── useRunStream.ts   WebSocket: live events, reconnect, de-duplication
    ├── lib/
    │   ├── events.ts         events -> display items (pairs tool calls with results)
    │   ├── events.test.ts    unit tests (vitest)
    │   ├── theme.ts          workspace and agent colors
    │   └── monaco.ts         loads Monaco from the bundle (works offline)
    └── components/
        ├── home/             Home screen, the orchestration graphic, New workspace
        ├── shell/            top bar (workspace switcher, Manual/Brain switch, agents), workspace settings
        ├── brain/            Brain sidebar, new task, task view (plan, router picks, runs)
        ├── apps/             the Apps section
        ├── AgentsPanel.tsx   agent list with status + Test connection
        ├── RunList.tsx       a workspace's Manual chats (polls every 5s)
        ├── NewTaskForm.tsx   Manual: agent + task -> Start chat
        ├── RunView.tsx       one run: Activity and Changes tabs, Cancel
        ├── ActivityItem.tsx  how each event looks
        ├── DiffViewer.tsx    file list + Monaco side-by-side diff (lazy-loaded)
        └── StatusBadge.tsx
```

## Rules

1. Components never call `fetch` directly; they use `api` from `api/client.ts`.
2. Logic that isn't about drawing (grouping, formatting) goes in `lib/` with a test.
3. If a backend model changes, update `api/types.ts` in the same commit.

## Build (normal use)

```powershell
npm install
npm run build      # writes dist/, which the backend serves at http://127.0.0.1:8000
```

## Run (development, with hot reload)

Start the backend first (`aihub --no-browser` in `backend/`), then in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173

```powershell
npm test            # unit tests
npm run typecheck   # TypeScript checks
npm run build       # production build into dist/
```

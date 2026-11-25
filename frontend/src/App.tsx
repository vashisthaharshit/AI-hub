// Layout: sidebar (agents + run history) on the left, main area on the right.
// The main area shows the New task form, a run, or Settings.

import { useCallback, useState } from "react";
import { AgentsPanel } from "./components/AgentsPanel";
import { NewTaskForm } from "./components/NewTaskForm";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { SettingsPage } from "./components/SettingsPage";
import { useAgents } from "./hooks/useAgents";

type Page = { kind: "new" } | { kind: "run"; id: string } | { kind: "settings" };

export default function App() {
  const agents = useAgents();
  const [page, setPage] = useState<Page>({ kind: "new" });
  const [runsVersion, setRunsVersion] = useState(0); // bump to refresh the run list now
  const selectedRunId = page.kind === "run" ? page.id : null;

  // After a run changes: refresh the run list, and quietly reload agents so
  // usage numbers the run reported (e.g. Claude's limits) show up.
  const { refresh: refreshAgents } = agents;
  const refreshRuns = useCallback(() => {
    setRunsVersion((v) => v + 1);
    void refreshAgents(false, true);
  }, [refreshAgents]);

  return (
    <div className="flex h-full">
      <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
        <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <div>
            <h1 className="text-base font-semibold">AI-Hub</h1>
            <p className="text-xs text-zinc-500">Manual mode</p>
          </div>
          <button
            onClick={() => setPage({ kind: "settings" })}
            className={`rounded px-2 py-1 text-xs hover:bg-zinc-800 ${page.kind === "settings" ? "bg-zinc-800 text-zinc-100" : "text-zinc-400"}`}
          >
            Settings
          </button>
        </header>
        <AgentsPanel
          agents={agents.agents}
          loading={agents.loading}
          error={agents.error}
          testingIds={agents.testingIds}
          onRefresh={() => agents.refresh(true)}
          onTest={agents.test}
          onUnhide={agents.unhide}
        />
        <RunList
          selectedId={selectedRunId}
          refreshKey={runsVersion}
          onSelect={(id) => setPage({ kind: "run", id })}
          onNew={() => setPage({ kind: "new" })}
          onDeleted={(ids) => {
            if (selectedRunId && ids.includes(selectedRunId)) setPage({ kind: "new" });
            refreshRuns();
          }}
        />
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        {page.kind === "run" ? (
          // key: a different run gets a fresh RunView (and a fresh WebSocket)
          <RunView
            key={page.id}
            runId={page.id}
            agents={agents.agents}
            onChanged={refreshRuns}
            onDeleted={() => {
              setPage({ kind: "new" });
              refreshRuns();
            }}
          />
        ) : page.kind === "settings" ? (
          <SettingsPage agents={agents.agents} />
        ) : (
          <NewTaskForm
            agents={agents.agents}
            onCreated={(run) => {
              refreshRuns();
              setPage({ kind: "run", id: run.id });
            }}
          />
        )}
      </main>
    </div>
  );
}

// Layout: sidebar (agents + run history) on the left, main area on the right.
// The main area shows either the New task form or the selected run.

import { useCallback, useState } from "react";
import { AgentsPanel } from "./components/AgentsPanel";
import { NewTaskForm } from "./components/NewTaskForm";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { useAgents } from "./hooks/useAgents";

export default function App() {
  const agents = useAgents();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runsVersion, setRunsVersion] = useState(0); // bump to refresh the run list now

  const refreshRuns = useCallback(() => setRunsVersion((v) => v + 1), []);

  return (
    <div className="flex h-full">
      <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
        <header className="border-b border-zinc-800 px-4 py-3">
          <h1 className="text-base font-semibold">AI-Hub</h1>
          <p className="text-xs text-zinc-500">Manual mode</p>
        </header>
        <AgentsPanel
          agents={agents.agents}
          loading={agents.loading}
          error={agents.error}
          testingId={agents.testingId}
          onRefresh={agents.refresh}
          onTest={agents.test}
        />
        <RunList
          selectedId={selectedRunId}
          refreshKey={runsVersion}
          onSelect={setSelectedRunId}
          onNew={() => setSelectedRunId(null)}
        />
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        {selectedRunId ? (
          // key: a different run gets a fresh RunView (and a fresh WebSocket)
          <RunView key={selectedRunId} runId={selectedRunId} agents={agents.agents} onChanged={refreshRuns} />
        ) : (
          <NewTaskForm
            agents={agents.agents}
            onCreated={(run) => {
              refreshRuns();
              setSelectedRunId(run.id);
            }}
          />
        )}
      </main>
    </div>
  );
}

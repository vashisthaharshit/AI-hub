// Layout: sidebar on the left, main area on the right, two modes:
//   Manual  sidebar = agents + run history; main = New task form, a run, or Settings
//   Apps    sidebar = connected repos;      main = Connect a repo, or one app

import { useCallback, useState } from "react";
import { AgentsPanel } from "./components/AgentsPanel";
import { AppView } from "./components/apps/AppView";
import { AppsSidebar } from "./components/apps/AppsSidebar";
import { ConnectAppForm } from "./components/apps/ConnectAppForm";
import { NewTaskForm } from "./components/NewTaskForm";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { SettingsPage } from "./components/SettingsPage";
import { useAgents } from "./hooks/useAgents";

type Page =
  | { kind: "new" }
  | { kind: "run"; id: string }
  | { kind: "settings" }
  | { kind: "connect" }            // Apps mode: connect a repo
  | { kind: "app"; id: string };   // Apps mode: one app

export default function App() {
  const agents = useAgents();
  const [page, setPage] = useState<Page>({ kind: "new" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [runsVersion, setRunsVersion] = useState(0); // bump to refresh the run list now
  const selectedRunId = page.kind === "run" ? page.id : null;
  const appsMode = page.kind === "connect" || page.kind === "app";
  const [lastAppId, setLastAppId] = useState<string | null>(null); // reopen it when coming back to Apps
  const [appsVersion, setAppsVersion] = useState(0); // bump to refresh the app list
  const refreshApps = useCallback(() => setAppsVersion((v) => v + 1), []);

  function go(next: Page) {
    if (next.kind === "app") setLastAppId(next.id);
    setPage(next);
    setSidebarOpen(false);
  }

  // After a run changes: refresh the run list, and quietly reload agents so
  // usage numbers the run reported (e.g. Claude's limits) show up.
  const { refresh: refreshAgents } = agents;
  const refreshRuns = useCallback(() => {
    setRunsVersion((v) => v + 1);
    void refreshAgents(false, true);
  }, [refreshAgents]);

  return (
    <div className="workspace">
      <header className="workspace-bar">
        <div className="flex min-w-0 items-center gap-3">
          <div className="brand-mark" aria-hidden="true"><span>›</span><span>_</span></div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-lg font-semibold tracking-tight">AI-Hub</h1>
            <nav className="flex gap-1" aria-label="Mode">
              <button
                className="workspace-button"
                aria-current={!appsMode && page.kind !== "settings" ? "page" : undefined}
                onClick={() => go({ kind: "new" })}
              >
                Manual
              </button>
              <button
                className="workspace-button"
                aria-current={appsMode ? "page" : undefined}
                onClick={() => go(lastAppId ? { kind: "app", id: lastAppId } : { kind: "connect" })}
              >
                Apps
              </button>
            </nav>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="workspace-button sidebar-toggle"
            aria-expanded={sidebarOpen}
            aria-controls="workspace-sidebar"
            onClick={() => setSidebarOpen((open) => !open)}
          >
            {appsMode ? "Apps" : <>Agents &amp; runs</>}
          </button>
          <button
            onClick={() => {
              setPage({ kind: "settings" });
              setSidebarOpen(false);
            }}
            aria-current={page.kind === "settings" ? "page" : undefined}
            className="workspace-button"
          >
            Settings
          </button>
        </div>
      </header>
      <aside id="workspace-sidebar" className="workspace-sidebar" data-open={sidebarOpen}>
        {appsMode ? (
          <AppsSidebar
            selectedId={page.kind === "app" ? page.id : null}
            connecting={page.kind === "connect"}
            refreshKey={appsVersion}
            onConnect={() => go({ kind: "connect" })}
            onSelect={(id) => go({ kind: "app", id })}
          />
        ) : (
        <>
        <button
          className="new-task-button"
          aria-current={page.kind === "new" ? "page" : undefined}
          onClick={() => {
            setPage({ kind: "new" });
            setSidebarOpen(false);
          }}
        >
          <span aria-hidden="true">+</span> New task
        </button>
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
          onSelect={(id) => {
            setPage({ kind: "run", id });
            setSidebarOpen(false);
          }}
          onDeleted={(ids) => {
            if (selectedRunId && ids.includes(selectedRunId)) setPage({ kind: "new" });
            refreshRuns();
          }}
        />
        </>
        )}
      </aside>

      <main className="workspace-main min-w-0 overflow-y-auto">
        {page.kind === "connect" ? (
          <ConnectAppForm
            onConnected={(app) => {
              refreshApps();
              go({ kind: "app", id: app.id });
            }}
          />
        ) : page.kind === "app" ? (
          <AppView
            key={page.id}
            appId={page.id}
            onChanged={refreshApps}
            onRemoved={() => {
              setLastAppId(null);
              refreshApps();
              go({ kind: "connect" });
            }}
          />
        ) : page.kind === "run" ? (
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

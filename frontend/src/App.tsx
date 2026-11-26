// AI-Hub's layout.
//
//   Home       pick a workspace (one per product = one repo) or create one
//   Workspace  everything for one product, in two modes:
//                Manual  you chat with one agent      sidebar: Manual chats
//                Brain   the Brain plans the job      sidebar: Brain tasks
//              Runs started by a Brain task stay in Brain until you move them to Manual.
//   Apps       GitHub repos used through buttons (outside workspaces)
//   Settings

import { useCallback, useState, type CSSProperties } from "react";
import type { Workspace } from "./api/types";
import { AppView } from "./components/apps/AppView";
import { AppsSidebar } from "./components/apps/AppsSidebar";
import { ConnectAppForm } from "./components/apps/ConnectAppForm";
import { BrainNewTask } from "./components/brain/BrainNewTask";
import { BrainSidebar } from "./components/brain/BrainSidebar";
import { BrainTaskView } from "./components/brain/BrainTaskView";
import { Home } from "./components/home/Home";
import { NewTaskForm } from "./components/NewTaskForm";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { SettingsPage } from "./components/SettingsPage";
import { TopBar, type Mode } from "./components/shell/TopBar";
import { WorkspaceSettingsDialog } from "./components/shell/WorkspaceSettingsDialog";
import { useAgents } from "./hooks/useAgents";
import { wsColor } from "./lib/theme";

type View = "home" | "workspace" | "apps" | "settings";
type ManualPage = { kind: "new" } | { kind: "run"; id: string };
type BrainPage = { kind: "new" } | { kind: "task"; id: string } | { kind: "run"; taskId: string; runId: string };
type AppsPage = { kind: "connect" } | { kind: "app"; id: string };

const MODE_KEY = "aihub.mode";

export default function App() {
  const agents = useAgents();
  const [view, setView] = useState<View>("home");
  const [beforeSettings, setBeforeSettings] = useState<View>("home");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [mode, setModeState] = useState<Mode>(() => (localStorage.getItem(MODE_KEY) === "brain" ? "brain" : "manual"));
  const [manualPage, setManualPage] = useState<ManualPage>({ kind: "new" });
  const [brainPage, setBrainPage] = useState<BrainPage>({ kind: "new" });
  const [appsPage, setAppsPage] = useState<AppsPage>({ kind: "connect" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState(false);

  // bump to refresh a list now
  const [runsVersion, setRunsVersion] = useState(0);
  const [brainVersion, setBrainVersion] = useState(0);
  const [appsVersion, setAppsVersion] = useState(0);
  const [homeVersion, setHomeVersion] = useState(0);

  const { refresh: refreshAgents } = agents;
  const refreshRuns = useCallback(() => {
    setRunsVersion((v) => v + 1);
    void refreshAgents(false, true); // usage numbers reported by the run
  }, [refreshAgents]);
  const refreshBrain = useCallback(() => setBrainVersion((v) => v + 1), []);
  const refreshApps = useCallback(() => setAppsVersion((v) => v + 1), []);

  function setMode(next: Mode) {
    setModeState(next);
    setSidebarOpen(false);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      // storage unavailable: just don't remember
    }
  }

  function openWorkspace(ws: Workspace) {
    if (ws.id !== workspace?.id) {
      setManualPage({ kind: "new" });
      setBrainPage({ kind: "new" });
    }
    setWorkspace(ws);
    setView("workspace");
    setSidebarOpen(false);
  }

  function goHome() {
    setView("home");
    setHomeVersion((v) => v + 1);
    setSidebarOpen(false);
  }

  const hasSidebar = view === "workspace" || view === "apps";
  const shellStyle = { ["--ws" as string]: view === "workspace" && workspace ? wsColor(workspace.color) : "var(--color-brain)" } as CSSProperties;

  return (
    <div className="shell" data-full={!hasSidebar} style={shellStyle}>
      <TopBar
        view={view}
        workspace={workspace}
        mode={mode}
        agentsState={agents}
        sidebarOpen={sidebarOpen}
        hasSidebar={hasSidebar}
        onHome={goHome}
        onSwitchWorkspace={openWorkspace}
        onEditWorkspace={() => setEditingWorkspace(true)}
        onMode={setMode}
        onSettings={() => {
          if (view !== "settings") setBeforeSettings(view);
          setView(view === "settings" ? beforeSettings : "settings");
        }}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
      />

      {hasSidebar && (
        <aside className="sidebar" data-open={sidebarOpen}>
          {view === "apps" ? (
            <AppsSidebar
              selectedId={appsPage.kind === "app" ? appsPage.id : null}
              connecting={appsPage.kind === "connect"}
              refreshKey={appsVersion}
              onConnect={() => { setAppsPage({ kind: "connect" }); setSidebarOpen(false); }}
              onSelect={(id) => { setAppsPage({ kind: "app", id }); setSidebarOpen(false); }}
            />
          ) : workspace && mode === "brain" ? (
            <BrainSidebar
              workspaceId={workspace.id}
              selectedId={brainPage.kind === "task" ? brainPage.id : brainPage.kind === "run" ? brainPage.taskId : null}
              creating={brainPage.kind === "new"}
              refreshKey={brainVersion}
              onNew={() => { setBrainPage({ kind: "new" }); setSidebarOpen(false); }}
              onSelect={(id) => { setBrainPage({ kind: "task", id }); setSidebarOpen(false); }}
            />
          ) : workspace ? (
            <>
              <button
                className="new-task-button"
                aria-current={manualPage.kind === "new" ? "page" : undefined}
                onClick={() => { setManualPage({ kind: "new" }); setSidebarOpen(false); }}
              >
                <span aria-hidden="true">+</span> New chat
              </button>
              <RunList
                workspaceId={workspace.id}
                selectedId={manualPage.kind === "run" ? manualPage.id : null}
                refreshKey={runsVersion}
                onSelect={(id) => { setManualPage({ kind: "run", id }); setSidebarOpen(false); }}
                onDeleted={(ids) => {
                  if (manualPage.kind === "run" && ids.includes(manualPage.id)) setManualPage({ kind: "new" });
                  refreshRuns();
                }}
              />
            </>
          ) : null}
        </aside>
      )}

      <main className="main min-w-0">
        {view === "home" && (
          <Home
            agents={agents.agents}
            refreshKey={homeVersion}
            onOpen={openWorkspace}
            onOpenApps={() => { setView("apps"); setSidebarOpen(false); }}
          />
        )}

        {view === "settings" && <SettingsPage agents={agents.agents} />}

        {view === "apps" &&
          (appsPage.kind === "app" ? (
            <AppView
              key={appsPage.id}
              appId={appsPage.id}
              onChanged={refreshApps}
              onRemoved={() => { refreshApps(); setAppsPage({ kind: "connect" }); }}
            />
          ) : (
            <ConnectAppForm onConnected={(app) => { refreshApps(); setAppsPage({ kind: "app", id: app.id }); }} />
          ))}

        {view === "workspace" && workspace && mode === "manual" &&
          (manualPage.kind === "run" ? (
            // key: a different run gets a fresh RunView (and a fresh WebSocket)
            <RunView
              key={manualPage.id}
              runId={manualPage.id}
              agents={agents.agents}
              onChanged={refreshRuns}
              onDeleted={() => { setManualPage({ kind: "new" }); refreshRuns(); }}
            />
          ) : (
            <NewTaskForm
              key={workspace.id}
              workspace={workspace}
              agents={agents.agents}
              onWorkspaceChanged={setWorkspace}
              onCreated={(run) => { refreshRuns(); setManualPage({ kind: "run", id: run.id }); }}
            />
          ))}

        {view === "workspace" && workspace && mode === "brain" && (
          brainPage.kind === "task" ? (
            <BrainTaskView
              key={brainPage.id}
              taskId={brainPage.id}
              workspace={workspace}
              agents={agents.agents}
              onChanged={() => { refreshBrain(); refreshRuns(); }}
              onDeleted={() => { refreshBrain(); setBrainPage({ kind: "new" }); }}
              onOpenRun={(runId) => setBrainPage({ kind: "run", taskId: brainPage.id, runId })}
            />
          ) : brainPage.kind === "run" ? (
            <div>
              <div className="mx-auto max-w-[56rem] px-[clamp(1rem,4vw,2.75rem)] pt-5">
                <button className="ghost-button" onClick={() => setBrainPage({ kind: "task", id: brainPage.taskId })}>
                  <span aria-hidden="true">‹</span> Back to the Brain task
                </button>
              </div>
              <RunView
                key={brainPage.runId}
                runId={brainPage.runId}
                agents={agents.agents}
                onChanged={refreshRuns}
                onDeleted={() => setBrainPage({ kind: "task", id: brainPage.taskId })}
              />
            </div>
          ) : (
            <BrainNewTask
              key={workspace.id}
              workspace={workspace}
              onCreated={(task) => { refreshBrain(); setBrainPage({ kind: "task", id: task.id }); }}
            />
          )
        )}
      </main>

      {editingWorkspace && workspace && (
        <WorkspaceSettingsDialog
          workspace={workspace}
          onClose={() => setEditingWorkspace(false)}
          onSaved={(ws) => { setWorkspace(ws); setEditingWorkspace(false); }}
          onDeleted={() => { setEditingWorkspace(false); setWorkspace(null); goHome(); }}
        />
      )}
    </div>
  );
}

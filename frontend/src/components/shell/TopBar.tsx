// The bar along the top: home, the workspace you're in (switch from here),
// Manual / Brain, your agents' status, and Settings.

import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { AgentStatus, Workspace } from "../../api/types";
import { isUsable } from "../../hooks/useAgents";
import { agentColor, tint, wsColor } from "../../lib/theme";
import { AgentsPanel } from "../AgentsPanel";

export type Mode = "manual" | "brain";

interface Props {
  view: "home" | "workspace" | "apps" | "settings";
  workspace: Workspace | null;
  mode: Mode;
  agentsState: {
    agents: AgentStatus[];
    loading: boolean;
    error: string | null;
    testingIds: Set<string>;
    refresh: (force?: boolean) => void;
    test: (id: string) => void;
    unhide: (id: string) => void;
  };
  sidebarOpen: boolean;
  hasSidebar: boolean;
  onHome: () => void;
  onSwitchWorkspace: (ws: Workspace) => void;
  onEditWorkspace: () => void;
  onMode: (mode: Mode) => void;
  onSettings: () => void;
  onToggleSidebar: () => void;
}

function useClickAway(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

export function BrandGlyph() {
  return (
    <span className="brand-glyph" aria-hidden="true">
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="10" cy="10" r="2.4" fill="currentColor" stroke="none" />
        <path d="M10 2.5v3.2M10 14.3v3.2M2.5 10h3.2M14.3 10h3.2" />
      </svg>
    </span>
  );
}

export function TopBar(p: Props) {
  const [wsMenu, setWsMenu] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [all, setAll] = useState<Workspace[]>([]);
  const wsRef = useClickAway(wsMenu, () => setWsMenu(false));
  const agentsRef = useClickAway(agentsOpen, () => setAgentsOpen(false));

  useEffect(() => {
    if (wsMenu) api.workspaces().then(setAll).catch(() => setAll([]));
  }, [wsMenu]);

  const shown = p.agentsState.agents.filter((a) => a.state !== "not_installed");

  return (
    <header className="topbar">
      <button className="brand" onClick={p.onHome} title="All workspaces">
        <BrandGlyph />
        <span className="hide-sm">AI-Hub</span>
      </button>

      {p.view === "workspace" && p.workspace && (
        <>
          <span className="topbar-divider hide-sm" />
          <div className="relative" ref={wsRef}>
            <button className="workspace-button border-transparent" onClick={() => setWsMenu((o) => !o)}
              aria-expanded={wsMenu} aria-haspopup="menu">
              <span className="swatch" style={tint(wsColor(p.workspace.color))} />
              <span className="max-w-[14rem] truncate text-sm text-zinc-100">{p.workspace.name}</span>
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className="text-zinc-500">
                <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
            {wsMenu && (
              <div className="menu left-0 top-[calc(100%+.4rem)]" role="menu">
                <p className="px-2.5 pb-1 pt-1.5 text-xs text-zinc-500">Switch workspace</p>
                {all.map((ws) => (
                  <button key={ws.id} role="menuitem" className="menu-item" aria-current={ws.id === p.workspace?.id}
                    onClick={() => { setWsMenu(false); p.onSwitchWorkspace(ws); }}>
                    <span className="swatch" style={tint(wsColor(ws.color))} />
                    <span className="min-w-0 flex-1 truncate">{ws.name}</span>
                    {ws.id === p.workspace?.id && <span className="text-xs text-zinc-500">current</span>}
                  </button>
                ))}
                <div className="my-1 h-px bg-zinc-800" />
                <button role="menuitem" className="menu-item" onClick={() => { setWsMenu(false); p.onEditWorkspace(); }}>
                  Workspace settings
                </button>
                <button role="menuitem" className="menu-item" onClick={() => { setWsMenu(false); p.onHome(); }}>
                  All workspaces
                </button>
              </div>
            )}
          </div>

          <div className="mode-switch ml-1" data-mode={p.mode} role="group" aria-label="Mode">
            <span className="mode-pill" aria-hidden="true" />
            <button aria-pressed={p.mode === "manual"} onClick={() => p.onMode("manual")}
              title="Chat with one agent yourself">
              Manual
            </button>
            <button aria-pressed={p.mode === "brain"} onClick={() => p.onMode("brain")}
              title="Describe a job; the Brain plans it across agents">
              <span className="live-dot" style={{ ["--dot" as string]: "var(--color-brain)" }} />
              Brain
            </button>
          </div>
        </>
      )}

      {p.view === "apps" && (
        <>
          <span className="topbar-divider hide-sm" />
          <span className="font-display text-base font-bold">Apps</span>
        </>
      )}
      {p.view === "settings" && (
        <>
          <span className="topbar-divider hide-sm" />
          <span className="font-display text-base font-bold">Settings</span>
        </>
      )}

      <div className="ml-auto flex items-center gap-2">
        {p.hasSidebar && (
          <button className="workspace-button sidebar-toggle" aria-expanded={p.sidebarOpen} onClick={p.onToggleSidebar}>
            Chats
          </button>
        )}
        <div className="relative" ref={agentsRef}>
          <button className="flex items-center gap-1.5" onClick={() => setAgentsOpen((o) => !o)} aria-expanded={agentsOpen}
            title="Your agents">
            {shown.length === 0 ? (
              <span className="agent-pill">
                <span className="live-dot" style={{ ["--dot" as string]: "#f2b25a" }} />
                {p.agentsState.loading ? "Checking agents..." : "No agents"}
              </span>
            ) : (
              shown.map((a) => (
                <span key={a.id} className="agent-pill">
                  <span className="live-dot" data-live={isUsable(a)}
                    style={{ ["--dot" as string]: isUsable(a) ? agentColor(a.id) : "#4d5880" }} />
                  <span className="hide-sm">{a.name}</span>
                </span>
              ))
            )}
          </button>
          {agentsOpen && (
            <div className="menu right-0 top-[calc(100%+.4rem)] w-[22rem] max-w-[calc(100vw-2rem)] origin-top-right p-0">
              <AgentsPanel
                agents={p.agentsState.agents}
                loading={p.agentsState.loading}
                error={p.agentsState.error}
                testingIds={p.agentsState.testingIds}
                onRefresh={() => p.agentsState.refresh(true)}
                onTest={p.agentsState.test}
                onUnhide={p.agentsState.unhide}
              />
            </div>
          )}
        </div>
        <button className="workspace-button" aria-current={p.view === "settings" ? "page" : undefined} onClick={p.onSettings}
          title="Settings">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <circle cx="10" cy="10" r="2.8" />
            <path d="M10 1.8v2.4M10 15.8v2.4M1.8 10h2.4M15.8 10h2.4M4.2 4.2l1.7 1.7M14.1 14.1l1.7 1.7M4.2 15.8l1.7-1.7M14.1 5.9l1.7-1.7" strokeLinecap="round" />
          </svg>
          <span className="hide-sm">Settings</span>
        </button>
      </div>
    </header>
  );
}

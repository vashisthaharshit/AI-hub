// The first screen: pick a workspace (one per product) or create one.
// New users get a short explanation of how AI-Hub works.

import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AgentStatus, Workspace } from "../../api/types";
import { isUsable } from "../../hooks/useAgents";
import { agentColor, timeAgoShort, tint, wsColor } from "../../lib/theme";
import { Constellation } from "./Constellation";
import { CreateWorkspaceDialog } from "./CreateWorkspaceDialog";

interface Props {
  agents: AgentStatus[];
  refreshKey: number;
  onOpen: (ws: Workspace) => void;
  onOpenApps: () => void;
}

const delay = (i: number) => ({ ["--i" as string]: i }) as React.CSSProperties;

export function Home({ agents, refreshKey, onOpen, onOpenApps }: Props) {
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.workspaces().then(setWorkspaces).catch(() => setWorkspaces([]));
  }, [refreshKey]);

  const firstTime = workspaces !== null && workspaces.length === 0;
  const ready = agents.filter(isUsable);

  return (
    <div className="home">
      <section className="home-hero">
        <div>
          <h1 className="enter" style={delay(0)}>
            {firstTime ? "Put your AI agents to work on your products." : "Where do you want to work?"}
          </h1>
          <p className="enter" style={delay(1)}>
            {firstTime
              ? "Each product gets a workspace. Inside it you can chat with one agent (Manual), or describe a bigger job and let the Brain split it across Claude Code and Codex."
              : "Pick a workspace to continue, or start a new one for another product."}
          </p>
          <div className="enter mt-5 flex flex-wrap items-center gap-2" style={delay(2)}>
            {ready.length > 0 ? (
              ready.map((a) => (
                <span key={a.id} className="agent-pill">
                  <span className="live-dot" data-live="true" style={{ ["--dot" as string]: agentColor(a.id) }} />
                  {a.name} is ready
                </span>
              ))
            ) : (
              <span className="agent-pill text-amber-300">
                <span className="live-dot" style={{ ["--dot" as string]: "#f2b25a" }} />
                No agent signed in yet: install Claude Code or Codex
              </span>
            )}
          </div>
        </div>
        <div className="enter" style={delay(2)}>
          <Constellation />
        </div>
      </section>

      {firstTime && (
        <ol className="steps-strip enter mb-10" style={delay(3)} aria-label="How it works">
          <li>
            <span className="n">1</span>
            <p className="mt-2 font-semibold">Create a workspace</p>
            <p className="mt-1 text-sm text-zinc-400">Point it at your product's Git folder.</p>
          </li>
          <li>
            <span className="n">2</span>
            <p className="mt-2 font-semibold">Ask for what you want</p>
            <p className="mt-1 text-sm text-zinc-400">Chat with one agent in Manual, or let the Brain plan a bigger job.</p>
          </li>
          <li>
            <span className="n">3</span>
            <p className="mt-2 font-semibold">Review, then keep</p>
            <p className="mt-1 text-sm text-zinc-400">Agents work on a copy. Your files change only when you click Keep.</p>
          </li>
        </ol>
      )}

      <section className="enter" style={delay(firstTime ? 4 : 3)}>
        <div className="mb-4 flex items-end justify-between gap-3">
          <h2 className="font-display text-xl font-bold tracking-tight">Workspaces</h2>
          {workspaces && workspaces.length > 0 && (
            <span className="text-sm text-zinc-500">{workspaces.length} product{workspaces.length > 1 ? "s" : ""}</span>
          )}
        </div>
        <div className="ws-grid">
          <button className="ws-new" onClick={() => setCreating(true)}>
            <span className="plus" aria-hidden="true">+</span>
            <span className="font-display text-lg font-bold text-zinc-100">New workspace</span>
            <span className="text-sm">Add a product from its repository folder</span>
          </button>
          {workspaces === null
            ? [0, 1].map((i) => <div key={i} className="ws-card animate-pulse" style={tint("#343e63")} />)
            : workspaces.map((ws) => (
                <button key={ws.id} className="ws-card" style={tint(wsColor(ws.color))} onClick={() => onOpen(ws)}>
                  <div className="flex items-center gap-2">
                    <span className="swatch" style={tint(wsColor(ws.color))} />
                    <span className="text-xs text-zinc-500">Active {timeAgoShort(ws.last_activity ?? ws.created_at)}</span>
                  </div>
                  <h3 className="mt-3">{ws.name}</h3>
                  <p className="mt-1 truncate font-mono text-xs text-zinc-500" title={ws.repo_path}>{ws.repo_path}</p>
                  <div className="mt-auto flex gap-4 pt-5 text-sm">
                    <span><span className="font-semibold text-zinc-100">{ws.manual_count}</span> <span className="text-zinc-500">manual</span></span>
                    <span><span className="font-semibold text-zinc-100">{ws.brain_count}</span> <span className="text-zinc-500">brain</span></span>
                  </div>
                </button>
              ))}
        </div>
      </section>

      <section className="enter mt-10" style={delay(firstTime ? 5 : 4)}>
        <button onClick={onOpenApps}
          className="group flex w-full flex-wrap items-center justify-between gap-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 px-5 py-4 text-left transition hover:border-zinc-600">
          <span>
            <span className="font-display text-lg font-bold">Apps</span>
            <span className="mt-0.5 block text-sm text-zinc-400">
              Use GitHub repos made for AI agents (a job search helper, a trading assistant…) through simple buttons.
            </span>
          </span>
          <span className="workspace-button group-hover:border-zinc-600">Open Apps</span>
        </button>
      </section>

      {creating && (
        <CreateWorkspaceDialog
          onClose={() => setCreating(false)}
          onCreated={(ws) => {
            setCreating(false);
            onOpen(ws);
          }}
        />
      )}
    </div>
  );
}

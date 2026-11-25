// Shows every agent AI-Hub knows about and whether it can be used.

import type { AgentStatus } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  agents: AgentStatus[];
  loading: boolean;
  error: string | null;
  testingId: string | null;
  onRefresh: () => void;
  onTest: (id: string) => void;
}

export function AgentsPanel({ agents, loading, error, testingId, onRefresh, onTest }: Props) {
  return (
    <section className="border-b border-zinc-800 px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Agents</h2>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="text-xs text-zinc-400 hover:text-zinc-100 disabled:opacity-50"
        >
          {loading ? "Checking..." : "Re-check"}
        </button>
      </div>

      {error && <p className="mb-2 rounded bg-red-500/10 px-2 py-1.5 text-xs text-red-300">{error}</p>}

      <ul className="space-y-2">
        {agents.map((agent) => {
          const canTest = agent.state !== "not_installed";
          return (
            <li key={agent.id} className="rounded-md bg-zinc-900 px-2.5 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{agent.name}</span>
                <StatusBadge state={agent.state} />
              </div>
              {agent.version && <p className="mt-0.5 text-[11px] text-zinc-500">{agent.version}</p>}
              {agent.help && <p className="mt-1 text-xs text-zinc-400">{agent.help}</p>}
              {canTest && agent.state !== "ready" && (
                <button
                  onClick={() => onTest(agent.id)}
                  disabled={testingId !== null}
                  className="mt-1.5 text-xs font-medium text-sky-400 hover:text-sky-300 disabled:opacity-50"
                >
                  {testingId === agent.id ? "Testing (sends a tiny prompt)..." : "Test connection"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

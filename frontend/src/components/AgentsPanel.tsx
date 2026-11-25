// Shows every agent AI-Hub knows about: whether it can be used, the plan it's
// signed in with, usage limits and credits (when the agent reports them), and
// models hidden after a "needs credits" failure.

import type { AccountInfo, AgentStatus } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  agents: AgentStatus[];
  loading: boolean;
  error: string | null;
  testingId: string | null;
  onRefresh: () => void;
  onTest: (id: string) => void;
  onUnhide: (id: string) => void;
}

export function AgentsPanel({ agents, loading, error, testingId, onRefresh, onTest, onUnhide }: Props) {
  return (
    <section className="border-b border-zinc-800 px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Agents</h2>
        <button
          onClick={onRefresh}
          disabled={loading}
          title="Check agents again and reload their models and usage"
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
              <p className="mt-0.5 text-[11px] text-zinc-500">
                {[agent.version, agent.account?.plan && `Plan: ${agent.account.plan}`,
                  agent.models.length > 0 && `${agent.models.length} models`]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {agent.account && <Account account={agent.account} />}
              {agent.help && <p className="mt-1 text-xs text-zinc-400">{agent.help}</p>}
              {agent.discovery_error && <p className="mt-1 text-[11px] text-amber-300/80">{agent.discovery_error}</p>}
              {agent.hidden_models.length > 0 && (
                <p className="mt-1 text-[11px] text-zinc-500">
                  Hidden (needs credits): {agent.hidden_models.join(", ")}{" "}
                  <button onClick={() => onUnhide(agent.id)} className="text-sky-400 hover:text-sky-300">
                    Show again
                  </button>
                </p>
              )}
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

function Account({ account }: { account: AccountInfo }) {
  const credits =
    account.unlimited_credits ? "Credits: unlimited"
      : account.has_credits === null || account.has_credits === undefined ? null
        : account.has_credits ? `Credits: ${account.credits_balance ?? "available"}`
          : "Credits: none";
  if (account.usage.length === 0 && !credits) return null;
  return (
    <div className="mt-1.5 space-y-1">
      {account.usage.map((u) => (
        <div key={u.label} title={u.resets_at ? `Resets ${new Date(u.resets_at).toLocaleString()}` : undefined}>
          <div className="flex justify-between text-[11px] text-zinc-400">
            <span>{u.label}</span>
            <span>{Math.round(u.used_percent)}% used</span>
          </div>
          <div className="h-1 overflow-hidden rounded bg-zinc-800">
            <div
              className={`h-full ${u.used_percent >= 90 ? "bg-red-500" : u.used_percent >= 70 ? "bg-amber-500" : "bg-sky-500"}`}
              style={{ width: `${Math.min(100, Math.max(0, u.used_percent))}%` }}
            />
          </div>
        </div>
      ))}
      {credits && <p className="text-[11px] text-zinc-400">{credits}</p>}
    </div>
  );
}

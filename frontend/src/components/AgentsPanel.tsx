// Shows every agent AI-Hub knows about: whether it can be used, the plan it's
// signed in with, usage limits and credits (when the agent reports them), and
// models hidden after a "needs credits" failure.

import type { AccountInfo, AgentStatus, UsageWindow } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  agents: AgentStatus[];
  loading: boolean;
  error: string | null;
  testingIds: Set<string>;
  onRefresh: () => void;
  onTest: (id: string) => void;
  onUnhide: (id: string) => void;
}

export function AgentsPanel({ agents, loading, error, testingIds, onRefresh, onTest, onUnhide }: Props) {
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
                  disabled={testingIds.has(agent.id)}
                  className="mt-1.5 text-xs font-medium text-sky-400 hover:text-sky-300 disabled:opacity-50"
                >
                  {testingIds.has(agent.id) ? "Testing (sends a tiny prompt)..." : "Test connection"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function UsageBar({ window: u }: { window: UsageWindow }) {
  const pct = u.used_percent;
  const resets = u.resets_at ? `resets ${formatReset(u.resets_at)}` : null;
  // Claude only reports the % near a limit; otherwise we show its status.
  const right =
    pct !== null ? `${Math.round(pct)}% used`
      : u.status === "ok" ? "OK"
        : u.status === "warning" ? "Near the limit"
          : u.status === "limit reached" ? "Limit reached"
            : "not reported yet";
  const color =
    u.status === "limit reached" || (pct ?? 0) >= 90 ? "bg-red-500"
      : u.status === "warning" || (pct ?? 0) >= 70 ? "bg-amber-500"
        : "bg-sky-500";
  return (
    <div title={u.updated_at ? `Updated ${new Date(u.updated_at).toLocaleString()}` : undefined}>
      <div className="flex justify-between gap-2 text-[11px] text-zinc-400">
        <span>{u.label}</span>
        <span className={u.status === "limit reached" ? "text-red-300" : u.status === "warning" ? "text-amber-300" : ""}>
          {right}
        </span>
      </div>
      {pct !== null && (
        <div className="h-1 overflow-hidden rounded bg-zinc-800">
          <div className={`h-full ${color}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
        </div>
      )}
      {resets && <p className="text-[10px] text-zinc-500">{resets}</p>}
    </div>
  );
}

/** "today 6:30 PM", "tomorrow 9:00 AM" or "Mon 9:00 AM". */
function formatReset(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (d.toDateString() === today.toDateString()) return `today ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
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
        <UsageBar key={u.label} window={u} />
      ))}
      {credits && <p className="text-[11px] text-zinc-400">{credits}</p>}
    </div>
  );
}

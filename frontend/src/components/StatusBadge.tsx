import type { AgentState, RunState } from "../api/types";

const STYLES: Record<AgentState | RunState, { label: string; className: string }> = {
  // agents
  ready: { label: "Ready", className: "bg-emerald-500/15 text-emerald-300" },
  installed: { label: "Installed", className: "bg-sky-500/15 text-sky-300" },
  not_signed_in: { label: "Not signed in", className: "bg-amber-500/15 text-amber-300" },
  not_installed: { label: "Not installed", className: "bg-zinc-700/40 text-zinc-400" },
  error: { label: "Error", className: "bg-red-500/15 text-red-300" },
  // runs
  pending: { label: "Pending", className: "bg-zinc-700/40 text-zinc-300" },
  running: { label: "Running", className: "bg-sky-500/15 text-sky-300" },
  succeeded: { label: "Succeeded", className: "bg-emerald-500/15 text-emerald-300" },
  failed: { label: "Failed", className: "bg-red-500/15 text-red-300" },
  cancelled: { label: "Cancelled", className: "bg-zinc-700/40 text-zinc-400" },
};

export function StatusBadge({ state }: { state: AgentState | RunState }) {
  const style = STYLES[state];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${style.className}`}>
      {state === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {style.label}
    </span>
  );
}

// The history of runs in the sidebar. Refreshes every few seconds so
// states (running -> succeeded) stay current without a page reload.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RunInfo } from "../api/types";
import { timeAgo } from "../lib/events";
import { StatusBadge } from "./StatusBadge";

interface Props {
  selectedId: string | null;
  refreshKey: number;
  onSelect: (id: string) => void;
  onNew: () => void;
}

const POLL_MS = 5000;

export function RunList({ selectedId, refreshKey, onSelect, onNew }: Props) {
  const [runs, setRuns] = useState<RunInfo[]>([]);

  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .runs()
        .then((r) => active && setRuns(r))
        .catch(() => {
          /* the Agents panel already shows backend errors */
        });
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refreshKey]);

  return (
    <section className="flex min-h-0 flex-1 flex-col px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Runs</h2>
        <button onClick={onNew} className="rounded bg-sky-600 px-2 py-0.5 text-xs font-medium hover:bg-sky-500">
          + New task
        </button>
      </div>

      {runs.length === 0 && <p className="text-xs text-zinc-500">No runs yet.</p>}

      <ul className="-mx-1 min-h-0 flex-1 space-y-1 overflow-y-auto">
        {runs.map((run) => (
          <li key={run.id}>
            <button
              onClick={() => onSelect(run.id)}
              className={`w-full rounded-md px-2 py-1.5 text-left hover:bg-zinc-800 ${
                run.id === selectedId ? "bg-zinc-800" : ""
              }`}
            >
              <p className="truncate text-sm">{run.prompt}</p>
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-zinc-500">
                <StatusBadge state={run.state} />
                <span>{run.agent_id}</span>
                {run.outcome && <span className={run.outcome === "kept" ? "text-emerald-400" : ""}>{run.outcome}</span>}
                <span>{timeAgo(run.created_at)}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

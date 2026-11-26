// The history of runs in the sidebar. Refreshes every few seconds so
// states (running -> succeeded) stay current without a page reload.
//
// "Select" turns on checkboxes for deleting several runs at once. Running
// runs can't be selected (cancel them first).

import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { RunInfo } from "../api/types";
import { timeAgo } from "../lib/events";
import { StatusBadge } from "./StatusBadge";

interface Props {
  selectedId: string | null;
  refreshKey: number;
  onSelect: (id: string) => void;
  onDeleted: (ids: string[]) => void;
}

const POLL_MS = 5000;
const isBusy = (run: RunInfo) => run.state === "running" || run.state === "pending";

export function RunList({ selectedId, refreshKey, onSelect, onDeleted }: Props) {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selecting, setSelecting] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

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
  }, [refreshKey, reload]);

  const selectable = runs.filter((r) => !isBusy(r));
  const allChecked = selectable.length > 0 && selectable.every((r) => checked.has(r.id));
  const keptCount = runs.filter((r) => checked.has(r.id) && r.outcome === "kept").length;

  function toggle(id: string) {
    setConfirming(false);
    setChecked((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function stopSelecting() {
    setSelecting(false);
    setChecked(new Set());
    setConfirming(false);
  }

  async function deleteChecked() {
    setDeleting(true);
    setMessage(null);
    try {
      const { deleted, failed } = await api.bulkDelete([...checked]);
      onDeleted(deleted);
      setMessage(failed.length ? `${failed.length} couldn't be deleted: ${failed[0].error}` : null);
      stopSelecting();
      setReload((r) => r + 1);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col px-3 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Runs</h2>
        <div className="flex items-center gap-2">
          {runs.length > 0 && (
            <button
              onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
              className="text-xs text-zinc-400 hover:text-zinc-100"
            >
              {selecting ? "Done" : "Select"}
            </button>
          )}
        </div>
      </div>

      {selecting && (
        <div className="mb-2 space-y-1.5 rounded-md bg-zinc-900 px-2 py-1.5 text-xs">
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-1.5 text-zinc-400">
              <input
                type="checkbox"
                checked={allChecked}
                onChange={() => setChecked(allChecked ? new Set() : new Set(selectable.map((r) => r.id)))}
                className="accent-sky-500"
              />
              Select all
            </label>
            <button
              onClick={() => setConfirming(true)}
              disabled={checked.size === 0 || deleting}
              className="rounded px-2 py-0.5 font-medium text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            >
              Delete {checked.size || ""}
            </button>
          </div>
          {confirming && (
            <div className="space-y-1.5 border-t border-zinc-800 pt-1.5">
              <p className="text-zinc-400">
                Delete {checked.size} run{checked.size === 1 ? "" : "s"}? Changes that weren't kept are deleted too.
                {keptCount > 0 && ` Kept branches (${keptCount}) stay in git.`}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={deleteChecked}
                  disabled={deleting}
                  className="rounded bg-red-600 px-2 py-0.5 font-medium hover:bg-red-500 disabled:opacity-50"
                >
                  {deleting ? "Deleting..." : "Yes, delete"}
                </button>
                <button onClick={() => setConfirming(false)} className="text-zinc-400 hover:text-zinc-100">
                  No
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {message && <p className="mb-2 rounded bg-red-500/10 px-2 py-1 text-xs text-red-300">{message}</p>}
      {runs.length === 0 && <p className="history-empty">No runs yet.</p>}

      <ul className="-mx-1 min-h-0 flex-1 space-y-1 overflow-y-auto">
        {runs.map((run) => (
          <li key={run.id} className="flex items-start gap-1">
            {selecting && (
              <input
                type="checkbox"
                aria-label={`Select ${run.prompt}`}
                checked={checked.has(run.id)}
                disabled={isBusy(run)}
                title={isBusy(run) ? "Running: cancel it first" : undefined}
                onChange={() => toggle(run.id)}
                className="mt-2.5 ml-1 accent-sky-500"
              />
            )}
            <button
              aria-current={run.id === selectedId ? "page" : undefined}
              onClick={() => (selecting && !isBusy(run) ? toggle(run.id) : onSelect(run.id))}
              className={`min-w-0 flex-1 rounded-md px-2 py-1.5 text-left hover:bg-zinc-800 ${
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

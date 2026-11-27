// Sidebar in Brain mode: "+ New task" and the Brain tasks.

import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { BrainTask } from "../../api/types";

const RUN_LABEL: Record<string, string> = {
  running: "Running…",
  waiting: "Waiting for you",
  done: "Done, ready to review",
  stopped: "Stopped",
  failed: "Needs a look",
  kept: "Kept",
};

const STATE_LABEL: Record<BrainTask["state"], string> = {
  direct: "Run directly",
  asking: "Questions for you",
  planned: "Plan ready",
};

interface Props {
  workspaceId: string;
  selectedId: string | null;
  creating: boolean;
  refreshKey: number;
  onNew: () => void;
  onSelect: (id: string) => void;
}

export function BrainSidebar({ workspaceId, selectedId, creating, refreshKey, onNew, onSelect }: Props) {
  const [tasks, setTasks] = useState<BrainTask[] | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => api.brainTasks(workspaceId).then((t) => active && setTasks(t)).catch(() => active && setTasks([]));
    void load();
    const timer = setInterval(load, 4000); // plans running in the background change state
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refreshKey, workspaceId]);

  return (
    <>
      <button className="new-task-button" aria-current={creating ? "page" : undefined} onClick={onNew}>
        <span aria-hidden="true">+</span> New Brain task
      </button>
      <section className="flex min-h-0 flex-1 flex-col px-3 py-3">
        <h2 className="sidebar-heading">Brain tasks</h2>
        {tasks === null ? (
          <p className="px-1 text-xs text-zinc-500">Loading...</p>
        ) : tasks.length === 0 ? (
          <p className="history-empty">No Brain tasks here yet. Describe a job and the Brain decides how to split it across your agents.</p>
        ) : (
          <ul className="space-y-1 overflow-y-auto">
            {tasks.map((t) => (
              <li key={t.id}>
                <button
                  onClick={() => onSelect(t.id)}
                  aria-current={t.id === selectedId ? "page" : undefined}
                  className="chat-item"
                >
                  <span className="block truncate text-sm font-medium">{t.request.split("\n")[0]}</span>
                  <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                    {t.execution && t.execution.state !== "discarded" ? (
                      <>
                        <span className="live-dot" data-live={t.execution.state === "running"}
                          style={{ ["--dot" as string]: t.execution.state === "waiting" ? "#f2b25a" : t.execution.state === "running" ? "var(--color-brain)" : t.execution.state === "done" || t.execution.state === "kept" ? "#4ade9b" : "#7f89ab" }} />
                        {RUN_LABEL[t.execution.state] ?? t.execution.state}
                      </>
                    ) : (
                      <>
                        {STATE_LABEL[t.state]}
                        {t.plan && t.state === "planned" && ` · ${t.plan.steps.length} steps`}
                      </>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

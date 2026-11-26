// Sidebar in Brain mode: "+ New task" and the Brain tasks.

import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { BrainTask } from "../../api/types";

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
    api.brainTasks(workspaceId).then(setTasks).catch(() => setTasks([]));
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
                  <span className="block text-xs text-zinc-500">
                    {STATE_LABEL[t.state]}
                    {t.plan && t.state === "planned" && ` · ${t.plan.steps.length} steps`}
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

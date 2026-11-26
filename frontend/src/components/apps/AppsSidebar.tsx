// Sidebar in Apps mode: "+ Connect a repo" and the connected apps.

import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AppInfo } from "../../api/types";

interface Props {
  selectedId: string | null;
  connecting: boolean;
  refreshKey: number;
  onConnect: () => void;
  onSelect: (id: string) => void;
}

export function AppsSidebar({ selectedId, connecting, refreshKey, onConnect, onSelect }: Props) {
  const [apps, setApps] = useState<AppInfo[] | null>(null);

  useEffect(() => {
    api.apps().then(setApps).catch(() => setApps([]));
  }, [refreshKey]);

  return (
    <>
      <button className="new-task-button" aria-current={connecting ? "page" : undefined} onClick={onConnect}>
        <span aria-hidden="true">+</span> Connect a repo
      </button>
      <section className="flex min-h-0 flex-1 flex-col px-3 py-3">
        <h2 className="sidebar-heading">Your apps</h2>
        {apps === null ? (
          <p className="px-1 text-xs text-zinc-500">Loading...</p>
        ) : apps.length === 0 ? (
          <p className="history-empty">No apps yet. Connect a GitHub repo to turn its commands into buttons.</p>
        ) : (
          <ul className="space-y-1 overflow-y-auto">
            {apps.map((app) => {
              const missing = app.manifest.requirements.filter((r) => !r.installed && !r.optional).length;
              return (
                <li key={app.id}>
                  <button
                    onClick={() => onSelect(app.id)}
                    aria-current={app.id === selectedId ? "page" : undefined}
                    className={`w-full rounded-md px-2.5 py-2 text-left ${
                      app.id === selectedId ? "bg-zinc-800" : "hover:bg-zinc-900"
                    }`}
                  >
                    <span className="block truncate text-sm font-medium">{app.name}</span>
                    <span className="block text-xs text-zinc-500">
                      {app.manifest.commands.length} actions
                      {missing > 0 && <span className="text-amber-400"> · {missing} tool{missing > 1 ? "s" : ""} missing</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}

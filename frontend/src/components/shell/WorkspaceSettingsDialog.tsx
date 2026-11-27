// Rename a workspace, change its color or test command, or delete it (only
// when it has no chats; the repo folder itself is never touched).

import { useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { MemoryNote, Workspace, WorkspaceColor } from "../../api/types";
import { WORKSPACE_COLORS } from "../../lib/theme";

interface Props {
  workspace: Workspace;
  onClose: () => void;
  onSaved: (ws: Workspace) => void;
  onDeleted: () => void;
}

export function WorkspaceSettingsDialog({ workspace, onClose, onSaved, onDeleted }: Props) {
  const [name, setName] = useState(workspace.name);
  const [testCommand, setTestCommand] = useState(workspace.test_command ?? "");
  const [color, setColor] = useState<WorkspaceColor>(workspace.color);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<MemoryNote[] | null>(null);

  useEffect(() => {
    api.workspaceNotes(workspace.id).then(setNotes).catch(() => setNotes([]));
  }, [workspace.id]);

  async function forget(noteId: string) {
    try {
      await api.deleteWorkspaceNote(workspace.id, noteId);
      setNotes((list) => (list ?? []).filter((n) => n.id !== noteId));
    } catch (err) {
      setError(errorText(err));
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.editWorkspace(workspace.id, { name, test_command: testCommand, color }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteWorkspace(workspace.id);
      onDeleted();
    } catch (err) {
      setError(errorText(err));
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog" onSubmit={save} role="dialog" aria-modal="true" aria-labelledby="ws-settings-title">
        <div className="space-y-1 px-6 pt-6">
          <h2 id="ws-settings-title" className="font-display text-2xl font-bold tracking-tight">Workspace settings</h2>
          <p className="break-all font-mono text-xs text-zinc-500">{workspace.repo_path}</p>
        </div>
        <div className="space-y-4 px-6 py-5">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} className={input} />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Test command</span>
            <input value={testCommand} onChange={(e) => setTestCommand(e.target.value)} placeholder="pytest -q"
              className={`${input} font-mono`} />
            <span className="block text-xs text-zinc-500">New chats in this workspace use it to check the agents' work.</span>
          </label>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Color</legend>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(WORKSPACE_COLORS) as WorkspaceColor[]).map((c) => (
                <button key={c} type="button" onClick={() => setColor(c)} aria-label={c} aria-pressed={color === c}
                  className={`h-8 w-8 rounded-lg border-2 transition ${color === c ? "scale-110 border-white" : "border-transparent hover:scale-105"}`}>
                  <span className="block h-full w-full rounded-md" style={{ background: WORKSPACE_COLORS[c] }} />
                </button>
              ))}
            </div>
          </fieldset>
          <div className="space-y-2">
            <p className="text-sm font-medium">Project memory</p>
            <p className="text-xs text-zinc-500">
              What agents learned about this repo in Brain tasks you kept. New steps get the relevant notes, so they explore less.
            </p>
            {notes === null ? null : notes.length === 0 ? (
              <p className="text-xs text-zinc-500">Nothing yet.</p>
            ) : (
              <ul className="max-h-48 space-y-1.5 overflow-y-auto pr-1">
                {notes.map((n) => (
                  <li key={n.id} className="flex items-start gap-2 rounded-lg bg-zinc-950 px-3 py-2 text-xs">
                    <span className="min-w-0 flex-1">
                      {n.text}
                      {n.stale_files.length > 0 && (
                        <span className="mt-0.5 block text-amber-300/90">Maybe outdated: {n.stale_files.join(", ")} changed since.</span>
                      )}
                    </span>
                    <button type="button" onClick={() => forget(n.id)} className="text-zinc-500 hover:text-red-300" title="Forget this note">✕</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {error && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-zinc-800 px-6 py-4">
          {confirmDelete ? (
            <span className="flex items-center gap-2 text-xs text-zinc-400">
              Delete this workspace? Your folder stays.
              <button type="button" onClick={remove} disabled={busy} className="rounded-md bg-red-900/60 px-2.5 py-1 text-red-200 hover:bg-red-900">Delete</button>
              <button type="button" onClick={() => setConfirmDelete(false)} className="px-1 hover:text-zinc-200">Keep it</button>
            </span>
          ) : (
            <button type="button" onClick={() => setConfirmDelete(true)} className="text-xs text-zinc-500 hover:text-red-300">
              Delete workspace
            </button>
          )}
          <span className="ml-auto flex gap-2">
            <button type="button" onClick={onClose} className="ghost-button border-transparent">Cancel</button>
            <button type="submit" disabled={busy || !name.trim()} className="run-button">Save changes</button>
          </span>
        </div>
      </form>
    </div>
  );
}

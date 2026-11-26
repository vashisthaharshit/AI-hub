// Create a workspace: pick the product's repo folder, name it, pick a color.

import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../api/client";
import type { Workspace, WorkspaceColor } from "../../api/types";
import { WORKSPACE_COLORS, tint } from "../../lib/theme";

interface Props {
  onClose: () => void;
  onCreated: (ws: Workspace) => void;
}

const folderName = (path: string) => path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";

export function CreateWorkspaceDialog({ onClose, onCreated }: Props) {
  const [repoPath, setRepoPath] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [testCommand, setTestCommand] = useState("");
  const [color, setColor] = useState<WorkspaceColor | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function setFolder(path: string) {
    setRepoPath(path);
    if (!nameTouched) setName(folderName(path));
  }

  async function browse() {
    try {
      const { path } = await api.pickFolder(repoPath, "Choose your product's repository folder");
      if (path) setFolder(path);
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function create(e?: React.FormEvent) {
    e?.preventDefault();
    if (!repoPath.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.createWorkspace({
        repo_path: repoPath.trim(), name: name.trim() || null, test_command: testCommand.trim() || null, color,
      }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm transition focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog" onSubmit={create} role="dialog" aria-modal="true" aria-labelledby="new-ws-title">
        <div className="space-y-1.5 px-6 pt-6">
          <h2 id="new-ws-title" className="font-display text-2xl font-bold tracking-tight">New workspace</h2>
          <p className="text-sm leading-relaxed text-zinc-400">
            A workspace is one product. Point it at the product's Git folder; every chat you start inside it works there.
          </p>
        </div>

        <div className="space-y-4 px-6 py-5">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Repository folder</span>
            <div className="flex gap-2">
              <input ref={first} value={repoPath} onChange={(e) => setFolder(e.target.value)}
                placeholder="D:\projects\my-shop" className={`${input} font-mono`} />
              <button type="button" onClick={browse} className="workspace-button shrink-0">Browse…</button>
            </div>
            <span className="block text-xs text-zinc-500">Must be a Git repo with at least one commit.</span>
          </label>

          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Name</span>
            <input value={name} onChange={(e) => { setName(e.target.value); setNameTouched(true); }}
              placeholder="My shop" className={input} />
          </label>

          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Test command <span className="font-normal text-zinc-500">(optional)</span></span>
            <input value={testCommand} onChange={(e) => setTestCommand(e.target.value)}
              placeholder="pytest -q   or   npm test" className={`${input} font-mono`} />
            <span className="block text-xs text-zinc-500">AI-Hub runs it to check the agents' work.</span>
          </label>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Color</legend>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(WORKSPACE_COLORS) as WorkspaceColor[]).map((c) => (
                <button key={c} type="button" onClick={() => setColor(c)} aria-label={c} aria-pressed={color === c}
                  style={tint(WORKSPACE_COLORS[c])}
                  className={`h-8 w-8 rounded-lg border-2 transition ${color === c ? "scale-110 border-white" : "border-transparent hover:scale-105"}`}>
                  <span className="block h-full w-full rounded-md" style={{ background: WORKSPACE_COLORS[c], boxShadow: `0 0 14px -2px ${WORKSPACE_COLORS[c]}` }} />
                </button>
              ))}
            </div>
          </fieldset>

          {error && <p role="alert" className="whitespace-pre-wrap rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-800 px-6 py-4">
          <button type="button" onClick={onClose} className="ghost-button border-transparent">Cancel</button>
          <button type="submit" disabled={!repoPath.trim() || busy} className="run-button">
            {busy ? "Creating..." : "Create workspace"}
          </button>
        </div>
      </form>
    </div>
  );
}

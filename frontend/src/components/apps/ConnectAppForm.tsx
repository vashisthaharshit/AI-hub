// "Connect a repo": paste a GitHub link (AI-Hub downloads it), or pick a
// folder that's already on this computer.

import { useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AppInfo } from "../../api/types";

export function ConnectAppForm({ onConnected }: { onConnected: (app: AppInfo) => void }) {
  const [source, setSource] = useState<"link" | "folder">("link");
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState("");
  const [defaultFolder, setDefaultFolder] = useState("");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  // The real Windows folder window. It opens on top of the browser.
  async function browse(current: string, title: string, set: (path: string) => void) {
    setPicking(true);
    setError(null);
    try {
      const { path } = await api.pickFolder(current, title);
      if (path) set(path);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPicking(false);
    }
  }

  const browseButton = (current: string, title: string, set: (path: string) => void) => (
    <button
      type="button"
      onClick={() => browse(current, title, set)}
      disabled={picking || busy}
      className="shrink-0 rounded-md border border-zinc-700 px-3 py-2 text-sm text-zinc-200 hover:border-zinc-500 disabled:opacity-50"
    >
      {picking ? "Choose in the window..." : "Browse…"}
    </button>
  );

  useEffect(() => {
    api.appsDefaultFolder().then(({ folder }) => {
      setDefaultFolder(folder);
      setFolder(folder);
    }).catch(() => {});
  }, []);

  const ready = source === "link" ? url.trim() !== "" : path.trim() !== "";

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const app = await api.connectApp(
        source === "link"
          ? { url: url.trim(), folder: folder.trim() && folder.trim() !== defaultFolder ? folder.trim() : null }
          : { path: path.trim() },
      );
      onConnected(app);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const inputClass = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm";

  return (
    <div className="task-page">
      <div className="task-intro">
        <h2>Connect a repo</h2>
        <p>
          Some GitHub repos are made to be used through an AI agent, like{" "}
          <span className="text-zinc-200">ai-job-search</span>. AI-Hub downloads the repo, finds its commands and
          turns them into buttons, so you don't have to type them in a terminal.
        </p>
      </div>

      <form onSubmit={connect} className="task-composer">
        <div className="task-fields">
          <div className="flex gap-2" role="tablist" aria-label="Where the repo comes from">
            {(["link", "folder"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={source === s}
                onClick={() => setSource(s)}
                className={`rounded-md border px-3 py-1.5 text-sm ${
                  source === s ? "border-sky-600 bg-sky-500/10 text-sky-200" : "border-zinc-700 text-zinc-400 hover:text-zinc-100"
                }`}
              >
                {s === "link" ? "GitHub link" : "Folder on this computer"}
              </button>
            ))}
          </div>

          {source === "link" ? (
            <>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">GitHub link</span>
                <input
                  autoFocus
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://github.com/ai-job-search-dev/ai-job-search"
                  className={inputClass}
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Download into</span>
                <div className="flex gap-2">
                  <input value={folder} onChange={(e) => setFolder(e.target.value)} className={inputClass} />
                  {browseButton(folder || defaultFolder, "Where should the repo be downloaded?", setFolder)}
                </div>
                <span className="text-xs text-zinc-500">
                  The repo gets its own folder inside this one.
                  {defaultFolder && folder !== defaultFolder && (
                    <>
                      {" "}
                      <button type="button" className="text-sky-300 hover:underline" onClick={() => setFolder(defaultFolder)}>
                        Use the default ({defaultFolder})
                      </button>
                    </>
                  )}
                </span>
              </label>
            </>
          ) : (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Folder</span>
              <div className="flex gap-2">
                <input
                  autoFocus
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="C:\Users\you\projects\my-repo"
                  className={inputClass}
                />
                {browseButton(path, "Choose the repo's folder", setPath)}
              </div>
              <span className="text-xs text-zinc-500">A repo you already downloaded. AI-Hub uses it where it is.</span>
            </label>
          )}

          <p className="text-xs text-zinc-500">
            🔒 Uploading to GitHub (git push) is switched off for repos AI-Hub downloads, so personal files you add,
            like your CV, stay on this computer.
          </p>
          {error && <p className="rounded-md border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>
        <div className="task-footer">
          <span className="keyboard-hint">{busy ? "Downloading the repo. This can take a minute..." : ""}</span>
          <button type="submit" className="run-button" disabled={!ready || busy}>
            {busy ? "Connecting..." : "Connect"}
          </button>
        </div>
      </form>
    </div>
  );
}

// "Update repo": look on GitHub for a newer version of the app, and get it.
// Before anything changes, the backend copies the user's changed and added
// files to a backup folder and keeps the old version (see backend apps/update.py).

import { useState } from "react";
import { api, errorText } from "../../api/client";
import type { AppInfo, AppUpdateCheck, AppUpdateResult } from "../../api/types";

interface Props {
  appId: string;
  busy: boolean; // the app is running something: can't update now
  onUpdated: (app: AppInfo) => void;
}

export function UpdatePanel({ appId, busy, onUpdated }: Props) {
  const [check, setCheck] = useState<AppUpdateCheck | null>(null);
  const [result, setResult] = useState<AppUpdateResult | null>(null);
  const [working, setWorking] = useState<"check" | "update" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function look() {
    setWorking("check");
    setError(null);
    setResult(null);
    try {
      setCheck(await api.checkAppUpdates(appId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWorking(null);
    }
  }

  async function update() {
    setWorking("update");
    setError(null);
    try {
      const r = await api.updateApp(appId);
      setResult(r);
      setCheck(null);
      if (r.updated) onUpdated(r.app);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWorking(null);
    }
  }

  return (
    <section className="space-y-3 rounded-lg border border-zinc-800 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Update this app</h3>
          <p className="text-xs text-zinc-500">
            Gets the newest version of the repo from GitHub. Your own files are backed up first and kept.
          </p>
        </div>
        <button onClick={look} disabled={working !== null} className="workspace-button text-xs">
          {working === "check" ? "Looking..." : "Check for updates"}
        </button>
      </div>

      {check && check.behind === 0 && check.ahead === 0 && (
        <p className="text-sm text-emerald-300">You have the newest version.</p>
      )}

      {check && check.ahead > 0 && (
        <p className="text-sm text-amber-300">
          Your copy has {check.ahead} saved change{check.ahead > 1 ? "s" : ""} of its own (commits) that aren't on
          GitHub{check.behind > 0 ? `, and GitHub has ${check.behind} new one${check.behind > 1 ? "s" : ""}` : ""}. Putting
          them together needs a merge, so AI-Hub won't do it with a button. You can do it in Manual mode or with Git.
        </p>
      )}

      {check && check.can_update && (
        <div className="space-y-2">
          <p className="text-sm">
            {check.behind} new change{check.behind > 1 ? "s" : ""} on GitHub:
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-zinc-300">
            {check.changes.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
            {check.behind > check.changes.length && <li className="text-zinc-500">and {check.behind - check.changes.length} more</li>}
          </ul>
          <p className="text-xs text-zinc-500">
            {check.local_changes > 0
              ? `${check.local_changes} file${check.local_changes > 1 ? "s" : ""} you changed or added will be backed up first, then kept as they are.`
              : "You haven't changed any of the repo's files, so nothing of yours is affected."}
          </p>
          <button
            onClick={update}
            disabled={working !== null || busy}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            title={busy ? "Wait for the current run to finish" : undefined}
          >
            {working === "update" ? "Updating..." : busy ? "Wait for the run to finish" : "Update now"}
          </button>
        </div>
      )}

      {result && (
        <div className="space-y-1 text-sm">
          <p className={result.conflicts.length ? "text-amber-300" : "text-emerald-300"}>{result.message}</p>
          {result.conflicts.length > 0 && (
            <ul className="list-disc pl-5 font-mono text-xs text-amber-200">
              {result.conflicts.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
          {result.backup && (
            <p className="break-all text-xs text-zinc-500">
              Backup: <span className="font-mono text-zinc-400">{result.backup}</span>
            </p>
          )}
        </div>
      )}

      {error && <p className="whitespace-pre-wrap text-sm text-red-300">{error}</p>}
    </section>
  );
}

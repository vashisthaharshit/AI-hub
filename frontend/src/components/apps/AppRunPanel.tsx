// One run of an app's command: what the agent is doing (live), Allow / Deny
// when it needs permission, a reply box when it asks you something, and
// "What it made": the files it created or changed, with Open buttons.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../api/client";
import type { OutputFile, RunInfo } from "../../api/types";
import { useRunStream } from "../../hooks/useRunStream";
import { groupEvents, pendingApprovals } from "../../lib/events";
import { ActivityItem } from "../ActivityItem";
import { StatusBadge } from "../StatusBadge";

const AGENT_NAMES: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };

interface Props {
  appId: string;
  runId: string;
  onChanged: () => void; // refresh the runs list
}

export function AppRunPanel({ appId, runId, onChanged }: Props) {
  const [generation, setGeneration] = useState(0); // bump to reconnect after a reply
  const { events, finalRun, status } = useRunStream(runId, generation);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.run(runId).then(setRun).catch((e) => setError(errorText(e)));
  }, [runId]);
  useEffect(() => {
    if (finalRun) {
      setRun(finalRun);
      onChanged();
    }
  }, [finalRun, onChanged]);

  const items = useMemo(() => groupEvents(events), [events]);
  const running = status !== "ended" && (run?.state === "running" || run?.state === "pending" || !run);
  const waiting = pendingApprovals(items).length > 0;
  const outputs = mergeOutputs(items);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [items.length]);

  async function sendReply() {
    if (!reply.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setRun(await api.followUp(runId, reply.trim(), null, null, null));
      setReply("");
      setGeneration((g) => g + 1);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    try {
      await api.cancel(runId);
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <section className="space-y-4 rounded-lg border border-zinc-800 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{run?.label ?? "Run"}</p>
          <p className="truncate font-mono text-[11px] text-zinc-500">
            {run && `${AGENT_NAMES[run.agent_id] ?? run.agent_id} · ${run.prompt.split("\n")[0].slice(0, 120)}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {run && <StatusBadge state={running ? "running" : run.state} />}
          {running && (
            <button onClick={stop} className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs hover:border-red-500 hover:text-red-300">
              Stop
            </button>
          )}
        </div>
      </div>

      {waiting && (
        <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          The agent is waiting for you: check the request below and click Allow or Deny.
        </p>
      )}

      <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
        {items.map((item) => (
          <ActivityItem key={item.seq} item={item} runId={runId} active={running} agentNames={AGENT_NAMES} />
        ))}
        {running && !waiting && <p className="text-xs text-sky-300">Working...</p>}
        <div ref={bottom} />
      </div>

      {outputs.length > 0 && <Outputs appId={appId} files={outputs} />}

      {!running && run && (
        <div className="space-y-2 border-t border-zinc-800 pt-3">
          <p className="text-xs text-zinc-400">
            {run.state === "succeeded"
              ? "Did it ask you something, or do you want a change? Reply here and it continues where it left off."
              : "Something went wrong. You can reply to try again or give it more details."}
          </p>
          <textarea
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void sendReply();
            }}
            rows={3}
            placeholder="Type your answer..."
            className="min-h-20 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={sendReply}
              disabled={busy || !reply.trim()}
              className="rounded-md bg-sky-300 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-sky-200 disabled:opacity-40"
            >
              {busy ? "Sending..." : "Send"}
            </button>
            <span className="text-[11px] text-zinc-500">Ctrl+Enter</span>
          </div>
        </div>
      )}
      {error && <p className="text-xs text-red-300">{error}</p>}
    </section>
  );
}

/** Files from every turn: the newest status of each path wins. */
function mergeOutputs(items: ReturnType<typeof groupEvents>): OutputFile[] {
  const byPath = new Map<string, OutputFile>();
  for (const item of items) {
    if (item.type !== "outputs") continue;
    for (const f of item.files) {
      const old = byPath.get(f.path);
      byPath.set(f.path, old?.status === "new" ? { ...f, status: "new" } : f);
    }
  }
  return [...byPath.values()];
}

function Outputs({ appId, files }: { appId: string; files: OutputFile[] }) {
  const [error, setError] = useState<string | null>(null);
  const open = (path: string, reveal: boolean) =>
    api.openAppFile(appId, path, reveal).catch((e) => setError(errorText(e)));
  const name = (path: string) => path.split("/").pop() ?? path;

  return (
    <div className="space-y-1.5 rounded-md border border-emerald-900/60 bg-emerald-950/20 p-3">
      <p className="text-xs font-semibold text-emerald-300">What it made</p>
      <ul className="space-y-1">
        {files.map((f) => (
          <li key={f.path} className="flex flex-wrap items-center gap-2 text-xs">
            <span className={f.status === "new" ? "text-emerald-300" : "text-sky-300"}>{f.status === "new" ? "New" : "Updated"}</span>
            <span className="text-zinc-200" title={f.path}>{name(f.path)}</span>
            <span className="truncate text-[11px] text-zinc-500">{f.path}</span>
            <span className="ml-auto flex gap-2">
              <button onClick={() => open(f.path, false)} className="text-sky-300 hover:underline">Open</button>
              <button onClick={() => open(f.path, true)} className="text-zinc-400 hover:underline">Show in folder</button>
            </span>
          </li>
        ))}
      </ul>
      {error && <p className="text-[11px] text-red-300">{error}</p>}
    </div>
  );
}

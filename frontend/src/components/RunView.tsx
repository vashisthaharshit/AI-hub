// One run: header (task, agent, state, actions), then two tabs:
//   Activity: live events, approvals, and a box for follow-up messages
//   Changes:  side-by-side diff of what the agent changed
//
// Actions depend on the run's situation:
//   running              -> Cancel
//   finished, untouched  -> Keep / Discard, and follow-up messages
//   kept or discarded    -> read-only history

import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus, RunInfo } from "../api/types";
import { useRunStream } from "../hooks/useRunStream";
import { groupEvents, pendingApprovals } from "../lib/events";
import { ActivityItem } from "./ActivityItem";
import { StatusBadge } from "./StatusBadge";

// Lazy: Monaco is only downloaded when the Changes tab is first opened.
const DiffViewer = lazy(() => import("./DiffViewer"));

type Tab = "activity" | "changes";

interface Props {
  runId: string;
  agents: AgentStatus[];
  onChanged: () => void; // tell the sidebar to refresh
}

export function RunView({ runId, agents, onChanged }: Props) {
  const [run, setRun] = useState<RunInfo | null>(null);
  const [tab, setTab] = useState<Tab>("activity");
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0); // +1 per follow-up: reconnects the stream
  const { events, finalRun, status } = useRunStream(runId, generation);
  const items = useMemo(() => groupEvents(events), [events]);
  const toolResults = useMemo(() => events.filter((e) => e.kind === "tool_result").length, [events]);

  useEffect(() => {
    api.run(runId).then(setRun).catch((e) => setError(errorText(e)));
  }, [runId]);

  // When a turn ends, the stream sends the run's final state.
  useEffect(() => {
    if (finalRun) {
      setRun(finalRun);
      onChanged();
    }
  }, [finalRun, onChanged]);

  // Auto-scroll to new events, but only if the user is already near the bottom.
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
      el.scrollTop = el.scrollHeight;
    }
  }, [items.length]);

  const state = run?.state ?? "running";
  const isRunning = state === "running" || state === "pending";
  const idle = Boolean(run) && !isRunning && !run?.outcome;
  const waitingForMe = isRunning && pendingApprovals(items).length > 0;
  const supportsFollowUp = Boolean(agents.find((a) => a.id === run?.agent_id)?.supports_follow_up);

  async function act(action: () => Promise<RunInfo | unknown>) {
    setError(null);
    try {
      const updated = await action();
      if (updated && typeof updated === "object" && "id" in updated) setRun(updated as RunInfo);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function sendFollowUp(prompt: string) {
    setError(null);
    try {
      setRun(await api.followUp(runId, prompt));
      setGeneration((g) => g + 1); // the previous turn's socket closed; open a new one
      onChanged();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-b border-zinc-800 px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="line-clamp-2 text-base font-medium">{run?.prompt ?? "Loading..."}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
              <StatusBadge state={state} />
              {run && <span>{run.agent_id}</span>}
              {run?.supervised && <span className="text-amber-300">supervised</span>}
              {run?.branch && <span className="font-mono">{run.branch}</span>}
              {waitingForMe && <span className="font-medium text-amber-300">needs your approval</span>}
              {status === "connecting" && isRunning && <span>connecting...</span>}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {isRunning && (
              <button onClick={() => act(() => api.cancel(runId))} className={BUTTON_DANGER}>
                Cancel
              </button>
            )}
            {idle && <KeepDiscard onKeep={() => act(() => api.keep(runId))} onDiscard={() => act(() => api.discard(runId))} />}
          </div>
        </div>

        {run?.outcome && <OutcomeBanner run={run} />}

        <nav className="mt-4 flex gap-4 text-sm">
          {(["activity", "changes"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`-mb-4 border-b-2 pb-2 capitalize ${
                tab === t ? "border-sky-500 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
      </header>

      {error && <p className="mx-6 mt-3 rounded bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

      {tab === "activity" ? (
        <>
          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <div className="mx-auto max-w-3xl space-y-3">
              {items.length === 0 && <p className="text-sm text-zinc-500">Waiting for the agent...</p>}
              {items.map((item) => (
                <ActivityItem key={item.seq} item={item} runId={runId} active={isRunning} />
              ))}
              {isRunning && items.length > 0 && <p className="text-xs text-zinc-500">Working...</p>}
            </div>
          </div>
          {idle && supportsFollowUp && <FollowUpBox onSend={sendFollowUp} />}
        </>
      ) : (
        <div className="min-h-0 flex-1">
          <Suspense fallback={<p className="p-6 text-sm text-zinc-500">Loading diff viewer...</p>}>
            {/* Files only change when a tool finishes, so refresh the diff on each tool result. */}
            <DiffViewer runId={runId} reloadKey={`${state}-${toolResults}-${run?.outcome ?? ""}`} />
          </Suspense>
        </div>
      )}
    </div>
  );
}

const BUTTON = "rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium";
const BUTTON_DANGER = `${BUTTON} hover:border-red-500 hover:text-red-300`;

function KeepDiscard({ onKeep, onDiscard }: { onKeep: () => void; onDiscard: () => void }) {
  // Discard deletes work, so it asks once more (inline, no browser popup).
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-zinc-400">Delete all changes and the branch?</span>
        <button onClick={onDiscard} className="rounded-md bg-red-600 px-3 py-1.5 font-medium hover:bg-red-500">
          Yes, discard
        </button>
        <button onClick={() => setConfirming(false)} className={BUTTON}>
          No
        </button>
      </div>
    );
  }
  return (
    <>
      <button onClick={onKeep} className={`${BUTTON} border-emerald-700 text-emerald-300 hover:bg-emerald-500/10`}>
        Keep
      </button>
      <button onClick={() => setConfirming(true)} className={BUTTON_DANGER}>
        Discard
      </button>
    </>
  );
}

function OutcomeBanner({ run }: { run: RunInfo }) {
  if (run.outcome === "kept") {
    return (
      <p className="mt-3 rounded-md bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
        Kept on branch <span className="font-mono">{run.branch}</span>. Merge it into your current branch with:{" "}
        <code className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono">git merge {run.branch}</code>
      </p>
    );
  }
  return (
    <p className="mt-3 rounded-md bg-zinc-800/60 px-3 py-2 text-xs text-zinc-400">
      Discarded. The worktree and branch were deleted.
    </p>
  );
}

function FollowUpBox({ onSend }: { onSend: (prompt: string) => Promise<boolean> }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);

  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    if (await onSend(text.trim())) setText("");
    setSending(false);
  }

  return (
    <div className="border-t border-zinc-800 px-6 py-3">
      <div className="mx-auto flex max-w-3xl gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void send();
          }}
          rows={2}
          placeholder="Follow up: ask for changes, e.g. 'also add input validation' (Ctrl + Enter)"
          className="min-w-0 flex-1 resize-none rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
        />
        <button
          onClick={send}
          disabled={!text.trim() || sending}
          className="self-end rounded-md bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
        >
          {sending ? "Sending..." : "Send"}
        </button>
      </div>
    </div>
  );
}

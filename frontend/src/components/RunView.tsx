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
import { isUsable } from "../hooks/useAgents";
import { useRunStream } from "../hooks/useRunStream";
import { groupEvents, pendingApprovals } from "../lib/events";
import { ActivityItem } from "./ActivityItem";
import { ModelPicker } from "./ModelSelect";
import { StatusBadge } from "./StatusBadge";

// Lazy: Monaco is only downloaded when the Changes tab is first opened.
const DiffViewer = lazy(() => import("./DiffViewer"));

type Tab = "activity" | "changes";

interface Props {
  runId: string;
  agents: AgentStatus[];
  onChanged: () => void; // tell the sidebar to refresh
  onDeleted: () => void;
}

export function RunView({ runId, agents, onChanged, onDeleted }: Props) {
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
  const agent = agents.find((a) => a.id === run?.agent_id);
  const agentNames = Object.fromEntries(agents.map((a) => [a.id, a.name]));
  // Follow-ups: the same agent if it can continue sessions, or switching to another one.
  const canFollowUp = Boolean(agent?.supports_follow_up) || agents.filter(isUsable).length > 1;

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

  async function sendFollowUp(prompt: string, model: string | null, effort: string | null, agentId: string | null) {
    setError(null);
    try {
      setRun(await api.followUp(runId, prompt, model, effort, agentId));
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
              {run && <span>{agentNames[run.agent_id] ?? run.agent_id}</span>}
              {run && <span>model: {run.model ?? "default"}</span>}
              {run?.effort && <span>effort: {run.effort}</span>}
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
            {run && !isRunning && (
              <DeleteButton
                kept={run.outcome === "kept"}
                onDelete={async () => {
                  setError(null);
                  try {
                    await api.deleteRun(runId);
                    onDeleted();
                  } catch (e) {
                    setError(errorText(e));
                  }
                }}
              />
            )}
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
                <ActivityItem key={item.seq} item={item} runId={runId} active={isRunning} agentNames={agentNames} />
              ))}
              {isRunning && items.length > 0 && <p className="text-xs text-zinc-500">Working...</p>}
            </div>
          </div>
          {idle && canFollowUp && run && (
            <FollowUpBox key={`${run.agent_id}-${run.model}-${run.effort}`} run={run} agents={agents} onSend={sendFollowUp} />
          )}
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

function DeleteButton({ kept, onDelete }: { kept: boolean; onDelete: () => void }) {
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="max-w-56 text-zinc-400">
          {kept
            ? "Remove from history? The kept branch stays in git."
            : "Delete this run? Its changes and branch are deleted too."}
        </span>
        <button onClick={onDelete} className="rounded-md bg-red-600 px-3 py-1.5 font-medium hover:bg-red-500">
          Delete
        </button>
        <button onClick={() => setConfirming(false)} className={BUTTON}>
          No
        </button>
      </div>
    );
  }
  return (
    <button onClick={() => setConfirming(true)} title="Delete this run" className={`${BUTTON} text-zinc-400 hover:border-red-500 hover:text-red-300`}>
      Delete
    </button>
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

interface FollowUpProps {
  run: RunInfo;
  agents: AgentStatus[];
  onSend: (prompt: string, model: string | null, effort: string | null, agentId: string | null) => Promise<boolean>;
}

// The box at the bottom of a finished run. Same agent = its session continues.
// Another agent = switch: it starts a new session in the same folder and gets
// a handoff (a summary of the run so far) before your message.
function FollowUpBox({ run, agents, onSend }: FollowUpProps) {
  const [text, setText] = useState("");
  const [agentId, setAgentId] = useState(run.agent_id);
  // Start on the run's current model and effort.
  const [model, setModel] = useState(run.model ?? "");
  const [effort, setEffort] = useState(run.effort ?? "");
  const [sending, setSending] = useState(false);

  const choices = agents.filter(isUsable);
  const agent = agents.find((a) => a.id === agentId);
  const switching = agentId !== run.agent_id;
  // Supervised runs can only move to agents that can pause for approval.
  const blocked = (a: AgentStatus) => run.supervised && !a.supports_supervised;

  function pickAgent(id: string) {
    setAgentId(id);
    // Model names belong to one agent: going back restores the run's model,
    // a new agent starts on its defaults.
    setModel(id === run.agent_id ? run.model ?? "" : "");
    setEffort(id === run.agent_id ? run.effort ?? "" : "");
  }

  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    if (await onSend(text.trim(), model || null, effort || null, switching ? agentId : null)) setText("");
    setSending(false);
  }

  return (
    <div className="border-t border-zinc-800 px-6 py-3">
      {switching && agent && (
        <p className="mx-auto mb-2 max-w-3xl rounded-md bg-sky-500/10 px-3 py-1.5 text-xs text-sky-200">
          Switching to {agent.name}: it starts a new session in the same folder (all changes so far are
          there) and first gets a summary of this run, then your message.
        </p>
      )}
      <div className="mx-auto flex max-w-3xl gap-2">
        <div className="flex w-52 shrink-0 flex-col gap-1.5 self-end">
          {choices.length > 1 && (
            <select
              aria-label="Agent"
              value={agentId}
              onChange={(e) => pickAgent(e.target.value)}
              title="Continue with this agent"
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm"
            >
              {choices.map((a) => (
                <option key={a.id} value={a.id} disabled={blocked(a)}>
                  {a.name}
                  {a.id === run.agent_id ? " (current)" : blocked(a) ? " (can't run supervised)" : ""}
                </option>
              ))}
            </select>
          )}
          {agent && agent.models.length > 0 && (
            <ModelPicker
              agent={agent}
              model={model}
              effort={effort}
              compact
              onChange={(m, e) => {
                setModel(m);
                setEffort(e);
              }}
            />
          )}
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void send();
          }}
          rows={2}
          placeholder={switching
            ? `What should ${agent?.name ?? "the new agent"} do next? (Ctrl + Enter)`
            : "Follow up: ask for changes, e.g. 'also add input validation' (Ctrl + Enter)"}
          className="min-w-0 flex-1 resize-none rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
        />
        <button
          onClick={send}
          disabled={!text.trim() || sending}
          className="self-end rounded-md bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
        >
          {sending ? "Sending..." : switching ? "Switch & send" : "Send"}
        </button>
      </div>
    </div>
  );
}

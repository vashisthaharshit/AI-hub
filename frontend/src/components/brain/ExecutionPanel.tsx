// A Brain plan while it runs, and after. Top: where it stands (a progress bar
// with one segment per step, time taken, what it's doing). Then what it needs
// from you, if anything. Then the steps as a timeline, grouped by what runs
// together: each with its status, the agent and model that did it, and its
// summary. At the end: tests, the changes, what the agents noted, Keep / Discard.

import { lazy, Suspense, useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AgentStatus, BrainAction, BrainTask, MemoryNote, StepProgress } from "../../api/types";
import { timeAgo } from "../../lib/events";
import { agentColor } from "../../lib/theme";

const DiffViewer = lazy(() => import("../DiffViewer"));

const STATE_TEXT: Record<StepProgress["state"], string> = {
  waiting: "Not started",
  running: "Working",
  passed: "Passed",
  failed: "Didn't pass",
  skipped: "Skipped",
};

const BANNER: Record<string, { label: string; color: string }> = {
  running: { label: "Running", color: "var(--color-brain)" },
  waiting: { label: "Waiting for you", color: "#f2b25a" },
  done: { label: "Done, ready for your review", color: "#4ade9b" },
  stopped: { label: "Stopped", color: "#8c95b5" },
  failed: { label: "Paused by a problem", color: "#f87171" },
  kept: { label: "Kept", color: "#4ade9b" },
};

/** `code` in agent text shown as code. */
export function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((part, i) =>
        part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
          <code key={i} className="inline-code">{part.slice(1, -1)}</code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function StatusIcon({ state }: { state: StepProgress["state"] }) {
  if (state === "running") return <span className="step-icon spin" data-state={state} aria-label="Working" />;
  const glyph = { waiting: "", passed: "✓", failed: "!", skipped: "–" }[state];
  return <span className="step-icon" data-state={state} aria-label={STATE_TEXT[state]}>{glyph}</span>;
}

function elapsed(from: string | null, to: string | null): string | null {
  if (!from) return null;
  const s = Math.max(0, ((to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

interface Props {
  task: BrainTask;
  agents: AgentStatus[];
  onUpdate: (task: BrainTask) => void;
  onOpenRun: (runId: string) => void;
}

export function ExecutionPanel({ task, agents, onUpdate, onOpenRun }: Props) {
  const ex = task.execution!;
  const plan = task.plan!;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [briefs, setBriefs] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [showChanges, setShowChanges] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const live = ex.state === "running" || ex.state === "waiting";
  const titleOf = (id: string) => plan.steps.find((s) => s.id === id)?.title ?? id;

  useEffect(() => {
    api.brainTaskNotes(task.id).then(setNotes).catch(() => setNotes([]));
  }, [task.id, ex.state, ex.wave]);

  useEffect(() => {
    if (ex.waiting_for === "approve") {
      setBriefs(Object.fromEntries(ex.waiting_steps.map((id) => [id, plan.steps.find((s) => s.id === id)?.brief ?? ""])));
    }
  }, [ex.waiting_for, ex.waiting_steps.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(label: string, fn: () => Promise<BrainTask>) {
    setBusy(label);
    setError(null);
    try {
      onUpdate(await fn());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  const answer = (action: BrainAction, withBriefs = false) =>
    act(action, () => api.answerBrainTask(task.id, action, withBriefs ? briefs : {}));

  function modelLabel(agentId: string | null | undefined, model: string | null | undefined): string {
    if (!model) return "default model";
    const found = agents.find((a) => a.id === agentId)?.models.find((m) => m.id === model);
    return found?.label ?? model;
  }
  const agentName = (id: string | null | undefined) => agents.find((a) => a.id === id)?.name ?? id ?? "Agent";

  const banner = BANNER[ex.state] ?? BANNER.stopped;
  const stepIds = plan.waves.flat();
  const passed = stepIds.filter((id) => ex.steps[id]?.state === "passed").length;
  const time = elapsed(ex.started_at, ex.finished_at);
  // The first line of a problem is the plain-words part; the rest are details.
  const [headline, ...details] = (ex.message ?? "").split("\n");

  return (
    <section className="space-y-5 rise">
      {/* where it stands */}
      <div className="exec-card" style={{ ["--accent" as string]: banner.color }}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="live-dot" data-live={live} style={{ ["--dot" as string]: banner.color }} />
          <span className="font-display text-xl font-bold tracking-tight">{banner.label}</span>
          <span className="text-sm text-zinc-500">
            {passed} of {stepIds.length} steps passed{time && ` · ${time}`}
          </span>
          <span className="ml-auto flex flex-wrap gap-2">
            {live && (
              <button onClick={() => act("stop", () => api.stopBrainTask(task.id))} disabled={busy !== null} className="workspace-button">
                {busy === "stop" ? "Stopping..." : "Stop"}
              </button>
            )}
            {(ex.state === "stopped" || ex.state === "failed") && (
              <>
                <button onClick={() => act("discard", () => api.discardBrainTask(task.id))} disabled={busy !== null} className="workspace-button">
                  Discard the work
                </button>
                <button onClick={() => act("continue", () => api.runBrainTask(task.id))} disabled={busy !== null} className="run-button">
                  {busy === "continue" ? "Continuing..." : "Continue"}
                </button>
              </>
            )}
            {ex.state === "done" && (
              <>
                <button onClick={() => act("discard", () => api.discardBrainTask(task.id))} disabled={busy !== null} className="workspace-button">
                  Discard
                </button>
                <button onClick={() => act("keep", () => api.keepBrainTask(task.id))} disabled={busy !== null} className="run-button">
                  {busy === "keep" ? "Keeping..." : "Keep the work"}
                </button>
              </>
            )}
          </span>
        </div>

        <div className="progress-track" aria-hidden="true">
          {stepIds.map((id) => (
            <span key={id} className="progress-seg" data-state={ex.steps[id]?.state ?? "waiting"} title={`${titleOf(id)}: ${STATE_TEXT[ex.steps[id]?.state ?? "waiting"]}`} />
          ))}
        </div>

        {headline && (
          <p className={`text-sm ${ex.state === "running" ? "thinking" : "text-zinc-200"}`}>
            <Rich text={headline} />
          </p>
        )}
        {details.length > 0 && ex.state === "failed" && (
          <details className="text-xs">
            <summary className="text-zinc-500">Details</summary>
            <pre className="mt-1.5 whitespace-pre-wrap rounded-lg bg-zinc-950 p-3 font-mono text-zinc-400">{details.join("\n")}</pre>
          </details>
        )}
        {ex.branch && ex.state !== "kept" && (
          <p className="text-xs text-zinc-500">
            Working on a copy (branch <code className="inline-code">{ex.branch}</code>). Your own folder isn't touched.
            {ex.baseline_tests === false && " The tests were already failing before the plan, so steps aren't judged by them."}
          </p>
        )}
      </div>

      {/* what it needs from you */}
      {ex.state === "waiting" && (
        <div className="exec-card space-y-3" style={{ ["--accent" as string]: "#f2b25a" }}>
          {ex.waiting_for === "approve" ? (
            <>
              <p className="font-semibold">
                Ready to start {ex.waiting_steps.length > 1 ? `${ex.waiting_steps.length} steps at the same time` : "the next step"}.
              </p>
              <p className="text-xs text-zinc-500">Change what the agent is told, if you want, then start.</p>
              {ex.waiting_steps.map((id) => (
                <label key={id} className="block space-y-1 text-sm">
                  <span className="font-medium">{titleOf(id)}</span>
                  <textarea
                    value={briefs[id] ?? ""}
                    onChange={(e) => setBriefs((b) => ({ ...b, [id]: e.target.value }))}
                    rows={3}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                </label>
              ))}
              <div className="flex flex-wrap gap-2">
                <button onClick={() => answer("continue", true)} disabled={busy !== null} className="run-button">
                  {ex.waiting_steps.length > 1 ? "Start these steps" : "Start this step"}
                </button>
                <button onClick={() => answer("skip")} disabled={busy !== null} className="workspace-button">Skip</button>
                <button onClick={() => answer("stop")} disabled={busy !== null} className="workspace-button">Stop here</button>
              </div>
            </>
          ) : ex.waiting_for === "conflict" ? (
            <>
              <p className="font-semibold">Two steps changed the same lines</p>
              <p className="whitespace-pre-wrap text-sm text-zinc-300"><Rich text={ex.message ?? ""} /></p>
              <div className="flex flex-wrap gap-2">
                <button onClick={() => answer("continue")} disabled={busy !== null} className="run-button">I fixed it, continue</button>
                <button onClick={() => answer("retry")} disabled={busy !== null} className="workspace-button">Ask an agent again</button>
                <button onClick={() => answer("stop")} disabled={busy !== null} className="workspace-button">Stop</button>
              </div>
            </>
          ) : (
            <>
              <p className="font-semibold">{ex.waiting_steps.length > 0 ? "A step didn't pass" : "The tests fail"}</p>
              <p className="whitespace-pre-wrap text-sm text-zinc-300"><Rich text={ex.message ?? ""} /></p>
              <p className="text-xs text-zinc-500">
                {ex.waiting_steps.length > 0
                  ? "It already got a retry with a stronger model. Open its run below to see what happened."
                  : "An agent already tried to fix them once."}
              </p>
              <div className="flex flex-wrap gap-2">
                <button onClick={() => answer("retry")} disabled={busy !== null} className="run-button">Try again</button>
                <button onClick={() => answer("skip")} disabled={busy !== null} className="workspace-button">
                  {ex.waiting_steps.length > 0 ? "Skip this step" : "Continue anyway"}
                </button>
                <button onClick={() => answer("stop")} disabled={busy !== null} className="workspace-button">Stop</button>
              </div>
            </>
          )}
        </div>
      )}

      {/* the steps */}
      <ol className="timeline" aria-label="Steps">
        {plan.waves.map((wave, i) => (
          <li key={i} className="timeline-wave">
            <p className="timeline-label">
              {wave.length > 1 ? `${wave.length} steps at the same time` : i === 0 ? "First" : i === plan.waves.length - 1 ? "Last" : "Then"}
            </p>
            <div className={wave.length > 1 ? "grid gap-2 lg:grid-cols-2" : ""}>
              {wave.map((id) => {
                const p = ex.steps[id];
                const step = plan.steps.find((s) => s.id === id);
                const state = p?.state ?? "waiting";
                const agentId = p?.agent_id ?? step?.assignment?.agent_id;
                const model = p?.model ?? step?.assignment?.model;
                const effort = p?.effort ?? step?.assignment?.effort;
                const long = (p?.message?.length ?? 0) > 180;
                return (
                  <div key={id} className="step-row" data-state={state}>
                    <StatusIcon state={state} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                        <span className="font-semibold text-zinc-100">{titleOf(id)}</span>
                        <span className="step-state text-xs" data-state={state}>{STATE_TEXT[state]}</span>
                        {p?.stepped_up && <span className="badge">stepped up</span>}
                        {p && p.attempts > 1 && <span className="badge">{p.attempts} tries</span>}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                        <span className="agent-chip" style={{ ["--dot" as string]: agentColor(agentId) }}>
                          {agentName(agentId)}
                        </span>
                        <span>{modelLabel(agentId, model)}</span>
                        {effort && <span className="text-zinc-500">{effort} effort</span>}
                      </div>
                      {p?.message && state !== "running" && (
                        <p className={`mt-2 text-sm text-zinc-300 ${long && !open[id] ? "line-clamp-3" : ""}`}>
                          <Rich text={p.message} />
                        </p>
                      )}
                      {long && state !== "running" && (
                        <button className="mt-1 text-xs text-sky-300 hover:text-sky-200" onClick={() => setOpen((o) => ({ ...o, [id]: !o[id] }))}>
                          {open[id] ? "Show less" : "Show more"}
                        </button>
                      )}
                    </div>
                    {p?.run_id && (
                      <button className="workspace-button shrink-0 self-start text-xs" onClick={() => onOpenRun(p.run_id!)}>
                        {state === "running" ? "Watch" : "Open run"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </li>
        ))}
      </ol>

      {/* the result */}
      {ex.final_tests !== null && (
        <details className="exec-card" style={{ ["--accent" as string]: ex.final_tests ? "#4ade9b" : "#f87171" }}>
          <summary className={`text-sm font-semibold ${ex.final_tests ? "text-emerald-300" : "text-red-300"}`}>
            {ex.final_tests ? "✓ The tests pass on the combined result" : "The tests fail on the combined result"}
          </summary>
          {ex.final_test_output && (
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-950 p-3 font-mono text-xs text-zinc-400">{ex.final_test_output}</pre>
          )}
        </details>
      )}

      {ex.base_commit && ex.state !== "discarded" && (
        <div className="overflow-hidden rounded-2xl border border-zinc-800">
          <button className="flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-zinc-900/60" onClick={() => setShowChanges((s) => !s)}>
            <span className="font-semibold">See the changes</span>
            <span className="text-xs text-zinc-500">{showChanges ? "Hide" : "Show"}</span>
          </button>
          {showChanges && (
            <div className="h-[32rem] border-t border-zinc-800">
              <Suspense fallback={<p className="p-6 text-sm text-zinc-500">Loading diff viewer...</p>}>
                <DiffViewer reloadKey={`${ex.state}-${ex.wave}-${passed}`} load={() => api.brainTaskFiles(task.id)} />
              </Suspense>
            </div>
          )}
        </div>
      )}

      {notes.length > 0 && (
        <details className="rounded-2xl border border-zinc-800 px-4 py-3" open={ex.state === "done"}>
          <summary className="font-semibold">
            What the agents noted <span className="font-normal text-zinc-500">({notes.length})</span>
          </summary>
          <p className="mt-1 text-xs text-zinc-500">
            {ex.state === "kept" ? "Saved to this workspace's project memory." : "Saved to project memory if you keep the work. Later steps already use them."}
          </p>
          <ul className="mt-3 space-y-2">
            {notes.map((n) => (
              <li key={n.id} className="note-row">
                <span className="min-w-0"><Rich text={n.text} /></span>
                {n.files.length > 0 && <span className="mt-1 block font-mono text-[11px] text-zinc-500">{n.files.join(" · ")}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {ex.log.length > 0 && (
        <details className="px-1 text-sm">
          <summary className="text-zinc-400">What happened ({ex.log.length})</summary>
          <ol className="mt-2 space-y-1.5 border-l border-zinc-800 pl-4">
            {ex.log.map((entry, i) => (
              <li key={i} className="text-xs text-zinc-400">
                <span className="text-zinc-600">{timeAgo(entry.at)}</span> <Rich text={entry.text} />
              </li>
            ))}
          </ol>
        </details>
      )}

      {error && <p role="alert" className="whitespace-pre-wrap rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
    </section>
  );
}

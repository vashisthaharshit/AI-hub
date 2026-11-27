// One Brain task: what triage decided, the planner's questions, and the plan.
// The plan is editable; AI-Hub checks every edit again (parallel steps never
// share files, a review step at the end) and the router picks an agent, model
// and effort for each step (you can change any of them). Running a plan is
// the next build step.

import { useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AgentStatus, BrainTask, Difficulty, PlanStep, RunInfo, StepAssignment, StepType, Workspace } from "../../api/types";
import { isUsable } from "../../hooks/useAgents";
import { timeAgo } from "../../lib/events";
import { agentColor, tint } from "../../lib/theme";
import { StatusBadge } from "../StatusBadge";
import { ModelPicker } from "../ModelSelect";
import { MODES } from "./BrainNewTask";
import { ExecutionPanel } from "./ExecutionPanel";

const TYPES: StepType[] = ["explore", "build", "test", "fix", "review"];
const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];
const HOW: Record<BrainTask["triage"]["how"], string> = { rules: "by simple rules", ai: "by a quick AI check", user: "by you" };

interface Props {
  taskId: string;
  workspace: Workspace;
  agents: AgentStatus[];
  onChanged: () => void;
  onDeleted: () => void;
  onOpenRun: (runId: string) => void; // opens the run inside Brain
}

export function BrainTaskView({ taskId, workspace, agents, onChanged, onDeleted, onOpenRun }: Props) {
  const [task, setTask] = useState<BrainTask | null>(null);
  const [steps, setSteps] = useState<PlanStep[]>([]);
  const [dirty, setDirty] = useState(false);
  const [answers, setAnswers] = useState<string[]>([]);
  const [direct, setDirect] = useState<StepAssignment | null>(null); // who runs a small task (yours or AI-Hub's pick)
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [runsVersion, setRunsVersion] = useState(0);

  // Runs this task started. They stay here (not in Manual) unless moved.
  useEffect(() => {
    let active = true;
    const load = () => api.runs({ brainTaskId: taskId }).then((r) => active && setRuns(r)).catch(() => {});
    void load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [taskId, runsVersion]);

  function show(t: BrainTask) {
    setTask(t);
    setSteps(t.plan?.steps.map((s) => ({ ...s, files: [...s.files], depends_on: [...s.depends_on] })) ?? []);
    setDirty(false);
    setAnswers(t.questions.map((_, i) => t.answers[i] ?? ""));
    setDirect(t.direct);
  }

  // While the plan runs, follow it (step states, what it needs from you).
  const execState = task?.execution?.state;
  useEffect(() => {
    if (execState !== "running" && execState !== "waiting") return;
    const timer = setInterval(() => {
      api.brainTask(taskId).then((t) => setTask(t)).catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [taskId, execState]);

  useEffect(() => {
    api.brainTask(taskId).then(show).catch((e) => setError(errorText(e)));
  }, [taskId]);

  async function act(label: string, fn: () => Promise<BrainTask | void>) {
    setBusy(label);
    setError(null);
    try {
      const t = await fn();
      if (t) {
        show(t);
        onChanged();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (!task) return <p className="p-10 text-sm text-zinc-500">{error ?? "Loading task..."}</p>;
  const plan = task.plan;
  // A plan that has started (and wasn't discarded) is shown live instead of editable.
  const executing = Boolean(task.execution && task.execution.state !== "discarded");
  const titleOf = (id: string) => steps.find((s) => s.id === id)?.title || id;

  function change(id: string, patch: Partial<PlanStep>) {
    setSteps((all) => all.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    setDirty(true);
  }

  function addStep() {
    let n = steps.length + 1;
    while (steps.some((s) => s.id === `s${n}`)) n += 1;
    const step: PlanStep = { id: `s${n}`, title: "New step", type: "build", difficulty: "medium", files: [], depends_on: [], brief: "", done_when: "", assignment: null };
    const review = steps.findIndex((s) => s.type === "review");
    setSteps((all) => (review === -1 ? [...all, step] : [...all.slice(0, review), step, ...all.slice(review)]));
    setDirty(true);
  }

  function removeStep(id: string) {
    setSteps((all) => all.filter((s) => s.id !== id).map((s) => ({ ...s, depends_on: s.depends_on.filter((d) => d !== id) })));
    setDirty(true);
  }

  async function runDirectly() {
    // The router's pick (e.g. a small model for a small task), or the user's own.
    // Tasks made before the router existed have no pick yet: ask for one.
    const pick = direct ?? task!.direct ?? (await api.brainDirect(task!.id)).direct;
    if (!pick) throw new Error("No usable agent found. Install and sign in to Claude Code or Codex.");
    const run = await api.createRun({
      agent_id: pick.agent_id, repo_path: task!.repo_path, prompt: task!.request, test_command: task!.test_command,
      supervised: false, model: pick.model, effort: pick.effort,
      workspace_id: workspace.id, brain_task_id: task!.id,   // listed under this Brain task, not in Manual
    });
    setRunsVersion((v) => v + 1);
    onOpenRun(run.id);
  }

  return (
    <div className="task-page space-y-6">
      <div className="task-intro">
        <h2 className="whitespace-pre-wrap">{task.request}</h2>
        <p className="break-all font-mono text-xs">{task.repo_path}</p>
      </div>

      {/* settings for this task */}
      <section className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-zinc-400">Cost vs quality</span>
          <select
            value={task.mode}
            onChange={(e) => act("mode", () => api.editBrainTask(task.id, { mode: e.target.value as BrainTask["mode"] }))}
            className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
          >
            {MODES.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-zinc-400">When it runs</span>
          <select
            value={task.approval}
            onChange={(e) => act("approval", () => api.editBrainTask(task.id, { approval: e.target.value as "all" | "each" }))}
            className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
          >
            <option value="all">Run all steps</option>
            <option value="each">Approve each step</option>
          </select>
        </label>
      </section>

      {/* triage */}
      <section className="rounded-lg border border-zinc-800 p-4 text-sm">
        <p>
          <span className="font-semibold">{task.triage.size === "small" ? "Small task" : "Bigger task"}</span>
          <span className="text-zinc-400"> — decided {HOW[task.triage.how]}</span>
        </p>
        {task.triage.reasons.length > 0 && <p className="mt-1 text-xs text-zinc-500">{task.triage.reasons.join(" · ")}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          {task.state === "direct" ? (
            <>
              <div className="w-full">
                <AssignmentRow
                  assignment={direct}
                  agents={agents}
                  onChange={(a) => setDirect(a ?? task.direct)}
                />
              </div>
              <button onClick={() => act("direct-run", runDirectly)} disabled={busy !== null} className="run-button">
                {busy === "direct-run" ? "Starting..." : "Run it with one agent"}
              </button>
              <button onClick={() => act("plan", () => api.planBrainTask(task.id, {}))} disabled={busy !== null} className="workspace-button text-xs">
                {busy === "plan" ? <span className="thinking">Planning...</span> : "Plan it instead"}
              </button>
            </>
          ) : executing ? null : (
            <button onClick={() => act("direct", () => api.brainDirect(task.id))} disabled={busy !== null} className="workspace-button text-xs">
              Just do it with one agent instead
            </button>
          )}
        </div>
      </section>

      {/* clarifying questions */}
      {task.state === "asking" && (
        <section className="space-y-3 rounded-lg border border-sky-800 bg-sky-500/5 p-4">
          <h3 className="text-sm font-semibold">A couple of questions first</h3>
          {task.questions.map((q, i) => (
            <label key={i} className="flex flex-col gap-1 text-sm">
              <span>{q}</span>
              <input
                value={answers[i] ?? ""}
                onChange={(e) => setAnswers((a) => a.map((v, j) => (j === i ? e.target.value : v)))}
                className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
              />
            </label>
          ))}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => act("plan", () => api.planBrainTask(task.id, { answers }))}
              disabled={busy !== null || answers.every((a) => !a.trim())}
              className="run-button"
            >
              {busy === "plan" ? "Planning..." : "Plan with these answers"}
            </button>
            <button onClick={() => act("plan", () => api.planBrainTask(task.id, { skip_questions: true }))} disabled={busy !== null} className="workspace-button text-xs">
              Skip, just plan it
            </button>
          </div>
        </section>
      )}

      {/* the plan */}
      {task.state === "planned" && plan && executing && (
        <ExecutionPanel
          task={task}
          agents={agents}
          onUpdate={(t) => {
            show(t);
            onChanged();
          }}
          onOpenRun={onOpenRun}
        />
      )}

      {task.state === "planned" && plan && !executing && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-base font-semibold">The plan</h3>
            {plan.made_by && <span className="text-xs text-zinc-500">Written by {plan.made_by}</span>}
          </div>

          {plan.problems.length > 0 && (
            <ul className="list-disc space-y-0.5 rounded-md bg-red-500/10 py-2 pl-7 pr-3 text-sm text-red-300">
              {plan.problems.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          )}
          {plan.single && (
            <p className="rounded-md bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              This plan has only one real step, so running it with one agent is cheaper.
            </p>
          )}
          {plan.routing_notes.length > 0 && (
            <ul className="list-disc space-y-0.5 rounded-md bg-amber-500/10 py-2 pl-7 pr-3 text-sm text-amber-300">
              {plan.routing_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          {plan.notes.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-zinc-500">
              {plan.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}

          {/* the order: steps in one column run at the same time; work flows left to right */}
          {!dirty && plan.waves.length > 0 && (
            <div className="plan-flow rise" aria-label="The order the steps run in">
              {plan.waves.map((wave, i) => (
                <div key={i} className="flex">
                  {i > 0 && <span className="plan-link" aria-hidden="true" />}
                  <div className="plan-wave">
                    <span className="plan-wave-label">{wave.length > 1 ? "At the same time" : i === 0 ? "First" : "Then"}</span>
                    {wave.map((id) => {
                      const st = steps.find((x) => x.id === id);
                      return (
                        <div key={id} className="plan-node" style={tint(agentColor(st?.assignment?.agent_id))}>
                          <span className="block font-semibold text-zinc-100">{titleOf(id)}</span>
                          {st?.assignment && (
                            <span className="mt-0.5 block text-xs text-zinc-500">
                              {st.assignment.agent_name}, {st.assignment.model_label}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="space-y-3">
            {steps.map((s, i) => (
              <StepCard
                key={s.id}
                number={i + 1}
                step={s}
                agents={agents}
                others={steps.filter((o) => o.id !== s.id && o.type !== "review")}
                onChange={(patch) => change(s.id, patch)}
                onRemove={s.type === "review" ? undefined : () => removeStep(s.id)}
              />
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button onClick={addStep} className="workspace-button text-xs">+ Add a step</button>
            <button
              onClick={() => act("save", () => api.editBrainTask(task.id, { steps }))}
              disabled={!dirty || busy !== null}
              className="workspace-button text-xs"
            >
              {busy === "save" ? "Checking..." : "Save changes"}
            </button>
            {dirty && (
              <button onClick={() => show(task)} className="px-1 text-xs text-zinc-500 hover:text-zinc-300">Undo changes</button>
            )}
            <button
              onClick={() => act("reroute", () => api.editBrainTask(task.id, { reroute: true }))}
              disabled={busy !== null || dirty}
              className="workspace-button text-xs"
              title="Let AI-Hub pick the agent and model for every step again (drops your own picks)"
            >
              {busy === "reroute" ? "Picking..." : "Pick agents again"}
            </button>
            <button onClick={() => act("plan", () => api.planBrainTask(task.id, {}))} disabled={busy !== null} className="workspace-button text-xs">
              {busy === "plan" ? "Planning..." : "Write a new plan"}
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-zinc-800 pt-4">
            <button
              onClick={() => act("run", () => api.runBrainTask(task.id))}
              disabled={busy !== null || dirty || plan.problems.length > 0 || steps.some((s) => !s.assignment)}
              className="run-button"
            >
              {busy === "run" ? "Starting..." : task.execution?.state === "discarded" ? "Run the plan again" : "Run the plan"}
            </button>
            <span className="text-xs text-zinc-500">
              {dirty
                ? "Save your changes first."
                : plan.problems.length > 0
                  ? "Fix the problems above first."
                  : task.approval === "each"
                    ? "It will pause before every step so you can check it."
                    : "Steps run on a copy of your repo. You review everything before keeping it."}
            </span>
          </div>
          {task.execution?.state === "discarded" && task.execution.message && (
            <p className="text-xs text-zinc-500">{task.execution.message}</p>
          )}
        </section>
      )}

      {runs.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-base font-semibold">Runs from this task</h3>
          <p className="text-xs text-zinc-500">They stay in Brain. Move one to Manual to keep chatting with it there.</p>
          <ul className="space-y-2">
            {runs.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
                <span className="h-2 w-2 rounded-full" style={{ background: agentColor(r.agent_id) }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{r.prompt.split("\n")[0]}</span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-zinc-500">
                    <StatusBadge state={r.state} />
                    {r.model ?? "default model"}
                    <span>{timeAgo(r.created_at)}</span>
                    {r.origin === "manual" && <span className="text-emerald-300">also in Manual</span>}
                  </span>
                </span>
                <button onClick={() => onOpenRun(r.id)} className="workspace-button text-xs">Open</button>
                {r.origin === "brain" && (
                  <button
                    onClick={() => act("move", async () => {
                      await api.moveToManual(r.id);
                      setRunsVersion((v) => v + 1);
                      onChanged();
                    })}
                    disabled={busy !== null}
                    className="workspace-button text-xs"
                    title="Show this run in Manual too, to keep chatting with it there"
                  >
                    Move to Manual
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="flex justify-end border-t border-zinc-800 pt-4">
        <button
          onClick={() => act("delete", async () => {
            await api.deleteBrainTask(task.id);
            onDeleted();
          })}
          disabled={busy !== null}
          className="workspace-button text-xs"
        >
          Delete task
        </button>
      </section>
      {error && <p role="alert" className="whitespace-pre-wrap text-sm text-red-300">{error}</p>}
    </div>
  );
}

interface StepProps {
  number: number;
  step: PlanStep;
  agents: AgentStatus[];
  others: PlanStep[];
  onChange: (patch: Partial<PlanStep>) => void;
  onRemove?: () => void;
}

function StepCard({ number, step: s, agents, others, onChange, onRemove }: StepProps) {
  const review = s.type === "review";
  const input = "rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm";
  return (
    <div className={`space-y-2.5 rounded-lg border p-3.5 ${review ? "border-zinc-800 bg-zinc-900/40" : "border-zinc-800"}`}>
      <div className="flex items-center gap-2">
        <span className="text-xs text-zinc-500">{number}.</span>
        <input value={s.title} onChange={(e) => onChange({ title: e.target.value })} className={`${input} min-w-0 flex-1 font-semibold`} />
        {!review && (
          <>
            <select value={s.type} onChange={(e) => onChange({ type: e.target.value as StepType })} className={input}>
              {TYPES.filter((t) => t !== "review").map((t) => <option key={t}>{t}</option>)}
            </select>
            <select value={s.difficulty} onChange={(e) => onChange({ difficulty: e.target.value as Difficulty })} className={input}>
              {DIFFICULTIES.map((d) => <option key={d}>{d}</option>)}
            </select>
          </>
        )}
        {onRemove && (
          <button onClick={onRemove} className="px-1 text-xs text-zinc-500 hover:text-red-300" title="Remove this step">✕</button>
        )}
      </div>
      {review && <p className="text-xs text-zinc-500">Always last, by a different agent from the one that wrote the code.</p>}
      <AssignmentRow assignment={s.assignment} agents={agents} onChange={(assignment) => onChange({ assignment })} />
      <label className="flex flex-col gap-1 text-xs text-zinc-400">
        What to do
        <textarea value={s.brief} onChange={(e) => onChange({ brief: e.target.value })} rows={2} className={input} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-zinc-400">
        Done when
        <input value={s.done_when} onChange={(e) => onChange({ done_when: e.target.value })} className={input} />
      </label>
      {!review && (
        <>
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Files it changes (comma-separated)
            <input
              value={s.files.join(", ")}
              onChange={(e) => onChange({ files: e.target.value.split(",").map((f) => f.trim()).filter(Boolean) })}
              className={`${input} font-mono`}
            />
          </label>
          {others.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
              Waits for:
              {others.map((o) => (
                <label key={o.id} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={s.depends_on.includes(o.id)}
                    onChange={(e) =>
                      onChange({ depends_on: e.target.checked ? [...s.depends_on, o.id] : s.depends_on.filter((d) => d !== o.id) })
                    }
                    className="accent-sky-500"
                  />
                  {o.title || o.id}
                </label>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// Who does the step: AI-Hub's pick with its reason, or the user's own.
function AssignmentRow({ assignment: a, agents, onChange }: {
  assignment: StepAssignment | null;
  agents: AgentStatus[];
  onChange: (a: StepAssignment | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const usable = agents.filter(isUsable);
  const agent = usable.find((x) => x.id === a?.agent_id) ?? usable[0];

  function pick(agentId: string, model: string, effort: string) {
    const chosen = usable.find((x) => x.id === agentId);
    if (!chosen) return;
    const option = chosen.models.find((m) => m.id === model);
    onChange({
      agent_id: chosen.id,
      agent_name: chosen.name,
      model: model || null,
      model_label: option?.label ?? chosen.default_label ?? "Default model",
      effort: effort || null,
      reason: "Your choice",
      manual: true,
    });
  }

  return (
    <div className="rounded-md bg-zinc-900/60 px-2.5 py-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {a ? (
          <span>
            <span className="font-semibold text-zinc-200">{a.agent_name}</span>
            <span className="text-zinc-300"> · {a.model_label}</span>
            {a.effort && <span className="text-zinc-400"> · {a.effort} effort</span>}
            <span className="block text-zinc-500">{a.manual ? "Your choice" : a.reason}</span>
          </span>
        ) : (
          <span className="text-zinc-500">AI-Hub picks the agent when you save.</span>
        )}
        <span className="flex gap-2">
          {usable.length > 0 && (
            <button type="button" onClick={() => setEditing((e) => !e)} className="text-sky-300 hover:text-sky-200">
              {editing ? "Done" : "Change"}
            </button>
          )}
          {a?.manual && (
            <button type="button" onClick={() => { onChange(null); setEditing(false); }} className="text-zinc-400 hover:text-zinc-200">
              Let AI-Hub pick
            </button>
          )}
        </span>
      </div>
      {editing && agent && (
        <div className="mt-2 flex flex-col gap-2">
          <select
            value={agent.id}
            onChange={(e) => pick(e.target.value, "", "")}
            className="rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm"
          >
            {usable.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          <ModelPicker
            agent={agent}
            model={a?.agent_id === agent.id ? a.model ?? "" : ""}
            effort={a?.agent_id === agent.id ? a.effort ?? "" : ""}
            onChange={(m, e) => pick(agent.id, m, e)}
            compact
          />
        </div>
      )}
    </div>
  );
}

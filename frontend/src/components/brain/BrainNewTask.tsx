// Brain: describe a task. AI-Hub decides whether it's big enough to plan
// (triage), asks questions if it's vague, and writes a plan you can edit.

import { useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { BrainMode, BrainTask, Workspace } from "../../api/types";

export const MODES: { id: BrainMode; label: string; text: string }[] = [
  { id: "cheapest", label: "Cheapest", text: "Smallest models that can do each step" },
  { id: "balanced", label: "Balanced", text: "Cheap models for easy steps, strong ones for hard steps" },
  { id: "best", label: "Best", text: "Strong models everywhere" },
];

const IDEAS = [
  "Add user login: an API endpoint, a login page and tests",
  "Add a dark mode to the whole app, with a toggle in the header",
  "Write tests for the parts of the backend that have none",
];

interface Props {
  workspace: Workspace;
  onCreated: (task: BrainTask) => void;
}

export function BrainNewTask({ workspace, onCreated }: Props) {
  const [request, setRequest] = useState("");
  const [testCommand, setTestCommand] = useState(workspace.test_command ?? "");
  const [mode, setMode] = useState<BrainMode | "">(""); // "" = default from Settings
  const [approval, setApproval] = useState<"all" | "each">("all");
  const [busy, setBusy] = useState<"auto" | "plan" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = Boolean(request.trim()) && busy === null;
  const [defaultMode, setDefaultMode] = useState<BrainMode>("balanced");
  useEffect(() => {
    api.brainSettings().then((s) => setDefaultMode(s.mode)).catch(() => {});
  }, []);

  async function submit(forcePlan: boolean) {
    if (!canSubmit) return;
    setBusy(forcePlan ? "plan" : "auto");
    setError(null);
    try {
      const task = await api.createBrainTask({
        workspace_id: workspace.id,
        request: request.trim(),
        test_command: testCommand.trim() || null,
        mode: mode || null,
        approval,
        plan: forcePlan ? true : null,
      });
      onCreated(task);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="task-page">
      <header className="task-intro rise">
        <h2>Describe the job. The Brain works out the rest.</h2>
        <p>
          Small jobs go straight to one agent. Bigger ones are split into steps for Claude Code and Codex, some running
          at the same time. You see and can change the plan before anything runs.
        </p>
      </header>

      <form
        className="task-composer rise"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(false);
        }}
      >
        <div className="task-fields">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">What do you want done?</span>
            <textarea
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit(false);
              }}
              rows={6}
              placeholder="Say what you want in plain words. Mention the parts involved (backend, page, tests) if you know them."
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
            />
          </label>
          {!request && (
            <div className="-mt-2 flex flex-wrap gap-2">
              {IDEAS.map((idea) => (
                <button key={idea} type="button" onClick={() => setRequest(idea)}
                  className="rounded-full border border-zinc-800 px-3 py-1 text-xs text-zinc-400 transition hover:border-zinc-600 hover:text-zinc-200">
                  {idea}
                </button>
              ))}
            </div>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">
              Test command <span className="font-normal text-zinc-500">(optional)</span>
            </span>
            <input
              value={testCommand}
              onChange={(e) => setTestCommand(e.target.value)}
              placeholder="pytest -q   or   npm test"
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-sm"
            />
            <span className="text-xs text-zinc-500">Checks each step's work and the combined result. From this workspace's settings.</span>
          </label>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">Cost vs quality</legend>
            <div className="flex flex-wrap gap-2">
              <ModeButton active={mode === ""} onClick={() => setMode("")} label="Default" text={`From Settings (${MODES.find((m) => m.id === defaultMode)?.label ?? "Balanced"})`} />
              {MODES.map((m) => (
                <ModeButton key={m.id} active={mode === m.id} onClick={() => setMode(m.id)} label={m.label} text={m.text} />
              ))}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">When it runs</legend>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" checked={approval === "all"} onChange={() => setApproval("all")} className="mt-1 accent-sky-500" />
              <span>
                Run all steps <span className="block text-xs text-zinc-500">Stops only if something fails that it can't fix.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" checked={approval === "each"} onChange={() => setApproval("each")} className="mt-1 accent-sky-500" />
              <span>
                Approve each step <span className="block text-xs text-zinc-500">Pauses before every step so you can check or change it.</span>
              </span>
            </label>
          </fieldset>

          {error && <p role="alert" className="rounded bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        <div className="task-footer">
          <button type="button" onClick={() => void submit(true)} disabled={!canSubmit} className="workspace-button text-xs">
            {busy === "plan" ? <span className="thinking">Planning...</span> : "Always plan it"}
          </button>
          <button type="submit" disabled={!canSubmit} className="run-button">
            {busy === "auto" ? "Thinking..." : "Continue"}
          </button>
        </div>
        {busy && <p className="px-1 pb-2 text-xs text-zinc-500">Writing a plan can take up to half a minute.</p>}
      </form>
    </div>
  );
}

function ModeButton({ active, onClick, label, text }: { active: boolean; onClick: () => void; label: string; text: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="choice"
    >
      <span className="block text-sm font-medium">{label}</span>
      <span className="block text-[11px] text-zinc-500">{text}</span>
    </button>
  );
}

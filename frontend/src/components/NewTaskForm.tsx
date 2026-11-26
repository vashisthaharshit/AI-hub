// Manual mode: pick an agent, say what you want, start the chat. Everything
// runs in the current workspace's repo (on a copy until you Keep it).

import { useState, type FormEvent } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus, RunInfo, Workspace } from "../api/types";
import { isUsable } from "../hooks/useAgents";
import { agentColor, tint } from "../lib/theme";
import { ModelPicker } from "./ModelSelect";

const AGENT_BLURB: Record<string, string> = {
  "claude-code": "Careful, thorough; can ask before each change",
  codex: "Fast and direct",
};

const IDEAS = [
  "Add a /health endpoint that returns {status: 'ok'}, with a test",
  "Explain how this project is structured and where to start",
  "Find and fix the bug that makes the tests fail",
];

interface Props {
  workspace: Workspace;
  agents: AgentStatus[];
  onCreated: (run: RunInfo) => void;
  onWorkspaceChanged: (ws: Workspace) => void;
}

export function NewTaskForm({ workspace, agents, onCreated, onWorkspaceChanged }: Props) {
  const usable = agents.filter(isUsable);
  const [agentId, setAgentId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [testCommand, setTestCommand] = useState(workspace.test_command ?? "");
  const [supervised, setSupervised] = useState(false);
  const [model, setModel] = useState(""); // "" = agent default
  const [effort, setEffort] = useState(""); // "" = model default
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedId = agentId || usable[0]?.id || "";
  const selected = usable.find((a) => a.id === selectedId);
  const canSupervise = Boolean(selected?.supports_supervised);
  const canSubmit = Boolean(selectedId && prompt.trim()) && !submitting;

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const test = testCommand.trim();
      if (test !== (workspace.test_command ?? "")) {
        onWorkspaceChanged(await api.editWorkspace(workspace.id, { test_command: test })); // remembered per workspace
      }
      const run = await api.createRun({
        agent_id: selectedId,
        repo_path: workspace.repo_path,
        workspace_id: workspace.id,
        prompt: prompt.trim(),
        supervised: canSupervise && supervised,
        test_command: test || null,
        model: model || null,
        effort: effort || null,
      });
      onCreated(run);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="task-page">
      <header className="task-intro rise">
        <h2>What should an agent do?</h2>
        <p>
          Pick an agent and describe the job. It works on its own copy of <span className="text-zinc-200">{workspace.name}</span>,
          so nothing changes in your files until you review and keep it.
        </p>
      </header>

      <form onSubmit={submit} className="task-composer rise">
        <div className="task-fields">
          {usable.length === 0 ? (
            <p className="rounded-lg bg-amber-500/10 px-3 py-2.5 text-sm text-amber-300">
              No agent is ready. Install Claude Code or Codex and sign in, then click your agents in the top bar and press Re-check.
            </p>
          ) : (
            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-medium">Agent</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {usable.map((a) => (
                  <button key={a.id} type="button" className="choice" aria-pressed={a.id === selectedId}
                    style={tint(agentColor(a.id))}
                    onClick={() => { setAgentId(a.id); setModel(""); setEffort(""); }}>
                    <span className="flex items-center gap-2">
                      <span className="live-dot" data-live={a.id === selectedId} style={{ ["--dot" as string]: agentColor(a.id) }} />
                      <span className="font-semibold">{a.name}</span>
                      {a.account?.plan && <span className="text-xs text-zinc-500">{a.account.plan}</span>}
                    </span>
                    <span className="mt-1 block text-xs text-zinc-500">{AGENT_BLURB[a.id] ?? "Coding agent"}</span>
                  </button>
                ))}
              </div>
              {selected && (
                <div className="pt-1">
                  <ModelPicker agent={selected} model={model} effort={effort}
                    onChange={(m, e) => { setModel(m); setEffort(e); }} />
                </div>
              )}
            </fieldset>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">What do you want done?</span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit();
              }}
              rows={5}
              placeholder="Describe the change in plain words. The more specific, the better."
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
            />
          </label>
          {!prompt && (
            <div className="-mt-2 flex flex-wrap gap-2">
              {IDEAS.map((idea) => (
                <button key={idea} type="button" onClick={() => setPrompt(idea)}
                  className="rounded-full border border-zinc-800 px-3 py-1 text-xs text-zinc-400 transition hover:border-zinc-600 hover:text-zinc-200">
                  {idea}
                </button>
              ))}
            </div>
          )}

          <details className="group rounded-lg border border-zinc-800 px-3 py-2">
            <summary className="text-sm text-zinc-300">More options</summary>
            <div className="mt-3 grid gap-3 pb-1">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Test command</span>
                <input
                  value={testCommand}
                  onChange={(e) => setTestCommand(e.target.value)}
                  placeholder="pytest -q   or   npm test"
                  className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-sm"
                />
                <span className="text-xs text-zinc-500">Runs after each turn to check the work. Saved for this workspace.</span>
              </label>
              <label className={`supervision-option ${canSupervise ? "" : "opacity-60"}`}>
                <input type="checkbox" checked={canSupervise && supervised} disabled={!canSupervise}
                  onChange={(e) => setSupervised(e.target.checked)} className="mt-0.5 h-4 w-4 accent-sky-500" />
                <span className="text-sm">
                  <span className="font-medium">Ask me before each change</span>
                  <span className="block text-xs text-zinc-500">
                    {canSupervise ? "The agent waits for your OK before editing files or running commands."
                      : "Only agents that can pause and ask support this."}
                  </span>
                </span>
              </label>
            </div>
          </details>

          {error && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        <div className="task-footer">
          <span className="keyboard-hint"><kbd>Ctrl</kbd> + <kbd>Enter</kbd> to start</span>
          <button type="submit" disabled={!canSubmit} className="run-button">
            {submitting ? "Starting..." : "Start chat"}
          </button>
        </div>
      </form>
    </div>
  );
}

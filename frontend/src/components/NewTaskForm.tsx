// Pick an agent and a repo, describe the task, press Run.
//
// Why a text box for the repo instead of a folder picker: a page running in
// a browser is never told a folder's full path on disk, for privacy. A native
// folder dialog needs the backend (or a desktop shell) to open it; that's a
// later improvement. Recent repos and each repo's test command are remembered.

import { useState, type FormEvent } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus, RunInfo } from "../api/types";
import { isUsable } from "../hooks/useAgents";
import { ModelPicker } from "./ModelSelect";

const RECENT_KEY = "aihub.recentRepos";
const TEST_CMD_KEY = "aihub.testCommands"; // { [repoPath]: "pytest -q" }

function readJson<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "") as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable: just don't remember
  }
}

function remember(repo: string, testCommand: string) {
  const recent = readJson<string[]>(RECENT_KEY, []);
  writeJson(RECENT_KEY, [repo, ...recent.filter((p) => p !== repo)].slice(0, 8));
  writeJson(TEST_CMD_KEY, { ...readJson<Record<string, string>>(TEST_CMD_KEY, {}), [repo]: testCommand });
}

interface Props {
  agents: AgentStatus[];
  onCreated: (run: RunInfo) => void;
}

export function NewTaskForm({ agents, onCreated }: Props) {
  const usable = agents.filter(isUsable);
  const recent = readJson<string[]>(RECENT_KEY, []);
  const savedTestCommands = readJson<Record<string, string>>(TEST_CMD_KEY, {});

  const [agentId, setAgentId] = useState("");
  const [repoPath, setRepoPath] = useState(recent[0] ?? "");
  const [prompt, setPrompt] = useState("");
  const [testCommand, setTestCommand] = useState(savedTestCommands[recent[0] ?? ""] ?? "");
  const [supervised, setSupervised] = useState(false);
  const [model, setModel] = useState(""); // "" = agent default
  const [effort, setEffort] = useState(""); // "" = model default
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedId = agentId || usable[0]?.id || "";
  const selected = usable.find((a) => a.id === selectedId);
  const canSupervise = Boolean(selected?.supports_supervised);
  const canSubmit = Boolean(selectedId && repoPath.trim() && prompt.trim()) && !submitting;

  function changeRepo(value: string) {
    setRepoPath(value);
    if (savedTestCommands[value.trim()] !== undefined) setTestCommand(savedTestCommands[value.trim()]);
  }

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const repo = repoPath.trim();
      const run = await api.createRun({
        agent_id: selectedId,
        repo_path: repo,
        prompt: prompt.trim(),
        supervised: canSupervise && supervised,
        test_command: testCommand.trim() || null,
        model: model || null,
        effort: effort || null,
      });
      remember(repo, testCommand.trim());
      onCreated(run);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="task-page">
      <header className="task-intro">
        <h2>New task</h2>
        <p>
          Give an agent a task in your Git repository. Each run gets its own branch and working copy.
        </p>
      </header>

      <form onSubmit={submit} className="task-composer">
        <div className="task-fields">
          <div className="task-agent-grid">
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className="text-sm font-medium">Agent</span>
              {usable.length === 0 ? (
                <p className="rounded bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
                  No usable agent found. Install and sign in to one, then press Re-check in the sidebar.
                </p>
              ) : (
                <select
                  value={selectedId}
                  onChange={(e) => {
                    setAgentId(e.target.value);
                    setModel(""); // models differ per agent
                    setEffort("");
                  }}
                  className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
                >
                  {usable.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              )}
            </label>

            {selected && (
              <div className="flex min-w-0 flex-col gap-1.5">
                <span className="text-sm font-medium">Model</span>
                <ModelPicker
                  agent={selected}
                  model={model}
                  effort={effort}
                  onChange={(m, e) => {
                    setModel(m);
                    setEffort(e);
                  }}
                />
              </div>
            )}
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Repository folder</span>
            <input
              value={repoPath}
              onChange={(e) => changeRepo(e.target.value)}
              list="recent-repos"
              placeholder="D:\projects\my-app"
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-sm"
            />
            <datalist id="recent-repos">
              {recent.map((p) => (
                <option key={p} value={p} />
              ))}
            </datalist>
            <span className="text-xs text-zinc-500">Must be a git repo with at least one commit.</span>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Task</span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit();
              }}
              rows={5}
              placeholder="Add a /health endpoint that returns {status: 'ok'} and a test for it"
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
            />
          </label>

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
            <span className="text-xs text-zinc-500">
              Run by AI-Hub after each turn to check the agent's work. Remembered per repo.
            </span>
          </label>

          <label className={`supervision-option ${canSupervise ? "" : "opacity-65"}`}>
            <input
              type="checkbox"
              checked={canSupervise && supervised}
              disabled={!canSupervise}
              onChange={(e) => setSupervised(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-sky-500"
            />
            <span className="text-sm">
              <span className="font-medium">Supervised</span>
              <span className="block text-xs text-zinc-500">
                {canSupervise
                  ? "Ask for approval before file edits and commands."
                  : "Available with agents that support approval prompts."}
              </span>
            </span>
          </label>

          {error && <p role="alert" className="rounded bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        <div className="task-footer">
          <span className="keyboard-hint"><kbd>Ctrl</kbd> / <kbd>&#8984;</kbd> + <kbd>Enter</kbd></span>
          <button
            type="submit"
            disabled={!canSubmit}
            className="run-button"
          >
            {submitting ? "Starting..." : "Run task"} <span aria-hidden="true">&rarr;</span>
          </button>
        </div>
      </form>
    </div>
  );
}

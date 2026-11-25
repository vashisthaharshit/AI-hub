// Settings: which LLM writes summaries (used for the handoff when you switch
// agents mid-run).
//
//   Off            -> no LLM: the handoff is a template built from the run's events
//   One of my agents -> e.g. Claude Code + a small model; uses that agent's plan, no key
//   Groq / OpenRouter -> YOUR OWN API key, saved in this computer's keychain
//
// The key is sent to the backend once, stored by the OS, and never sent back.

import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus, SettingsView, SummaryProvider, SummarySettings, SummaryTest } from "../api/types";
import { isUsable } from "../hooks/useAgents";
import { ModelPicker } from "./ModelSelect";

const PROVIDERS: { id: SummaryProvider; title: string; text: string }[] = [
  { id: "off", title: "Off (template only)", text: "No LLM. The new agent gets a structured list of what happened. Free and instant." },
  { id: "agent", title: "One of my agents", text: "Your own Claude Code or Codex writes the summary. Uses that agent's plan; no key needed." },
  { id: "groq", title: "Groq (my own key)", text: "Fast open models. Get a key at console.groq.com." },
  { id: "openrouter", title: "OpenRouter (my own key)", text: "Many models, some free. Get a key at openrouter.ai." },
];

export function SettingsPage({ agents }: { agents: AgentStatus[] }) {
  const [view, setView] = useState<SettingsView | null>(null);
  const [form, setForm] = useState<SummarySettings>({ provider: "off", agent_id: null, model: null, effort: null });
  const [keyInput, setKeyInput] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [test, setTest] = useState<SummaryTest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.settings().then((v) => {
      setView(v);
      setForm(v.summary);
    }).catch((e) => setError(errorText(e)));
  }, []);

  const usable = agents.filter(isUsable);
  const isHttp = form.provider === "groq" || form.provider === "openrouter";
  const summaryAgent = agents.find((a) => a.id === form.agent_id);

  function update(patch: Partial<SummarySettings>) {
    setForm((f) => ({ ...f, ...patch }));
    setTest(null);
    setSaved(false);
  }

  function pickProvider(provider: SummaryProvider) {
    setModels([]);
    update({
      provider,
      agent_id: provider === "agent" ? form.agent_id ?? usable[0]?.id ?? null : null,
      model: null,
      effort: null,
    });
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (!view) {
    return <p className="p-10 text-sm text-zinc-500">{error ?? "Loading settings..."}</p>;
  }

  const keySaved = isHttp && view.keys[form.provider];

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5 px-6 py-10">
      <div>
        <h2 className="text-xl font-semibold">Settings</h2>
        <p className="mt-1 text-sm text-zinc-400">
          <span className="font-medium text-zinc-200">Summary model</span>: writes the summary a new agent gets when you
          switch agents in the middle of a run.
        </p>
      </div>

      <fieldset className="space-y-2">
        {PROVIDERS.map((p) => (
          <label
            key={p.id}
            className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 ${
              form.provider === p.id ? "border-sky-600 bg-sky-500/5" : "border-zinc-800 hover:border-zinc-700"
            }`}
          >
            <input
              type="radio"
              name="provider"
              checked={form.provider === p.id}
              onChange={() => pickProvider(p.id)}
              className="mt-1 accent-sky-500"
            />
            <span>
              <span className="text-sm font-medium">{p.title}</span>
              <span className="block text-xs text-zinc-500">{p.text}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {form.provider === "agent" && (
        <div className="space-y-2 rounded-md border border-zinc-800 p-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Agent</span>
            <select
              aria-label="Summary agent"
              value={form.agent_id ?? ""}
              onChange={(e) => update({ agent_id: e.target.value, model: null, effort: null })}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
            >
              {usable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          {summaryAgent && (
            <>
              <span className="text-sm font-medium">Model</span>
              <ModelPicker
                agent={summaryAgent}
                model={form.model ?? ""}
                effort={form.effort ?? ""}
                onChange={(m, e) => update({ model: m || null, effort: e || null })}
              />
              <p className="text-xs text-zinc-500">
                Tip: a small, fast model (like Haiku) with low effort is plenty for summaries and uses less of your plan.
              </p>
            </>
          )}
        </div>
      )}

      {isHttp && (
        <div className="space-y-3 rounded-md border border-zinc-800 p-3">
          {!view.keyring_available && (
            <p className="rounded bg-amber-500/10 px-2 py-1.5 text-xs text-amber-300">
              Saving keys needs the keyring package. In the backend folder run: pip install -e .
            </p>
          )}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Your API key</span>
              <span className={`text-xs ${keySaved ? "text-emerald-400" : "text-zinc-500"}`}>
                {keySaved ? "Saved in this computer's keychain" : "Not set"}
              </span>
            </div>
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                aria-label="API key"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder={keySaved ? "Enter a new key to replace it" : "Paste your key"}
                className="min-w-0 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-sm"
              />
              <button
                onClick={() => run("key", async () => {
                  setView(await api.saveKey(form.provider, keyInput.trim()));
                  setKeyInput("");
                })}
                disabled={keyInput.trim().length < 8 || busy !== null}
                className="rounded-md bg-zinc-700 px-3 text-sm hover:bg-zinc-600 disabled:opacity-40"
              >
                Save key
              </button>
              {keySaved && (
                <button
                  onClick={() => run("key", async () => setView(await api.deleteKey(form.provider)))}
                  disabled={busy !== null}
                  className="rounded-md border border-zinc-700 px-3 text-sm hover:border-red-500 hover:text-red-300"
                >
                  Remove
                </button>
              )}
            </div>
            <p className="text-xs text-zinc-500">
              Stored by your operating system (Windows Credential Manager), never in AI-Hub's files or the repo,
              and never shown again.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="flex items-center justify-between text-sm font-medium">
              <label htmlFor="provider-model">Model</label>
              <button
                type="button"
                onClick={() => run("models", async () => setModels((await api.providerModels(form.provider)).models))}
                disabled={!keySaved || busy !== null}
                className="text-xs font-normal text-sky-400 hover:text-sky-300 disabled:opacity-40"
              >
                {busy === "models" ? "Loading..." : models.length ? `${models.length} models loaded` : "Load models"}
              </button>
            </span>
            <input
              id="provider-model"
              aria-label="Provider model"
              list="provider-models"
              value={form.model ?? ""}
              onChange={(e) => update({ model: e.target.value || null })}
              placeholder={form.provider === "groq" ? "e.g. llama-3.3-70b-versatile" : "e.g. meta-llama/llama-3.3-70b-instruct"}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-sm"
            />
            <datalist id="provider-models">
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </div>
      )}

      {error && <p className="rounded bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

      {test && (
        <div className={`rounded-md border px-3 py-2.5 text-sm ${test.ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5"}`}>
          <p className={`text-xs font-semibold ${test.ok ? "text-emerald-300" : "text-red-300"}`}>
            {test.ok ? `Works · ${test.used} · ${((test.duration_ms ?? 0) / 1000).toFixed(1)}s` : `Didn't work · ${test.used}`}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-zinc-300">{test.ok ? test.summary : test.error}</p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() => run("save", async () => {
            setView(await api.saveSummary(form));
            setSaved(true);
          })}
          disabled={busy !== null}
          className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
        >
          Save
        </button>
        {form.provider !== "off" && (
          <button
            onClick={() => run("test", async () => setTest(await api.testSummary(form)))}
            disabled={busy !== null}
            className="rounded-md border border-zinc-700 px-4 py-2 text-sm hover:border-zinc-500 disabled:opacity-40"
          >
            {busy === "test" ? "Testing..." : "Test"}
          </button>
        )}
        {saved && <span className="text-xs text-emerald-400">Saved</span>}
        {form.provider !== "off" && !test && (
          <span className="text-xs text-zinc-500">Test summarizes a tiny example, so you know it works.</span>
        )}
      </div>
    </div>
  );
}

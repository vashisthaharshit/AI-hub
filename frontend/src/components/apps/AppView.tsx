// One connected app, in three tabs:
//   Guide            for beginners: what it does, what to ask, how to start, a box to type in
//   Commands         one card per command, for people who know what they want
//   Activity         the runs of this app
//   Setup & details  setup form, tools it needs (with Install), what the repo lets the agent do,
//                    and Update (newest version from GitHub, with a backup first)

import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AppInfo, AppPermissions, RunInfo, SetupView } from "../../api/types";
import { StatusBadge } from "../StatusBadge";
import { AppRunPanel } from "./AppRunPanel";
import { CommandCard } from "./CommandCard";
import { GuidePanel } from "./GuidePanel";
import { SetupPanel } from "./SetupPanel";
import { ToolsPanel } from "./ToolsPanel";
import { UpdatePanel } from "./UpdatePanel";
import { useGuide } from "./useGuide";

const AGENT_NAMES: Record<string, string> = { claude: "Claude Code", codex: "Codex" };

type Tab = "guide" | "commands" | "activity" | "setup";
const TABS: { id: Tab; label: string }[] = [
  { id: "guide", label: "Guide" },
  { id: "commands", label: "Commands" },
  { id: "activity", label: "Activity" },
  { id: "setup", label: "Setup & details" },
];

interface Props {
  appId: string;
  onChanged: () => void; // refresh the sidebar list
  onRemoved: () => void;
}

export function AppView({ appId, onChanged, onRemoved }: Props) {
  const [app, setApp] = useState<AppInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"rescan" | "remove" | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [tab, setTab] = useState<Tab>("guide");
  const guideState = useGuide(appId); // the Guide tab, and the forms on the command cards
  const [setupView, setSetupView] = useState<SetupView | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [runsVersion, setRunsVersion] = useState(0);
  const refreshRuns = useCallback(() => setRunsVersion((v) => v + 1), []);

  useEffect(() => {
    let active = true;
    const load = () => api.appRuns(appId).then((r) => active && setRuns(r)).catch(() => {});
    void load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [appId, runsVersion]);
  const runBusy = runs.some((r) => r.state === "running" || r.state === "pending"); // one run at a time

  async function startRun(body: { command?: string; values?: Record<string, string>; message?: string }) {
    const run = await api.runApp(appId, body);
    setSelectedRun(run.id);
    setTab("activity");
    refreshRuns();
  }

  useEffect(() => {
    api.appSetup(appId).then(setSetupView).catch(() => setSetupView(null));
  }, [appId, app?.updated_at]);
  const [highlight, setHighlight] = useState<string | null>(null); // command card to point at

  // Guide -> "Setup your profile →" opens that card in the Commands tab.
  useEffect(() => {
    if (tab !== "commands" || !highlight) return;
    document.getElementById(`cmd-${highlight}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    const timer = setTimeout(() => setHighlight(null), 2500);
    return () => clearTimeout(timer);
  }, [tab, highlight]);

  useEffect(() => {
    api.app(appId).then(setApp).catch((e) => setError(errorText(e)));
  }, [appId]);

  if (!app) return <p className="p-10 text-sm text-zinc-500">{error ?? "Loading app..."}</p>;
  const m = app.manifest;
  const missingTools = m.requirements.filter((r) => !r.installed && !r.optional);
  const tabs = TABS.filter((t) => t.id !== "commands" || m.commands.length > 0)
    .filter((t) => t.id !== "guide" || m.tier !== 3);
  const current = tabs.some((t) => t.id === tab) ? tab : tabs[0].id;

  async function rescan() {
    setBusy("rescan");
    setError(null);
    try {
      setApp(await api.rescanApp(appId));
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("remove");
    try {
      await api.forgetApp(appId);
      onRemoved();
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  return (
    <div className="task-page space-y-6">
      <div className="task-intro">
        <h2>{m.name}</h2>
        {m.summary && <p>{m.summary}</p>}
        {m.agents.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {m.agents.map((a) => (
              <span key={a} className="rounded-full border border-zinc-700 px-2.5 py-0.5 text-zinc-300">
                {a === m.primary_agent ? "Built for " : "Also works with "}
                {AGENT_NAMES[a] ?? a}
              </span>
            ))}
          </div>
        )}
      </div>

      {m.tier === 3 && (
        <Notice>
          This repo has no instructions for an AI agent (no CLAUDE.md, AGENTS.md or commands), so there's nothing to
          turn into buttons. You can still work on it with an agent in Manual mode.
        </Notice>
      )}
      {m.tier === 2 && (
        <Notice>
          This repo has instructions for an agent but no commands, so there are no buttons yet. Asking it questions
          in a chat is coming in a later step.
        </Notice>
      )}

      <nav className="flex gap-1 border-b border-zinc-800" aria-label="App sections">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            aria-current={current === t.id ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              current === t.id ? "border-sky-400 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-200"
            }`}
          >
            {t.label}
            {t.id === "setup" && (missingTools.length > 0 || (setupView?.missing.length ?? 0) > 0) && (
              <span className="ml-1.5 text-amber-400">!</span>
            )}
          </button>
        ))}
      </nav>

      {current !== "setup" && setupView && setupView.missing.length > 0 && (
        <p className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-sky-900/60 bg-sky-950/30 px-3 py-2 text-sm text-sky-200">
          <span>
            Finish setup: {setupView.missing.length} setting{setupView.missing.length > 1 ? "s" : ""} needed (like{" "}
            {setupView.setup.fields.find((f) => f.id === setupView.missing[0])?.label}).
          </span>
          <button onClick={() => setTab("setup")} className="rounded-md bg-sky-300 px-2.5 py-1 text-xs font-semibold text-zinc-950">
            Open settings
          </button>
        </p>
      )}

      {current !== "setup" && missingTools.length > 0 && (
        <p className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          <span>
            {missingTools.map((t) => t.label).join(", ")} {missingTools.length > 1 ? "are" : "is"} missing. The app
            may not work until {missingTools.length > 1 ? "they're" : "it's"} installed.
          </span>
          <button onClick={() => setTab("setup")} className="rounded-md bg-amber-300 px-2.5 py-1 text-xs font-semibold text-zinc-950">
            Install
          </button>
        </p>
      )}

      {current === "guide" && (
        <GuidePanel
          state={guideState}
          commands={m.commands}
          busy={runBusy}
          onAsk={(message) => startRun({ message })}
          onOpenCommand={(name) => {
            setHighlight(name);
            setTab("commands");
          }}
        />
      )}

      {current === "commands" && (
        <section>
          <p className="mb-3 text-xs text-zinc-500">
            Each card runs one of the repo's commands with{" "}
            {new Set(m.commands.map((c) => c.agent)).size > 1
              ? "the agent it was made for (marked on Codex cards)"
              : AGENT_NAMES[m.commands[0]?.agent ?? m.primary_agent ?? ""] ?? "the agent"}
            , in this app's folder. You'll see what it does in the Activity tab.
            {guideState.writing && " Making the cards simpler..."}
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {m.commands.map((c) => (
              <CommandCard
                key={c.source + c.name}
                appId={appId}
                command={c}
                form={guideState.guide?.commands[c.name] ?? null}
                highlighted={c.name === highlight}
                busy={runBusy}
                onRun={(values) => startRun({ command: c.name, values })}
              />
            ))}
          </div>
        </section>
      )}

      {current === "activity" && (
        <div className="space-y-4">
          {runs.length === 0 ? (
            <p className="text-sm text-zinc-500">Nothing has run yet. Click Run on a command, or type a request in the Guide.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {runs.map((r) => (
                <li key={r.id}>
                  <button
                    onClick={() => setSelectedRun(r.id)}
                    className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
                      (selectedRun ?? runs[0].id) === r.id ? "border-sky-600 bg-sky-500/10" : "border-zinc-800 hover:border-zinc-600"
                    }`}
                  >
                    <span>{r.label ?? "Run"}</span>
                    <StatusBadge state={r.state} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {runs.length > 0 && (
            <AppRunPanel key={selectedRun ?? runs[0].id} appId={appId} runId={selectedRun ?? runs[0].id} onChanged={refreshRuns} />
          )}
        </div>
      )}

      {current === "setup" && (
        <>
          {setupView && <TrustChoice appId={appId} view={setupView} onChange={setSetupView} />}
          {setupView && <SetupPanel appId={appId} view={setupView} onSaved={setSetupView} />}
          {(m.plugins.length > 0 || m.setup.installed_skills.length > 0) && (
            <p className="text-xs text-zinc-500">
              {m.plugins.length > 0 &&
                `Loaded as a Claude Code plugin (${m.plugins.map((p) => p.name).join(", ")}) when it runs. Nothing is installed on your computer. `}
              {m.setup.installed_skills.length > 0 &&
                `${m.setup.installed_skills.length} skill${m.setup.installed_skills.length > 1 ? "s" : ""} copied into .claude/skills so Claude Code can find them.`}
            </p>
          )}
          {m.requirements.length > 0 && (
            <ToolsPanel
              appId={appId}
              requirements={m.requirements}
              onChange={(requirements) => {
                setApp({ ...app, manifest: { ...m, requirements } });
                onChanged();
              }}
            />
          )}
          <PermissionsSection permissions={m.permissions} />
          {app.url && (
            <UpdatePanel
              appId={appId}
              busy={runBusy}
              onUpdated={(updated) => {
                setApp(updated);
                onChanged();
              }}
            />
          )}
        </>
      )}

      <section className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 pt-4 text-xs text-zinc-500">
        <span className="min-w-0 break-all">
          Folder: <span className="font-mono text-zinc-400">{app.path}</span>
        </span>
        <div className="flex items-center gap-2">
          <button onClick={rescan} disabled={busy !== null} className="workspace-button text-xs">
            {busy === "rescan" ? "Reading..." : "Read the folder again"}
          </button>
          {confirmRemove ? (
            <>
              <span>Remove from the list? The folder and your files stay.</span>
              <button onClick={remove} disabled={busy !== null} className="rounded-md bg-red-900/60 px-2.5 py-1 text-red-200 hover:bg-red-900">
                Remove
              </button>
              <button onClick={() => setConfirmRemove(false)} className="px-1 hover:text-zinc-200">Cancel</button>
            </>
          ) : (
            <button onClick={() => setConfirmRemove(true)} className="workspace-button text-xs">Remove from list</button>
          )}
        </div>
      </section>
      {error && <p className="text-sm text-red-300">{error}</p>}
    </div>
  );
}

function TrustChoice({ appId, view, onChange }: { appId: string; view: SetupView; onChange: (v: SetupView) => void }) {
  const [error, setError] = useState<string | null>(null);
  const money = view.setup.always_ask.length > 0 || view.setup.money_commands.length > 0;
  async function set(trusted: boolean) {
    setError(null);
    try {
      await api.setAppTrust(appId, trusted);
      onChange({ ...view, trusted });
    } catch (e) {
      setError(errorText(e));
    }
  }
  const options: [boolean, string, string][] = [
    [false, "Ask me first (recommended)", "Editing files in this app's folder is allowed. Anything else (running programs, the internet, outside services) waits for your Allow."],
    [true, "Trust this repo", "AI-Hub says yes for you, so commands run without stopping." + (money ? " Orders and payments still always ask you." : "")],
  ];
  return (
    <section className="space-y-2 rounded-lg border border-zinc-800 p-4">
      <h3 className="text-sm font-semibold">When the agent wants to do something</h3>
      {options.map(([value, title, text]) => (
        <label key={title} className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 ${
          view.trusted === value ? "border-sky-600 bg-sky-500/5" : "border-zinc-800 hover:border-zinc-700"}`}>
          <input type="radio" name={`trust-${appId}`} checked={view.trusted === value} onChange={() => set(value)} className="mt-1 accent-sky-500" />
          <span>
            <span className="text-sm font-medium">{title}</span>
            <span className="block text-xs text-zinc-500">{text}</span>
          </span>
        </label>
      ))}
      {error && <p className="text-xs text-red-300">{error}</p>}
    </section>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return <p className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2.5 text-sm text-zinc-300">{children}</p>;
}

function PermissionsSection({ permissions: p }: { permissions: AppPermissions }) {
  if (!p.found) {
    return (
      <p className="text-xs text-zinc-500">
        This repo doesn't set its own rules for the agent. AI-Hub will ask you before the agent runs commands or
        edits files.
      </p>
    );
  }
  const groups: [string, string[], string][] = [
    ["Allowed without asking", p.allow, "text-zinc-300"],
    ["Asks you first", p.ask, "text-amber-200"],
    ["Never allowed", p.deny, "text-zinc-500"],
  ];
  return (
    <details className="rounded-lg border border-zinc-800 p-4">
      <summary className="cursor-pointer text-sm font-semibold">
        What the repo lets the agent do{" "}
        <span className="text-xs font-normal text-zinc-500">
          ({p.allow.length} allowed, {p.ask.length} ask first, {p.deny.length} blocked)
        </span>
      </summary>
      {p.error && <p className="mt-2 text-xs text-red-300">{p.error}</p>}
      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        {groups.map(([title, items, color]) => (
          <div key={title}>
            <h4 className="mb-1 text-xs font-semibold text-zinc-400">{title}</h4>
            {items.length === 0 ? (
              <p className="text-xs text-zinc-600">Nothing</p>
            ) : (
              <ul className={`space-y-0.5 text-xs ${color}`}>
                {items.map((item) => <li key={item} className="break-words">{item}</li>)}
              </ul>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

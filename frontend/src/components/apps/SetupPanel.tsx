// "Your settings": what the app needs from the user before it works.
//
//   Keys & accounts   API keys, tokens (saved in Windows' Credential Manager,
//                     never shown again) and other environment variables
//   Safe mode         paper trading / sandbox / dry run switches; turning one
//                     off asks for a clear "I understand" first
//   <config file>     settings AI-Hub writes into the repo's config file
//   Connections       outside services (MCP servers); money ones always ask
//
// Built from the repo (backend: apps/setup.py), the same for every repo.

import { useState } from "react";
import { api, errorText } from "../../api/client";
import type { SetupField, SetupView } from "../../api/types";

interface Props {
  appId: string;
  view: SetupView;
  onSaved: (view: SetupView) => void;
}

export function SetupPanel({ appId, view, onSaved }: Props) {
  const { setup } = view;
  const [draft, setDraft] = useState<Record<string, string>>({}); // changed answers only
  const [showMore, setShowMore] = useState(false);
  const [confirmOff, setConfirmOff] = useState<SetupField | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  if (setup.fields.length === 0 && setup.servers.length === 0) return null;

  const value = (f: SetupField) => draft[f.id] ?? view.values[f.id] ?? "";
  const set = (f: SetupField, v: string) => {
    setDraft((d) => ({ ...d, [f.id]: v }));
    setSavedNote(null);
  };
  const isUnsafe = (f: SetupField, v: string) => f.safety && f.safe_value !== null && v !== "" && v.toLowerCase() !== f.safe_value;

  const safety = setup.fields.filter((f) => f.safety);
  const regular = setup.fields.filter((f) => !f.safety);
  const main = regular.filter((f) => !f.advanced);
  const more = regular.filter((f) => f.advanced);
  const groups = groupBy(main);
  const changed = Object.keys(draft).length > 0;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.saveAppSetup(appId, draft, confirmed);
      setDraft({});
      setConfirmed(false);
      onSaved(next);
      setSavedNote(next.missing.length ? `Saved. ${next.missing.length} still needed.` : "Saved. Everything it needs is filled in.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-5 rounded-lg border border-zinc-800 p-4">
      <div>
        <h3 className="text-sm font-semibold">Your settings</h3>
        <p className="text-xs text-zinc-500">
          {view.missing.length
            ? `${view.missing.length} needed before the app works. Keys are kept in Windows' Credential Manager, not in files.`
            : "Everything needed is filled in. Keys are kept in Windows' Credential Manager, not in files."}
        </p>
      </div>

      {safety.length > 0 && (
        <div className="space-y-2 rounded-md border border-emerald-900/60 bg-emerald-950/20 p-3">
          <p className="text-xs font-semibold text-emerald-300">🛡️ Safe mode</p>
          {safety.map((f) => {
            const on = !isUnsafe(f, value(f));
            return (
              <label key={f.id} className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-emerald-500"
                  checked={on}
                  onChange={(e) => {
                    if (e.target.checked) set(f, f.safe_value ?? "true");
                    else setConfirmOff(f); // turning safe mode off: ask first
                  }}
                />
                <span>
                  <span className="text-zinc-200">{f.label}</span>{" "}
                  <span className={on ? "text-emerald-400" : "text-red-300"}>{on ? "on (safe)" : "OFF: real actions"}</span>
                  {f.help && <span className="block text-[11px] text-zinc-500">{f.help}</span>}
                </span>
              </label>
            );
          })}
          {confirmOff && (
            <div className="space-y-2 rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-200">
              <p>
                Turning off <b>{confirmOff.label}</b> means the app works with real accounts, which can cost real
                money. You will still be asked before each order or payment.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    set(confirmOff, (confirmOff.safe_value ?? "true") === "true" ? "false" : "true");
                    setConfirmed(true);
                    setConfirmOff(null);
                  }}
                  className="rounded-md bg-red-800 px-2.5 py-1 font-semibold text-white hover:bg-red-700"
                >
                  I understand, turn it off
                </button>
                <button onClick={() => setConfirmOff(null)} className="px-2 hover:text-white">Keep it on</button>
              </div>
            </div>
          )}
        </div>
      )}

      {groups.map(([title, fields]) => (
        <div key={title} className="space-y-3">
          <p className="text-xs font-semibold text-zinc-400">{title}</p>
          {fields.map((f) => (
            <SetupInput key={f.id} field={f} value={value(f)} secretSet={view.secrets_set.includes(f.id)}
              missing={view.missing.includes(f.id) && !draft[f.id]} onChange={(v) => set(f, v)} />
          ))}
        </div>
      ))}

      {more.length > 0 && (
        <div>
          <button onClick={() => setShowMore((s) => !s)} className="text-xs text-zinc-400 hover:text-zinc-100" aria-expanded={showMore}>
            {showMore ? "▾" : "▸"} More settings ({more.length}, the defaults usually work)
          </button>
          {showMore && (
            <div className="mt-3 space-y-3 border-l border-zinc-800 pl-3">
              {more.map((f) => (
                <SetupInput key={f.id} field={f} value={value(f)} secretSet={view.secrets_set.includes(f.id)}
                  missing={false} onChange={(v) => set(f, v)} />
              ))}
            </div>
          )}
        </div>
      )}

      {setup.servers.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-zinc-400">Connections to outside services</p>
          {setup.servers.map((s) => (
            <div key={s.name} className="rounded-md bg-zinc-900 px-3 py-2 text-xs">
              <p>
                <span className="font-medium text-zinc-200">{s.name}</span>{" "}
                {view.servers_ready[s.name]
                  ? <span className="text-emerald-400">✓ ready</span>
                  : <span className="text-amber-300">needs: {s.env.join(", ")}</span>}
              </p>
              {s.money && (
                <p className="mt-0.5 text-[11px] text-amber-200">
                  💸 Can move real money (orders or payments). AI-Hub asks you before every use, even if you trust the
                  repo.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {setup.files.length > 0 && (
        <p className="text-[11px] text-zinc-500">
          Saving writes: {setup.files.map((f) => f.target).join(", ")} (inside the app's folder, made from the
          repo's examples; anything else you change in them is kept).
        </p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={busy || (!changed && view.saved)}
          className="rounded-md bg-sky-300 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-sky-200 disabled:opacity-40"
        >
          {busy ? "Saving..." : "Save settings"}
        </button>
        {savedNote && <span className="text-xs text-emerald-400">{savedNote}</span>}
        {error && <span className="text-xs text-red-300">{error}</span>}
      </div>
    </section>
  );
}

function groupBy(fields: SetupField[]): [string, SetupField[]][] {
  const groups = new Map<string, SetupField[]>();
  for (const f of fields) {
    const title = f.kind === "env" ? "Keys and accounts" : f.kind === "plugin" ? `Plugin settings (${f.target})` : `Saved in ${f.target}`;
    groups.set(title, [...(groups.get(title) ?? []), f]);
  }
  return [...groups.entries()];
}

const box = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs";

function SetupInput({ field: f, value, secretSet, missing, onChange }: {
  field: SetupField; value: string; secretSet: boolean; missing: boolean; onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-zinc-200">
        {f.label}
        {f.required && <span className="text-sky-300"> *</span>}
        {missing && <span className="ml-2 text-[11px] text-amber-300">needed</span>}
      </p>
      {f.type === "toggle" ? (
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input type="checkbox" className="accent-sky-500" checked={value === "true"}
            onChange={(e) => onChange(e.target.checked ? "true" : "false")} />
          {value === "true" ? "On" : "Off"}
        </label>
      ) : f.type === "choice" ? (
        <div className="flex flex-wrap gap-1.5">
          {f.choices.map((c) => (
            <button key={c} type="button" onClick={() => onChange(c)}
              className={`rounded-full border px-2.5 py-1 text-xs ${value === c ? "border-sky-500 bg-sky-500/15 text-sky-100" : "border-zinc-700 text-zinc-300"}`}>
              {c}
            </button>
          ))}
        </div>
      ) : (
        <input
          type={f.type === "secret" ? "password" : f.type === "number" ? "number" : f.type === "email" ? "email" : "text"}
          autoComplete="off"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={f.type === "secret" && secretSet ? "•••••••• saved (type to replace)" : f.default ?? ""}
          className={box}
        />
      )}
      {f.help && <p className="text-[11px] text-zinc-500">{f.help}</p>}
    </div>
  );
}

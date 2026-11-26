// One command as a card: a plain title and description, the questions it
// needs (from the guide's form), and Run. Fields that are rarely needed sit
// under "Add details (optional)" so the card stays simple.

import { useState } from "react";
import type { AppCommand, CommandForm, FormField } from "../../api/types";
import { FileDrop } from "./FileDrop";

interface Props {
  appId: string;
  command: AppCommand;
  form: CommandForm | null;
  highlighted: boolean;
  busy: boolean; // this app is already running something
  onRun: (values: Record<string, string>) => Promise<void>;
}

export function CommandCard({ appId, command: c, form, highlighted, busy, onRun }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [showMore, setShowMore] = useState(false);
  const [confirming, setConfirming] = useState(false); // risky commands: "Are you sure?"
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = form?.fields ?? [];
  const main = fields.filter((f) => !f.advanced);
  const more = fields.filter((f) => f.advanced);
  const missing = main.filter((f) => f.required && !values[f.key]?.trim());
  const set = (key: string, value: string) => setValues((v) => ({ ...v, [key]: value }));

  async function start() {
    setConfirming(false);
    setStarting(true);
    setError(null);
    try {
      await onRun(values);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  const border =
    c.kind === "start" ? "border-sky-700 bg-sky-500/5" : c.kind === "danger" ? "border-red-900/70" : "border-zinc-800";

  return (
    <div
      id={`cmd-${c.name}`}
      className={`flex flex-col gap-3 rounded-lg border p-3.5 transition-shadow ${border} ${
        highlighted ? "ring-2 ring-sky-400" : ""
      }`}
    >
      <div>
        {c.kind === "start" && <span className="mb-1 block text-[11px] font-semibold text-sky-300">⭐ Start here</span>}
        {c.kind === "danger" && <span className="mb-1 block text-[11px] font-semibold text-red-300">Careful: can delete data</span>}
        {c.money && (
          <span className="mb-1 block text-[11px] font-semibold text-amber-300">
            💸 Can use real money: asks you before every order or payment
          </span>
        )}
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-semibold">{form?.title ?? c.title}</p>
          <span className="flex shrink-0 items-center gap-1.5">
            {c.agent === "codex" && (
              <span className="rounded border border-zinc-700 px-1 text-[10px] text-zinc-400" title="A Codex skill: runs with Codex">
                Codex
              </span>
            )}
            <code className="truncate text-[11px] text-zinc-600" title={c.from_skill ? "A skill you can start like a command" : c.source}>
              {c.invoke || `/${c.name}`}
            </code>
          </span>
        </div>
        {(form?.description ?? c.description) && (
          <p className="mt-0.5 text-xs text-zinc-400">{form?.description ?? c.description}</p>
        )}
      </div>

      {main.map((f) => (
        <Field key={f.key} appId={appId} field={f} value={values[f.key] ?? ""} onChange={(v) => set(f.key, v)} />
      ))}

      {more.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowMore((s) => !s)}
            className="text-xs text-zinc-400 hover:text-zinc-100"
            aria-expanded={showMore}
          >
            {showMore ? "▾" : "▸"} Add details (optional)
          </button>
          {showMore && (
            <div className="mt-2 space-y-3 border-l border-zinc-800 pl-3">
              {more.map((f) => (
                <Field key={f.key} appId={appId} field={f} value={values[f.key] ?? ""} onChange={(v) => set(f.key, v)} />
              ))}
            </div>
          )}
        </div>
      )}

      {confirming && (
        <div className="space-y-2 rounded-md border border-red-900 bg-red-950/40 p-2.5 text-xs text-red-200">
          <p>{c.money ? "This can use real money. You'll still approve each order or payment." : "This can delete your data in this app."} Run it?</p>
          <div className="flex gap-2">
            <button onClick={() => void start()} className="rounded-md bg-red-800 px-2.5 py-1 font-semibold text-white hover:bg-red-700">
              Yes, run it
            </button>
            <button onClick={() => setConfirming(false)} className="px-2 hover:text-white">Cancel</button>
          </div>
        </div>
      )}

      <div className="mt-auto flex items-center gap-2">
        <button
          onClick={() => (c.kind === "danger" || c.money ? setConfirming(true) : void start())}
          disabled={busy || starting || missing.length > 0 || confirming}
          title={busy ? "This app is already running something" : undefined}
          className="rounded-md bg-sky-300 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-200 disabled:bg-zinc-800 disabled:font-normal disabled:text-zinc-500"
        >
          {starting ? "Starting..." : "Run"}
        </button>
        {missing.length > 0 && <span className="text-[11px] text-zinc-500">Fill in: {missing.map((f) => f.label).join(", ")}</span>}
        {busy && missing.length === 0 && <span className="text-[11px] text-zinc-500">Another command is running</span>}
      </div>
      {error && <p className="text-[11px] text-red-300">{error}</p>}
    </div>
  );
}

const box = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs";

function Field({ appId, field: f, value, onChange }: {
  appId: string; field: FormField; value: string; onChange: (v: string) => void;
}) {
  const [mode, setMode] = useState<"link" | "text">("link"); // url_or_text

  if (f.type === "toggle") {
    return (
      <label className="flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={value !== ""}
          onChange={(e) => onChange(e.target.checked ? f.value ?? "" : "")}
          className="mt-0.5 accent-sky-500"
        />
        <span>
          <span className="text-zinc-200">{f.label}</span>
          {f.help && <span className="block text-[11px] text-zinc-500">{f.help}</span>}
        </span>
      </label>
    );
  }

  return (
    <div className="space-y-1">
      <p className="text-xs text-zinc-200">
        {f.label}
        {f.required && <span className="text-sky-300"> *</span>}
      </p>

      {f.type === "choice" ? (
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={f.label}>
          {f.choices.map((ch) => (
            <button
              key={ch.value}
              type="button"
              role="radio"
              aria-checked={value === ch.value}
              // click again to clear an optional choice
              onClick={() => onChange(value === ch.value && !f.required ? "" : ch.value)}
              className={`rounded-full border px-2.5 py-1 text-xs ${
                value === ch.value ? "border-sky-500 bg-sky-500/15 text-sky-100" : "border-zinc-700 text-zinc-300 hover:border-zinc-500"
              }`}
            >
              {ch.label}
            </button>
          ))}
        </div>
      ) : f.type === "file" ? (
        <FileDrop
          appId={appId}
          field={f}
          saved={value ? value.split("\n") : []}
          onSaved={(paths) => onChange(paths.join("\n"))}
        />
      ) : f.type === "url_or_text" ? (
        <>
          <div className="flex gap-1 text-[11px]">
            {(["link", "text"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m);
                  onChange("");
                }}
                className={`rounded px-2 py-0.5 ${mode === m ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-100"}`}
              >
                {m === "link" ? "Paste a link" : "Paste the text"}
              </button>
            ))}
          </div>
          {mode === "link" ? (
            <input type="url" value={value} onChange={(e) => onChange(e.target.value)} placeholder="https://..." className={box} />
          ) : (
            <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={5} placeholder="Paste it here" className={`${box} min-h-28 resize-y`} />
          )}
        </>
      ) : f.type === "long_text" ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={3} placeholder={f.placeholder ?? ""} className={`${box} min-h-20 resize-y`} />
      ) : (
        <input
          type={f.type === "number" ? "number" : f.type === "date" ? "date" : f.type === "url" ? "url" : "text"}
          min={f.type === "number" ? 1 : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={f.placeholder ?? ""}
          className={box}
        />
      )}

      {f.help && <p className="text-[11px] text-zinc-500">{f.help}</p>}
    </div>
  );
}

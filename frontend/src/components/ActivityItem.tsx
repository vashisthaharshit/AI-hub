// How one item of a run's activity looks: a message, a tool card, an
// approval request, the result, the checks...

import { useState } from "react";
import { api, errorText } from "../api/client";
import {
  formatCost, formatDuration, formatTokens, summarizeToolInput,
  type ApprovalItem, type Item, type ToolItem,
} from "../lib/events";

interface Props {
  item: Item;
  runId: string;
  active: boolean; // is the run working right now? (approvals can only be answered then)
}

export function ActivityItem({ item, runId, active }: Props) {
  switch (item.type) {
    case "user":
      return (
        <div className="ml-auto max-w-[85%] rounded-lg bg-sky-600/20 px-3 py-2 text-sm whitespace-pre-wrap">
          {(item.followUp || item.model) && (
            <p className="mb-0.5 text-[11px] text-sky-300">
              {[item.followUp && "Follow-up", item.model && `model: ${item.model}`, item.effort && `effort: ${item.effort}`]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {item.text}
        </div>
      );
    case "session":
      return <p className="text-xs text-zinc-500">Agent started{item.model ? ` · ${item.model}` : ""}</p>;
    case "text":
      return <div className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-100">{item.text}</div>;
    case "tool":
      return <ToolCard tool={item} />;
    case "approval":
      return <ApprovalCard approval={item} runId={runId} active={active} />;
    case "result":
      return <ResultCard item={item} />;
    case "checks":
      return <ChecksCard item={item} />;
    case "error":
      return <p className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-300">{item.message}</p>;
    case "raw":
      return <Collapsible label="Other event" body={JSON.stringify(item.data, null, 2)} />;
  }
}

function ToolCard({ tool }: { tool: ToolItem }) {
  const [open, setOpen] = useState(false);
  const summary = summarizeToolInput(tool.input);
  const running = !tool.result;
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <span className={`text-xs ${tool.result?.isError ? "text-red-400" : "text-zinc-500"}`}>{open ? "▾" : "▸"}</span>
        <span className="text-xs font-semibold text-sky-300">{tool.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-400">{summary}</span>
        {running && <span className="text-[11px] text-zinc-500">running...</span>}
        {tool.result?.isError && <span className="text-[11px] text-red-400">failed</span>}
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-800 px-3 py-2">
          <Pre label="Input" text={JSON.stringify(tool.input, null, 2)} />
          {tool.result && <Pre label="Output" text={tool.result.output || "(empty)"} />}
        </div>
      )}
    </div>
  );
}

function ApprovalCard({ approval, runId, active }: { approval: ApprovalItem; runId: string; active: boolean }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const waiting = !approval.decision && active;

  async function decide(allow: boolean) {
    setSending(true);
    setError(null);
    try {
      await api.decide(runId, approval.id, allow, allow ? undefined : "Denied by the user in AI-Hub.");
    } catch (e) {
      setError(errorText(e));
      setSending(false);
    }
    // On success the card updates itself when the approval_resolved event arrives.
  }

  const border = waiting
    ? "border-amber-500/50 bg-amber-500/5"
    : approval.decision?.behavior === "allow"
      ? "border-zinc-800 bg-zinc-900/60"
      : "border-red-500/30 bg-red-500/5";

  return (
    <div className={`rounded-md border px-3 py-2.5 ${border}`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-amber-300">
          {waiting ? "Waiting for your approval" : "Approval"}
        </span>
        <span className="text-xs font-semibold text-sky-300">{approval.toolName}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-400">
          {summarizeToolInput(approval.input)}
        </span>
        {approval.decision && (
          <span className={`text-[11px] font-medium ${approval.decision.behavior === "allow" ? "text-emerald-400" : "text-red-400"}`}>
            {approval.decision.behavior === "allow" ? "Allowed" : "Denied"}
          </span>
        )}
        {!approval.decision && !active && <span className="text-[11px] text-zinc-500">no decision</span>}
      </div>
      {waiting && (
        <>
          <Pre text={JSON.stringify(approval.input, null, 2)} />
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => decide(true)}
              disabled={sending}
              className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500 disabled:opacity-50"
            >
              Allow
            </button>
            <button
              onClick={() => decide(false)}
              disabled={sending}
              className="rounded-md border border-zinc-600 px-3 py-1 text-xs font-medium hover:border-red-500 hover:text-red-300 disabled:opacity-50"
            >
              Deny
            </button>
          </div>
          {error && <p className="mt-1 text-xs text-red-300">{error}</p>}
        </>
      )}
    </div>
  );
}

function ResultCard({ item }: { item: Extract<Item, { type: "result" }> }) {
  const r = item.result;
  const stats: [string, string | number][] = [
    ["Cost", formatCost(r.cost_usd)],
    ["Time", formatDuration(r.duration_ms)],
    ["Tokens in", formatTokens(r.input_tokens)],
    ["Tokens out", formatTokens(r.output_tokens)],
    ["Turns", r.num_turns ?? "-"],
  ];
  return (
    <div
      className={`rounded-md border px-3 py-3 ${
        r.success ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5"
      }`}
    >
      <p className={`text-sm font-semibold ${r.success ? "text-emerald-300" : "text-red-300"}`}>
        {r.success ? "Finished" : "Did not finish"}
      </p>
      {r.summary && <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">{r.summary}</p>}
      <Stats stats={stats} />
    </div>
  );
}

function ChecksCard({ item }: { item: Extract<Item, { type: "checks" }> }) {
  const m = item.metrics;
  const [open, setOpen] = useState(false);
  const tests =
    m.tests_passed === null || m.tests_passed === undefined
      ? { text: "No test command", className: "text-zinc-500" }
      : m.tests_passed
        ? { text: "Tests passed", className: "text-emerald-300" }
        : { text: `Tests failed (exit ${m.test_exit_code})`, className: "text-red-300" };
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="font-semibold text-zinc-400">Checks</span>
        <span>
          {m.files_changed} file{m.files_changed === 1 ? "" : "s"} changed
        </span>
        <span className="font-mono">
          <span className="text-emerald-400">+{m.lines_added}</span>{" "}
          <span className="text-red-400">-{m.lines_removed}</span>
        </span>
        <span className={tests.className}>{tests.text}</span>
        {m.test_duration_ms != null && <span className="text-zinc-500">{formatDuration(m.test_duration_ms)}</span>}
        {m.test_output && (
          <button onClick={() => setOpen(!open)} className="text-zinc-400 hover:text-zinc-100">
            {open ? "Hide output" : "Show output"}
          </button>
        )}
      </div>
      {open && m.test_output && <Pre label={m.test_command ?? "Output"} text={m.test_output} />}
    </div>
  );
}

function Stats({ stats }: { stats: [string, string | number][] }) {
  return (
    <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
      {stats.map(([label, value]) => (
        <div key={label}>
          <dt className="text-[11px] text-zinc-500">{label}</dt>
          <dd className="text-sm font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Collapsible({ label, body }: { label: string; body: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs text-zinc-500">
      <button onClick={() => setOpen(!open)} className="hover:text-zinc-300">
        {open ? "▾" : "▸"} {label}
      </button>
      {open && <Pre text={body} />}
    </div>
  );
}

function Pre({ label, text }: { label?: string; text: string }) {
  return (
    <div className="mt-2">
      {label && <p className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>}
      <pre className="max-h-72 overflow-auto rounded bg-zinc-950 p-2 font-mono text-xs text-zinc-300">{text}</pre>
    </div>
  );
}

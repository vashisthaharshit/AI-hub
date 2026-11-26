// Guide tab: what the repo does, what you can ask it, how to start, and a
// box to type what you want. Made for people who have never seen the repo.
//
// The guide is loaded by AppView (useGuide), because the Commands tab uses
// it too: it holds each command's form.

import { useState } from "react";
import type { AppCommand } from "../../api/types";
import type { GuideState } from "./useGuide";

interface Props {
  state: GuideState;
  commands: AppCommand[];
  busy: boolean; // the app is already running something
  onOpenCommand: (name: string) => void; // jump to that card in the Commands tab
  onAsk: (message: string) => Promise<void>; // run a typed request
}

export function GuidePanel({ state, commands, busy, onOpenCommand, onAsk }: Props) {
  const { guide, writing, error, write } = state;
  const [request, setRequest] = useState("");
  const [sending, setSending] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  async function ask() {
    if (!request.trim()) return;
    setSending(true);
    setAskError(null);
    try {
      await onAsk(request.trim());
      setRequest("");
    } catch (e) {
      setAskError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  if (!guide) return <p className="text-sm text-zinc-500">{error ?? "Loading the guide..."}</p>;

  const titleOf = (name: string | null) =>
    (name && guide.commands[name]?.title) || commands.find((c) => c.name === name)?.title || name;

  return (
    <div className="space-y-6">
      {(writing || error || guide.source === "basic") && (
        <p className="text-xs text-zinc-500">
          {writing
            ? "✍️ Writing a simpler guide for this repo (once, with the cheapest model)..."
            : error
              ? `Showing the basic guide. ${error}`
              : "Showing the basic guide."}
          {!writing && guide.source === "basic" && (
            <>
              {" "}
              <button onClick={() => write(false)} className="text-sky-300 hover:underline">Try again</button>
            </>
          )}
        </p>
      )}

      <section>
        <h3 className="mb-1.5 text-sm font-semibold">What it does</h3>
        <p className="text-sm leading-relaxed text-zinc-300">{guide.what_it_does}</p>
      </section>

      {guide.examples.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold">What you can ask it</h3>
          <p className="mb-2 text-xs text-zinc-500">Click one to put it in the box below, then change it to fit you.</p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {guide.examples.map((ex) => (
              <li key={ex.request}>
                <button
                  onClick={() => setRequest(ex.request)}
                  className="flex h-full w-full flex-col items-start gap-1 rounded-lg border border-zinc-800 px-3 py-2 text-left hover:border-sky-700 hover:bg-sky-500/5"
                >
                  <span className="text-sm text-zinc-200">“{ex.request}”</span>
                  {ex.command && <span className="text-[11px] text-zinc-500">uses {titleOf(ex.command)}</span>}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {guide.steps.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">How to start</h3>
          <ol className="space-y-2">
            {guide.steps.map((step, i) => (
              <li key={step.title + i} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs font-semibold text-sky-300">
                  {i + 1}
                </span>
                <div className="min-w-0 text-sm">
                  <p className="font-medium">
                    {step.title}
                    {step.command && (
                      <button
                        onClick={() => onOpenCommand(step.command!)}
                        className="ml-2 text-[11px] font-normal text-sky-300 hover:underline"
                      >
                        {titleOf(step.command)} →
                      </button>
                    )}
                  </p>
                  {step.text && <p className="text-xs text-zinc-400">{step.text}</p>}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {guide.needs.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">What it needs from you</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-zinc-300">
            {guide.needs.map((n) => <li key={n}>{n}</li>)}
          </ul>
        </section>
      )}

      <section className="task-composer">
        <div className="task-fields">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">What do you want to do?</span>
            <textarea
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              placeholder={guide.examples[0] ? `e.g. ${guide.examples[0].request}` : "Describe what you want in your own words"}
              rows={3}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void ask();
              }}
            />
          </label>
        </div>
        <div className="task-footer">
          <span className="keyboard-hint">
            {askError ? <span className="text-red-300">{askError}</span>
              : busy ? "This app is already running something. See the Activity tab."
              : "The agent works in this app's folder, using the repo's own commands as needed. Ctrl+Enter to send."}
          </span>
          <button className="run-button" onClick={() => void ask()} disabled={busy || sending || !request.trim()}>
            {sending ? "Starting..." : "Go"}
          </button>
        </div>
      </section>

      {guide.source === "ai" && (
        <p className="text-[11px] text-zinc-600">
          Guide written by {guide.made_by}.{" "}
          <button onClick={() => write(true)} disabled={writing} className="hover:text-zinc-400 hover:underline">
            Write it again
          </button>
        </p>
      )}
    </div>
  );
}

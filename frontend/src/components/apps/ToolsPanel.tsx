// "Tools it needs": what's installed, and an Install button for what's missing.
//
// Installing runs winget on the backend. We ask once before starting (it's a
// real install), then poll for new log lines every second until it's done,
// and finally look for every tool again.

import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AppRequirement, InstallJob } from "../../api/types";

const POLL_MS = 1000;

interface Props {
  appId: string;
  requirements: AppRequirement[];
  onChange: (requirements: AppRequirement[]) => void;
}

export function ToolsPanel({ appId, requirements, onChange }: Props) {
  const [confirming, setConfirming] = useState<string | null>(null); // tool id
  const [job, setJob] = useState<InstallJob | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const missing = requirements.filter((r) => !r.installed);
  const needed = missing.filter((r) => !r.optional);
  const running = job?.state === "running";

  async function checkAgain() {
    setChecking(true);
    setError(null);
    try {
      onChange(await api.appTools(appId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setChecking(false);
    }
  }

  async function install(toolId: string) {
    setConfirming(null);
    setError(null);
    setLines([]);
    try {
      setJob(await api.installTool(toolId));
    } catch (e) {
      setError(errorText(e));
    }
  }

  // Poll while the install runs.
  const jobId = job?.id;
  useEffect(() => {
    if (!jobId || !running) return;
    let count = 0; // log lines we already have
    let active = true;
    const timer = setInterval(async () => {
      try {
        const next = await api.installProgress(jobId, count - 1);
        if (!active) return;
        count = next.line_count;
        if (next.lines.length) setLines((old) => [...old, ...next.lines].slice(-300));
        setJob(next);
        if (next.state !== "running") {
          clearInterval(timer);
          void checkAgain();
        }
      } catch (e) {
        if (active) setError(errorText(e));
      }
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, running]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  return (
    <section className="rounded-lg border border-zinc-800 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Tools it needs</h3>
          <p className="text-xs text-zinc-500">
            {needed.length === 0
              ? "Everything it needs is installed."
              : `${needed.length} missing. Install ${needed.length > 1 ? "them" : "it"} before running the app.`}
          </p>
        </div>
        <button onClick={checkAgain} disabled={checking || running} className="workspace-button text-xs">
          {checking ? "Checking..." : "Check again"}
        </button>
      </div>

      <ul className="divide-y divide-zinc-800">
        {requirements.map((tool) => (
          <li key={tool.id} className="py-2.5">
            <div className="flex items-start gap-3">
              <span
                aria-hidden="true"
                className={`mt-0.5 w-4 text-center text-sm ${
                  tool.installed ? "text-emerald-400" : tool.optional ? "text-zinc-500" : "text-amber-400"
                }`}
              >
                {tool.installed ? "✓" : tool.optional ? "○" : "!"}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  <span className="font-medium">{tool.label}</span>{" "}
                  <span className="text-xs text-zinc-500">
                    {tool.installed
                      ? tool.auto ? "set up automatically" : "installed"
                      : tool.optional ? "optional, not installed" : "not installed"}
                  </span>
                </p>
                <p className="text-xs text-zinc-500">{tool.why}</p>
              </div>
              {!tool.installed && (
                tool.can_install ? (
                  <button
                    onClick={() => setConfirming(tool.id)}
                    disabled={running}
                    className="rounded-md bg-sky-300 px-2.5 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-200 disabled:opacity-40"
                  >
                    Install
                  </button>
                ) : (
                  tool.website && (
                    <a href={tool.website} target="_blank" rel="noreferrer" className="text-xs text-sky-300 hover:underline">
                      Get it ↗
                    </a>
                  )
                )
              )}
            </div>

            {confirming === tool.id && (
              <div className="mt-2 ml-7 space-y-2 rounded-md bg-zinc-900 p-3 text-xs text-zinc-400">
                <p>
                  AI-Hub will install <span className="text-zinc-200">{tool.label}</span> with winget, Windows' own app
                  installer. {tool.note}
                </p>
                <p>By installing you accept {tool.label}'s license. Windows may ask you for permission.</p>
                <div className="flex gap-2">
                  <button
                    onClick={() => install(tool.id)}
                    className="rounded-md bg-sky-300 px-2.5 py-1 font-semibold text-zinc-950 hover:bg-sky-200"
                  >
                    Install {tool.label}
                  </button>
                  <button onClick={() => setConfirming(null)} className="rounded-md px-2.5 py-1 hover:text-zinc-100">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {job && job.tool_id === tool.id && (
              <div className="mt-2 ml-7 space-y-2">
                <p
                  className={`text-xs ${
                    job.state === "running" ? "text-sky-300"
                      : job.state === "installed" ? "text-emerald-400"
                      : job.state === "failed" ? "text-red-300" : "text-amber-300"
                  }`}
                >
                  {job.state === "running" ? `Installing ${job.label}...` : job.message}
                </p>
                <pre
                  ref={logRef}
                  className="max-h-48 overflow-auto rounded-md bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed text-zinc-400"
                >
                  {`> ${job.command}\n` + lines.join("\n")}
                </pre>
              </div>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
    </section>
  );
}

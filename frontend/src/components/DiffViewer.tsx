// Side-by-side view of what the agent changed: file list on the left,
// Monaco's diff editor (old | new) on the right.
//
// This file is loaded lazily (see RunView): Monaco is several MB, so the app
// only downloads it the first time someone opens the Changes tab.

import { DiffEditor } from "@monaco-editor/react";
import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { ChangedFile } from "../api/types";
import { languageFor } from "../lib/monaco";

const STATUS_LABEL: Record<ChangedFile["status"], { text: string; className: string }> = {
  A: { text: "A", className: "text-emerald-400" },
  M: { text: "M", className: "text-amber-400" },
  D: { text: "D", className: "text-red-400" },
  T: { text: "T", className: "text-zinc-400" },
};

export default function DiffViewer({ runId, reloadKey }: { runId: string; reloadKey: string }) {
  const [files, setFiles] = useState<ChangedFile[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .files(runId)
      .then((list) => {
        if (!active) return;
        setFiles(list);
        setSelected((current) => (current && list.some((f) => f.path === current) ? current : list[0]?.path ?? null));
      })
      .catch((e) => active && setError(errorText(e)));
    return () => {
      active = false;
    };
  }, [runId, reloadKey]);

  if (error) return <p className="p-6 text-sm text-red-300">{error}</p>;
  if (!files) return <p className="p-6 text-sm text-zinc-500">Loading changes...</p>;
  if (files.length === 0) return <p className="p-6 text-sm text-zinc-500">No files changed yet.</p>;

  const file = files.find((f) => f.path === selected) ?? files[0];

  return (
    <div className="flex h-full min-h-0">
      <ul className="w-64 shrink-0 overflow-y-auto border-r border-zinc-800 py-2">
        {files.map((f) => (
          <li key={f.path}>
            <button
              onClick={() => setSelected(f.path)}
              className={`flex w-full items-center gap-2 px-3 py-1 text-left font-mono text-xs hover:bg-zinc-800 ${
                f.path === file.path ? "bg-zinc-800" : ""
              }`}
            >
              <span className={`w-3 font-bold ${STATUS_LABEL[f.status].className}`}>{STATUS_LABEL[f.status].text}</span>
              <span className="truncate" title={f.path}>
                {f.path}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="min-w-0 flex-1">
        {file.binary || file.too_large ? (
          <p className="p-6 text-sm text-zinc-500">
            {file.binary ? "Binary file: no text preview." : "File too large to preview."}
          </p>
        ) : (
          <DiffEditor
            key={file.path}
            original={file.original ?? ""}
            modified={file.modified ?? ""}
            language={languageFor(file.path)}
            theme="vs-dark"
            options={{
              readOnly: true,
              renderSideBySide: true,
              automaticLayout: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              fontSize: 13,
            }}
          />
        )}
      </div>
    </div>
  );
}

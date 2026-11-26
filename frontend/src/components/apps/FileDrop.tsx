// Drag a file here or click to choose. Each file is uploaded right away and
// saved inside the app's folder (e.g. documents/cv/), where the repo expects it.

import { useRef, useState } from "react";
import { api, errorText } from "../../api/client";
import type { FormField } from "../../api/types";

interface Props {
  appId: string;
  field: FormField;
  saved: string[]; // paths already saved, relative to the app's folder
  onSaved: (paths: string[]) => void;
}

export function FileDrop({ appId, field, saved, onSaved }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const folder = field.save_to ?? "uploads";
  const types = field.file_types.join(", ");

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    const list = field.multiple ? [...files] : [files[0]];
    const bad = field.file_types.length
      ? list.find((f) => !field.file_types.some((t) => f.name.toLowerCase().endsWith(t.toLowerCase())))
      : undefined;
    if (bad) {
      setError(`"${bad.name}" isn't one of: ${types}`);
      return;
    }
    setError(null);
    const paths = field.multiple ? [...saved] : [];
    for (const file of list) {
      setBusy(file.name);
      try {
        paths.push((await api.uploadFile(appId, folder, file)).path);
      } catch (e) {
        setError(`${file.name}: ${errorText(e)}`);
        break;
      }
    }
    setBusy(null);
    onSaved(paths);
  }

  return (
    <div className="space-y-1.5">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void upload(e.dataTransfer.files);
        }}
        className={`flex w-full flex-col items-center gap-0.5 rounded-md border border-dashed px-3 py-3 text-center text-xs ${
          over ? "border-sky-400 bg-sky-500/10 text-sky-200" : "border-zinc-600 text-zinc-400 hover:border-zinc-400"
        }`}
      >
        <span className="text-base" aria-hidden="true">📄</span>
        <span>
          {busy ? `Saving ${busy}...` : <>Drop {field.multiple ? "files" : "a file"} here or <span className="text-sky-300">choose</span></>}
        </span>
        {types && <span className="text-[11px] text-zinc-500">{types}</span>}
      </button>
      <input
        ref={input}
        type="file"
        hidden
        multiple={field.multiple}
        accept={types || undefined}
        onChange={(e) => {
          void upload(e.target.files);
          e.target.value = "";
        }}
      />
      {saved.map((p) => (
        <p key={p} className="text-[11px] text-emerald-400">✓ Saved as {p}</p>
      ))}
      {error && <p className="text-[11px] text-red-300">{error}</p>}
    </div>
  );
}

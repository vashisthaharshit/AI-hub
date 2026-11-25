// Load Monaco (VS Code's editor) from our own bundle instead of a CDN.
//
// By default @monaco-editor/react downloads Monaco from jsdelivr.net at
// runtime. For a local tool that's a bad fit: it breaks offline and sends a
// request to a third party every time. Bundling it makes the app bigger, but
// it works anywhere. That's the trade-off we pick.

import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

// Monaco runs heavy work in a Web Worker. The diff editor only needs the
// basic editor worker (no TypeScript/JSON language workers).
self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

loader.config({ monaco });

const LANGUAGES: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  json: "json", md: "markdown", html: "html", css: "css", scss: "scss", yml: "yaml", yaml: "yaml",
  toml: "ini", sh: "shell", ps1: "powershell", go: "go", rs: "rust", java: "java", cs: "csharp",
  cpp: "cpp", c: "c", h: "c", sql: "sql", xml: "xml", rb: "ruby", php: "php",
};

export function languageFor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return LANGUAGES[ext] ?? "plaintext";
}

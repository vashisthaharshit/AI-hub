// Every call to the backend goes through this file, so URLs and error
// handling live in one place. URLs are relative ("/api/...") on purpose:
// in dev, Vite's proxy forwards them; in production, the backend serves the UI.

import type {
  AgentStatus, ChangedFile, RunCreate, RunInfo, SettingsView, SummarySettings, SummaryTest,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  return res.json() as Promise<T>;
}

// FastAPI errors look like {"detail": "text"} or, for validation errors,
// {"detail": [{"msg": "...", "loc": [...]}, ...]}. Turn both into one sentence.
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? "Invalid input").join("; ");
    }
  } catch {
    // not JSON, fall through
  }
  return `${res.status} ${res.statusText}`;
}

export const api = {
  agents: (refresh = false) => request<AgentStatus[]>(`/api/agents${refresh ? "?refresh=true" : ""}`),
  testAgent: (id: string) => request<AgentStatus>(`/api/agents/${id}/test`, { method: "POST" }),
  unhideModels: (id: string) => request<AgentStatus>(`/api/agents/${id}/models/unhide`, { method: "POST" }),

  runs: () => request<RunInfo[]>("/api/runs"),
  run: (id: string) => request<RunInfo>(`/api/runs/${id}`),
  createRun: (body: RunCreate) =>
    request<RunInfo>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  files: (id: string) => request<ChangedFile[]>(`/api/runs/${id}/files`),
  cancel: (id: string) => request<{ cancelled: boolean }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  // agentId different from the run's agent = switch agents (the new one gets a handoff)
  followUp: (id: string, prompt: string, model: string | null, effort: string | null, agentId: string | null) =>
    request<RunInfo>(`/api/runs/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ prompt, model, effort, agent_id: agentId }),
    }),
  bulkDelete: (ids: string[]) =>
    request<{ deleted: string[]; failed: { id: string; error: string }[] }>("/api/runs/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  deleteRun: (id: string) => request<{ deleted: boolean }>(`/api/runs/${id}`, { method: "DELETE" }),
  keep: (id: string) => request<RunInfo>(`/api/runs/${id}/keep`, { method: "POST" }),
  discard: (id: string) => request<RunInfo>(`/api/runs/${id}/discard`, { method: "POST" }),
  decide: (id: string, approvalId: string, allow: boolean, message?: string) =>
    request<{ ok: boolean }>(`/api/runs/${id}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ allow, message: message ?? null }),
    }),

  settings: () => request<SettingsView>("/api/settings"),
  saveSummary: (s: SummarySettings) =>
    request<SettingsView>("/api/settings/summary", { method: "PUT", body: JSON.stringify(s) }),
  testSummary: (s: SummarySettings) =>
    request<SummaryTest>("/api/settings/summary/test", { method: "POST", body: JSON.stringify(s) }),
  saveKey: (provider: string, key: string) =>
    request<SettingsView>(`/api/settings/keys/${provider}`, { method: "PUT", body: JSON.stringify({ key }) }),
  deleteKey: (provider: string) => request<SettingsView>(`/api/settings/keys/${provider}`, { method: "DELETE" }),
  providerModels: (provider: string) => request<{ models: string[] }>(`/api/settings/models/${provider}`),
};

/** Builds a ws:// URL on the same host the page was loaded from. */
export function wsUrl(path: string): string {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// Every call to the backend goes through this file, so URLs and error
// handling live in one place. URLs are relative ("/api/...") on purpose:
// in dev, Vite's proxy forwards them; in production, the backend serves the UI.

import type {
  AgentStatus, AppConnect, BrainCreate, BrainMode, BrainSettings, BrainTask, PlanStep, AppGuide, AppInfo, AppRunCreate, AppUpdateCheck, AppUpdateResult, SetupView, AppRequirement, ChangedFile, InstallJob, RunCreate, RunInfo, SettingsView,
  SummarySettings, SummaryTest, Workspace,
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

  // Manual's chats in a workspace (Brain's runs stay out until moved), or one Brain task's runs.
  runs: (opts: { workspaceId?: string | null; brainTaskId?: string | null } = {}) => {
    const params = new URLSearchParams();
    if (opts.workspaceId) params.set("workspace_id", opts.workspaceId);
    if (opts.brainTaskId) params.set("brain_task_id", opts.brainTaskId);
    const q = params.toString();
    return request<RunInfo[]>(`/api/runs${q ? `?${q}` : ""}`);
  },
  moveToManual: (id: string) => request<RunInfo>(`/api/runs/${id}/move-to-manual`, { method: "POST" }),

  workspaces: () => request<Workspace[]>("/api/workspaces"),
  workspace: (id: string) => request<Workspace>(`/api/workspaces/${id}`),
  createWorkspace: (body: { repo_path: string; name?: string | null; test_command?: string | null; color?: string | null }) =>
    request<Workspace>("/api/workspaces", { method: "POST", body: JSON.stringify(body) }),
  editWorkspace: (id: string, body: { name?: string; test_command?: string; color?: string }) =>
    request<Workspace>(`/api/workspaces/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteWorkspace: (id: string) => request<{ deleted: boolean }>(`/api/workspaces/${id}`, { method: "DELETE" }),
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

  // Brain tab. Creating or planning can take 10-40 s (the planner writes the plan).
  brainTasks: (workspaceId?: string | null) =>
    request<BrainTask[]>(`/api/brain/tasks${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""}`),
  brainTask: (id: string) => request<BrainTask>(`/api/brain/tasks/${id}`),
  createBrainTask: (body: BrainCreate) =>
    request<BrainTask>("/api/brain/tasks", { method: "POST", body: JSON.stringify(body) }),
  planBrainTask: (id: string, body: { answers?: string[]; skip_questions?: boolean }) =>
    request<BrainTask>(`/api/brain/tasks/${id}/plan`, { method: "POST", body: JSON.stringify(body) }),
  editBrainTask: (id: string, body: { steps?: PlanStep[]; mode?: BrainMode; approval?: "all" | "each"; reroute?: boolean }) =>
    request<BrainTask>(`/api/brain/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  brainSettings: () => request<BrainSettings>("/api/settings/brain"),
  saveBrainSettings: (body: BrainSettings) =>
    request<BrainSettings>("/api/settings/brain", { method: "PUT", body: JSON.stringify(body) }),
  brainDirect: (id: string) => request<BrainTask>(`/api/brain/tasks/${id}/direct`, { method: "POST" }),
  deleteBrainTask: (id: string) => request<{ deleted: boolean }>(`/api/brain/tasks/${id}`, { method: "DELETE" }),

  // Apps tab
  apps: () => request<AppInfo[]>("/api/apps"),
  app: (id: string) => request<AppInfo>(`/api/apps/${id}`),
  appsDefaultFolder: () => request<{ folder: string }>("/api/apps/default-folder"),
  // Opens the computer's own "choose a folder" window (the backend runs on this
  // computer); waits until the user picks one. path = null if they cancelled.
  pickFolder: (start?: string, title?: string) =>
    request<{ path: string | null }>("/api/pick-folder", {
      method: "POST",
      body: JSON.stringify({ start: start || null, title: title ?? null }),
    }),
  connectApp: (body: AppConnect) => request<AppInfo>("/api/apps", { method: "POST", body: JSON.stringify(body) }),
  rescanApp: (id: string) => request<AppInfo>(`/api/apps/${id}/rescan`, { method: "POST" }),
  // Looks on GitHub (fetch only, nothing changes); then update = backup + move to GitHub's version.
  checkAppUpdates: (id: string) => request<AppUpdateCheck>(`/api/apps/${id}/updates`),
  updateApp: (id: string) => request<AppUpdateResult>(`/api/apps/${id}/update`, { method: "POST" }),
  forgetApp: (id: string) => request<{ deleted: boolean }>(`/api/apps/${id}`, { method: "DELETE" }),
  guide: (id: string) => request<AppGuide>(`/api/apps/${id}/guide`),
  // Slow (the cheapest model writes it); returns the saved guide at once if still current.
  writeGuide: (id: string, force = false) =>
    request<AppGuide>(`/api/apps/${id}/guide${force ? "?force=true" : ""}`, { method: "POST" }),
  // The file goes as the raw body; the backend saves it inside the app's folder.
  uploadFile: async (id: string, folder: string, file: File): Promise<{ path: string }> => {
    const params = new URLSearchParams({ folder, name: file.name });
    const res = await fetch(`/api/apps/${id}/upload?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    if (!res.ok) throw new Error(await errorMessage(res));
    return res.json();
  },
  appSetup: (id: string) => request<SetupView>(`/api/apps/${id}/setup`),
  // values: field id -> answer ("" clears it). confirmUnsafe: the user agreed to turn a safe-mode switch off.
  saveAppSetup: (id: string, values: Record<string, string>, confirmUnsafe = false) =>
    request<SetupView>(`/api/apps/${id}/setup`, {
      method: "PUT",
      body: JSON.stringify({ values, confirm_unsafe: confirmUnsafe }),
    }),
  runApp: (id: string, body: AppRunCreate) =>
    request<RunInfo>(`/api/apps/${id}/runs`, { method: "POST", body: JSON.stringify(body) }),
  appRuns: (id: string) => request<RunInfo[]>(`/api/apps/${id}/runs`),
  setAppTrust: (id: string, trusted: boolean) =>
    request<{ trusted: boolean }>(`/api/apps/${id}/trust`, { method: "PUT", body: JSON.stringify({ trusted }) }),
  // Opens the file with its usual program on this computer (reveal: show it in File Explorer).
  openAppFile: (id: string, path: string, reveal = false) =>
    request<{ ok: boolean }>(`/api/apps/${id}/open`, { method: "POST", body: JSON.stringify({ path, reveal }) }),
  appTools: (id: string) => request<AppRequirement[]>(`/api/apps/${id}/tools`),
  installTool: (toolId: string) => request<InstallJob>(`/api/tools/${toolId}/install`, { method: "POST" }),
  // `after`: the last log line number we already have (-1 = none)
  installProgress: (jobId: string, after: number) =>
    request<InstallJob>(`/api/tools/installs/${jobId}?after=${after}`),
};

/** Builds a ws:// URL on the same host the page was loaded from. */
export function wsUrl(path: string): string {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// TypeScript copies of the Pydantic models in backend/aihub/models.py.
// Keep the two in sync: if you change a model there, change it here too.

export type AgentState = "installed" | "ready" | "not_signed_in" | "not_installed" | "error";

export interface ModelOption {
  id: string;
  label: string; // e.g. "Sonnet 5.5"
  description: string | null;
  efforts: string[]; // effort levels this model accepts; empty = no effort control
  default_effort: string | null;
  note: string | null; // e.g. "GPT-5.6 Sol retires on December 23, 2026."
}

export interface UsageWindow {
  label: string; // "5-hour limit", "Weekly limit"
  used_percent: number;
  resets_at: string | null;
}

export interface AccountInfo {
  plan: string | null;
  usage: UsageWindow[];
  has_credits: boolean | null; // null = this agent doesn't report credits
  credits_balance: string | null;
  unlimited_credits: boolean | null;
}

export interface AgentStatus {
  id: string;
  name: string;
  state: AgentState;
  version: string | null;
  path: string | null;
  help: string | null;
  supports_supervised: boolean;
  supports_follow_up: boolean;
  models: ModelOption[]; // empty = only the agent's default
  default_label: string | null; // e.g. "Default (recommended) · Opus 5.5 ..."
  default_efforts: string[]; // effort levels when "Default" model is picked
  default_effort: string | null;
  account: AccountInfo | null;
  hidden_models: string[]; // hidden after a "needs credits" failure
  discovery_error: string | null;
}

export type EventKind =
  | "user_message"
  | "session_started"
  | "text"
  | "tool_call"
  | "tool_result"
  | "approval_request"
  | "approval_resolved"
  | "result"
  | "checks"
  | "error"
  | "raw";

export interface RunResult {
  success: boolean;
  summary: string | null;
  cost_usd: number | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
}

export interface RunMetrics {
  files_changed: number;
  lines_added: number;
  lines_removed: number;
  files: string[];
  test_command: string | null;
  tests_passed: boolean | null;
  test_exit_code: number | null;
  test_duration_ms: number | null;
  test_output: string | null;
}

export type RunState = "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type RunOutcome = "kept" | "discarded";

export interface RunInfo {
  id: string;
  agent_id: string;
  repo_path: string;
  prompt: string;
  state: RunState;
  created_at: string;
  branch: string | null;
  worktree_path: string | null;
  base_commit: string | null;
  session_id: string | null;
  model: string | null; // latest turn's model; null = agent default
  effort: string | null; // latest turn's effort; null = model default
  supervised: boolean;
  test_command: string | null;
  result: RunResult | null;
  metrics: RunMetrics | null;
  outcome: RunOutcome | null;
}

export interface RunCreate {
  agent_id: string;
  repo_path: string;
  prompt: string;
  supervised: boolean;
  test_command: string | null;
  model: string | null;
  effort: string | null;
}

export interface ChangedFile {
  path: string;
  status: "A" | "M" | "D" | "T";
  original: string | null;
  modified: string | null;
  binary: boolean;
  too_large: boolean;
}

// Messages sent over the WebSocket /ws/runs/{id}
export interface EventMessage {
  type: "event";
  seq: number;
  kind: EventKind;
  data: Record<string, unknown>;
}

export interface EndMessage {
  type: "end";
  run: RunInfo;
}

export type SocketMessage = EventMessage | EndMessage;

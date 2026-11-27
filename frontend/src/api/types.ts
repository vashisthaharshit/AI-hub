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
  used_percent: number | null; // null = not reported (Claude only sends it near a limit)
  status: string | null; // "ok" | "warning" | "limit reached"
  resets_at: string | null;
  updated_at: string | null;
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
  | "usage"
  | "outputs" // app runs: files a turn created or changed
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
  kind: "task" | "app" | "step"; // app = an app's command in its folder; step = one step of a Brain task
  app_id: string | null;
  label: string | null; // what the user clicked, e.g. "Check and apply to a job"
  workspace_id: string | null;
  origin: "manual" | "brain"; // brain = started by a Brain task, hidden from Manual until moved
  brain_task_id: string | null;
}

export interface AppRunCreate {
  command?: string | null; // a card's command name
  values?: Record<string, string>; // the card's answers, by field key
  message?: string | null; // or a request typed in the Guide
  agent_id?: string | null;
}

export interface OutputFile {
  path: string;
  status: "new" | "changed";
  size: number;
}

export interface RunCreate {
  agent_id: string;
  repo_path: string;
  prompt: string;
  supervised: boolean;
  test_command: string | null;
  model: string | null;
  effort: string | null;
  workspace_id?: string | null; // the workspace this chat belongs to
  brain_task_id?: string | null; // started from a Brain task: listed there, not in Manual
}

export interface ChangedFile {
  path: string;
  status: "A" | "M" | "D" | "T";
  original: string | null;
  modified: string | null;
  binary: boolean;
  too_large: boolean;
}

// ---------- settings: the summary model ----------

export type SummaryProvider = "off" | "agent" | "groq" | "openrouter";

export interface SummarySettings {
  provider: SummaryProvider;
  agent_id: string | null;
  model: string | null;
  effort: string | null;
}

export interface SettingsView {
  summary: SummarySettings;
  keys: Record<string, boolean>; // provider -> key saved? (the key itself is never sent)
  keyring_available: boolean;
}

export interface SummaryTest {
  ok: boolean;
  summary: string | null;
  used: string | null;
  duration_ms: number | null;
  error: string | null;
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

// ---------- Apps (repos built on an agent, used through buttons) ----------

export type InputMode = "none" | "optional" | "required";

export interface AppCommand {
  name: string; // what follows the slash: "apply" -> /apply
  namespace: string | null;
  title: string; // button text
  description: string | null;
  argument_hint: string | null; // e.g. "either a URL or pasted text"
  input: InputMode; // show a text field?
  kind: "start" | "normal" | "danger"; // start = "Start here", danger = ask first
  agent: "claude" | "codex";
  source: string; // file it came from
  from_skill: boolean; // a skill the repo tells users to type (/scrape)
  invoke: string; // exactly what is typed: "/apply", "/day-trader-skill:trader-scanner" or a Codex skill "$scan"
  plugin: string | null;
  money: boolean; // can move money: always asks first
}

export interface AppSkill {
  name: string;
  description: string | null;
  agent: "claude" | "codex";
  source: string;
  user_invocable: boolean;
  plugin: string | null;
}

export interface AppRequirement {
  id: string; // "git", "python", "bun", "latex", "docker"...
  label: string;
  why: string;
  installed: boolean;
  optional: boolean;
  auto: boolean; // AI-Hub sets it up by itself (Python, via uv)
  can_install: boolean; // AI-Hub can install it (winget, Windows)
  website: string | null;
  note: string | null; // e.g. "Large download"
}

export interface AppPermissions {
  found: boolean;
  allow: string[];
  ask: string[];
  deny: string[];
  raw: string[];
  error: string | null;
}

export interface AppPlugin {
  name: string;
  root: string;
  description: string | null;
}

export type SetupType = "text" | "secret" | "number" | "toggle" | "email" | "choice";

export interface SetupField {
  id: string;
  kind: "env" | "file" | "plugin";
  target: string; // env var, config file path, or plugin name
  key: string;
  label: string;
  type: SetupType;
  required: boolean;
  advanced: boolean;
  default: string | null;
  help: string | null;
  choices: string[];
  safety: boolean; // safe-mode switch (paper, sandbox, dry run)
  safe_value: string | null;
}

export interface SetupFile {
  source: string;
  target: string;
  editable: boolean;
  exists: boolean;
}

export interface McpServerInfo {
  name: string;
  command: string | null;
  args: string[];
  url: string | null;
  env: string[];
  source: string;
  plugin: string | null;
  money: boolean;
}

export interface AppSetup {
  fields: SetupField[];
  files: SetupFile[];
  servers: McpServerInfo[];
  always_ask: string[];
  money_commands: string[];
  installed_skills: string[];
}

export interface SetupView {
  setup: AppSetup;
  values: Record<string, string>; // non-secret answers
  secrets_set: string[];
  missing: string[];
  servers_ready: Record<string, boolean>;
  unsafe: string[];
  saved: boolean;
  trusted: boolean; // "Trust this repo": auto-yes, except money tools
}

export interface AppManifest {
  name: string;
  summary: string | null;
  tier: 1 | 2 | 3; // 1 commands, 2 only instructions, 3 not an app
  agents: string[];
  primary_agent: string | null;
  commands: AppCommand[];
  skills: AppSkill[];
  requirements: AppRequirement[];
  permissions: AppPermissions;
  context_files: string[];
  plugins: AppPlugin[];
  setup: AppSetup;
}

export interface AppInfo {
  id: string;
  name: string;
  url: string | null;
  path: string;
  created_at: string;
  updated_at: string;
  manifest: AppManifest;
}

// "Update repo": how far the app's copy is behind GitHub (backend apps/update.py)
export interface AppUpdateCheck {
  behind: number; // new changes on GitHub
  ahead: number; // commits only in this copy: can't update with a button
  upstream: string; // e.g. origin/main
  changes: string[]; // titles of the newest changes
  local_changes: number; // files the user changed or added (backed up first)
  can_update: boolean;
}

export interface AppUpdateResult {
  updated: boolean;
  message: string;
  check: AppUpdateCheck;
  backup: string | null; // backup folder
  backed_up_files: number;
  conflicts: string[];
  app: AppInfo;
}

export interface AppConnect {
  url?: string | null;
  folder?: string | null; // where to clone into; default ~/aihub/repos
  path?: string | null; // an existing local folder instead of a link
}

export type InstallState = "running" | "installed" | "restart_needed" | "failed";

export interface InstallJob {
  id: string;
  tool_id: string;
  label: string;
  state: InstallState;
  command: string;
  started_at: string;
  finished_at: string | null;
  message: string | null;
  lines: string[]; // only the lines after the `after` we asked for
  line_count: number;
}

export interface GuideExample {
  request: string; // what a user could type
  command: string | null; // the command it would use
}

export interface GuideStep {
  title: string;
  text: string | null;
  command: string | null;
}

export type FieldType =
  | "text" | "long_text" | "url" | "url_or_text" | "file" | "choice" | "toggle" | "number" | "date";

export interface FieldChoice {
  value: string; // exactly what the command expects
  label: string; // what the user sees
}

export interface FormField {
  key: string;
  label: string; // a plain question
  type: FieldType;
  required: boolean;
  advanced: boolean; // hidden under "Add details (optional)"
  placeholder: string | null;
  help: string | null;
  choices: FieldChoice[];
  value: string | null; // toggle: what is added when on, e.g. "--all"
  file_types: string[]; // file: e.g. [".pdf", ".docx"]
  save_to: string | null; // file: folder in the repo
  multiple: boolean;
}

export interface CommandForm {
  title: string | null; // friendlier button name
  description: string | null;
  fields: FormField[];
  argument: string; // "{company} [--limit {limit}]"; filled in by the backend when running
}

export interface AppGuide {
  what_it_does: string;
  examples: GuideExample[];
  steps: GuideStep[];
  needs: string[];
  commands: Record<string, CommandForm>; // command name -> its form
  source: "ai" | "basic"; // basic = built without AI from the commands
  made_by: string | null; // e.g. "Claude Code · haiku"
  fingerprint: string;
  created_at: string;
}

// ---------- Brain (backend brain/) ----------

export type BrainMode = "cheapest" | "balanced" | "best";
export type StepType = "explore" | "build" | "test" | "review" | "fix";
export type Difficulty = "easy" | "medium" | "hard";

// Who does a step (backend brain/router.py)
export interface StepAssignment {
  agent_id: string;
  agent_name: string;
  model: string | null; // null = the agent's default model
  model_label: string;
  effort: string | null; // null = the model's default effort
  reason: string;
  manual: boolean; // the user picked it; kept while it can be used
}

export interface PlanStep {
  id: string;
  title: string;
  type: StepType;
  difficulty: Difficulty;
  files: string[]; // what it will likely touch; decides what runs in parallel
  depends_on: string[]; // step ids that must finish first
  brief: string; // what the agent is told to do
  done_when: string; // how AI-Hub checks it worked
  assignment: StepAssignment | null; // the router fills it in
}

export interface BrainPlan {
  steps: PlanStep[];
  waves: string[][]; // steps in one wave run at the same time
  notes: string[]; // what AI-Hub fixed in the plan
  routing_notes: string[]; // from the router
  problems: string[]; // must be fixed before it can run
  single: boolean; // only one work step: better to run it directly
  made_by: string | null;
}

export interface TriageResult {
  size: "small" | "big";
  how: "rules" | "ai" | "user";
  reasons: string[];
}

export interface BrainTask {
  id: string;
  workspace_id: string | null;
  repo_path: string;
  request: string;
  test_command: string | null;
  mode: BrainMode;
  approval: "all" | "each";
  state: "direct" | "asking" | "planned";
  triage: TriageResult;
  questions: string[];
  answers: string[];
  plan: BrainPlan | null;
  direct: StepAssignment | null; // small tasks: the one agent + model the router picked
  execution: BrainExecution | null; // running the plan
  created_at: string;
  updated_at: string;
}

export interface BrainCreate {
  workspace_id?: string | null;
  repo_path?: string | null;
  request: string;
  test_command?: string | null;
  mode?: BrainMode | null;
  approval?: "all" | "each";
  plan?: boolean | null; // true = always plan, false = never, null = triage decides
}

export interface BrainSettings {
  mode: BrainMode; // the default for new Brain tasks
}

// ---------- Workspaces (one per product = one repo; backend workspaces.py) ----------

export type WorkspaceColor = "sky" | "violet" | "emerald" | "amber" | "rose" | "cyan" | "fuchsia" | "lime";

export interface Workspace {
  id: string;
  name: string;
  repo_path: string;
  test_command: string | null;
  color: WorkspaceColor;
  created_at: string;
  updated_at: string;
  manual_count: number;
  brain_count: number;
  last_activity: string | null;
}

// ---------- Brain: running a plan (backend brain/executor.py) ----------

export interface StepProgress {
  step_id: string;
  state: "waiting" | "running" | "passed" | "failed" | "skipped";
  run_id: string | null; // open it to watch the agent
  attempts: number;
  stepped_up: boolean; // retried with a stronger model
  agent_id: string | null;
  model: string | null;
  effort: string | null;
  message: string | null; // what went wrong, or the agent's summary
  branch: string | null;
  worktree_path: string | null;
  merged: boolean;
}

export type ExecutionState = "running" | "waiting" | "done" | "stopped" | "failed" | "kept" | "discarded";

export interface BrainExecution {
  state: ExecutionState;
  wave: number;
  steps: Record<string, StepProgress>;
  branch: string | null;
  worktree_path: string | null;
  base_commit: string | null;
  baseline_tests: boolean | null;
  final_tests: boolean | null;
  final_test_output: string | null;
  waiting_for: "approve" | "failure" | "conflict" | null;
  waiting_steps: string[];
  message: string | null;
  fixup_runs: string[];
  log: { at: string; text: string }[];
  started_at: string | null;
  finished_at: string | null;
}

export type BrainAction = "continue" | "retry" | "skip" | "stop";

export interface MemoryNote {
  id: string;
  workspace_id: string | null;
  task_id: string | null;
  scope: "task" | "repo";
  text: string;
  files: string[];
  stale_files: string[]; // changed since the note was written
  created_at: string;
}

export interface StepStat {
  step_type: string;
  difficulty: string;
  tier: string;
  passed: number;
  total: number;
}

export interface BrainLearning {
  stats: StepStat[];
  adjustments: string[];
  triage_overrides: Record<string, number>;
}

"""Data shapes shared by the whole backend (Pydantic models).

Pydantic checks the shape of data at runtime: a bad request is rejected
with a clear error instead of crashing deep inside the code.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- Agents ----------

AgentState = Literal[
    "installed",      # found on this machine; sign-in not checked yet
    "ready",          # found and a test prompt worked
    "not_signed_in",  # found, but the test prompt failed with an auth error
    "not_installed",  # program not found
    "error",          # found, but something else went wrong
]


class ModelOption(BaseModel):
    id: str                          # what we pass to the CLI's --model flag, e.g. "sonnet"
    label: str                       # shown in the UI, e.g. "Sonnet 5.5"
    description: str | None = None   # e.g. "Most efficient for simpler tasks"
    efforts: list[str] = Field(default_factory=list)  # effort levels this model accepts
    default_effort: str | None = None
    note: str | None = None          # e.g. "Retires on December 23, 2026"


class UsageWindow(BaseModel):
    label: str                        # e.g. "5-hour limit", "Weekly limit"
    used_percent: float | None = None # None = the agent didn't say (Claude only sends it near a limit)
    status: str | None = None         # "ok" | "warning" | "limit reached" (when the agent reports it)
    resets_at: str | None = None      # ISO time
    updated_at: str | None = None     # when we learned this


class AccountInfo(BaseModel):
    """What the agent's own CLI tells us about the signed-in account."""

    plan: str | None = None                 # e.g. "Claude Pro", "plus"
    usage: list[UsageWindow] = Field(default_factory=list)
    has_credits: bool | None = None         # None = this agent doesn't report credits
    credits_balance: str | None = None
    unlimited_credits: bool | None = None


class Discovery(BaseModel):
    """Models + account info, asked from the CLI itself (see each adapter's discover())."""

    models: list[ModelOption] = Field(default_factory=list)
    default_label: str | None = None  # e.g. "Default (recommended) · Opus 5.5"
    default_efforts: list[str] = Field(default_factory=list)  # effort levels of the default model
    default_effort: str | None = None
    account: AccountInfo | None = None
    error: str | None = None          # discovery failed; built-in fallback list in use


class AgentStatus(BaseModel):
    id: str
    name: str
    state: AgentState
    version: str | None = None
    path: str | None = None
    help: str | None = None  # what the user should do next, shown in the UI
    supports_supervised: bool = False  # can pause and ask before each edit/command
    supports_follow_up: bool = False   # can continue a previous session
    models: list[ModelOption] = Field(default_factory=list)  # empty = only the agent's default
    default_label: str | None = None
    default_efforts: list[str] = Field(default_factory=list)  # effort levels when "Default" is picked
    default_effort: str | None = None
    account: AccountInfo | None = None
    hidden_models: list[str] = Field(default_factory=list)  # hidden after a "needs credits" failure
    discovery_error: str | None = None


# ---------- Events: what happened during a run ----------

EventKind = Literal[
    "user_message",       # the task or a follow-up the user sent
    "session_started",    # agent booted (model, session id)
    "text",               # agent wrote a message
    "tool_call",          # agent used a tool (edit a file, run a command...)
    "tool_result",        # what the tool returned
    "approval_request",   # supervised mode: agent wants to do something, waiting for the user
    "approval_resolved",  # the user's decision
    "result",             # end of a turn: cost, tokens, time
    "checks",             # AI-Hub's own measurements after a turn (files, tests)
    "usage",              # plan usage window reported by the agent (e.g. Claude's 5-hour limit)
    "outputs",            # app runs: files the turn created or changed in the app's folder
    "error",              # something failed
    "raw",                # anything we don't understand yet (kept, never dropped)
]


class AgentEvent(BaseModel):
    """One normalized step. Every adapter converts its own output into these,
    so the UI and database never need to know which agent produced them."""

    kind: EventKind
    data: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    success: bool
    summary: str | None = None
    cost_usd: float | None = None   # None when the agent doesn't report cost (e.g. Codex)
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    num_turns: int | None = None


class RunMetrics(BaseModel):
    """Measured by AI-Hub itself after each turn, the same way for every agent."""

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    files: list[str] = Field(default_factory=list)
    test_command: str | None = None
    tests_passed: bool | None = None   # None = no test command
    test_exit_code: int | None = None
    test_duration_ms: int | None = None
    test_output: str | None = None     # last part of the output


# ---------- Runs: one task given to one agent ----------

RunState = Literal["pending", "running", "succeeded", "failed", "cancelled"]
RunOutcome = Literal["kept", "discarded"]


class RunCreate(BaseModel):
    agent_id: str
    repo_path: str
    workspace_id: str | None = None    # the workspace this chat belongs to
    brain_task_id: str | None = None   # started from a Brain task: listed there, not in Manual
    prompt: str = Field(min_length=1)
    supervised: bool = False           # ask before each edit / command
    test_command: str | None = None    # e.g. "pytest -q"; run after each turn
    model: str | None = None           # None = the agent's default model
    effort: str | None = None          # None = the model's default effort


class BulkDelete(BaseModel):
    ids: list[str] = Field(min_length=1)


class FollowUp(BaseModel):
    prompt: str = Field(min_length=1)
    model: str | None = None     # switch model for this turn and the ones after it
    effort: str | None = None
    agent_id: str | None = None  # switch to another agent (gets a handoff of the run so far)


# ---------- Settings: the summary model ----------

SummaryProvider = Literal["off", "agent", "groq", "openrouter"]


class SummarySettings(BaseModel):
    """Which LLM writes summaries (e.g. the handoff when switching agents).

    off        -> no LLM: the handoff is the plain template built from events
    agent      -> one of the user's own agents (e.g. Claude Code + Haiku); uses their plan
    groq / openrouter -> the user's OWN API key, kept in the OS keychain
    """

    provider: SummaryProvider = "off"
    agent_id: str | None = None
    model: str | None = None
    effort: str | None = None


class SettingsView(BaseModel):
    summary: SummarySettings
    keys: dict[str, bool]        # provider -> is a key saved? (the key itself is never sent)
    keyring_available: bool


class KeyUpdate(BaseModel):
    key: str = Field(min_length=8)


class SummaryTest(BaseModel):
    ok: bool
    summary: str | None = None
    used: str | None = None      # e.g. "Claude Code · haiku"
    duration_ms: int | None = None
    error: str | None = None


class ApprovalDecision(BaseModel):
    allow: bool
    message: str | None = None  # optional reason, shown to the agent when denying


class ApprovalRequest(BaseModel):
    """Sent by the approval MCP server (aihub/approval_mcp.py) on behalf of Claude Code."""

    run_id: str
    token: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ChangedFile(BaseModel):
    """One file an agent changed, with both versions for a side-by-side diff."""

    path: str
    status: Literal["A", "M", "D", "T"]  # added, modified, deleted, type changed
    original: str | None = None  # content before (None if added, binary or too large)
    modified: str | None = None  # content after (None if deleted, binary or too large)
    binary: bool = False
    too_large: bool = False


class RunInfo(BaseModel):
    id: str
    agent_id: str
    repo_path: str
    prompt: str
    state: RunState
    created_at: str
    branch: str | None = None
    worktree_path: str | None = None
    base_commit: str | None = None      # the commit the run started from
    session_id: str | None = None       # the agent's own session id, for follow-ups
    model: str | None = None            # model used by the latest turn (None = agent default)
    effort: str | None = None           # effort used by the latest turn (None = default)
    supervised: bool = False
    test_command: str | None = None
    result: RunResult | None = None     # result of the latest turn
    metrics: RunMetrics | None = None   # measured after the latest turn
    outcome: RunOutcome | None = None   # set once the user keeps or discards the work
    # "task" = Manual mode (own worktree). "app" = a connected app's command,
    # run IN the app's folder (its data must stay in one place): no keep/discard.
    kind: Literal["task", "app"] = "task"
    app_id: str | None = None
    label: str | None = None            # app runs: what the user clicked, e.g. "Check and apply to a job"
    workspace_id: str | None = None     # the workspace (one repo) this chat belongs to
    # Where it shows up: "manual" runs are in Manual's list; "brain" runs were
    # started by a Brain task and only show there, until moved to Manual.
    origin: Literal["manual", "brain"] = "manual"
    brain_task_id: str | None = None


# ---------- Apps (repos built on an agent, used through buttons) ----------

class AppCommand(BaseModel):
    """One slash command of the repo; shown as a button."""

    name: str                       # what follows the slash: "apply" -> /apply
    namespace: str | None = None    # sub-folder of .claude/commands, if any
    title: str                      # button text, e.g. "Apply"
    description: str | None = None  # what it does (from the file)
    argument_hint: str | None = None  # e.g. "either a URL or pasted text"
    input: Literal["none", "optional", "required"] = "none"  # text field next to the button?
    kind: Literal["start", "normal", "danger"] = "normal"  # start = "Start here", danger = ask first
    agent: Literal["claude", "codex"] = "claude"           # whose format the command is in
    source: str                     # file it came from, relative to the repo
    from_skill: bool = False        # a skill the repo tells users to type (/scrape)
    invoke: str = ""                # exactly what to type: "/apply" or "/day-trader-skill:trader-scanner"
    plugin: str | None = None       # the plugin it comes from (loaded with --plugin-dir when run)
    money: bool = False             # can move money (orders, payments): always asks first


class AppSkill(BaseModel):
    name: str
    description: str | None = None
    agent: Literal["claude", "codex"]
    source: str
    user_invocable: bool = True
    plugin: str | None = None
    # Only used while reading the repo (to decide which skills are buttons).
    raw_description: str = Field(default="", exclude=True)
    body: str = Field(default="", exclude=True)


class AppRequirement(BaseModel):
    """A program the repo needs on this machine."""

    id: str                 # "git", "python", "bun", "latex", "docker"...
    label: str
    why: str                # shown to the user: why we think it's needed
    installed: bool
    optional: bool = False
    auto: bool = False      # AI-Hub sets it up by itself (Python, via uv)
    can_install: bool = False  # AI-Hub can install it (winget, on Windows)
    website: str | None = None # where to get it by hand
    note: str | None = None    # shown before installing, e.g. "Large download"


class AppPermissions(BaseModel):
    """What the repo's .claude/settings.json lets the agent do, in plain words."""

    found: bool
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    raw: list[str] = Field(default_factory=list)  # the original rules
    error: str | None = None


class AppPlugin(BaseModel):
    """A Claude Code plugin inside the repo; runs get `claude --plugin-dir <root>`."""

    name: str
    root: str   # relative to the repo ("." = the repo itself)
    description: str | None = None


SetupType = Literal["text", "secret", "number", "toggle", "email", "choice"]


class SetupField(BaseModel):
    """One question in an app's setup form (Setup & details tab)."""

    id: str               # "env:ALPACA_API_KEY", "file:trader/config.json:sec_user_agent", "plugin:x:token"
    kind: Literal["env", "file", "plugin"]
    target: str           # env var name, config file path, or plugin name
    key: str              # env var name, path inside the JSON ("risk.max_loss"), or userConfig key
    label: str
    type: SetupType = "text"
    required: bool = False
    advanced: bool = False          # hidden under "More settings"
    default: str | None = None
    help: str | None = None
    choices: list[str] = Field(default_factory=list)
    safety: bool = False            # a safe-mode switch (paper, sandbox, dry run...)
    safe_value: str | None = None   # "true" or "false": the safe position


class SetupFile(BaseModel):
    source: str      # the example, e.g. trader-budget/config.example.json
    target: str      # the real file AI-Hub creates, e.g. trader/config.json
    editable: bool   # JSON: AI-Hub can fill in values. Other formats: copied as they are
    exists: bool = False


class McpServerInfo(BaseModel):
    """A connection to an outside service (MCP server) the repo uses."""

    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: list[str] = Field(default_factory=list)   # variables it needs (filled from the setup form)
    source: str                       # where it's defined
    plugin: str | None = None         # comes with this plugin (loaded with it)
    money: bool = False               # a broker / payment service: every use asks first
    spec: dict[str, Any] = Field(default_factory=dict)  # the original config (to write .mcp.json)


class AppSetup(BaseModel):
    fields: list[SetupField] = Field(default_factory=list)
    files: list[SetupFile] = Field(default_factory=list)
    servers: list[McpServerInfo] = Field(default_factory=list)
    always_ask: list[str] = Field(default_factory=list)       # MCP servers whose tools always ask
    money_commands: list[str] = Field(default_factory=list)   # commands that can move money
    installed_skills: list[str] = Field(default_factory=list) # skills AI-Hub copied into .claude/skills


class AppManifest(BaseModel):
    name: str
    summary: str | None = None      # first paragraph of the README
    tier: Literal[1, 2, 3]          # 1 commands, 2 only instructions, 3 not an app
    agents: list[str] = Field(default_factory=list)   # agents it was made for
    primary_agent: str | None = None
    commands: list[AppCommand] = Field(default_factory=list)
    skills: list[AppSkill] = Field(default_factory=list)
    requirements: list[AppRequirement] = Field(default_factory=list)
    permissions: AppPermissions = Field(default_factory=lambda: AppPermissions(found=False))
    context_files: list[str] = Field(default_factory=list)
    plugins: list[AppPlugin] = Field(default_factory=list)
    setup: AppSetup = Field(default_factory=AppSetup)


class SetupView(BaseModel):
    """The setup form with what the user has filled in so far."""

    setup: AppSetup
    values: dict[str, str] = Field(default_factory=dict)   # non-secret answers (saved, else the default)
    secrets_set: list[str] = Field(default_factory=list)   # secret fields that have a saved value
    missing: list[str] = Field(default_factory=list)       # required fields still empty
    servers_ready: dict[str, bool] = Field(default_factory=dict)  # MCP server -> all its variables filled
    unsafe: list[str] = Field(default_factory=list)        # safe-mode switches currently OFF
    saved: bool = False                                    # has the user saved this form once
    trusted: bool = False                                  # "Trust this repo" (money tools still ask)


class SetupSave(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)  # field id -> answer ("" or null clears it)
    confirm_unsafe: bool = False   # the user confirmed turning a safe-mode switch off


InstallState = Literal[
    "running",
    "installed",       # the program can be found now
    "restart_needed",  # installer finished fine, but Windows doesn't show the program yet
    "failed",
]


class InstallJob(BaseModel):
    """One install of a tool, polled by the UI while it runs."""

    id: str
    tool_id: str
    label: str
    state: InstallState
    command: str                    # what runs, shown to the user
    started_at: str
    finished_at: str | None = None
    message: str | None = None      # result, in plain words
    lines: list[str] = Field(default_factory=list)  # log lines after the `after` the UI asked for
    line_count: int = 0             # total lines so far; pass line_count - 1 as `after` next time


class GuideExample(BaseModel):
    request: str                 # what a user could type, e.g. "Apply to this job: <link>"
    command: str | None = None   # the command it would use


class GuideStep(BaseModel):
    title: str
    text: str | None = None
    command: str | None = None


FieldType = Literal["text", "long_text", "url", "url_or_text", "file", "choice", "toggle", "number", "date"]


class FieldChoice(BaseModel):
    value: str   # exactly what the command expects, e.g. "profile"
    label: str   # what the user sees, e.g. "Only my profile"


class FormField(BaseModel):
    """One question on a command's card. Beginners answer questions; AI-Hub
    turns the answers into the text after the command (see CommandForm.argument)."""

    key: str
    label: str                       # a plain question, e.g. "Which company is the interview with?"
    type: FieldType = "text"
    required: bool = False
    advanced: bool = False           # hidden under "Add details (optional)"
    placeholder: str | None = None
    help: str | None = None
    choices: list[FieldChoice] = Field(default_factory=list)  # for "choice"
    value: str | None = None         # for "toggle": what is added when it's on, e.g. "--all"
    file_types: list[str] = Field(default_factory=list)       # for "file", e.g. [".pdf", ".docx"]
    save_to: str | None = None       # for "file": folder in the repo, e.g. "documents/cv"
    multiple: bool = False           # for "file": several files at once


class CommandForm(BaseModel):
    title: str | None = None         # friendlier button name, e.g. "Find jobs"
    description: str | None = None   # one plain sentence
    fields: list[FormField] = Field(default_factory=list)
    # How answers become the command's input: "{company} [--limit {limit}]".
    # [ ] = optional part, dropped when a field inside it is empty.
    argument: str = ""


class AppGuide(BaseModel):
    """The Guide tab of an app (see apps/guide.py)."""

    what_it_does: str
    examples: list[GuideExample] = Field(default_factory=list)
    steps: list[GuideStep] = Field(default_factory=list)   # how to get started, in order
    needs: list[str] = Field(default_factory=list)         # what the user must provide
    commands: dict[str, CommandForm] = Field(default_factory=dict)  # command name -> its form
    source: Literal["ai", "basic"]   # basic = built without AI from the commands
    made_by: str | None = None       # e.g. "Claude Code · haiku"
    fingerprint: str                 # of what the model saw; changes when the repo does
    created_at: str


class AppRunCreate(BaseModel):
    """Run one of an app's commands (a card's Run button), or a request typed
    in the Guide's box (`message`)."""

    command: str | None = None                  # command name, e.g. "apply"
    values: dict[str, str] = Field(default_factory=dict)  # the card's answers, by field key
    message: str | None = None                  # free text instead of a command
    agent_id: str | None = None                 # default: the agent the repo was made for
    model: str | None = None
    effort: str | None = None


class AppTrust(BaseModel):
    trusted: bool   # True: AI-Hub says yes to the agent's actions by itself, EXCEPT money tools


class OpenPath(BaseModel):
    path: str             # relative to the app's folder
    reveal: bool = False  # True: show it in File Explorer instead of opening it


class AppConnect(BaseModel):
    """Connect a GitHub link (cloned into `folder`) or a folder already on this machine (`path`)."""

    url: str | None = None
    folder: str | None = None   # where to clone into; default ~/aihub/repos
    path: str | None = None     # an existing local clone instead of a link


class AppInfo(BaseModel):
    id: str
    name: str
    url: str | None = None
    path: str
    created_at: str
    updated_at: str
    manifest: AppManifest


class AppUpdateCheck(BaseModel):
    """How far the app's copy is behind GitHub (see apps/update.py)."""

    behind: int                 # new changes (commits) on GitHub
    ahead: int                  # commits only in this copy: can't update with a button
    upstream: str               # e.g. origin/main
    changes: list[str] = Field(default_factory=list)  # titles of the newest changes
    local_changes: int = 0      # files the user changed or added (backed up first)
    can_update: bool


class AppUpdateResult(BaseModel):
    updated: bool
    message: str
    check: AppUpdateCheck
    backup: str | None = None   # backup folder
    backed_up_files: int = 0
    conflicts: list[str] = Field(default_factory=list)
    app: AppInfo


# ---------- Brain (Phase 3: plan a task across agents) ----------

BrainMode = Literal["cheapest", "balanced", "best"]


class StepAssignment(BaseModel):
    """Who does a step (see brain/router.py)."""

    agent_id: str
    agent_name: str
    model: str | None = None        # None = the agent's default model
    model_label: str
    effort: str | None = None       # None = the model's default effort
    reason: str                     # shown to the user, e.g. "Sonnet: medium build step, Balanced mode"
    manual: bool = False            # the user picked it; the router keeps it while it can be used


class PlanStep(BaseModel):
    """One step of a Brain plan (see brain/planner.py)."""

    id: str
    title: str
    type: Literal["explore", "build", "test", "review", "fix"] = "build"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    files: list[str] = Field(default_factory=list)       # what it will likely touch (decides what runs in parallel)
    depends_on: list[str] = Field(default_factory=list)  # step ids that must finish first
    brief: str                                           # what the agent is told to do
    done_when: str                                       # how AI-Hub checks it worked
    assignment: StepAssignment | None = None             # agent + model + effort (the router fills it in)


class BrainPlan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    waves: list[list[str]] = Field(default_factory=list)  # steps in one wave run at the same time
    notes: list[str] = Field(default_factory=list)        # what code fixed in the plan, shown to the user
    routing_notes: list[str] = Field(default_factory=list)  # from the router (e.g. "your pick can't be used")
    problems: list[str] = Field(default_factory=list)     # what still needs fixing before it can run
    single: bool = False                                   # only one work step: better to run it directly
    made_by: str | None = None                             # which model wrote it


class TriageResult(BaseModel):
    size: Literal["small", "big"]
    how: Literal["rules", "ai", "user"]
    reasons: list[str] = Field(default_factory=list)


class BrainTask(BaseModel):
    id: str
    workspace_id: str | None = None
    repo_path: str
    request: str
    test_command: str | None = None
    mode: BrainMode = "balanced"
    approval: Literal["all", "each"] = "all"   # run the whole plan, or approve before each step
    # direct  = small: run it with one agent (no plan)
    # asking  = the planner asked clarifying questions
    # planned = there is a plan to review / edit
    state: Literal["direct", "asking", "planned"]
    triage: TriageResult
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    plan: BrainPlan | None = None
    direct: StepAssignment | None = None       # small tasks: the one agent + model the router picked
    created_at: str
    updated_at: str


class BrainCreate(BaseModel):
    workspace_id: str | None = None            # the usual way: the workspace's repo is used
    repo_path: str | None = None               # or a repo folder directly
    request: str = Field(min_length=1)
    test_command: str | None = None
    mode: BrainMode | None = None              # None = the default from Settings
    approval: Literal["all", "each"] = "all"
    plan: bool | None = None                   # True = always plan, False = never; None = triage decides


class BrainPlanRequest(BaseModel):
    answers: list[str] = Field(default_factory=list)  # answers to the planner's questions, in order
    skip_questions: bool = False                     # "Just plan it" without answering


class BrainTaskEdit(BaseModel):
    steps: list[PlanStep] | None = None        # the edited plan (checked again by code)
    mode: BrainMode | None = None
    approval: Literal["all", "each"] | None = None
    reroute: bool = False                      # pick agents again for every step (drops your own picks)


class BrainSettings(BaseModel):
    mode: BrainMode = "balanced"               # the default for new Brain tasks


# ---------- Workspaces (one per product = one repo; holds its chats) ----------

class Workspace(BaseModel):
    id: str
    name: str
    repo_path: str
    test_command: str | None = None    # used by new chats in this workspace
    color: str = "sky"                 # accent shown on its card
    created_at: str
    updated_at: str
    # filled in when listing (not stored)
    manual_count: int = 0
    brain_count: int = 0
    last_activity: str | None = None


class WorkspaceCreate(BaseModel):
    repo_path: str = Field(min_length=1)
    name: str | None = None            # default: the folder's name
    test_command: str | None = None
    color: str | None = None


class WorkspaceEdit(BaseModel):
    name: str | None = None
    test_command: str | None = None
    color: str | None = None

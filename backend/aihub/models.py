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
    label: str                       # e.g. "5-hour limit", "Weekly limit"
    used_percent: float
    resets_at: str | None = None     # ISO time


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
    prompt: str = Field(min_length=1)
    supervised: bool = False           # ask before each edit / command
    test_command: str | None = None    # e.g. "pytest -q"; run after each turn
    model: str | None = None           # None = the agent's default model
    effort: str | None = None          # None = the model's default effort


class FollowUp(BaseModel):
    prompt: str = Field(min_length=1)
    model: str | None = None  # switch model for this turn and the ones after it
    effort: str | None = None


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

"""Handoffs: what a new agent is told when you switch agents mid-run.

An agent's session can't move to another agent (Claude can't open a Codex
session). But two things CAN carry over:
  1. the files: the new agent works in the same worktree, so it sees every change
  2. the context: rebuilt here from AI-Hub's own event log, which looks the
     same for every agent (this is why every event is saved in one format)

build_history() turns the events into a plain-text history (no LLM, free,
predictable). build_prompt() wraps a history (or an LLM summary of it) with
an intro and the user's new message. That whole text is what the new agent
receives as its first prompt.
"""

from collections import Counter
from dataclasses import dataclass, field

from aihub.models import AgentEvent, RunInfo, RunMetrics

MAX_TURN_TEXT = 1500       # per-turn result text kept in the history
MAX_HISTORY_CHARS = 12000  # keep the newest turns if the history is longer
MAX_FILES_LISTED = 30


@dataclass
class Turn:
    request: str
    agent: str
    model: str | None = None
    tools: Counter = field(default_factory=Counter)
    files: list[str] = field(default_factory=list)
    last_text: str = ""
    result: str | None = None
    success: bool | None = None
    errors: list[str] = field(default_factory=list)
    checks: RunMetrics | None = None


def collect_turns(events: list[AgentEvent], first_agent: str) -> list[Turn]:
    """Group the flat event list into turns (one per user message)."""
    turns: list[Turn] = []
    agent = first_agent
    for event in events:
        d = event.data
        if event.kind == "user_message":
            agent = d.get("agent_id") or agent
            turns.append(Turn(request=str(d.get("text", "")), agent=agent, model=d.get("model")))
            continue
        if not turns:
            continue
        turn = turns[-1]
        if event.kind == "tool_call":
            turn.tools[str(d.get("name", "tool"))] += 1
            tool_input = d.get("input") or {}
            path = tool_input.get("file_path") or tool_input.get("path")
            if isinstance(path, str) and path and path not in turn.files:
                turn.files.append(path)
        elif event.kind == "text" and d.get("text"):
            turn.last_text = str(d["text"])
        elif event.kind == "result":
            turn.result = d.get("summary") or None
            turn.success = d.get("success")
        elif event.kind == "error":
            turn.errors.append(str(d.get("message", "")))
        elif event.kind == "checks":
            turn.checks = RunMetrics(**d)
    return turns


def build_history(events: list[AgentEvent], run: RunInfo) -> str:
    """The run so far, as plain text. Newest turns win if it's too long."""
    turns = collect_turns(events, run.agent_id)
    if not turns:
        return ""

    blocks = []
    for number, turn in enumerate(turns, 1):
        who = turn.agent + (f" ({turn.model})" if turn.model else "")
        lines = [f"### Turn {number}: {who}", f"Request: {turn.request.strip()}"]
        outcome = turn.result or turn.last_text
        if outcome:
            status = "" if turn.success is None else (" (finished)" if turn.success else " (did not finish)")
            lines.append(f"Result{status}: {_clip(outcome.strip(), MAX_TURN_TEXT)}")
        if turn.tools:
            lines.append("Tools used: " + ", ".join(f"{name} x{n}" for name, n in turn.tools.most_common()))
        if turn.files:
            lines.append("Files touched: " + ", ".join(turn.files[:MAX_FILES_LISTED]))
        if turn.errors:
            lines.append("Errors: " + _clip(" | ".join(turn.errors), 500))
        if turn.checks and turn.checks.tests_passed is not None:
            verdict = "passed" if turn.checks.tests_passed else f"FAILED (exit {turn.checks.test_exit_code})"
            lines.append(f"Tests ({turn.checks.test_command}): {verdict}")
        blocks.append("\n".join(lines))

    # Keep the original request (turn 1) and as many of the newest turns as fit.
    history = blocks[0]
    kept: list[str] = []
    budget = MAX_HISTORY_CHARS - len(history)
    for block in reversed(blocks[1:]):
        if len(block) + 2 > budget:
            break
        kept.insert(0, block)
        budget -= len(block) + 2
    skipped = len(blocks) - 1 - len(kept)
    parts = [history]
    if skipped:
        parts.append(f"({skipped} older turn(s) left out to keep this short)")
    parts += kept
    return "\n\n".join(parts)


def describe_changes(metrics: RunMetrics | None) -> str:
    if not metrics or not metrics.files_changed:
        return "No files are changed yet."
    files = ", ".join(metrics.files[:MAX_FILES_LISTED])
    more = f" and {len(metrics.files) - MAX_FILES_LISTED} more" if len(metrics.files) > MAX_FILES_LISTED else ""
    return (f"{metrics.files_changed} file(s) changed (+{metrics.lines_added} -{metrics.lines_removed}): "
            f"{files}{more}")


def build_prompt(*, previous_agents: str, branch: str | None, original_task: str,
                 history: str, history_is_summary: bool, changes: str, new_message: str) -> str:
    """The first message the new agent receives."""
    title = "Summary of the work so far" if history_is_summary else "What happened so far"
    return "\n\n".join([
        f"You are continuing a coding task in this folder. Another AI agent ({previous_agents}) worked "
        f"on it before you. All of its file changes are already in this folder"
        f"{f' (branch {branch})' if branch else ''}; nothing has been committed. Read the files "
        "before changing them; don't redo work that is already done.",
        f"## Original task\n{original_task.strip()}",
        f"## {title}\n{history}",
        f"## Current state of the changes\n{changes}",
        f"## Your task now\n{new_message.strip()}",
    ])


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 15].rstrip() + " ...(shortened)"

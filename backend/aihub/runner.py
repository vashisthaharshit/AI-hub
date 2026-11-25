"""RunManager: starts runs, records their events, and streams them live.

A run is one task on one agent in one worktree. It can have several TURNS:
the first prompt, then follow-up messages that continue the same agent
session in the same worktree.

Flow of one turn:
  1. record the user's message as an event
  2. start the agent in the background (an asyncio Task)
  3. for every event: save it to SQLite, then push it to every open WebSocket
  4. at the end: measure the result (files changed, tests) and save the state

Also here:
  - approvals (supervised mode): the agent's request waits on a Future
    until the user clicks Allow / Deny
  - keep / discard: what happens to the run's branch at the end

This file knows nothing about FastAPI or HTTP, so it can be tested and reused
on its own (the orchestrator in Phase 3 will use it directly).
"""

from __future__ import annotations

import asyncio
import re
import secrets
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from aihub import checks, config, handoff, worktree
from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.db import Database, now_iso
from aihub.models import (
    AgentEvent, ApprovalDecision, ApprovalRequest, ChangedFile, RunCreate, RunInfo, RunMetrics, RunResult,
    UsageWindow,
)

if TYPE_CHECKING:  # only for type hints: avoids an import cycle
    from aihub.summarizer import Summarizer

_END = None  # marker put on a subscriber's queue when a turn finishes

# Error text that means "this model needs usage credits the account doesn't have".
# Only checked on failed turns, and only in error messages.
_CREDITS = re.compile(r"\b(usage credits?|credit balance|out of credits|insufficient credits?|buy credits|requires? (usage )?credits)\b", re.I)


def needs_credits(message: str) -> bool:
    return bool(_CREDITS.search(message or ""))


class RunError(Exception):
    """A request that can't be done in the run's current state (shown to the user)."""


@dataclass
class _PendingApproval:
    run_id: str
    future: asyncio.Future


class RunManager:
    def __init__(self, db: Database, registry: AgentRegistry, summarizer: Summarizer | None = None):
        self.db = db
        self.registry = registry
        self.summarizer = summarizer  # optional: writes LLM summaries for handoffs
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._next_seq: dict[str, int] = {}
        self._tokens: dict[str, str] = {}  # run_id -> secret for the approval bridge
        self._approvals: dict[str, _PendingApproval] = {}

    # ---------- starting, continuing, stopping ----------

    async def start(self, req: RunCreate) -> RunInfo:
        adapter = self.registry.get(req.agent_id)        # KeyError if unknown
        if req.supervised and not adapter.supports_supervised:
            raise RunError(f"{adapter.name} doesn't support supervised mode yet.")
        repo = await worktree.repo_root(Path(req.repo_path))  # GitError if not a repo
        base = await worktree.head_commit(repo)

        run_id = uuid4().hex[:12]
        self.db.insert_run(RunInfo(
            id=run_id, agent_id=adapter.id, repo_path=str(repo), prompt=req.prompt,
            state="pending", created_at=now_iso(), base_commit=base,
            supervised=req.supervised, test_command=(req.test_command or "").strip() or None,
            model=req.model or None, effort=req.effort or None,
        ))
        try:
            branch, path = await worktree.create_worktree(repo, run_id, base)
        except Exception:
            self.db.update_run(run_id, state="failed")
            raise
        self.db.update_run(run_id, branch=branch, worktree_path=str(path), state="running")
        self._launch(run_id, adapter, req.prompt, path, resume=None,
                     model=req.model or None, effort=req.effort or None)
        return self.db.get_run(run_id)

    async def follow_up(self, run_id: str, prompt: str, model: str | None = None,
                        effort: str | None = None, agent_id: str | None = None) -> RunInfo:
        """Send another message to a finished run, in the same worktree.

        Same agent: its session continues (it remembers everything).
        Another agent (agent_id): a NEW session starts, and its first prompt is
        a handoff: a summary of the run so far + your message (see handoff.py).
        """
        run = self._require_idle(run_id)
        if agent_id and agent_id != run.agent_id:
            return await self._switch_agent(run, prompt, agent_id, model, effort)
        adapter = self.registry.get(run.agent_id)
        if not adapter.supports_follow_up:
            raise RunError(f"{adapter.name} doesn't support follow-up messages.")
        if not run.session_id:
            raise RunError("This run has no agent session to continue.")
        # Not given = keep using the run's current model / effort. A new model
        # without an effort resets effort to that model's default.
        if model and model != run.model:
            effort = effort or None
        else:
            effort = effort or run.effort
        model = model or run.model
        self.db.update_run(run_id, state="running", model=model, effort=effort)
        self._launch(run_id, adapter, prompt, Path(run.worktree_path), resume=run.session_id,
                     model=model, effort=effort)
        return self.db.get_run(run_id)

    async def _switch_agent(self, run: RunInfo, prompt: str, agent_id: str,
                            model: str | None, effort: str | None) -> RunInfo:
        adapter = self.registry.get(agent_id)  # KeyError if unknown
        if run.supervised and not adapter.supports_supervised:
            raise RunError(f"This run is supervised, and {adapter.name} can't pause for approval. "
                           "Pick an agent that supports supervised mode.")
        # The new agent starts fresh: no session yet, and its own model/effort
        # (the old agent's model names mean nothing to it).
        self.db.update_run(run.id, state="running", agent_id=adapter.id, session_id=None,
                           model=model or None, effort=effort or None)
        self._launch(run.id, adapter, prompt, Path(run.worktree_path), resume=None,
                     model=model or None, effort=effort or None, handoff_from=run)
        return self.db.get_run(run.id)

    async def _build_handoff(self, run: RunInfo, new_message: str) -> tuple[str, dict]:
        """The new agent's first prompt, plus details saved with the user's message."""
        events = [event for _, event in self.db.events(run.id)]
        turns = handoff.collect_turns(events, run.agent_id)
        history = handoff.build_history(events, run)
        changes = handoff.describe_changes(run.metrics)
        agents = []
        for turn in turns:
            name = self._agent_name(turn.agent)
            if name not in agents:
                agents.append(name)

        kind, note, body = "template", None, history
        settings = self.summarizer.settings() if self.summarizer else None
        if settings and settings.provider != "off":
            try:
                body = await self.summarizer.summarize(f"{history}\n\nCurrent changes: {changes}", settings)
                kind, note = "llm", f"Summarized by {self.summarizer.describe(settings)}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed summary must not block the switch
                note = f"The summary model failed ({exc}); the template handoff was used instead."

        prompt = handoff.build_prompt(
            previous_agents=", ".join(agents) or self._agent_name(run.agent_id),
            branch=run.branch,
            original_task=turns[0].request if turns else run.prompt,
            history=body,
            history_is_summary=kind == "llm",
            changes=changes,
            new_message=new_message,
        )
        return prompt, {"handoff": prompt, "handoff_kind": kind, "handoff_note": note,
                        "switched_from": run.agent_id}

    def _agent_name(self, agent_id: str) -> str:
        try:
            return self.registry.get(agent_id).name
        except KeyError:
            return agent_id

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if not task:
            return False
        task.cancel()  # raises CancelledError inside _execute -> adapter kills its process
        return True

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _launch(self, run_id: str, adapter: AgentAdapter, prompt: str, path: Path,
                resume: str | None, model: str | None, effort: str | None,
                handoff_from: RunInfo | None = None) -> None:
        self._next_seq[run_id] = self.db.last_seq(run_id) + 1  # continue numbering after earlier turns
        self._tasks[run_id] = asyncio.create_task(
            self._execute(run_id, adapter, prompt, path, resume, model, effort, handoff_from))

    # ---------- one turn ----------

    async def _execute(self, run_id: str, adapter: AgentAdapter, prompt: str, path: Path,
                       resume: str | None, model: str | None, effort: str | None,
                       handoff_from: RunInfo | None = None) -> None:
        run = self.db.get_run(run_id)
        options = RunOptions(resume_session_id=resume, model=model, effort=effort)
        if run.supervised:
            token = secrets.token_urlsafe(24)
            self._tokens[run_id] = token
            options.approval_env = {"AIHUB_URL": config.base_url(), "AIHUB_RUN_ID": run_id, "AIHUB_TOKEN": token}

        state = "failed"
        result: RunResult | None = None
        errors: list[str] = []
        try:
            # Switching agents: build the handoff first (may call the summary
            # model, so it happens here in the background, not in the request).
            sent_prompt, extra = prompt, {}
            if handoff_from is not None:
                sent_prompt, extra = await self._build_handoff(handoff_from, prompt)
            self._record(run_id, AgentEvent(kind="user_message", data={
                "text": prompt, "follow_up": resume is not None or handoff_from is not None,
                "model": model, "effort": effort, "agent_id": adapter.id, **extra,
            }))
            async for event in adapter.run(sent_prompt, path, options):
                self._record(run_id, event)
                if event.kind == "error":
                    errors.append(str(event.data.get("message", "")))
                elif event.kind == "usage":
                    self.registry.update_usage(adapter.id, UsageWindow(**event.data))
                if event.kind == "session_started" and event.data.get("session_id"):
                    self.db.update_run(run_id, session_id=event.data["session_id"])
                elif event.kind == "result":
                    result = RunResult(**event.data)
            state = "succeeded" if result and result.success else "failed"
        except asyncio.CancelledError:
            state = "cancelled"
            self._record(run_id, AgentEvent(kind="error", data={"message": "Cancelled by user."}))
            raise
        except Exception as exc:  # noqa: BLE001 - any crash becomes a visible error event
            self._record(run_id, AgentEvent(kind="error", data={"message": f"AI-Hub error: {exc}"}))
        finally:
            self._tokens.pop(run_id, None)
            self._deny_pending(run_id, "The run ended.")
            if state == "failed" and model:
                failed_summary = result.summary if result and not result.success and result.summary else ""
                failure = " ".join([*errors, failed_summary])
                if needs_credits(failure):
                    self.db.hide_model(adapter.id, model, "needs credits")
                    self._record(run_id, AgentEvent(kind="error", data={
                        "message": f"'{model}' seems to need usage credits on this account. "
                                   "It's now hidden from the model list (Agents panel → show hidden models).",
                    }))
            metrics = None
            if state != "cancelled":
                try:
                    metrics = await checks.measure(path, run.base_commit or "HEAD", run.test_command)
                    self._record(run_id, AgentEvent(kind="checks", data=metrics.model_dump()))
                except asyncio.CancelledError:
                    state = "cancelled"  # cancelled while tests were running
                except Exception as exc:  # noqa: BLE001
                    self._record(run_id, AgentEvent(kind="error", data={"message": f"Checks failed: {exc}"}))
            self._finish_turn(run_id, state, result, metrics)

    def _finish_turn(self, run_id: str, state: str, result: RunResult | None, metrics: RunMetrics | None) -> None:
        fields: dict = {"state": state, "result": result}
        if metrics is not None:
            fields["metrics"] = metrics
        self.db.update_run(run_id, **fields)
        self._tasks.pop(run_id, None)
        for queue in self._subscribers.get(run_id, ()):
            queue.put_nowait(_END)

    def _record(self, run_id: str, event: AgentEvent) -> None:
        seq = self._next_seq.get(run_id, self.db.last_seq(run_id) + 1)
        self._next_seq[run_id] = seq + 1
        self.db.append_event(run_id, seq, event)          # 1. save (source of truth)
        for queue in self._subscribers.get(run_id, ()):   # 2. then notify live viewers
            queue.put_nowait((seq, event))

    # ---------- supervised mode: approvals ----------

    async def request_approval(self, req: ApprovalRequest) -> dict:
        """Called (via HTTP) by the approval MCP server. Waits for the user's decision."""
        expected = self._tokens.get(req.run_id)
        if not expected or not secrets.compare_digest(expected, req.token):
            raise PermissionError("Invalid approval token")

        approval_id = uuid4().hex[:8]
        future = asyncio.get_running_loop().create_future()
        self._approvals[approval_id] = _PendingApproval(req.run_id, future)
        self._record(req.run_id, AgentEvent(kind="approval_request", data={
            "id": approval_id, "tool_name": req.tool_name, "input": req.input,
        }))
        try:
            decision: ApprovalDecision = await future
        finally:
            self._approvals.pop(approval_id, None)

        behavior = "allow" if decision.allow else "deny"
        self._record(req.run_id, AgentEvent(kind="approval_resolved", data={
            "id": approval_id, "behavior": behavior, "message": decision.message,
        }))
        return {"behavior": behavior, "message": decision.message}

    def decide(self, run_id: str, approval_id: str, decision: ApprovalDecision) -> bool:
        pending = self._approvals.get(approval_id)
        if not pending or pending.run_id != run_id or pending.future.done():
            return False
        pending.future.set_result(decision)
        return True

    def _deny_pending(self, run_id: str, reason: str) -> None:
        for pending in list(self._approvals.values()):
            if pending.run_id == run_id and not pending.future.done():
                pending.future.set_result(ApprovalDecision(allow=False, message=reason))

    # ---------- the changes ----------

    async def changed_files(self, run_id: str) -> list[ChangedFile]:
        run = self._require_run(run_id)
        base = run.base_commit or "HEAD"
        if run.outcome == "kept":
            # The worktree folder is gone; compare the base commit with the kept branch.
            return await worktree.changed_files(Path(run.repo_path), base, run.branch)
        if run.outcome == "discarded" or not run.worktree_path:
            return []
        return await worktree.changed_files(Path(run.worktree_path), base)

    async def keep(self, run_id: str) -> RunInfo:
        """Commit the work to the run's branch and remove the worktree folder.
        The branch stays, so the user can merge it: git merge aihub/run-<id>"""
        run = self._require_idle(run_id)
        first_line = (run.prompt.strip().splitlines() or ["changes"])[0][:72]
        await worktree.commit_all(Path(run.worktree_path), f"AI-Hub: {first_line}\n\nRun {run.id} by {run.agent_id}")
        await worktree.remove_worktree(Path(run.repo_path), Path(run.worktree_path))
        self.db.update_run(run_id, outcome="kept", worktree_path=None)
        return self.db.get_run(run_id)

    async def discard(self, run_id: str) -> RunInfo:
        """Delete the worktree folder AND the branch. The work is gone."""
        run = self._require_idle(run_id)
        repo = Path(run.repo_path)
        await worktree.remove_worktree(repo, Path(run.worktree_path))
        if run.branch:
            await worktree.delete_branch(repo, run.branch)
        self.db.update_run(run_id, outcome="discarded", worktree_path=None)
        return self.db.get_run(run_id)

    async def delete(self, run_id: str) -> None:
        """Remove a run from AI-Hub.

        - Work not kept yet (worktree / branch still there): it's discarded too.
        - Kept work: only the history goes; the branch stays in git.
        """
        run = self._require_run(run_id)
        if self.is_active(run_id):
            raise RunError("The run is still working. Cancel it first.")
        repo = Path(run.repo_path)
        if run.outcome is None and repo.is_dir():
            if run.worktree_path and Path(run.worktree_path).exists():
                await worktree.remove_worktree(repo, Path(run.worktree_path))
            try:
                await worktree.prune(repo)
                if run.branch:
                    await worktree.delete_branch(repo, run.branch)
            except worktree.GitError:
                pass  # branch already gone (e.g. deleted by hand): nothing to clean
        self.db.delete_run(run_id)
        self._next_seq.pop(run_id, None)

    async def delete_many(self, run_ids: list[str]) -> tuple[list[str], list[dict]]:
        """Delete several runs. One failure doesn't stop the others."""
        deleted, failed = [], []
        for run_id in run_ids:
            try:
                await self.delete(run_id)
                deleted.append(run_id)
            except (KeyError, RunError, worktree.GitError) as exc:
                failed.append({"id": run_id, "error": exc.args[0] if exc.args else str(exc)})
        return deleted, failed

    def _require_run(self, run_id: str) -> RunInfo:
        run = self.db.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found")
        return run

    def _require_idle(self, run_id: str) -> RunInfo:
        """A run that exists, isn't running, and still has its worktree."""
        run = self._require_run(run_id)
        if self.is_active(run_id):
            raise RunError("The run is still working. Wait for it to finish or cancel it.")
        if run.outcome:
            raise RunError(f"This run was already {run.outcome}.")
        if not run.worktree_path or not Path(run.worktree_path).exists():
            raise RunError("The run's worktree folder no longer exists.")
        return run

    # ---------- live streaming ----------

    async def stream(self, run_id: str, after_seq: int = -1) -> AsyncIterator[tuple[int, AgentEvent]]:
        """Yield past events from the database, then live ones until the turn ends.

        We subscribe BEFORE reading history. Otherwise an event that arrives
        between "read history" and "subscribe" would be lost. That can cause
        a duplicate instead, so events are de-duplicated by `seq`.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            last = after_seq
            for seq, event in self.db.events(run_id, after_seq):
                yield seq, event
                last = seq
            finished = not self.is_active(run_id)
            while True:
                if finished and queue.empty():
                    return  # turn is over and nothing is left to send
                item = queue.get_nowait() if finished else await queue.get()
                if item is _END:
                    return
                seq, event = item
                if seq > last:
                    yield seq, event
                    last = seq
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

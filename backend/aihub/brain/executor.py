"""Running a Brain plan: steps, checks, parallel work, merging, retries.

    one task worktree + branch (aihub/brain-<task>)    <- your real folder is never touched
      wave 1: steps that run at the same time
        each in its own worktree, branched from the task branch
        -> checked -> committed -> merged into the task branch
           (conflict -> a fix-up agent resolves it -> else you decide)
        -> the tests on the combined result (fail -> a fix-up agent -> else you decide)
      wave 2: one step -> runs right in the task worktree -> checked -> committed
      ...
      review step (by a different agent) -> final tests -> "done"
    you review the changes -> Keep (branch stays, notes become project memory)
                           or Discard (branch and notes deleted)

Each step is a run of kind "step" (RunManager.start_step), so you can open it
and watch the agent like any run. A step is checked after its turn:
  - the agent finished without an error
  - it didn't report "STEP RESULT: blocked"
  - steps that run alone: the tests pass (only if they passed before the plan
    started; tests that were already failing aren't held against a step)
A failed check gets ONE retry with a stronger model (router.step_up): the
same run continues, so the agent sees what went wrong. After that, you decide:
retry, skip the step, or stop.

"Approve each step": before every wave AI-Hub waits; you can edit the briefs.
Everything is saved after each change, so a plan stopped (or cut off by
closing AI-Hub) continues from the wave it was in.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from aihub import checks, config, worktree
from aihub.agents.registry import AgentRegistry
from aihub.brain import learning, router
from aihub.brain.memory import Memory, format_notes, parse_report
from aihub.brain.tasks import Brain, BrainError
from aihub.db import Database, now_iso
from aihub.models import (
    AgentStatus, BrainAnswer, BrainExecution, BrainTask, ChangedFile, ExecutionLogEntry, PlanStep, RunInfo,
    StepAssignment, StepProgress,
)
from aihub.runner import RunError, RunManager

TEST_TAIL = 1500
MAX_LOG = 200


def is_worktree(folder: Path) -> bool:
    """A real git copy: the folder AND its .git link. On Windows a failed delete can
    leave the folder without .git, and git commands there fail with "not a git repository"."""
    return folder.is_dir() and (folder / ".git").exists()


def _tail(text: str | None, n: int = TEST_TAIL) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


class BrainRunner:
    def __init__(self, db: Database, runs: RunManager, registry: AgentRegistry, brain: Brain, memory: Memory):
        self.db = db
        self.runs = runs
        self.registry = registry
        self.brain = brain
        self.memory = memory
        self._loops: dict[str, asyncio.Task] = {}
        self._gates: dict[str, asyncio.Future] = {}
        self._gate_locks: dict[str, asyncio.Lock] = {}
        self._tokens: dict[str, tuple[str, str, str]] = {}   # memory token -> (task id, step id, folder)
        self._last_tests: dict[str, tuple[int, str]] = {}    # task id -> latest test run (code, output)

    # ================= public =================

    def is_running(self, task_id: str) -> bool:
        return task_id in self._loops

    async def start(self, task_id: str) -> BrainTask:
        """Run the plan, or continue a stopped one from the wave it was in."""
        if self.is_running(task_id):
            raise BrainError("This plan is already running.")
        task = self.brain.get(task_id)
        if task.state != "planned" or not task.plan or not task.plan.steps:
            raise BrainError("There is no plan to run yet.")
        if task.plan.problems:
            raise BrainError("Fix the plan's problems first (shown in red above the steps).")
        missing = [s.title for s in task.plan.steps if not s.assignment]
        if missing:
            raise BrainError("No agent is ready for some steps. Sign in to Claude Code or Codex, then click Pick agents again.")
        ex = task.execution
        if ex is None or ex.state == "discarded":
            ex = BrainExecution(state="running", steps={s.id: StepProgress(step_id=s.id) for s in task.plan.steps},
                                started_at=now_iso())
            self._log(ex, "Started the plan.")
        elif ex.state in ("stopped", "failed"):
            for s in task.plan.steps:   # steps added while stopped
                ex.steps.setdefault(s.id, StepProgress(step_id=s.id))
            ex.state, ex.waiting_for, ex.waiting_steps = "running", None, []
            self._log(ex, "Continued the plan.")
        else:
            raise BrainError(f"This plan is {ex.state}.")
        ex.message = "Starting…"
        task.execution = ex
        self.brain._save(task)
        self._loops[task_id] = asyncio.create_task(self._loop(task_id))
        return self.brain.get(task_id)

    async def answer(self, task_id: str, answer: BrainAnswer) -> BrainTask:
        """The user's reply while the plan waits (approve / retry / skip / stop)."""
        task = self.brain.get(task_id)
        if answer.briefs and task.plan:
            for step in task.plan.steps:
                if step.id in answer.briefs and answer.briefs[step.id].strip():
                    step.brief = answer.briefs[step.id].strip()
            self.brain._save(task)
        gate = self._gates.get(task_id)
        if gate and not gate.done():
            gate.set_result(answer)
            await asyncio.sleep(0.05)   # let the loop record what happens next
            return self.brain.get(task_id)
        if answer.action == "stop":
            return await self.stop(task_id)
        return await self.start(task_id)   # nothing waiting (e.g. after a restart): continue

    async def stop(self, task_id: str) -> BrainTask:
        loop = self._loops.get(task_id)
        task = self.brain.get(task_id)
        ex = task.execution
        if ex:
            for prog in ex.steps.values():
                if prog.run_id and self.runs.is_active(prog.run_id):
                    self.runs.cancel(prog.run_id)
            for run_id in ex.fixup_runs:
                if self.runs.is_active(run_id):
                    self.runs.cancel(run_id)
        if loop:
            loop.cancel()
            await asyncio.wait({loop})
        def mark(t: BrainTask) -> None:
            if t.execution and t.execution.state in ("running", "waiting"):
                t.execution.state = "stopped"
                t.execution.waiting_for, t.execution.waiting_steps = None, []
                t.execution.message = "Stopped. Continue to pick up from where it stopped, or discard the work."
                for prog in t.execution.steps.values():
                    if prog.state == "running":
                        prog.state = "waiting"
                self._log(t.execution, "Stopped by you.")
        return self._mut(task_id, mark)

    async def shutdown(self) -> None:
        """AI-Hub is closing: stop the loops (recover() marks them stopped at the next start)."""
        loops = list(self._loops.values())
        for loop in loops:
            loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)

    def recover(self) -> None:
        """At startup: plans that were running when AI-Hub closed are 'stopped'."""
        for task in self.db.list_brain_tasks(500):
            ex = task.execution
            if ex and ex.state in ("running", "waiting"):
                ex.state, ex.waiting_for, ex.waiting_steps = "stopped", None, []
                ex.message = "AI-Hub was closed while this plan ran. Continue to pick up from where it stopped."
                for prog in ex.steps.values():
                    if prog.state == "running":
                        prog.state = "waiting"
                self.brain._save(task)

    async def files(self, task_id: str) -> list[ChangedFile]:
        task = self.brain.get(task_id)
        ex = task.execution
        if not ex or not ex.base_commit or ex.state == "discarded":
            return []
        repo = Path(task.repo_path)
        if ex.state == "kept":
            return await worktree.changed_files(repo, ex.base_commit, ex.branch)
        if not ex.worktree_path or not is_worktree(Path(ex.worktree_path)):
            return []
        return await worktree.changed_files(Path(ex.worktree_path), ex.base_commit)

    async def keep(self, task_id: str) -> BrainTask:
        task = self._idle(task_id)
        ex = task.execution
        folder = Path(ex.worktree_path)
        await worktree.commit_all(folder, f"AI-Hub Brain: {self._first_line(task.request)}\n\nBrain task {task.id}")
        promoted = self.memory.promote(task.id, folder)
        await self._remove_step_worktrees(task, delete_branches=True)
        leftover = None
        try:
            await worktree.remove_worktree(Path(task.repo_path), folder)
        except worktree.GitError as exc:
            leftover = str(exc)
        def mark(t: BrainTask) -> None:
            t.execution.state, t.execution.worktree_path = "kept", None
            if leftover:
                self._log(t.execution, f"Note: {leftover}")
            t.execution.message = (f"Kept on branch {ex.branch}. Merge it into your branch with: git merge {ex.branch}"
                                   + (f" · {promoted} note{'s' if promoted != 1 else ''} saved to project memory." if promoted else ""))
            t.execution.finished_at = t.execution.finished_at or now_iso()
            self._log(t.execution, "Kept the work.")
        return self._mut(task_id, mark)

    async def discard(self, task_id: str) -> BrainTask:
        task = self._idle(task_id)
        ex = task.execution
        repo = Path(task.repo_path)
        await self._remove_step_worktrees(task, delete_branches=True)
        if ex.worktree_path and Path(ex.worktree_path).exists():
            try:
                await worktree.remove_worktree(repo, Path(ex.worktree_path))
            except worktree.GitError as exc:
                raise BrainError(f"{exc} Then click Discard again.") from None
        await worktree.prune(repo)
        try:
            await worktree.prune(repo)
            if ex.branch:
                await worktree.delete_branch(repo, ex.branch)
        except worktree.GitError:
            pass
        self.memory.forget_task(task.id)
        def mark(t: BrainTask) -> None:
            t.execution.state, t.execution.worktree_path = "discarded", None
            t.execution.message = "Discarded. The plan is still here: run it again or change it."
            self._log(t.execution, "Discarded the work.")
        return self._mut(task_id, mark)

    async def memory_tool(self, token: str, tool: str, args: dict) -> str:
        """Called by brain/memory_mcp.py (through /api/internal/memory) for a running step."""
        entry = self._tokens.get(token)
        if not entry or not token:
            raise PermissionError("Invalid memory token")
        task_id, step_id, folder = entry
        task = self.brain.get(task_id)
        if tool == "find_code":
            return await asyncio.to_thread(self.memory.code.find, Path(folder), str(args.get("name", "")))
        if tool == "search_notes":
            notes = self.memory.search(str(args.get("query", "")), workspace_id=task.workspace_id, task_id=task.id,
                                       folder=Path(folder), limit=8)
            return format_notes(notes) if notes else "No notes about that yet."
        if tool == "add_note":
            files = args.get("files") if isinstance(args.get("files"), list) else []
            note = self.memory.add(workspace_id=task.workspace_id, task_id=task.id, text=str(args.get("text", "")),
                                   files=[str(f) for f in files], folder=Path(folder))
            return "Saved." if note else "Too short to save."
        if tool == "task_status":
            return self._status_text(task, step_id)
        return f"Unknown tool '{tool}'."

    # ================= the loop =================

    async def _loop(self, task_id: str) -> None:
        try:
            await self._run(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the user; the plan can be continued
            def fail(t: BrainTask) -> None:
                t.execution.state = "failed"
                t.execution.message = ("AI-Hub hit a problem it couldn't fix on its own. Click Continue to try again "
                                       f"from where it stopped.\n{exc}")
                self._log(t.execution, f"Error: {exc}")
            self._mut(task_id, fail)
        finally:
            self._loops.pop(task_id, None)
            gate = self._gates.pop(task_id, None)
            if gate and not gate.done():
                gate.cancel()

    async def _run(self, task_id: str) -> None:
        task = self.brain.get(task_id)
        await self._prepare(task)
        await self._agents()
        task = self.brain.get(task_id)
        waves = task.plan.waves

        for i in range(task.execution.wave, len(waves)):
            self._mut(task_id, lambda t, i=i: setattr(t.execution, "wave", i))
            task = self.brain.get(task_id)
            ids = [sid for sid in waves[i] if task.execution.steps[sid].state not in ("passed", "skipped")
                   or (task.execution.steps[sid].branch and not task.execution.steps[sid].merged)]
            if not ids:
                continue
            todo = [sid for sid in ids if task.execution.steps[sid].state not in ("passed", "skipped")]
            if task.approval == "each" and todo:
                titles = ", ".join(self._step(task, s).title for s in todo)
                answer = await self._ask(task_id, "approve", todo,
                                         f"Next: {titles}. Check the brief{'s' if len(todo) > 1 else ''}, then start.")
                if answer.action == "stop":
                    await self._stopped(task_id)
                    return
                if answer.action == "skip":
                    self._mut(task_id, lambda t: [self._set_step(t, s, state="skipped", message="Skipped by you.") for s in todo])
                    continue
            if len(ids) == 1 and not task.execution.steps[ids[0]].branch:
                ok = await self._do_step(task_id, ids[0], Path(task.execution.worktree_path), parallel=False)
                if ok and self.brain.get(task_id).execution.steps[ids[0]].state == "passed":
                    await worktree.commit_all(Path(task.execution.worktree_path),
                                              f"Step: {self._step(task, ids[0]).title}")
            else:
                ok = await self._do_parallel(task_id, ids)
            if not ok:
                await self._stopped(task_id)
                return

        if not await self._final_check(task_id):
            await self._stopped(task_id)
            return

        def done(t: BrainTask) -> None:
            t.execution.state = "done"
            t.execution.wave = len(t.plan.waves)
            t.execution.finished_at = now_iso()
            passed = sum(p.state == "passed" for p in t.execution.steps.values())
            t.execution.message = f"Done: {passed} of {len(t.execution.steps)} steps passed. Review the changes, then Keep or Discard."
            self._log(t.execution, "All steps finished.")
        self._mut(task_id, done)

    async def _prepare(self, task: BrainTask) -> None:
        """The task's worktree (made once; reused when continuing) and the baseline tests."""
        ex = task.execution
        repo = Path(task.repo_path)
        if ex.worktree_path and is_worktree(Path(ex.worktree_path)):
            return
        branch = ex.branch or f"aihub/brain-{task.id}"
        path = config.WORKTREES_DIR / f"brain-{task.id}"
        config.WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
        if path.exists():   # half-deleted earlier: clear what's left, the branch has the saved work
            await asyncio.to_thread(worktree._delete_folder, path)
            if path.exists():
                path = config.WORKTREES_DIR / f"brain-{task.id}-{secrets.token_hex(3)}"
        await worktree.prune(repo)
        if ex.branch:
            await worktree._git("worktree", "add", str(path), branch, cwd=repo)
            base = ex.base_commit
        else:
            base = await worktree.head_commit(repo)
            await worktree._git("worktree", "add", "-b", branch, str(path), base, cwd=repo)

        baseline = None
        if task.test_command and not ex.branch:
            self._mut(task.id, lambda t: setattr(t.execution, "message", "Running the tests once before any step…"))
            code, _, _ = await checks.run_tests(task.test_command, path)
            baseline = code == 0

        def setup(t: BrainTask) -> None:
            e = t.execution
            if not e.branch:
                e.baseline_tests = baseline
                if baseline is False:
                    self._log(e, "The tests were already failing before the plan, so steps aren't judged by them.")
            e.branch, e.worktree_path, e.base_commit = branch, str(path), base
            self._log(e, f"Working on branch {branch} (your folder isn't touched).")
        self._mut(task.id, setup)

    # ================= one step =================

    async def _do_step(self, task_id: str, sid: str, folder: Path, parallel: bool) -> bool:
        """Run one step until it passes, is skipped, or the user stops. False = stop the plan."""
        task = self.brain.get(task_id)
        step = self._step(task, sid)
        assignment = step.assignment
        judge_tests = not parallel and bool(task.test_command) and task.execution.baseline_tests is not False
        base = await worktree.head_commit(folder)
        token = secrets.token_urlsafe(18)
        self._tokens[token] = (task_id, sid, str(folder))
        run: RunInfo | None = None
        attempt = 0
        try:
            prompt = self._brief(task, step, folder, parallel)
            run = await self._start_run(task, step, folder, base, assignment, prompt, token,
                                        test_command=task.test_command if not parallel else None)
            while True:
                attempt += 1
                self._mut(task_id, lambda t: self._set_step(t, sid, state="running", run_id=run.id, attempts=attempt,
                                                            agent_id=assignment.agent_id, model=assignment.model,
                                                            effort=assignment.effort, message=None))
                self._mut(task_id, lambda t: setattr(t.execution, "message", f"Working on: {step.title}"))
                run = await self.runs.wait(run.id)
                ok, why, summary, notes = self._judge(run, judge_tests)
                self._remember(task, sid, notes, folder)
                self._learn(task, step, assignment, run, ok, attempt)
                if ok:
                    self._mut(task_id, lambda t: (self._set_step(t, sid, state="passed", message=summary),
                                                  self._log(t.execution, f"{step.title}: passed.")))
                    return True
                self._mut(task_id, lambda t: (self._set_step(t, sid, state="failed", message=why),
                                              self._log(t.execution, f"{step.title}: didn't pass ({why[:120]}).")))
                stronger = router.step_up(assignment, await self._agents()) if attempt == 1 else None
                if stronger:
                    assignment = stronger
                    self._mut(task_id, lambda t: (self._set_step(t, sid, stepped_up=True),
                                                  self._log(t.execution, f"{step.title}: {stronger.reason}.")))
                    run = await self._retry(task, step, folder, run, assignment, why, token, parallel)
                    continue
                answer = await self._ask(task_id, "failure", [sid],
                                         f"“{step.title}” didn't pass: {why}")
                if answer.action == "stop":
                    return False
                if answer.action == "skip":
                    self._mut(task_id, lambda t: (self._set_step(t, sid, state="skipped", message="Skipped by you."),
                                                  self._log(t.execution, f"{step.title}: skipped by you.")))
                    return True
                run = await self._retry(task, step, folder, run, assignment, why, token, parallel)
        finally:
            self._tokens.pop(token, None)
            if run:
                self.runs.end_step(run.id)

    async def _start_run(self, task: BrainTask, step: PlanStep, folder: Path, base: str, a: StepAssignment,
                         prompt: str, token: str, test_command: str | None) -> RunInfo:
        adapter = self.registry.get(a.agent_id)
        return await self.runs.start_step(
            workspace_id=task.workspace_id, brain_task_id=task.id, repo_path=task.repo_path, folder=folder,
            base_commit=base, adapter=adapter, prompt=prompt, label=step.title, model=a.model, effort=a.effort,
            test_command=test_command, memory_env={"AIHUB_URL": config.base_url(), "AIHUB_MEMORY_TOKEN": token})

    async def _retry(self, task: BrainTask, step: PlanStep, folder: Path, run: RunInfo, a: StepAssignment,
                     why: str, token: str, parallel: bool) -> RunInfo:
        """Continue the same run (it remembers what it tried); a new run if that isn't possible."""
        message = (f"Your step isn't done yet: {why}\n\nFix it. Change only what this step needs. "
                   "When you finish, end your reply with the STEP RESULT and NOTES lines again.")
        try:
            return await self.runs.follow_up(run.id, message, model=a.model, effort=a.effort,
                                             agent_id=a.agent_id if a.agent_id != run.agent_id else None)
        except (RunError, KeyError):
            self.runs.end_step(run.id)
            prompt = self._brief(task, step, folder, parallel) + f"\n\nA previous attempt at this step didn't pass: {why}"
            return await self._start_run(task, step, folder, await worktree.head_commit(folder), a, prompt, token,
                                         test_command=task.test_command if not parallel else None)

    def _judge(self, run: RunInfo, judge_tests: bool) -> tuple[bool, str, str | None, list[str]]:
        """(passed?, why not, short summary, notes for memory)."""
        events = [e for _, e in self.db.events(run.id)]
        last_turn = 0
        for i, e in enumerate(events):
            if e.kind == "user_message":
                last_turn = i
        turn = events[last_turn:]
        texts = [str(e.data.get("text", "")) for e in turn if e.kind == "text"]
        final = texts[-1] if texts else ""
        status, reason, notes = parse_report("\n".join(texts[-2:]))
        summary = next((ln.strip() for ln in final.splitlines() if ln.strip() and "STEP RESULT" not in ln.upper()), None)
        if run.state == "cancelled":
            return False, "it was stopped before it finished", summary, notes
        if run.state != "succeeded":
            errors = [str(e.data.get("message", "")) for e in turn if e.kind == "error"]
            return False, f"the agent stopped with an error: {errors[-1] if errors else 'unknown error'}", summary, notes
        if status == "blocked":
            return False, f"the agent says it's blocked: {reason or 'no reason given'}", summary, notes
        if judge_tests and run.metrics and run.metrics.tests_passed is False:
            return False, f"the tests fail ({run.metrics.test_command}):\n{_tail(run.metrics.test_output)}", summary, notes
        return True, "", (summary or "Done.")[:300], notes

    def _remember(self, task: BrainTask, sid: str, notes: list[str], folder: Path) -> None:
        for text in notes:
            self.memory.add(workspace_id=task.workspace_id, task_id=task.id, text=text, files=[], folder=folder)

    def _learn(self, task: BrainTask, step: PlanStep, a: StepAssignment, run: RunInfo, ok: bool, attempt: int) -> None:
        r = run.result
        learning.record(self.db, workspace_id=task.workspace_id, task_id=task.id, step_type=step.type,
                        difficulty=step.difficulty, agent_id=a.agent_id, model=a.model, tier=self._tier(a),
                        mode=task.mode, passed=ok, attempt=attempt,
                        input_tokens=r.input_tokens if r else None, output_tokens=r.output_tokens if r else None,
                        cost_usd=r.cost_usd if r else None, duration_ms=r.duration_ms if r else None)

    def _tier(self, a: StepAssignment) -> str:
        for agent in self.registry_cache or []:
            if agent.id == a.agent_id:
                model = next((m for m in agent.models if m.id == a.model), None)
                return router.tier_of(model) if model else "medium"
        return "medium"

    registry_cache: list[AgentStatus] | None = None

    async def _agents(self) -> list[AgentStatus]:
        self.registry_cache = await self.registry.scan(False, self.db.hidden_models())
        return self.registry_cache

    # ================= parallel steps =================

    async def _do_parallel(self, task_id: str, ids: list[str]) -> bool:
        task = self.brain.get(task_id)
        repo = Path(task.repo_path)
        main = Path(task.execution.worktree_path)
        await worktree.commit_all(main, "AI-Hub: work so far")
        base = await worktree.head_commit(main)
        await self._agents()

        todo = [sid for sid in ids if task.execution.steps[sid].state not in ("passed", "skipped")]
        folders: dict[str, Path] = {}
        for sid in todo:   # a fresh worktree per step, from the task branch
            branch = f"aihub/brain-{task.id}-{sid}"
            path = config.WORKTREES_DIR / f"brain-{task.id}-{sid}"
            if path.exists():
                try:
                    await worktree.remove_worktree(repo, path)
                except worktree.GitError:
                    path = config.WORKTREES_DIR / f"brain-{task.id}-{sid}-{secrets.token_hex(3)}"  # old one still locked
            await worktree.prune(repo)
            try:
                await worktree.delete_branch(repo, branch)
            except worktree.GitError:
                pass
            await worktree._git("worktree", "add", "-b", branch, str(path), base, cwd=repo)
            folders[sid] = path
            self._mut(task_id, lambda t, sid=sid, b=branch, p=path: self._set_step(
                t, sid, branch=b, worktree_path=str(p), merged=False))
        self._log_now(task_id, f"Running {len(todo)} steps at the same time." if len(todo) > 1 else "")

        results = await asyncio.gather(*(self._do_step(task_id, sid, folders[sid], True) for sid in todo))
        if not all(results):
            return False

        # merge each finished step into the task branch
        lost = []
        for sid in ids:
            task = self.brain.get(task_id)
            prog = task.execution.steps[sid]
            if prog.merged or not prog.branch:
                continue
            step = self._step(task, sid)
            if prog.state == "passed":
                folder = Path(prog.worktree_path) if prog.worktree_path else None
                if folder and is_worktree(folder):
                    await worktree.commit_all(folder, f"Step: {step.title}")
                elif not await self._has_own_work(task, sid):
                    # Its copy was (half-)deleted before its work was saved: run it again.
                    lost.append(sid)
                    self._log_now(task_id, f"The temporary copy for “{step.title}” was lost before its work was saved, "
                                           "so it will run again.")
                    await self._remove_step_worktree(task, prog, delete_branch=True)
                    self._mut(task_id, lambda t, sid=sid: self._set_step(
                        t, sid, state="waiting", run_id=None, attempts=0, stepped_up=False, message=None,
                        branch=None, worktree_path=None, merged=False))
                    continue
                if not await self._merge(task_id, sid):
                    return False
            await self._remove_step_worktree(task, prog, delete_branch=True)
            self._mut(task_id, lambda t, sid=sid: self._set_step(t, sid, merged=True, worktree_path=None))
        if lost:
            return await self._do_parallel(task_id, ids)

        # the combined result must still pass the tests
        task = self.brain.get(task_id)
        if task.test_command and task.execution.baseline_tests is not False:
            return await self._tests_or_fix(task_id, "after combining the steps")
        return True

    async def _has_own_work(self, task: BrainTask, sid: str) -> bool:
        """Does a parallel step's branch hold work the task branch doesn't have yet,
        or was it merged already? (False = its work only lived in the lost folder.)"""
        main = Path(task.execution.worktree_path)
        prog = task.execution.steps[sid]
        title = self._step(task, sid).title
        try:
            log = await worktree._git("log", "--format=%s", "-n", "200", cwd=main)
            if f"Merge step: {title}" in log:
                return True
            ahead = await worktree._git("rev-list", "--count", f"HEAD..{prog.branch}", cwd=main)
            return int(ahead.strip() or 0) > 0
        except worktree.GitError:
            return False

    async def _merge(self, task_id: str, sid: str) -> bool:
        task = self.brain.get(task_id)
        main = Path(task.execution.worktree_path)
        step = self._step(task, sid)
        prog = task.execution.steps[sid]
        identity = await self._identity(main)
        try:
            await worktree._git(*identity, "merge", "--no-ff", "--no-edit", "-m", f"Merge step: {step.title}",
                                prog.branch, cwd=main)
            self._log_now(task_id, f"Combined: {step.title}.")
            return True
        except worktree.GitError:
            pass
        conflicted = [f for f in (await worktree._git("diff", "--name-only", "--diff-filter=U", cwd=main)).splitlines() if f]
        if not conflicted:
            raise worktree.GitError(f"Couldn't combine “{step.title}” with the other steps.")
        self._log_now(task_id, f"“{step.title}” changed the same lines as another step ({', '.join(conflicted)}). Asking an agent to combine them.")
        others = [self._step(task, s) for s, p in task.execution.steps.items() if p.merged and s != sid]
        intents = "\n".join(f"- {s.title}: {s.brief}" for s in [*others, step])
        prompt = (f"Several steps of a plan were done at the same time in separate copies, and some changed the same "
                  f"lines. Git couldn't combine them. These files have conflict markers (<<<<<<<, =======, >>>>>>>):\n"
                  f"{chr(10).join('- ' + f for f in conflicted)}\n\nWhat each step was meant to do:\n{intents}\n\n"
                  "Edit those files so all of the changes work together, and remove every conflict marker. "
                  "Don't run git commands; AI-Hub commits for you.\n\n" + self._report_rules())
        while True:
            await self._fixup(task_id, f"Combine: {step.title}", prompt, test_command=None)
            left = [f for f in conflicted if self._has_markers(main / f)]
            if not left:
                await worktree.commit_all(main, f"Merge step: {step.title} (conflicts resolved)")
                self._log_now(task_id, f"Combined: {step.title} (conflicts resolved by an agent).")
                return True
            answer = await self._ask(task_id, "conflict", [sid],
                                     f"These files still have conflict markers: {', '.join(left)}. Fix them in "
                                     f"{main} yourself and click Continue, ask an agent to try again, or stop.")
            if answer.action == "stop":
                await worktree._git("merge", "--abort", cwd=main)
                return False
            if answer.action == "continue":
                left = [f for f in conflicted if self._has_markers(main / f)]
                if not left:
                    await worktree.commit_all(main, f"Merge step: {step.title} (conflicts resolved by hand)")
                    return True
            # retry: loop again

    @staticmethod
    def _has_markers(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return any(line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "=======" for line in text.splitlines())

    async def _tests_or_fix(self, task_id: str, when: str) -> bool:
        """Run the tests on the task worktree; if they fail, a fix-up agent gets one try, then the user decides."""
        task = self.brain.get(task_id)
        main = Path(task.execution.worktree_path)
        self._mut(task_id, lambda t: setattr(t.execution, "message", f"Running the tests {when}…"))
        code, output, _ = await checks.run_tests(task.test_command, main)
        tries = 0
        try:
            return await self._fix_tests_loop(task_id, task, main, when, code, output, tries)
        finally:
            self._record_tests(task_id)

    async def _fix_tests_loop(self, task_id: str, task: BrainTask, main: Path, when: str,
                              code: int, output: str, tries: int) -> bool:
        while code != 0:
            if tries == 0:
                self._log_now(task_id, f"The tests fail {when}. Asking an agent to fix them.")
                prompt = (f"The steps of a plan were combined and now the tests fail. Test command: "
                          f"{task.test_command}\n\nOutput (end):\n{_tail(output)}\n\nFind the cause and fix it, keeping "
                          f"what each step was meant to do:\n"
                          + "\n".join(f"- {s.title}: {s.brief}" for s in task.plan.steps if s.type != "review")
                          + "\n\nDon't run git commands.\n\n" + self._report_rules())
                await self._fixup(task_id, f"Fix the tests {when}", prompt, test_command=task.test_command)
            else:
                answer = await self._ask(task_id, "failure", [], f"The tests still fail {when}:\n{_tail(output, 600)}")
                if answer.action == "stop":
                    return False
                if answer.action == "skip":
                    self._log_now(task_id, "Continuing although the tests fail (your choice).")
                    return True
                if answer.action == "retry":
                    prompt = (f"The tests still fail. Test command: {task.test_command}\n\nOutput (end):\n"
                              f"{_tail(output)}\n\nFix the cause. Don't run git commands.\n\n" + self._report_rules())
                    await self._fixup(task_id, f"Fix the tests {when} (again)", prompt, test_command=task.test_command)
            tries += 1
            code, output, _ = await checks.run_tests(task.test_command, main)
            self._last_tests[task_id] = (code, output)
        self._last_tests[task_id] = (code, output)
        await worktree.commit_all(main, f"AI-Hub: fixes {when}")
        return True

    def _record_tests(self, task_id: str) -> None:
        if task_id in self._last_tests:
            code, output = self._last_tests.pop(task_id)
            self._mut(task_id, lambda t: (setattr(t.execution, "final_tests", code == 0),
                                          setattr(t.execution, "final_test_output", _tail(output, 4000))))

    async def _fixup(self, task_id: str, label: str, prompt: str, test_command: str | None) -> RunInfo | None:
        task = self.brain.get(task_id)
        main = Path(task.execution.worktree_path)
        a = router.strongest(await self._agents())
        if not a:
            raise BrainError("No agent is ready for the fix-up. Sign in to Claude Code or Codex, then click Continue.")
        token = secrets.token_urlsafe(18)
        self._tokens[token] = (task_id, "fixup", str(main))
        try:
            run = await self.runs.start_step(
                workspace_id=task.workspace_id, brain_task_id=task.id, repo_path=task.repo_path, folder=main,
                base_commit=await worktree.head_commit(main), adapter=self.registry.get(a.agent_id), prompt=prompt,
                label=label, model=a.model, effort=a.effort, test_command=test_command,
                memory_env={"AIHUB_URL": config.base_url(), "AIHUB_MEMORY_TOKEN": token})
            self._mut(task_id, lambda t: (t.execution.fixup_runs.append(run.id),
                                          setattr(t.execution, "message", f"{label} ({a.agent_name})…")))
            run = await self.runs.wait(run.id)
            self.runs.end_step(run.id)
            return run
        finally:
            self._tokens.pop(token, None)

    async def _final_check(self, task_id: str) -> bool:
        task = self.brain.get(task_id)
        main = Path(task.execution.worktree_path)
        await worktree.commit_all(main, "AI-Hub: final changes")
        if not task.test_command:
            return True
        if task.execution.baseline_tests is False:   # report only: they were failing before the plan
            code, output, _ = await checks.run_tests(task.test_command, main)
            self._last_tests[task_id] = (code, output)
            self._record_tests(task_id)
            return True
        return await self._tests_or_fix(task_id, "at the end")

    # ================= waiting for the user =================

    async def _ask(self, task_id: str, kind: str, step_ids: list[str], message: str) -> BrainAnswer:
        lock = self._gate_locks.setdefault(task_id, asyncio.Lock())
        async with lock:   # parallel steps that fail ask one after another
            future = asyncio.get_running_loop().create_future()
            self._gates[task_id] = future
            def wait(t: BrainTask) -> None:
                t.execution.state, t.execution.waiting_for, t.execution.waiting_steps = "waiting", kind, step_ids
                t.execution.message = message
                self._log(t.execution, "Waiting for you: " + message.splitlines()[0][:160])
            self._mut(task_id, wait)
            try:
                answer: BrainAnswer = await future
            finally:
                self._gates.pop(task_id, None)
            def resume(t: BrainTask) -> None:
                t.execution.state, t.execution.waiting_for, t.execution.waiting_steps = "running", None, []
                t.execution.message = {"continue": "Continuing…", "retry": "Trying again…", "skip": "Skipping…",
                                       "stop": "Stopping…"}[answer.action]
            self._mut(task_id, resume)
            return answer

    async def _stopped(self, task_id: str) -> None:
        def mark(t: BrainTask) -> None:
            t.execution.state = "stopped"
            t.execution.waiting_for, t.execution.waiting_steps = None, []
            t.execution.message = "Stopped. Continue to pick up from where it stopped, or discard the work."
            self._log(t.execution, "Stopped.")
        self._mut(task_id, mark)

    # ================= briefs =================

    @staticmethod
    def _report_rules() -> str:
        return ("When you finish, end your reply with exactly these lines:\n"
                "STEP RESULT: done\n"
                "(or, if you can't do it: STEP RESULT: blocked: <why>)\n"
                "NOTES:\n"
                "- <one short fact later steps should know: a decision, where something is, a gotcha; with file paths>\n"
                "(1-3 notes, or leave NOTES empty)")

    def _brief(self, task: BrainTask, step: PlanStep, folder: Path, parallel: bool) -> str:
        ex = task.execution
        lines = ["You are one of several AI coding agents working on one task in this repository. "
                 "Each agent does one step of a plan. Do only your step.", "",
                 f"The whole task: {task.request}", "", "The plan:"]
        for n, s in enumerate(task.plan.steps, start=1):
            prog = ex.steps.get(s.id)
            if s.id == step.id:
                mark = "YOU"
            elif prog and prog.state == "passed":
                mark = "done"
            elif prog and prog.state == "skipped":
                mark = "skipped"
            else:
                mark = "later" if not parallel or s.id not in self._wave_of(task, step.id) else "at the same time"
            who = f" ({prog.agent_id}): {prog.message}" if prog and prog.state == "passed" and prog.message else ""
            lines.append(f"{n}. [{mark}] {s.title}{who}")
        lines += ["", f"Your step: {step.title}", f"What to do: {step.brief}", f"Done when: {step.done_when}"]
        if step.files:
            lines.append(f"Files you'll likely change: {', '.join(step.files)}")
        if parallel:
            others = [self._step(task, s) for s in self._wave_of(task, step.id) if s != step.id]
            if others:
                lines += ["", "These steps run at the same time in separate copies. Don't change their files:"]
                lines += [f"- {o.title}: {', '.join(o.files) or 'files not listed'}" for o in others]
        if step.type == "review":
            lines += ["", "Review everything the other steps changed. Fix small problems directly; describe bigger "
                          "ones in your reply and in NOTES."]
        notes = self.memory.search(f"{step.title} {step.brief}", workspace_id=task.workspace_id, task_id=task.id,
                                   folder=folder, files=step.files, limit=5)
        if notes:
            lines += ["", "What earlier steps and past tasks learned (project memory):", format_notes(notes)]
        lines += ["", "Tools: if you have the aihub_memory tools, use find_code (where things are defined and used) "
                      "and search_notes before exploring the repo yourself, and add_note for anything later steps "
                      "should know.",
                  "Don't commit and don't run git push: AI-Hub handles git.", "", self._report_rules()]
        return "\n".join(lines)

    @staticmethod
    def _wave_of(task: BrainTask, sid: str) -> list[str]:
        return next((w for w in task.plan.waves if sid in w), [sid])

    def _status_text(self, task: BrainTask, current: str) -> str:
        ex = task.execution
        lines = [f"Task: {task.request}", ""]
        for n, s in enumerate(task.plan.steps, start=1):
            prog = ex.steps.get(s.id) if ex else None
            state = "you" if s.id == current else (prog.state if prog else "waiting")
            lines.append(f"{n}. {s.title} [{state}]" + (f" by {prog.agent_id}: {prog.message}"
                                                      if prog and prog.state == "passed" and prog.message else ""))
        return "\n".join(lines)

    # ================= helpers =================

    @staticmethod
    def _step(task: BrainTask, sid: str) -> PlanStep:
        return next(s for s in task.plan.steps if s.id == sid)

    @staticmethod
    def _set_step(t: BrainTask, sid: str, **fields) -> None:
        prog = t.execution.steps.setdefault(sid, StepProgress(step_id=sid))
        for key, value in fields.items():
            setattr(prog, key, value)

    def _mut(self, task_id: str, change) -> BrainTask:
        """Read the task fresh, change it, save it (the user may have edited it meanwhile)."""
        task = self.brain.get(task_id)
        change(task)
        return self.brain._save(task)

    @staticmethod
    def _log(ex: BrainExecution, text: str) -> None:
        if text:
            ex.log.append(ExecutionLogEntry(at=now_iso(), text=text))
            del ex.log[:-MAX_LOG]

    def _log_now(self, task_id: str, text: str) -> None:
        if text:
            self._mut(task_id, lambda t: self._log(t.execution, text))

    def _idle(self, task_id: str) -> BrainTask:
        if self.is_running(task_id):
            raise BrainError("The plan is still running. Stop it first.")
        task = self.brain.get(task_id)
        ex = task.execution
        if not ex or ex.state in ("kept", "discarded"):
            raise BrainError("There is no work to keep or discard.")
        if not ex.worktree_path or not is_worktree(Path(ex.worktree_path)):
            raise BrainError("The plan's working copy is missing. Click Continue to rebuild it from its branch, "
                             "or Discard.")
        return task

    async def _remove_step_worktrees(self, task: BrainTask, delete_branches: bool) -> None:
        for prog in task.execution.steps.values():
            await self._remove_step_worktree(task, prog, delete_branches)

    async def _remove_step_worktree(self, task: BrainTask, prog: StepProgress, delete_branch: bool) -> None:
        """Clean up a parallel step's copy. Never fails the plan: its work is already merged."""
        repo = Path(task.repo_path)
        if prog.worktree_path and Path(prog.worktree_path).exists():
            try:
                await worktree.remove_worktree(repo, Path(prog.worktree_path))
            except worktree.GitError as exc:
                self._log_now(task.id, f"Note: {exc}")
                return
        if delete_branch and prog.branch:
            try:
                await worktree.delete_branch(repo, prog.branch)
            except worktree.GitError:
                pass

    @staticmethod
    async def _identity(folder: Path) -> list[str]:
        try:
            await worktree._git("config", "user.email", cwd=folder)
            return []
        except worktree.GitError:
            return ["-c", "user.name=AI-Hub", "-c", "user.email=aihub@localhost"]

    @staticmethod
    def _first_line(text: str) -> str:
        return (text.strip().splitlines() or ["changes"])[0][:72]

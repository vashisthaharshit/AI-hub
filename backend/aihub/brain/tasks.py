"""Brain tasks: triage -> (questions) -> plan -> the user edits it.

The planner and the AI triage check use the cheapest model available, the
same one that writes app guides (apps/guide.py pick_writer): the summary
model from Settings if set, else the user's own agent with its smallest
model at the lowest effort.

After every plan or edit, the router (brain/router.py) gives each step an
agent, model and effort. Running the plan comes in the next build steps.
"""

import asyncio
import uuid
from pathlib import Path

from aihub import workspaces, worktree
from aihub.agents.registry import AgentRegistry
from aihub.apps.guide import pick_writer
from aihub.brain import learning, planner, router, triage
from aihub.brain.digest import repo_digest
from aihub.db import Database, now_iso
from aihub.models import (
    BrainCreate, BrainPlan, BrainPlanRequest, BrainSettings, BrainTask, BrainTaskEdit, PlanStep, TriageResult,
)
from aihub.summarizer import Summarizer, SummaryError

PLAN_TOKENS = 2000
TRIAGE_TOKENS = 150


class BrainError(RuntimeError):
    """Shown to the user as-is."""


class Brain:
    def __init__(self, db: Database, summarizer: Summarizer, registry: AgentRegistry):
        self.db = db
        self.summarizer = summarizer
        self.registry = registry
        self._locks: dict[str, asyncio.Lock] = {}

    def settings(self) -> BrainSettings:
        return BrainSettings(**(self.db.get_setting("brain", {}) or {}))

    def save_settings(self, settings: BrainSettings) -> BrainSettings:
        self.db.set_setting("brain", settings.model_dump())
        return settings

    def default_mode(self) -> str:
        return self.settings().mode

    async def route(self, task: BrainTask) -> None:
        """Pick an agent, model and effort for every step (in place).

        A task run directly is routed like a one-step plan: a small task is an
        easy step (Balanced -> a small model); one the user chose to run
        directly although it looked big is a medium step."""
        agents = await self.registry.scan(False, self.db.hidden_models())
        if task.state == "direct":
            difficulty = "medium" if task.triage.how == "user" else "easy"
            step = PlanStep(id="task", title="The whole task", type="build", difficulty=difficulty,
                            brief=task.request, done_when="The task is done.")
            router.route(BrainPlan(steps=[step], waves=[["task"]]), agents, task.mode, learning.load(self.db))
            task.direct = step.assignment
        elif task.plan:
            task.plan.routing_notes = router.route(task.plan, agents, task.mode, learning.load(self.db))

    async def _writer(self):
        settings = await pick_writer(self.summarizer, self.registry, self.db.hidden_models())
        if settings is None:
            raise BrainError("No AI is available to plan. Sign in to Claude Code, Codex or Antigravity, "
                             "or set a summary model in Settings.")
        return settings

    async def _ask(self, instructions: str, text: str, max_tokens: int) -> tuple[str, str]:
        settings = await self._writer()
        try:
            answer = await self.summarizer.complete(instructions, text, settings, max_tokens)
        except SummaryError as exc:
            raise BrainError(f"The planner couldn't answer: {exc}") from None
        return answer, self.summarizer.describe(settings)

    def get(self, task_id: str) -> BrainTask:
        task = self.db.get_brain_task(task_id)
        if not task:
            raise KeyError(f"Brain task '{task_id}' not found")
        return task

    def _save(self, task: BrainTask) -> BrainTask:
        task.updated_at = now_iso()
        self.db.save_brain_task(task)
        return task

    # ---------- triage ----------

    async def triage(self, request: str) -> TriageResult:
        size, reasons = triage.rule_triage(request)
        if size != "unsure":
            return TriageResult(size=size, how="rules", reasons=reasons)
        try:
            answer, _ = await self._ask(triage.AI_INSTRUCTIONS, request, TRIAGE_TOKENS)
            parsed = triage.parse_ai_answer(answer)
        except BrainError:
            parsed = None
        if parsed:
            return TriageResult(size=parsed[0], how="ai", reasons=[parsed[1]] if parsed[1] else reasons)
        return TriageResult(size="small", how="rules",
                            reasons=[*reasons, "not sure, so running it directly (you can plan it instead)"])

    # ---------- create / plan ----------

    async def create(self, req: BrainCreate) -> BrainTask:
        if req.workspace_id:
            ws = self.db.get_workspace(req.workspace_id)
            if not ws:
                raise BrainError("That workspace doesn't exist any more.")
            repo_path = ws.repo_path
        elif req.repo_path:
            repo_path = req.repo_path
        else:
            raise BrainError("Pick a workspace first.")
        try:
            repo = await worktree.repo_root(Path(repo_path).expanduser())
        except worktree.GitError as exc:
            raise BrainError(f"That folder isn't a Git repository: {exc}") from None
        ws = workspaces.ensure_for_repo(self.db, str(repo))
        if req.plan is None:
            found = await self.triage(req.request)
        else:
            found = TriageResult(size="big" if req.plan else "small", how="user",
                                 reasons=["you chose to plan it" if req.plan else "you chose to run it directly"])
        now = now_iso()
        task = BrainTask(
            id=uuid.uuid4().hex[:12], workspace_id=ws.id, repo_path=str(repo), request=req.request.strip(),
            test_command=(req.test_command or ws.test_command or "").strip() or None, mode=req.mode or self.default_mode(),
            approval=req.approval, state="direct", triage=found, created_at=now, updated_at=now,
        )
        if found.size == "big":
            self._save(task)
            return await self.plan(task.id, BrainPlanRequest())
        await self.route(task)
        return self._save(task)

    async def plan(self, task_id: str, req: BrainPlanRequest) -> BrainTask:
        """Ask the planner (again). With answers: the questions are answered, plan now."""
        async with self._locks.setdefault(task_id, asyncio.Lock()):
            task = self.get(task_id)
            if task.execution and task.execution.state not in ("kept", "discarded"):
                raise BrainError("This plan has work in progress. Keep or discard it before writing a new plan.")
            task.execution = None
            if req.answers:
                task.answers = [a.strip() for a in req.answers]
            final = bool(task.questions) and (bool(req.answers) or req.skip_questions)
            if task.triage.size == "small":
                if task.triage.how != "user":
                    learning.count_override(self.db, "small_to_plan")
                task.triage = TriageResult(size="big", how="user", reasons=["you chose to plan it"])
            digest = await asyncio.to_thread(repo_digest, Path(task.repo_path))

            problems: list[str] | None = None
            for attempt in range(2):   # one retry if the plan has problems
                text = planner.planner_input(task.request, digest.text, task.test_command,
                                             task.questions if final else [], task.answers if final else [],
                                             problems)
                answer, made_by = await self._ask(planner.INSTRUCTIONS, text, PLAN_TOKENS)
                try:
                    data = planner.parse(answer)
                    questions = [] if final else planner.questions_of(data)
                    if questions:
                        task.questions, task.answers, task.plan, task.state = questions, [], None, "asking"
                        return self._save(task)
                    plan = planner.build_plan(planner.steps_of(data), made_by)
                except planner.PlanError as exc:
                    if attempt == 0:
                        problems = [str(exc)]
                        continue
                    raise BrainError(str(exc)) from None
                if not plan.problems or attempt == 1:
                    break
                problems = plan.problems
            task.plan, task.state = plan, "planned"
            await self.route(task)
            return self._save(task)

    # ---------- the user's changes ----------

    async def edit(self, task_id: str, req: BrainTaskEdit) -> BrainTask:
        task = self.get(task_id)
        if task.execution and task.execution.state in ("running", "waiting") and req.steps is not None:
            raise BrainError("The plan is running. Stop it before changing its steps.")
        if req.mode:
            task.mode = req.mode
        if req.approval:
            task.approval = req.approval
        if req.steps is not None:
            if not task.plan:
                raise BrainError("This task has no plan to edit yet.")
            made_by = task.plan.made_by
            task.plan = planner.build_plan([s.model_copy() for s in req.steps], made_by)
        if task.plan and req.reroute:
            for s in task.plan.steps:
                s.assignment = None
        await self.route(task)   # a new mode, difficulty or step changes who does what
        return self._save(task)

    async def run_directly(self, task_id: str) -> BrainTask:
        task = self.get(task_id)
        if task.execution and task.execution.state not in ("discarded",):
            raise BrainError("This plan has already started. Stop or discard it first.")
        if task.triage.size == "big" and task.triage.how != "user":
            learning.count_override(self.db, "big_to_direct")
        task.state, task.plan = "direct", None
        task.triage = TriageResult(size="small", how="user", reasons=["you chose to run it directly"])
        await self.route(task)
        return self._save(task)


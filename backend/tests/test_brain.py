"""Brain step 1: triage, the planner and its code checks (brain/)."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from aihub.brain import planner, triage
from aihub.brain.digest import repo_digest
from aihub.brain.tasks import Brain, BrainError
from aihub.db import Database
from aihub.models import (
    AgentStatus, BrainCreate, BrainPlanRequest, BrainTaskEdit, ModelOption, PlanStep, SummarySettings,
)


# ---------- triage rules ----------

@pytest.mark.parametrize("request_, size", [
    ("fix the typo in README.md", "small"),
    ("rename get_user to fetch_user in backend/users.py", "small"),
    ("Build login with an API endpoint, a login page in the frontend and tests", "big"),
    ("Refactor the database layer and migrate all queries to the new schema", "big"),
    ("- add export button\n- add CSV endpoint\n- write tests\n- update the docs", "big"),
])
def test_rule_triage(request_, size):
    assert triage.rule_triage(request_)[0] == size


def test_unclear_request_is_unsure():
    size, reasons = triage.rule_triage("Make the reports page load the data for the selected customer and show totals per month")
    assert size == "unsure" and reasons


def test_parse_ai_triage():
    assert triage.parse_ai_answer('ok {"size": "big", "reason": "Two areas."}') == ("big", "Two areas.")
    assert triage.parse_ai_answer('{"size": "medium"}') is None
    assert triage.parse_ai_answer("no json") is None


# ---------- plan checks ----------

def step(sid, files=(), deps=(), kind="build", **kw):
    return PlanStep(id=sid, title=kw.get("title", sid), type=kind, files=list(files), depends_on=list(deps),
                    brief=kw.get("brief", "do it"), done_when=kw.get("done_when", "tests pass"))


def test_parallel_steps_never_share_files():
    plan = planner.build_plan([step("s1", ["backend/auth.py"]), step("s2", ["frontend/Login.tsx"]),
                               step("s3", ["backend"])])
    by_id = {s.id: s for s in plan.steps}
    assert by_id["s3"].depends_on == ["s1"]                 # both touch backend/
    assert by_id["s2"].depends_on == []                     # different files: runs with s1
    assert plan.waves[0] == ["s1", "s2"] and plan.waves[1] == ["s3"]
    assert any("both change" in n for n in plan.notes)


def test_review_step_is_added_last():
    plan = planner.build_plan([step("s1", ["a.py"]), step("s2", ["b.py"])])
    review = plan.steps[-1]
    assert review.type == "review" and review.depends_on == ["s1", "s2"] and review.files == ["a.py", "b.py"]
    assert plan.waves[-1] == [review.id] and not plan.problems and not plan.single


def test_problems():
    assert planner.build_plan([step("a", deps=["b"]), step("b", deps=["a"])]).problems  # circle
    assert planner.build_plan([step(f"s{i}") for i in range(7)]).problems                # too many
    assert planner.build_plan([step("s1", done_when="")]).problems                       # no check
    assert planner.build_plan([step("s1")]).single                                       # just run it


def test_bad_ids_and_unknown_dependencies_are_fixed():
    plan = planner.build_plan([step("s1"), step("s1", deps=["nope", "s1"])])
    ids = [s.id for s in plan.steps]
    assert len(set(ids)) == len(ids)
    assert plan.steps[1].depends_on in ([], ["s1"])


def test_parse_planner_answer():
    data = planner.parse('Sure!\n```json\n{"steps": [{"title": "X", "type": "weird", "difficulty": "HARD", '
                         '"files": "app.py", "brief": "b", "done_when": "d"}]}\n```')
    [s] = planner.steps_of(data)
    assert (s.id, s.type, s.difficulty, s.files) == ("s1", "build", "hard", ["app.py"])
    assert planner.questions_of({"questions": ["a?", "b?", "c?"]}) == ["a?", "b?"]
    with pytest.raises(planner.PlanError):
        planner.parse("nothing here")


def test_digest(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\nA demo app.", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("x = 1", encoding="utf-8")
    d = repo_digest(tmp_path)
    assert "A demo app." in d.text and "backend/: main.py" in d.text and "backend/main.py" in d.files


# ---------- the whole flow, with a fake planner model ----------

class FakeSummarizer:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def settings(self):
        return SummarySettings(provider="groq", model="cheap")

    def describe(self, s):
        return "Groq · cheap"

    async def complete(self, instructions, text, settings=None, max_tokens=800):
        self.calls.append(text)
        return self.answers.pop(0)


PLAN = json.dumps({"steps": [
    {"id": "s1", "title": "API", "type": "build", "difficulty": "medium", "files": ["backend/auth.py"],
     "depends_on": [], "brief": "Add POST /login.", "done_when": "pytest passes"},
    {"id": "s2", "title": "Page", "type": "build", "difficulty": "easy", "files": ["frontend/Login.tsx"],
     "depends_on": [], "brief": "Add the page.", "done_when": "npm run build passes"},
]})


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "README.md").write_text("# r", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
    return folder


class FakeRegistry:
    async def scan(self, refresh=False, hidden=None):
        return [AgentStatus(id="claude-code", name="Claude Code", state="ready",
                            models=[ModelOption(id="haiku", label="Haiku"), ModelOption(id="sonnet", label="Sonnet")])]


def brain(tmp_path, answers):
    return Brain(Database(tmp_path / "aihub.db"), FakeSummarizer(answers), registry=FakeRegistry())


def test_small_task_runs_directly_without_any_ai_call(tmp_path, repo):
    b = brain(tmp_path, [])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="fix the typo in README.md")))
    assert task.state == "direct" and task.triage.how == "rules" and task.plan is None
    assert b.summarizer.calls == []


def test_big_task_gets_a_plan(tmp_path, repo):
    b = brain(tmp_path, [PLAN])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo),
                                            request="Build login with an API endpoint, a login page and tests")))
    assert task.state == "planned" and task.mode == "balanced"
    assert [s.id for s in task.plan.steps] == ["s1", "s2", "review"] and task.plan.made_by == "Groq · cheap"
    assert all(s.assignment and s.assignment.agent_id == "claude-code" for s in task.plan.steps)  # routed
    assert b.db.get_brain_task(task.id).plan.waves == [["s1", "s2"], ["review"]]


def test_unsure_task_asks_the_cheap_model(tmp_path, repo):
    b = brain(tmp_path, ['{"size": "small", "reason": "One page."}'])
    task = asyncio.run(b.create(BrainCreate(
        repo_path=str(repo), request="Make the reports page load the data for the selected customer and show totals per month")))
    assert task.state == "direct" and task.triage.how == "ai" and task.triage.reasons == ["One page."]


def test_questions_then_plan(tmp_path, repo):
    b = brain(tmp_path, ['{"questions": ["Which page?"]}', PLAN])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="make the app better", plan=True)))
    assert task.state == "asking" and task.questions == ["Which page?"]
    task = asyncio.run(b.plan(task.id, BrainPlanRequest(answers=["The login page"])))
    assert task.state == "planned"
    assert "A: The login page" in b.summarizer.calls[-1] and "Do not ask more questions" in b.summarizer.calls[-1]


def test_one_retry_when_the_plan_has_problems(tmp_path, repo):
    bad = json.dumps({"steps": [{"id": "s1", "title": "X", "brief": "b"}]})   # no done_when
    b = brain(tmp_path, [bad, PLAN])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="x", plan=True)))
    assert task.state == "planned" and not task.plan.problems
    assert "previous plan had these problems" in b.summarizer.calls[-1]


def test_edits_are_checked_again(tmp_path, repo):
    b = brain(tmp_path, [PLAN])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="x", plan=True)))
    steps = [s for s in task.plan.steps if s.type != "review"]
    steps[1].files = ["backend/auth.py"]     # now both edit the same file
    task = asyncio.run(b.edit(task.id, BrainTaskEdit(steps=steps, mode="best", approval="each")))
    assert task.mode == "best" and task.approval == "each"
    assert {s.id: s for s in task.plan.steps}["s2"].depends_on == ["s1"]


def test_not_a_repo(tmp_path):
    with pytest.raises(BrainError, match="isn't a Git repository"):
        asyncio.run(brain(tmp_path, []).create(BrainCreate(repo_path=str(tmp_path), request="x")))


def test_direct_task_gets_a_small_model(tmp_path, repo):
    b = brain(tmp_path, [])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="fix the typo in README.md")))
    assert task.direct.agent_id == "claude-code" and task.direct.model == "haiku"   # easy step, Balanced
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="fix the typo in README.md", mode="best")))
    assert task.direct.model == "sonnet"


def test_running_a_big_task_directly_uses_a_medium_model(tmp_path, repo):
    b = brain(tmp_path, [PLAN])
    task = asyncio.run(b.create(BrainCreate(repo_path=str(repo), request="x", plan=True)))
    task = asyncio.run(b.run_directly(task.id))
    assert task.state == "direct" and task.direct.model == "sonnet"

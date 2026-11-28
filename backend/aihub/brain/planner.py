"""The planner: turn a request into 2-5 steps that different agents can run.

One cheap call does both jobs: if the request is too vague it answers with
1-2 clarifying questions, otherwise with the plan. After the user answers,
it's called again with the answers and told not to ask more.

The plan is then checked and fixed BY CODE (free, and more reliable than
trusting a small model):

  checked (a problem -> the planner gets one retry, then the user sees it)
    - 1-5 work steps, each with a title, a brief and a "done when" check
    - no circular "depends on"
  fixed automatically (and the user is told, in plan.notes)
    - two steps that could run at the same time but share a file: the later
      one now waits for the earlier one (parallel steps never edit the same file)
    - a final review step, by a fresh pair of eyes, depends on every other step
    - unknown "depends on" ids are dropped; type/difficulty get sane defaults

waves = the order things run in: every step in a wave can run in parallel.
"""

import json
import re

from aihub.models import BrainPlan, PlanStep

MAX_WORK_STEPS = 5
MAX_QUESTIONS = 2
STEP_TYPES = ("explore", "build", "test", "review", "fix")
DIFFICULTIES = ("easy", "medium", "hard")

INSTRUCTIONS = f"""You plan coding work for a team of AI coding agents (Claude Code, Codex, Antigravity).
You get a request and a short description of the repository. Split the work into a few steps
that different agents can do, some at the same time.

Rules:
- 2 to {MAX_WORK_STEPS} work steps. Never split work that touches the same files into steps that run
  at the same time; make one wait for the other with depends_on.
- Each step: a short title, a type ({", ".join(STEP_TYPES)}), a difficulty ({", ".join(DIFFICULTIES)}),
  the files or folders it will likely touch (repo-relative paths, new files allowed),
  depends_on (ids of steps that must finish first), a brief (what the agent must do, 1-4 sentences,
  specific to this repo), and done_when (a concrete check: a command that passes, a file that exists...).
- Don't add a final review step; it is added for you.
- Only if the request is too vague to plan well, ask at most {MAX_QUESTIONS} short questions instead.

Answer with JSON only, one of:
{{"questions": ["..."]}}
{{"steps": [{{"id": "s1", "title": "...", "type": "build", "difficulty": "medium", "files": ["..."],
  "depends_on": [], "brief": "...", "done_when": "..."}}]}}"""


class PlanError(RuntimeError):
    pass


def planner_input(request: str, digest: str, test_command: str | None,
                  questions: list[str], answers: list[str], problems: list[str] | None = None) -> str:
    parts = [f"Request:\n{request.strip()}"]
    if test_command:
        parts.append(f"The repo's test command: {test_command}")
    if questions:
        qa = "\n".join(f"Q: {q}\nA: {(answers[i] if i < len(answers) else '').strip() or '(no answer)'}"
                       for i, q in enumerate(questions))
        parts.append(f"Your questions and the user's answers:\n{qa}\nDo not ask more questions; make the plan.")
    if problems:
        parts.append("Your previous plan had these problems; fix them:\n- " + "\n- ".join(problems))
    parts.append(f"Repository:\n{digest}")
    return "\n\n".join(parts)


def parse(answer: str) -> dict:
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        raise PlanError("The planner didn't answer with JSON.")
    try:
        data = json.loads(answer[start:end + 1])
    except ValueError:
        raise PlanError("The planner's JSON couldn't be read.") from None
    if not isinstance(data, dict):
        raise PlanError("The planner's answer isn't a JSON object.")
    return data


def questions_of(data: dict) -> list[str]:
    qs = data.get("questions")
    if isinstance(qs, list) and not data.get("steps"):
        return [str(q).strip() for q in qs if str(q).strip()][:MAX_QUESTIONS]
    return []


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().strip("/") for v in value or [] if str(v).strip()] if isinstance(value, list) else []


def steps_of(data: dict) -> list[PlanStep]:
    raw = data.get("steps")
    if not isinstance(raw, list) or not raw:
        raise PlanError("The planner's answer has no steps.")
    steps = []
    for i, s in enumerate(raw, start=1):
        if not isinstance(s, dict):
            continue
        kind = str(s.get("type") or "build").strip().lower()
        diff = str(s.get("difficulty") or "medium").strip().lower()
        steps.append(PlanStep(
            id=str(s.get("id") or f"s{i}").strip() or f"s{i}",
            title=str(s.get("title") or "").strip(),
            type=kind if kind in STEP_TYPES else "build",
            difficulty=diff if diff in DIFFICULTIES else "medium",
            files=_str_list(s.get("files")),
            depends_on=_str_list(s.get("depends_on")),
            brief=str(s.get("brief") or "").strip(),
            done_when=str(s.get("done_when") or "").strip(),
        ))
    return steps


# ---------- checking and fixing ----------

def _waves(steps: list[PlanStep]) -> list[list[str]] | None:
    """Topological levels; None if the steps depend on each other in a circle."""
    deps = {s.id: set(s.depends_on) for s in steps}
    done: set[str] = set()
    waves = []
    while len(done) < len(steps):
        wave = [s.id for s in steps if s.id not in done and deps[s.id] <= done]
        if not wave:
            return None
        waves.append(wave)
        done |= set(wave)
    return waves


def _needs(steps: list[PlanStep]) -> dict[str, set[str]]:
    """Every step a step waits for, directly or not."""
    by_id = {s.id: s for s in steps}
    memo: dict[str, set[str]] = {}

    def walk(sid: str, seen: frozenset) -> set[str]:
        if sid in memo:
            return memo[sid]
        out: set[str] = set()
        for d in by_id[sid].depends_on:
            if d in by_id and d not in seen:
                out |= {d} | walk(d, seen | {d})
        memo[sid] = out
        return out
    return {s.id: walk(s.id, frozenset({s.id})) for s in steps}


def _overlap(a: list[str], b: list[str]) -> str | None:
    """A file/folder both steps touch ("backend" overlaps "backend/auth.py")."""
    for x in a:
        for y in b:
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return x if len(x) >= len(y) else y
    return None


def check(steps: list[PlanStep]) -> tuple[list[PlanStep], list[str], list[str]]:
    """(fixed steps, notes for the user, problems that need a new plan)."""
    notes, problems = [], []
    work = [s for s in steps if s.type != "review"]
    reviews = [s for s in steps if s.type == "review"]

    # unique ids
    seen: set[str] = set()
    for i, s in enumerate(work, start=1):
        if s.id in seen or not re.fullmatch(r"[\w-]{1,20}", s.id):
            s.id = f"s{i}"
            while s.id in seen:
                s.id += "x"
        seen.add(s.id)

    if not work:
        problems.append("There are no work steps.")
    if len(work) > MAX_WORK_STEPS:
        problems.append(f"Too many steps ({len(work)}); use at most {MAX_WORK_STEPS}.")
    for s in work:
        missing = [name for name, value in (("title", s.title), ("brief", s.brief), ("done_when", s.done_when)) if not value]
        if missing:
            problems.append(f"Step {s.id} is missing: {', '.join(missing)}.")
        dropped = [d for d in s.depends_on if d not in seen or d == s.id]
        if dropped:
            s.depends_on = [d for d in s.depends_on if d not in dropped]
    if _waves(work) is None:
        problems.append("Some steps wait for each other in a circle.")
        return work, notes, problems

    # parallel steps never share files: the later one waits
    for _ in range(len(work) * len(work)):
        needs = _needs(work)
        clash = next(((a, b, f) for i, a in enumerate(work) for b in work[i + 1:]
                      if a.id not in needs[b.id] and b.id not in needs[a.id]
                      and (f := _overlap(a.files, b.files))), None)
        if not clash:
            break
        a, b, f = clash
        b.depends_on.append(a.id)
        notes.append(f"'{b.title}' now waits for '{a.title}': both change {f}.")

    # one review at the end, depending on every work step
    review = reviews[0] if reviews else None
    if review is None:
        review = PlanStep(id="review", title="Review all changes", type="review", difficulty="medium",
                          brief="Read the full set of changes from the other steps. Look for bugs, missing pieces "
                                "and parts that don't fit together, and fix small problems.",
                          done_when="The changes make sense together and the tests pass.")
        notes.append("Added a final review step, done by a different agent from the one that wrote the code.")
    review.id = "review" if "review" not in seen else "review-final"
    review.depends_on = [s.id for s in work]
    review.files = sorted({f for s in work for f in s.files})
    return [*work, review], notes, problems


def build_plan(steps: list[PlanStep], made_by: str | None = None, extra_notes: list[str] | None = None) -> BrainPlan:
    fixed, notes, problems = check(steps)
    waves = _waves(fixed) or []
    work = [s for s in fixed if s.type != "review"]
    return BrainPlan(steps=fixed, waves=waves, notes=[*(extra_notes or []), *notes], problems=problems,
                     single=len(work) == 1 and not problems, made_by=made_by)

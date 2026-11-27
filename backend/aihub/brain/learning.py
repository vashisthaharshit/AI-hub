"""Learning from history: which model sizes actually pass which kinds of steps.

Every attempt at a Brain step is logged (step type, difficulty, agent, model,
model size, mode, passed?, tokens, cost). The router reads the totals:

  - a SMALLER model size has passed this kind of step often enough
    (>= MIN_SAMPLES tries, >= DOWN_RATE passed) -> use the smaller size (cheaper)
  - the planned size fails this kind of step too often
    (>= MIN_SAMPLES tries, < UP_RATE passed)   -> go one size up (fewer retries)

Best mode never steps down. Everything stays explainable: the step's reason
says what was learned ("learned: small models passed 9 of 10 easy test steps").
Stats are shared across workspaces (they're about the agents, not the repo).
"""

from aihub.db import Database
from aihub.models import BrainLearning, StepStat

MIN_SAMPLES = 5
DOWN_RATE = 0.85
UP_RATE = 0.6
TIERS = ("small", "medium", "strong")

Stats = dict[tuple[str, str, str], tuple[int, int]]   # (type, difficulty, tier) -> (passed, total)


def load(db: Database) -> Stats:
    return {(r["step_type"], r["difficulty"], r["tier"]): (int(r["passed"] or 0), int(r["total"]))
            for r in db.step_stats()}


def adjust(level: int, step_type: str, difficulty: str, mode: str, stats: Stats | None) -> tuple[int, str | None]:
    """(new model size level 0-2, reason or None)."""
    if not stats:
        return level, None
    if level > 0 and mode != "best":
        passed, total = stats.get((step_type, difficulty, TIERS[level - 1]), (0, 0))
        if total >= MIN_SAMPLES and passed / total >= DOWN_RATE:
            return level - 1, (f"learned: {TIERS[level - 1]} models passed {passed} of {total} "
                               f"{difficulty} {step_type} steps")
    passed, total = stats.get((step_type, difficulty, TIERS[level]), (0, 0))
    if level < 2 and total >= MIN_SAMPLES and passed / total < UP_RATE:
        return level + 1, (f"learned: {TIERS[level]} models passed only {passed} of {total} "
                           f"{difficulty} {step_type} steps")
    return level, None


def record(db: Database, *, workspace_id: str | None, task_id: str, step_type: str, difficulty: str,
           agent_id: str, model: str | None, tier: str, mode: str, passed: bool, attempt: int,
           input_tokens: int | None = None, output_tokens: int | None = None, cost_usd: float | None = None,
           duration_ms: int | None = None) -> None:
    db.log_step(workspace_id=workspace_id, task_id=task_id, step_type=step_type, difficulty=difficulty,
                agent_id=agent_id, model=model, tier=tier, mode=mode, passed=int(passed), attempt=attempt,
                input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd, duration_ms=duration_ms)


def count_override(db: Database, kind: str) -> None:
    """Triage overrides ("small_to_plan", "big_to_direct"): shown to the user, a hint the rules need tuning."""
    data = db.get_setting("brain_overrides", {}) or {}
    data[kind] = int(data.get(kind, 0)) + 1
    db.set_setting("brain_overrides", data)


def summary(db: Database) -> BrainLearning:
    stats = load(db)
    rows = [StepStat(step_type=t, difficulty=d, tier=tier, passed=p, total=n) for (t, d, tier), (p, n) in sorted(stats.items())]
    adjustments = []
    for step_type, difficulty in sorted({(t, d) for t, d, _ in stats}):
        for level in range(3):
            new, why = adjust(level, step_type, difficulty, "balanced", stats)
            if why and new != level:
                adjustments.append(f"{difficulty} {step_type} steps: {TIERS[level]} → {TIERS[new]} model ({why})")
    return BrainLearning(stats=rows, adjustments=adjustments,
                         triage_overrides=db.get_setting("brain_overrides", {}) or {})

"""Brain step 2: the router picks an agent, model and effort for each step."""

from aihub.brain import planner, router
from aihub.models import (
    AccountInfo, AgentStatus, BrainPlan, ModelOption, PlanStep, StepAssignment, UsageWindow,
)

EFFORTS = ["low", "medium", "high", "xhigh"]


def claude(used=None, hidden=()):
    return AgentStatus(
        id="claude-code", name="Claude Code", state="ready", hidden_models=list(hidden),
        models=[ModelOption(id="haiku", label="Haiku", efforts=EFFORTS), ModelOption(id="sonnet", label="Sonnet", efforts=EFFORTS),
                ModelOption(id="opus", label="Opus", efforts=EFFORTS)],
        account=AccountInfo(usage=[UsageWindow(label="5-hour limit", used_percent=used)]) if used is not None else None,
    )


def codex(used=None, state="ready"):
    return AgentStatus(
        id="codex", name="Codex", state=state,
        models=[ModelOption(id="gpt-5.1-codex-mini", label="GPT-5.1-Codex-Mini", efforts=EFFORTS),
                ModelOption(id="gpt-5.1-codex", label="GPT-5.1-Codex", efforts=EFFORTS),
                ModelOption(id="gpt-5.1-codex-max", label="GPT-5.1-Codex-Max", efforts=EFFORTS)],
        account=AccountInfo(usage=[UsageWindow(label="Weekly limit", used_percent=used)]) if used is not None else None,
    )


def step(sid, difficulty="medium", files=(), deps=(), kind="build"):
    return PlanStep(id=sid, title=sid, type=kind, difficulty=difficulty, files=list(files), depends_on=list(deps),
                    brief="b", done_when="d")


def plan(*steps) -> BrainPlan:
    return planner.build_plan(list(steps))


def models(p: BrainPlan) -> dict[str, str]:
    return {s.id: s.assignment.model for s in p.steps}


def test_tiers_by_name():
    assert [router.tier_of(m) for m in claude().models] == ["small", "medium", "strong"]
    assert [router.tier_of(m) for m in codex().models] == ["small", "medium", "strong"]


def test_mode_table():
    def pick(mode):
        p = plan(step("e", "easy", ["a"]), step("m", "medium", ["b"]), step("h", "hard", ["c"]))
        router.route(p, [claude()], mode)
        return [models(p)[x] for x in ("e", "m", "h")]
    assert pick("cheapest") == ["haiku", "haiku", "sonnet"]
    assert pick("balanced") == ["haiku", "sonnet", "opus"]
    assert pick("best") == ["sonnet", "opus", "opus"]


def test_effort():
    p = plan(step("e", "easy", ["a"]), step("m", "medium", ["b"]), step("h", "hard", ["c"]))
    router.route(p, [claude()], "balanced")
    by = {s.id: s.assignment.effort for s in p.steps}
    assert (by["e"], by["m"], by["h"]) == ("low", "medium", "high")   # always explicit (Codex defaults to low)
    router.route(p, [claude()], "best")
    assert {s.id: s.assignment.effort for s in p.steps}["h"] == "xhigh"


def test_review_is_never_small():
    p = plan(step("e", "easy", ["a"]))
    router.route(p, [claude()], "cheapest")
    review = p.steps[-1]
    assert review.type == "review" and review.assignment.model == "sonnet"


def test_parallel_steps_are_spread_across_agents():
    p = plan(step("a", files=["x"]), step("b", files=["y"]))
    router.route(p, [claude(), codex()], "balanced")
    a, b, review = p.steps
    assert {a.assignment.agent_id, b.assignment.agent_id} == {"claude-code", "codex"}
    assert "spread out" in b.assignment.reason


def test_review_by_a_different_agent():
    p = plan(step("a", files=["x"]), step("b", files=["x"]))   # b waits for a: one wave each
    router.route(p, [claude(), codex()], "balanced")
    a, b, review = p.steps
    assert a.assignment.agent_id == b.assignment.agent_id == "claude-code"
    assert review.assignment.agent_id == "codex" and "different agent" in review.assignment.reason


def test_agent_near_its_limit_is_avoided():
    p = plan(step("a", files=["x"]))
    router.route(p, [claude(used=95), codex(used=10)], "balanced")
    assert p.steps[0].assignment.agent_id == "codex"
    assert "near its usage limit" in p.steps[0].assignment.reason


def test_used_up_agent_only_when_nothing_else():
    p = plan(step("a", files=["x"]))
    router.route(p, [claude(used=100)], "balanced")
    assert p.steps[0].assignment.agent_id == "claude-code" and "nothing else fits" in p.steps[0].assignment.reason


def test_hidden_models_and_missing_agents():
    p = plan(step("h", "hard", ["x"]))
    router.route(p, [claude(hidden=["opus"]), codex(state="not_installed")], "balanced")
    h = p.steps[0].assignment
    assert h.agent_id == "claude-code" and h.model == "sonnet" and "closest one" in h.reason
    assert router.route(plan(step("a")), [codex(state="not_signed_in")], "balanced")  # a note: no agent


def test_user_choice_wins_while_usable():
    p = plan(step("a", "easy", ["x"]))
    p.steps[0].assignment = StepAssignment(agent_id="codex", agent_name="Codex", model="gpt-5.1-codex-max",
                                           model_label="Max", reason="mine", manual=True)
    assert router.route(p, [claude(), codex()], "cheapest") == []
    assert p.steps[0].assignment.model == "gpt-5.1-codex-max"
    notes = router.route(p, [claude()], "cheapest")               # Codex is gone now
    assert notes and p.steps[0].assignment.agent_id == "claude-code" and not p.steps[0].assignment.manual


def opt(id_, label, description=None):
    return ModelOption(id=id_, label=label, description=description, efforts=EFFORTS)


# What the agents really report (from scripts/probe_models.py): Codex's names don't
# say their size, the descriptions do; both lists include older generations.
REAL_CODEX = [opt("gpt-6.2-sol", "GPT-6.2-Sol", "Fast, go-to model for coding and everyday work."),
              opt("gpt-6.2-astra", "GPT-6.2-Astra", "Our most capable model for complex coding and professional work."),
              opt("gpt-6.1-terra", "GPT-6.1 Terra", "Efficient model that excels at visual understanding and computer use."),
              opt("gpt-6-astra", "GPT-6-Astra", "Previous generation model for complex coding and professional work."),
              opt("gpt-6-luna", "GPT-6-Luna", "Fast and affordable model for easier tasks."),
              opt("gpt-5.6-luna", "GPT-5.6-Luna", "Legacy fast model for simpler tasks.")]
REAL_CLAUDE = [opt("default", "Default (recommended)", "Opus 5.5 · Best for everyday, complex tasks"),
               opt("opus", "Opus 5.5", "For complex work and everyday tasks"),
               opt("sonnet", "Sonnet 5.5", "Most efficient for simpler tasks"),
               opt("fable", "Fable 5.2", "For your toughest challenges"),
               opt("haiku", "Haiku 5.5", "Fastest for quick answers"),
               opt("claude-haiku-4-5-20251001", "Haiku 4.5", "Fastest for quick answers"),
               opt("claude-opus-4-8", "Opus 4.8", "Best for everyday, complex tasks")]


def pick(models, mode, difficulty):
    agent = AgentStatus(id="a", name="A", state="ready", models=models)
    p = plan(step("s", difficulty, ["x"]))
    router.route(p, [agent], mode)
    return p.steps[0].assignment.model


def test_real_codex_models_by_description():
    assert [pick(REAL_CODEX, "balanced", d) for d in ("easy", "medium", "hard")] == ["gpt-6-luna", "gpt-6.2-sol", "gpt-6.2-astra"]
    assert router.size_of(REAL_CODEX[3]) == 2 and router.is_old(REAL_CODEX[3])     # previous generation: never picked


def test_real_claude_models_newest_and_top_only_when_wanted():
    assert [pick(REAL_CLAUDE, "balanced", d) for d in ("easy", "medium", "hard")] == ["haiku", "sonnet", "opus"]
    assert pick(REAL_CLAUDE, "best", "hard") == "fable"                               # the top model: Best + hard only
    assert router.size_of(REAL_CLAUDE[0]) == 2                                        # "default" is Opus
    up = router.step_up(StepAssignment(agent_id="a", agent_name="A", model="sonnet", model_label="", reason="",
                                       manual=False), [AgentStatus(id="a", name="A", state="ready", models=REAL_CLAUDE)])
    assert up.model == "opus"

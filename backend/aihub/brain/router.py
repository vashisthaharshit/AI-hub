"""The router: which agent, model and effort does each step of a plan get?

All code, no AI: it's free, instant and explainable ("Sonnet: medium build
step, Balanced mode"). In order:

1. Hard rules (never broken)
   - only agents that are installed and signed in
   - never a model hidden after a credits failure
   - an agent at (or very near) a usage limit is only used if nothing else is left
2. The mode (Cheapest / Balanced / Best) and the step's difficulty pick a
   model size; each agent's models are sorted into small / medium / strong
   by name (Haiku / Sonnet / Opus; ...-mini / ... / ...-max)
       difficulty:   easy    medium   hard
       Cheapest      small   small    medium
       Balanced      small   medium   strong
       Best          medium  strong   strong (higher effort)
   Effort follows the same idea: easy steps low, hard steps high.
   A review is never done by a small model.
3. Spread the load
   - agents with more usage left are preferred
   - steps that run at the same time are spread across agents
   - the review goes to a different agent from the one(s) that wrote the code
4. The user's own choice for a step always wins, while that agent and model
   can still be used.

5. Learning (brain/learning.py): when history shows a smaller model passes a
   kind of step reliably, use it; when the planned size keeps failing, go up.
6. step_up(): after a failed check, the next stronger model (same agent if it
   has one, else the other agent) for one retry.
"""

import re

from aihub.brain import learning
from aihub.models import AgentStatus, BrainPlan, ModelOption, PlanStep, StepAssignment

TIERS = ("small", "medium", "strong")

# How big a model is: from its name first, then its description. Agents don't
# say "small / large": Claude names its sizes (Haiku < Sonnet < Opus < Fable),
# Codex describes them ("fast and affordable ... easier tasks", "most capable
# ... complex coding"). Sizes: 0 small, 1 medium, 2 strong, 3 top (the very
# biggest: only for Best mode's hard steps and step-ups).
NAME_SIZES = (
    (3, ("fable",)),
    (2, ("opus", "-max", " max", "-pro", " pro", "ultra")),
    (1, ("sonnet",)),
    (0, ("haiku", "mini", "nano", "flash", "lite", "small", "spark")),
)
DESCRIPTION_SIZES = (
    (3, ("toughest", "hardest", "longest-running")),
    (2, ("most capable", "complex coding", "complex work", "complex task", "deep reasoning", "most intelligent",
         "most powerful")),
    (0, ("affordable", "easier task", "simpler task", "simple task", "quick answer", "lightweight", "cheapest",
         "low-latency")),
)
OLD_WORDS = ("legacy", "previous generation", "deprecated", "older model", "retire")
SPECIAL_WORDS = ("visual", "computer use", "vision", "image", "audio", "realtime", "voice")
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
MODE_SHIFT = {"cheapest": -1, "balanced": 0, "best": 1}
DIFFICULTY_LEVEL = {"easy": 0, "medium": 1, "hard": 2}
MODE_LABEL = {"cheapest": "Cheapest", "balanced": "Balanced", "best": "Best"}
NEAR_LIMIT = 90.0   # % of a usage window: avoid this agent if another one fits
AT_LIMIT = 98.0     # treat as used up


def size_of(model: ModelOption) -> int:
    """0 small, 1 medium, 2 strong, 3 top."""
    description = (model.description or "").lower()
    head = description.split(" · ")[0] if " · " in description else ""   # "Opus 5.5 · Best for ..." (an alias)
    name = f" {model.id} {model.label} {head} ".lower()
    for size, words in NAME_SIZES:
        if any(w in name for w in words):
            return size
    for size, words in DESCRIPTION_SIZES:
        if any(w in description for w in words):
            return size
    return 1


def tier_of(model: ModelOption) -> str:
    return TIERS[min(size_of(model), 2)]


def _version(model: ModelOption) -> float:
    """5.5 from "Opus 5.5", 6.2 from "GPT-6.2-Sol", 6.0 from "GPT-6-Luna" (0 if none)."""
    for text in (model.label, model.description or "", model.id):
        m = re.search(r"(\d+)(?:\.(\d+))?", text)
        if m:
            return float(f"{m.group(1)}.{m.group(2) or 0}")
    return 0.0


def is_old(model: ModelOption) -> bool:
    text = f"{model.description or ''} {model.note or ''}".lower()
    return any(w in text for w in OLD_WORDS)


def rank(model: ModelOption, want_top: bool = False) -> tuple:
    """Among models of the same size, smaller is better: current over legacy, general
    over specialized (vision / computer use), the top model only when wanted, the
    newest version, a named model over the "default" alias."""
    special = any(w in (model.description or "").lower() for w in SPECIAL_WORDS)
    size = size_of(model)
    top_mismatch = size >= 2 and (size == 3) != want_top
    return (is_old(model), special, top_mismatch, -_version(model), model.id == "default")


def best_of(models: list[ModelOption], want_top: bool = False) -> ModelOption | None:
    ordered = sorted(enumerate(models), key=lambda im: (*rank(im[1], want_top), im[0]))
    return ordered[0][1] if ordered else None


def target_tier(step: PlanStep, mode: str) -> int:
    if mode == "cheapest" and step.difficulty != "easy":
        level = DIFFICULTY_LEVEL[step.difficulty] - 1      # small, small, medium
    else:
        level = DIFFICULTY_LEVEL[step.difficulty] + max(MODE_SHIFT.get(mode, 0), 0)
    if step.type == "review":
        level = max(level, 1)
    return max(0, min(level, 2))


def pick_effort(model: ModelOption | None, step: PlanStep, mode: str) -> str | None:
    """Low for easy steps, high for hard ones, shifted by the mode; None = the model's default."""
    efforts = [e for e in (model.efforts if model else []) if e in EFFORT_ORDER]
    if not efforts:
        return None
    target = EFFORT_ORDER.index("low") + DIFFICULTY_LEVEL[step.difficulty] + MODE_SHIFT.get(mode, 0)
    # Always explicit: some agents default to low effort (Codex), too little for a medium step.
    return min(efforts, key=lambda e: (abs(EFFORT_ORDER.index(e) - target), EFFORT_ORDER.index(e)))


def usage_used(agent: AgentStatus) -> tuple[float, str | None]:
    """Highest % used of any usage window, and that window's label."""
    best, label = 0.0, None
    for w in (agent.account.usage if agent.account else []):
        used = 100.0 if (w.status or "").lower() == "limit reached" else (w.used_percent or 0.0)
        if used > best:
            best, label = used, w.label
    return best, label


def usable_models(agent: AgentStatus) -> list[ModelOption]:
    hidden = set(agent.hidden_models)
    return [m for m in agent.models if m.id not in hidden]


def _model_for(agent: AgentStatus, tier: int, mode: str, want_top: bool = False) -> tuple[ModelOption | None, int]:
    """The agent's model closest to the wanted size, and how far off it is.

    Agents that only offer their default model (no list) count as medium."""
    models = usable_models(agent)
    if not models:
        return None, abs(tier - 1)
    by_tier: dict[int, list[ModelOption]] = {}
    for m in models:
        by_tier.setdefault(TIERS.index(tier_of(m)), []).append(m)
    # nearest size; on a tie, go smaller in Cheapest mode and bigger otherwise
    order = sorted(by_tier, key=lambda t: (abs(t - tier), t if mode == "cheapest" else -t))
    chosen = order[0]
    return best_of(by_tier[chosen], want_top), abs(chosen - tier)


def usable_agents(agents: list[AgentStatus]) -> list[AgentStatus]:
    return [a for a in agents if a.state in ("ready", "installed")]


def _valid(assignment: StepAssignment, agents: dict[str, AgentStatus]) -> bool:
    agent = agents.get(assignment.agent_id)
    if not agent:
        return False
    return assignment.model is None or assignment.model in {m.id for m in usable_models(agent)} or not agent.models


def route(plan: BrainPlan, agents: list[AgentStatus], mode: str, stats: learning.Stats | None = None) -> list[str]:
    """Give every step an assignment (in place). Returns notes for the user."""
    notes: list[str] = []
    usable = {a.id: a for a in usable_agents(agents)}
    if not usable:
        for s in plan.steps:
            if not (s.assignment and s.assignment.manual):
                s.assignment = None
        return ["No agent is ready: install and sign in to Claude Code or Codex, then pick again."]

    wave_of = {sid: i for i, wave in enumerate(plan.waves) for sid in wave}
    per_wave: dict[int, dict[str, int]] = {}
    writers: dict[str, str] = {}   # step id -> agent that does it

    for step in sorted(plan.steps, key=lambda s: wave_of.get(s.id, 99)):
        wave_load = per_wave.setdefault(wave_of.get(step.id, 99), {})
        if step.assignment and step.assignment.manual:
            if _valid(step.assignment, usable):
                wave_load[step.assignment.agent_id] = wave_load.get(step.assignment.agent_id, 0) + 1
                writers[step.id] = step.assignment.agent_id
                continue
            notes.append(f"'{step.title}': your choice ({step.assignment.agent_name}) can't be used now, so AI-Hub picked again.")

        tier, learned = learning.adjust(target_tier(step, mode), step.type, step.difficulty, mode, stats)
        if step.type == "review":
            tier = max(tier, 1)
        wrote: dict[str, int] = {}     # for a review: agent -> how many of the steps it wrote
        if step.type == "review":
            for d in step.depends_on:
                if d in writers:
                    wrote[writers[d]] = wrote.get(writers[d], 0) + 1
        written = sum(wrote.values()) or 1
        options = []
        for agent in usable.values():
            model, gap = _model_for(agent, tier, mode, want_top=mode == "best" and step.difficulty == "hard")
            used, window = usage_used(agent)
            score = gap * 10.0
            score += 1000.0 if used >= AT_LIMIT else 25.0 if used >= NEAR_LIMIT else used / 25.0
            score += wave_load.get(agent.id, 0) * 3.0     # spread steps that run together
            score += 15.0 * wrote.get(agent.id, 0) / written   # a fresh pair of eyes for the review
            score += 0.0 if agent.id == "claude-code" else 0.5  # tiny, stable tie-break
            options.append((score, agent, model, gap, used, window))
        options.sort(key=lambda o: o[0])
        _, agent, model, gap, used, window = options[0]

        effort = pick_effort(model, step, mode)
        size = TIERS[tier]
        why = [f"{step.difficulty} {step.type} step, {MODE_LABEL.get(mode, mode)} mode"]
        if learned:
            why.append(learned)
        if gap:
            why.append(f"no {size} model here, closest one")
        if used >= NEAR_LIMIT:
            why.append(f"{agent.name} is at {used:.0f}% of its {window or 'usage limit'}, but nothing else fits")
        else:
            busy = [o for o in options[1:] if o[4] >= NEAR_LIMIT]
            if busy:
                why.append(f"{busy[0][1].name} is near its usage limit")
        if wave_load and agent.id not in wave_load and len(usable) > 1:
            why.append("spread out to run at the same time")
        if step.type == "review" and wrote:
            if agent.id not in wrote:
                why.append("a different agent from the one that wrote the code")
            elif len(usable) == 1:
                why.append("only one agent is available, so the same one reviews")
            else:
                why.append("both agents wrote code; this one wrote less of it")
        step.assignment = StepAssignment(
            agent_id=agent.id, agent_name=agent.name,
            model=model.id if model else None, model_label=model.label if model else (agent.default_label or "Default model"),
            effort=effort, reason=f"{model.label if model else 'Default model'}: " + "; ".join(why), manual=False,
        )
        wave_load[agent.id] = wave_load.get(agent.id, 0) + 1
        writers[step.id] = agent.id
    return notes


def step_up(current: StepAssignment, agents: list[AgentStatus]) -> StepAssignment | None:
    """A stronger choice for one retry after a failed check, or None if there's none.

    Same agent, next size up (it keeps its session and knows what it tried),
    e.g. Sonnet -> Opus -> Fable, or Sol -> Astra. Already the biggest: the
    other agent's strong model (a fresh pair of eyes, with a handoff). Else
    the same model with more effort.
    """
    usable = {a.id: a for a in usable_agents(agents)}
    agent = usable.get(current.agent_id)
    models = usable_models(agent) if agent else []
    now = next((m for m in models if m.id == current.model), None)
    size = size_of(now) if now else 1
    bigger = [m for m in models if size_of(m) > size and not is_old(m)]
    if bigger:
        next_size = min(size_of(m) for m in bigger)
        model = best_of([m for m in bigger if size_of(m) == next_size], want_top=next_size == 3)
        return _assignment(agent, model, f"retry with a stronger model ({model.label})")
    others = [a for a in usable.values() if a.id != current.agent_id]
    for other in sorted(others, key=lambda a: usage_used(a)[0]):
        pool = [m for m in usable_models(other) if not is_old(m)] or usable_models(other)
        if not pool and not other.models:
            return StepAssignment(agent_id=other.id, agent_name=other.name, model=None,
                                  model_label=other.default_label or "Default model", effort=None,
                                  reason=f"retry with {other.name} for a fresh pair of eyes", manual=False)
        if pool:
            want = 3 if size >= 3 else 2
            candidates = [m for m in pool if size_of(m) == want] or [m for m in pool if size_of(m) >= 2] or pool
            model = best_of(candidates, want_top=want == 3)
            return _assignment(other, model, f"retry with {other.name} ({model.label}) for a fresh pair of eyes")
    if now and current.effort not in ("high", "xhigh", "max") and "high" in now.efforts:
        return _assignment(agent, now, f"retry with more effort ({now.label}, high)", effort="high")
    return None


def _assignment(agent: AgentStatus, model: ModelOption, why: str, effort: str | None = None) -> StepAssignment:
    if effort is None:
        levels = [e for e in model.efforts if e in EFFORT_ORDER]
        effort = "high" if "high" in levels else (max(levels, key=EFFORT_ORDER.index) if levels else None)
    return StepAssignment(agent_id=agent.id, agent_name=agent.name, model=model.id, model_label=model.label,
                          effort=effort, reason=why, manual=False)


def strongest(agents: list[AgentStatus], avoid: str | None = None) -> StepAssignment | None:
    """For fix-up work (merge conflicts, failing tests after combining): a strong
    current model (Opus, Astra) of the agent with the most usage left."""
    usable = sorted(usable_agents(agents), key=lambda a: (a.id == avoid, usage_used(a)[0] >= NEAR_LIMIT, usage_used(a)[0]))
    for agent in usable:
        pool = [m for m in usable_models(agent) if not is_old(m)] or usable_models(agent)
        if pool:
            strong = [m for m in pool if size_of(m) == 2] or [m for m in pool if size_of(m) == 3] or pool
            model = best_of(strong)
            return _assignment(agent, model, f"fix-up by a strong model with usage left ({model.label})")
        return StepAssignment(agent_id=agent.id, agent_name=agent.name, model=None,
                              model_label=agent.default_label or "Default model", effort=None,
                              reason="fix-up by the agent with the most usage left", manual=False)
    return None

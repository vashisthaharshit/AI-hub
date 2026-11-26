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

Later build steps: step up to a stronger model when a check fails, and learn
from history which choices actually work.
"""

from aihub.models import AgentStatus, BrainPlan, ModelOption, PlanStep, StepAssignment

TIERS = ("small", "medium", "strong")
SMALL_WORDS = ("haiku", "mini", "nano", "flash", "lite", "small", "spark")
STRONG_WORDS = ("opus", "max", "pro", "ultra")
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
MODE_SHIFT = {"cheapest": -1, "balanced": 0, "best": 1}
DIFFICULTY_LEVEL = {"easy": 0, "medium": 1, "hard": 2}
MODE_LABEL = {"cheapest": "Cheapest", "balanced": "Balanced", "best": "Best"}
NEAR_LIMIT = 90.0   # % of a usage window: avoid this agent if another one fits
AT_LIMIT = 98.0     # treat as used up


def tier_of(model: ModelOption) -> str:
    text = f"{model.id} {model.label}".lower()
    if any(w in text for w in SMALL_WORDS):
        return "small"
    if any(w in text for w in STRONG_WORDS):
        return "strong"
    return "medium"


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
    if step.difficulty == "medium" and mode == "balanced":
        return None                                           # the model's own default is right here
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


def _model_for(agent: AgentStatus, tier: int, mode: str) -> tuple[ModelOption | None, int]:
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
    return by_tier[chosen][0], abs(chosen - tier)


def usable_agents(agents: list[AgentStatus]) -> list[AgentStatus]:
    return [a for a in agents if a.state in ("ready", "installed")]


def _valid(assignment: StepAssignment, agents: dict[str, AgentStatus]) -> bool:
    agent = agents.get(assignment.agent_id)
    if not agent:
        return False
    return assignment.model is None or assignment.model in {m.id for m in usable_models(agent)} or not agent.models


def route(plan: BrainPlan, agents: list[AgentStatus], mode: str) -> list[str]:
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

        tier = target_tier(step, mode)
        wrote: dict[str, int] = {}     # for a review: agent -> how many of the steps it wrote
        if step.type == "review":
            for d in step.depends_on:
                if d in writers:
                    wrote[writers[d]] = wrote.get(writers[d], 0) + 1
        written = sum(wrote.values()) or 1
        options = []
        for agent in usable.values():
            model, gap = _model_for(agent, tier, mode)
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

"""Triage: is a task big enough to plan, or should one agent just do it?

Planning has a cost (a planner call, and every extra agent explores the repo
again), so small tasks skip it. Three layers:

  1. rules    free and instant; handle the clear cases
  2. AI check only when the rules aren't sure: one tiny call to the cheapest
              model ("small or big?")
  3. the user can always override ("Plan it instead" / "Just do it")

When still unsure (no AI available, or it fails), the default is to run
directly: cheaper, and the user can switch to a plan with one click.
"""

import json
import re

BIG_WORDS = ("feature", "implement", "build", "refactor", "migrate", "migration", "redesign", "rewrite",
             "integrate", "integration", "overhaul", "architecture", "end-to-end", "end to end",
             "from scratch", "whole", "entire", "system", "authentication", "dashboard", "set up", "setup")
SMALL_WORDS = ("typo", "rename", "bump", "small", "tiny", "quick", "tweak", "one line", "one-line",
               "comment", "log message", "wording", "spelling", "color", "colour", "minor")
FIX_WORDS = ("fix", "change", "update", "remove", "delete")
AREAS = {
    "backend": ("backend", "server", "api", "endpoint", "route"),
    "frontend": ("frontend", "ui", "page", "screen", "component", "button", "form", "css"),
    "tests": ("test", "tests", "testing", "pytest", "unit test"),
    "database": ("database", "db", "schema", "table", "sql"),
    "docs": ("readme", "docs", "documentation"),
    "deploy": ("docker", "deploy", "ci", "pipeline", "workflow"),
}
_FILE = re.compile(r"[\w./\\-]+\.(?:py|tsx?|jsx?|css|html|md|json|toml|ya?ml|sql|go|rs|java|rb|php|sh)\b")


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def rule_triage(request: str) -> tuple[str, list[str]]:
    """'small', 'big' or 'unsure', with the reasons (shown to the user)."""
    text = request.lower()
    score, reasons = 0, []

    areas = [name for name, words in AREAS.items() if any(_has(text, w) for w in words)]
    if len(areas) >= 2:
        score += len(areas) - 1
        reasons.append(f"touches several areas ({', '.join(areas)})")

    items = [ln for ln in request.splitlines() if re.match(r"\s*(?:[-*•]|\d+[.)])\s+\S", ln)]
    if len(items) >= 3:
        score += 2
        reasons.append(f"a list of {len(items)} things to do")

    big = [w for w in BIG_WORDS if _has(text, w)]
    if big:
        score += min(2 + len(big) - 1, 3)   # two big words ("refactor ... migrate") are a clear sign
        reasons.append(f"big-work words ({', '.join(big[:3])})")

    words = len(text.split())
    if words > 80:
        score += 1
        reasons.append("a long request")

    small = [w for w in SMALL_WORDS if _has(text, w)]
    if small:
        score -= 2
        reasons.append(f"small-change words ({', '.join(small[:3])})")
    files = set(_FILE.findall(request))
    if len(files) == 1 and not big:
        score -= 1
        reasons.append(f"about one file ({next(iter(files))})")
    if words <= 12 and not big and any(text.startswith(w) for w in FIX_WORDS):
        score -= 1
        reasons.append("a short fix")

    if score >= 3:
        return "big", reasons
    if score < 0 or (score == 0 and words <= 10):
        return "small", reasons or ["a short, single change"]
    return "unsure", reasons or ["not clear from the words alone"]


AI_INSTRUCTIONS = (
    "You decide whether a coding task should be split into a plan of several steps for different AI agents, "
    "or is small enough for one agent to just do. Small: a fix or change in one place, a few files, one area. "
    "Big: a feature touching several areas (e.g. backend + frontend + tests), a refactor, a migration. "
    'Answer with JSON only: {"size": "small" or "big", "reason": "one short sentence"}'
)


def parse_ai_answer(answer: str) -> tuple[str, str] | None:
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(answer[start:end + 1])
    except ValueError:
        return None
    size = str(data.get("size", "")).strip().lower() if isinstance(data, dict) else ""
    if size not in ("small", "big"):
        return None
    return size, str(data.get("reason") or "").strip()

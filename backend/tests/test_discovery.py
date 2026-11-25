"""Model + account discovery, effort flags, and hiding models that need credits.

The sample replies below have the same shape as what `scripts/probe_models.py`
printed on a real machine (account details removed).
"""

import asyncio
import os
import sys
import textwrap

import pytest

from aihub.agents.base import AgentAdapter, RunOptions
from aihub.agents.claude_code import ClaudeCodeAdapter, parse_initialize
from aihub.agents.codex import CodexAdapter, parse_discovery
from aihub.agents.registry import AgentRegistry
from aihub.models import Discovery, ModelOption
from aihub.runner import needs_credits

CLAUDE_INIT = {
    "models": [
        {"value": "default", "displayName": "Default (recommended)", "description": "Opus 5.5 · Best for everyday, complex tasks",
         "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        {"value": "opus", "displayName": "Opus 5.5", "description": "For complex work and everyday tasks",
         "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        {"value": "sonnet", "displayName": "Sonnet 5.5", "description": "Most efficient for simpler tasks",
         "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        {"value": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5", "description": "Fastest for quick answers",
         "supportsThinkingOff": True},
        {"value": "claude-opus-4-6", "displayName": "Opus 4.6", "description": "Best for everyday, complex tasks",
         "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "max"]},
    ],
    "account": {"subscriptionType": "Claude Pro", "apiProvider": "firstParty", "email": "x@example.com"},
}

CODEX_MODELS = [
    {"id": "gpt-6.2-sol", "model": "gpt-6.2-sol", "displayName": "GPT-6.2-Sol", "description": "Flagship",
     "hidden": False, "isDefault": True, "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "Fast"},
                                   {"reasoningEffort": "high", "description": "Deep"},
                                   {"reasoningEffort": "ultra", "description": "Deepest"}],
     "upgradeInfo": None},
    {"id": "gpt-5.6-sol", "model": "gpt-5.6-sol", "displayName": "GPT-5.6-Sol", "hidden": False, "isDefault": False,
     "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
     "upgradeInfo": {"migrationMarkdown": "GPT-5.6 Sol retires on December 23, 2026.\n\nSwitch to GPT-6.2 Sol."}},
    {"id": "internal", "model": "internal-review", "displayName": "Internal", "hidden": True},
]
CODEX_ACCOUNT = {"account": {"type": "chatgpt", "email": "x@example.com", "planType": "plus"}}
CODEX_LIMITS = {"rateLimits": {
    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1795620000},
    "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 1796000000},
    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
}}


# ---------- parsing ----------

def test_claude_initialize_is_parsed():
    d = parse_initialize(CLAUDE_INIT, fallback=[])
    assert [m.label for m in d.models] == ["Opus 5.5", "Sonnet 5.5", "Haiku 4.5", "Opus 4.6"]
    assert d.default_label == "Default (recommended) · Opus 5.5 · Best for everyday, complex tasks"
    assert d.models[0].id == "opus" and d.models[0].efforts[-1] == "max"
    assert d.models[2].efforts == []                          # Haiku 4.5: no effort control
    assert d.models[3].efforts == ["low", "medium", "high", "max"]
    assert d.account.plan == "Claude Pro"
    assert d.default_efforts == ["low", "medium", "high", "xhigh", "max"]  # effort works on "Default" too
    assert "email" not in d.account.model_dump()              # we never keep the email


def test_claude_empty_reply_falls_back():
    fallback = [ModelOption(id="opus", label="Opus")]
    d = parse_initialize({}, fallback)
    assert d.models == fallback and d.error


def test_codex_discovery_is_parsed():
    d = parse_discovery(CODEX_MODELS, CODEX_ACCOUNT, CODEX_LIMITS)
    assert [m.label for m in d.models] == ["GPT-6.2-Sol", "GPT-5.6-Sol"]  # hidden one skipped
    assert d.default_label == "Default · GPT-6.2-Sol"
    assert d.default_efforts == ["low", "high", "ultra"] and d.default_effort == "low"
    assert d.models[0].efforts == ["low", "high", "ultra"] and d.models[0].default_effort == "low"
    assert d.models[1].note == "GPT-5.6 Sol retires on December 23, 2026."
    acct = d.account
    assert acct.plan == "plus"
    assert [(u.label, u.used_percent) for u in acct.usage] == [("5-hour limit", 12), ("Weekly limit", 40)]
    assert acct.has_credits is False and acct.credits_balance == "0"


def test_codex_discovery_without_account_extras():
    d = parse_discovery(CODEX_MODELS[:1], {}, {})
    assert d.account.plan is None and d.account.usage == [] and d.account.has_credits is None


# ---------- effort flags ----------

def test_effort_flags():
    claude = ClaudeCodeAdapter().build_command("claude", RunOptions(model="opus", effort="xhigh"))
    assert claude[claude.index("--effort") + 1] == "xhigh"
    codex = CodexAdapter().build_command("codex", RunOptions(model="gpt-6.2-sol", effort="high", resume_session_id="t"))
    assert codex[codex.index("-c") + 1] == "model_reasoning_effort=high"
    assert codex.index("-c") < codex.index("resume")
    assert "--effort" not in ClaudeCodeAdapter().build_command("claude", RunOptions())


# ---------- credits detection ----------

@pytest.mark.parametrize("text, expected", [
    ("This model requires usage credits. Buy credits to continue.", True),
    ("Your credit balance is too low to access this model", True),
    ("API Error: insufficient credits", True),
    ("Invalid API key. Please run /login", False),
    ("I gave credit to the original author in the README", False),
    ("", False),
])
def test_needs_credits(text, expected):
    assert needs_credits(text) is expected


# ---------- registry: cache, refresh, test result, hidden models ----------

class CountingAdapter(AgentAdapter):
    id = "counting"
    name = "Counting"

    def __init__(self):
        self.discoveries = 0

    async def detect(self):
        return self.status("installed")

    async def test_connection(self):
        return self.status("ready")

    async def discover(self):
        self.discoveries += 1
        return Discovery(models=[ModelOption(id="a", label="A"), ModelOption(id="b", label="B")])

    async def run(self, prompt, workdir, options=None):
        yield  # pragma: no cover


def test_registry_caches_and_hides():
    async def scenario():
        adapter = CountingAdapter()
        reg = AgentRegistry([adapter])
        [s] = await reg.scan()
        [s] = await reg.scan()
        assert adapter.discoveries == 1                       # cached
        [s] = await reg.scan(refresh=True)
        assert adapter.discoveries == 2                       # Re-check asks again

        [s] = await reg.scan(hidden={"counting": {"b"}})
        assert [m.id for m in s.models] == ["a"] and s.hidden_models == ["b"]

        assert s.state == "installed"
        reg.remember_test(await adapter.test_connection())
        [s] = await reg.scan()
        assert s.state == "ready"                             # test result is remembered

    asyncio.run(scenario())


# ---------- the real discovery code against fake CLIs ----------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX scripts as fake CLIs")


def fake_cli(tmp_path, monkeypatch, name: str, body: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / name
    exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@posix_only
def test_claude_discover_end_to_end(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, "claude", f"""
        import json, sys
        INIT = {CLAUDE_INIT!r}
        for line in sys.stdin:
            msg = json.loads(line)
            if msg.get("type") == "control_request":
                print("not json noise", flush=True)
                print(json.dumps({{"type": "control_response", "response": {{
                    "subtype": "success", "request_id": msg["request_id"], "response": INIT}}}}), flush=True)
    """)
    d = asyncio.run(ClaudeCodeAdapter().discover())
    assert d.error is None and d.models[0].label == "Opus 5.5" and d.account.plan == "Claude Pro"


@posix_only
def test_codex_discover_end_to_end(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, "codex", f"""
        import json, sys
        MODELS, ACCOUNT, LIMITS = {CODEX_MODELS!r}, {CODEX_ACCOUNT!r}, {CODEX_LIMITS!r}
        def reply(i, result): print(json.dumps({{"id": i, "result": result}}), flush=True)
        for line in sys.stdin:
            msg = json.loads(line)
            m, i = msg.get("method"), msg.get("id")
            if m == "initialize": reply(i, {{"userAgent": "fake"}})
            elif m == "model/list":
                # two pages, to test pagination
                if (msg.get("params") or {{}}).get("cursor"): reply(i, {{"data": MODELS[1:], "nextCursor": None}})
                else:
                    print(json.dumps({{"method": "account/updated", "params": {{}}}}), flush=True)
                    reply(i, {{"data": MODELS[:1], "nextCursor": "page2"}})
            elif m == "account/read": reply(i, ACCOUNT)
            elif m == "account/rateLimits/read": reply(i, LIMITS)
    """)
    d = asyncio.run(CodexAdapter().discover())
    assert d.error is None
    assert [m.id for m in d.models] == ["gpt-6.2-sol", "gpt-5.6-sol"]
    assert d.account.plan == "plus" and d.account.usage[1].label == "Weekly limit"


@posix_only
def test_discover_failure_falls_back(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, "codex", "import sys; sys.exit(1)\n")
    d = asyncio.run(CodexAdapter().discover())
    assert d.models == [] and "Couldn't read models" in d.error


# ---------- Claude usage windows from rate_limit_event ----------

def test_rate_limit_event_becomes_usage():
    from aihub.agents.claude_code import parse_line
    import json as _json

    [ev] = parse_line(_json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "resetsAt": 1795620000, "rateLimitType": "five_hour", "isUsingOverage": False}}))
    assert ev.kind == "usage"
    assert ev.data["label"] == "5-hour limit" and ev.data["status"] == "ok"
    assert ev.data["used_percent"] is None and ev.data["resets_at"].startswith("2026-11-")

    [ev] = parse_line(_json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed_warning", "resetsAt": 1796000000000, "utilization": 0.85, "rateLimitType": "seven_day"}}))
    assert ev.data["label"] == "Weekly limit" and ev.data["status"] == "warning"
    assert ev.data["used_percent"] == 85.0                    # 0-1 fraction -> percent
    assert parse_line(_json.dumps({"type": "rate_limit_event", "rate_limit_info": {}})) == []


def test_registry_merges_live_usage():
    from aihub.models import UsageWindow

    async def scenario():
        adapter = CountingAdapter()
        reg = AgentRegistry([adapter])
        [s] = await reg.scan()
        assert s.account is None
        reg.update_usage("counting", UsageWindow(label="5-hour limit", status="ok"))
        reg.update_usage("counting", UsageWindow(label="5-hour limit", status="warning", used_percent=82))
        [s] = await reg.scan(refresh=True)                    # survives a Re-check
        assert [(u.label, u.status, u.used_percent) for u in s.account.usage] == [("5-hour limit", "warning", 82)]

    asyncio.run(scenario())

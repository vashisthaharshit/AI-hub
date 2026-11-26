"""Guide tab: the basic guide, the AI guide (with a fake LLM), and model choice."""

import asyncio
import json
from pathlib import Path

import pytest

from aihub.apps import guide as g
from aihub.db import Database
from aihub.models import (
    AgentStatus, AppCommand, AppInfo, AppManifest, ModelOption, SummarySettings,
)


def cmd(name, kind="normal", description=None):
    return AppCommand(name=name, title=name.title(), description=description or f"Does {name}",
                      kind=kind, source=f".claude/commands/{name}.md")


@pytest.fixture
def app(tmp_path) -> AppInfo:
    (tmp_path / "README.md").write_text('<p align="center"><img src="x.svg"></p>\n\n'
                                        "# Jobs\n\nFind jobs &amp; apply.\n", encoding="utf-8")
    (tmp_path / "SETUP.md").write_text("Put your CV in documents/.\n", encoding="utf-8")
    manifest = AppManifest(name="ai-job-search", summary="Find jobs and apply.", tier=1,
                           commands=[cmd("setup", "start", "Profile Onboarding"), cmd("apply"),
                                     cmd("scrape"), cmd("reset", "danger")])
    return AppInfo(id="a1", name="ai-job-search", path=str(tmp_path), created_at="t", updated_at="t",
                   manifest=manifest)


@pytest.fixture
def db(tmp_path, app):
    database = Database(tmp_path / "t.db")
    database.insert_app(app)
    yield database
    database.close()


GOOD = {
    "what_it_does": "Finds jobs for you and writes your applications.",
    "examples": [{"request": "Find new jobs for me", "command": "/scrape"},
                 {"request": "Apply to this job: <link>", "command": "apply"},
                 {"request": "Something", "command": "made-up"}],
    "steps": [{"title": "Set up your profile", "text": "Add your CV.", "command": "setup"}],
    "needs": ["Your CV"],
}


class FakeSummarizer:
    def __init__(self, answer: str, chosen: SummarySettings | None = None):
        self.answer = answer
        self.chosen = chosen or SummarySettings()
        self.calls: list[tuple[str, str, SummarySettings]] = []

    def settings(self):
        return self.chosen

    def describe(self, s):
        return f"{s.agent_id or s.provider} · {s.model}"

    async def complete(self, instructions, text, settings, max_tokens=800):
        self.calls.append((instructions, text, settings))
        return self.answer


class FakeRegistry:
    def __init__(self, agents):
        self.agents = agents

    async def scan(self, refresh=False, hidden=None):
        return self.agents


def claude(state="ready"):
    return AgentStatus(id="claude", name="Claude Code", state=state, models=[
        ModelOption(id="opus", label="Opus", efforts=["low", "high"]),
        ModelOption(id="haiku", label="Haiku 4.5", efforts=["low", "medium"]),
    ])


def test_digest_is_compact_and_plain(app):
    text = g.digest(app)
    assert "- apply: Does apply [none]" in text
    assert "Find jobs & apply." in text and "<img" not in text and "<p" not in text
    assert "SETUP.md (start):" in text and "Put your CV" in text


def test_basic_guide(app):
    guide = g.basic_guide(app.manifest, "fp")
    assert guide.source == "basic" and guide.what_it_does == "Find jobs and apply."
    assert [s.command for s in guide.steps] == ["setup", "apply", "scrape"]   # start first, no reset
    assert "reset" not in [e.command for e in guide.examples]


def test_parse_answer_handles_fences_and_bad_commands():
    answer = "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```"
    data = g.parse_answer(answer, {"setup", "apply", "scrape"})
    assert [e["command"] for e in data["examples"]] == ["scrape", "apply", None]   # "/scrape" -> scrape
    assert data["steps"][0] == {"title": "Set up your profile", "text": "Add your CV.", "command": "setup"}


@pytest.mark.parametrize("answer", ["no json here", "{not: json}", '{"examples": []}'])
def test_parse_answer_rejects_junk(answer):
    with pytest.raises(g.GuideError):
        g.parse_answer(answer, set())


def test_cheapest_settings_picks_small_model_low_effort():
    s = g.cheapest_settings(claude())
    assert (s.provider, s.agent_id, s.model, s.effort) == ("agent", "claude", "haiku", "low")


def test_cheapest_settings_skips_hidden_models():
    agent = claude()
    agent.hidden_models = ["haiku"]
    assert g.cheapest_settings(agent).model is None   # falls back to the agent's default


def test_pick_writer_prefers_the_users_summary_model():
    chosen = SummarySettings(provider="groq", model="llama-3.1-8b-instant")
    writer = asyncio.run(g.pick_writer(FakeSummarizer("", chosen), FakeRegistry([claude()])))
    assert writer == chosen


def test_pick_writer_none_when_no_agent():
    assert asyncio.run(g.pick_writer(FakeSummarizer(""), FakeRegistry([claude("not_installed")]))) is None


class TestGuides:
    def test_write_saves_and_reuses(self, db, app):
        fake = FakeSummarizer(json.dumps(GOOD))
        guides = g.Guides(db, fake, FakeRegistry([claude()]))
        assert guides.current(app).source == "basic"

        written = asyncio.run(guides.write(app))
        assert written.source == "ai" and written.made_by == "claude · haiku"
        assert fake.calls[0][2].model == "haiku"
        assert guides.current(app).what_it_does == GOOD["what_it_does"]

        asyncio.run(guides.write(app))           # still current: no second model call
        assert len(fake.calls) == 1
        asyncio.run(guides.write(app, force=True))
        assert len(fake.calls) == 2

    def test_repo_change_makes_it_stale(self, db, app):
        guides = g.Guides(db, FakeSummarizer(json.dumps(GOOD)), FakeRegistry([claude()]))
        asyncio.run(guides.write(app))
        app.manifest.commands.append(cmd("interview"))
        assert guides.current(app).source == "basic"   # back to basic until rewritten

    def test_no_ai_available(self, db, app):
        guides = g.Guides(db, FakeSummarizer("{}"), FakeRegistry([]))
        with pytest.raises(g.GuideError, match="No AI is available"):
            asyncio.run(guides.write(app))


# ---------- command forms ----------

def test_input_notes_take_the_bullet_list(tmp_path):
    f = tmp_path / "rank.md"
    f.write_text("# /rank\n\nIntro.\n\n`$ARGUMENTS` may contain:\n\n- Nothing -> rank 10 jobs\n"
                 "- `--all` -> re-rank everything\n- `--limit <N>` -> max jobs\n\nOther text here.\n",
                 encoding="utf-8")
    assert g.input_notes(f) == ["`$ARGUMENTS` may contain:", "- Nothing -> rank 10 jobs",
                                "- `--all` -> re-rank everything", "- `--limit <N>` -> max jobs"]


def test_digest_has_input_notes_and_user_folders(tmp_path):
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "rank.md").write_text("`$ARGUMENTS` may contain a focus area.\n")
    (tmp_path / "documents" / "cv").mkdir(parents=True)
    (tmp_path / "documents" / "README.md").write_text("Put your CV in cv/.\n")
    rank = AppCommand(name="rank", title="Rank", input="optional", source=".claude/commands/rank.md")
    app = AppInfo(id="x", name="r", path=str(tmp_path), created_at="t", updated_at="t",
                  manifest=AppManifest(name="r", tier=1, commands=[rank]))
    text = g.digest(app)
    assert "    `$ARGUMENTS` may contain a focus area." in text
    assert "- documents/ (subfolders: cv): Put your CV in cv/." in text


def test_basic_form():
    none = g.basic_form(AppCommand(name="a", title="A", source="x"))
    assert none.fields == [] and none.argument == ""
    apply = g.basic_form(AppCommand(name="apply", title="Apply", input="required", source="x",
                                    argument_hint="either a URL or pasted text"))
    f = apply.fields[0]
    assert (f.type, f.required, f.advanced, apply.argument) == ("url_or_text", True, False, "{value}")
    opt = g.basic_form(AppCommand(name="i", title="I", input="optional", source="x", argument_hint="a company name"))
    f = opt.fields[0]
    assert (f.type, f.label, f.advanced, f.help) == ("text", "A company name", True, "You can leave this empty.")


def test_parse_form_makes_the_models_form_safe():
    command = AppCommand(name="rank", title="Rank", input="optional", source="x")
    form = g.parse_form({
        "title": "Rank my jobs", "description": "Scores new jobs.",
        "fields": [
            {"key": "Focus Area", "label": "Any area to focus on?", "type": "text"},
            {"key": "all", "label": "Re-rank everything", "type": "toggle", "value": "--all", "advanced": True},
            {"key": "empty_toggle", "type": "toggle"},                         # adds nothing: dropped
            {"key": "scope", "type": "choice", "choices": []},                 # no choices: becomes text
            {"key": "cv", "type": "file", "save_to": "../../etc"},             # outside the repo: -> uploads
            {"key": "cv2", "type": "file", "save_to": "documents\\cv", "file_types": [".pdf", "pdf"]},
            {"key": "x", "type": "spaceship"},                                 # unknown type: text
            {"key": "focus_area", "type": "text"},                             # duplicate key: dropped
        ],
        "argument": "{focus_area} {all} {nonexistent} [--limit {limit}]",
    }, command)
    by_key = {f.key: f for f in form.fields}
    assert list(by_key) == ["focus_area", "all", "scope", "cv", "cv2", "x"]
    assert by_key["all"].value == "--all" and by_key["all"].advanced
    assert by_key["scope"].type == "text" and by_key["x"].type == "text"
    assert by_key["cv"].save_to == "uploads"
    assert by_key["cv2"].save_to == "documents/cv" and by_key["cv2"].file_types == [".pdf"]
    assert form.argument == "{focus_area} {all}  [--limit ]"   # unknown placeholders removed
    assert form.title == "Rank my jobs"


def test_parse_answer_fills_missing_forms_with_basic_ones():
    commands = [AppCommand(name="apply", title="Apply", input="required", source="x", argument_hint="a URL"),
                AppCommand(name="rank", title="Rank", source="x")]
    answer = dict(GOOD, commands={"/apply": {"title": "Apply to a job", "fields": [
        {"key": "job", "label": "Job link or description", "type": "url_or_text", "required": True}],
        "argument": "{job}"}})
    data = g.parse_answer(json.dumps(answer), commands)
    assert data["commands"]["apply"].title == "Apply to a job"
    assert data["commands"]["rank"].fields == []        # the model skipped it: basic form


@pytest.mark.parametrize("template,values,expected", [
    ("{job}", {"job": " https://x.com/1 "}, "https://x.com/1"),
    ("{focus} {all} [--limit {limit}] [--top {top}]", {"focus": "data science", "all": "--all", "top": "3"},
     "data science --all --top 3"),
    ("{focus} [--limit {limit}]", {}, ""),
    ("followup [{days}]", {"days": "14"}, "followup 14"),
    ("", {"x": "y"}, ""),
])
def test_fill_argument(template, values, expected):
    assert g.fill_argument(template, values) == expected

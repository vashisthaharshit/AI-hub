"""Codex skills (.agents/skills) as buttons, started as `$name`."""

from pathlib import Path

from aihub.apps import running
from aihub.apps.manifest import build_manifest
from aihub.models import AppInfo


def skill(repo: Path, where: str, name: str, description: str, body: str = "Do it with $ARGUMENTS.") -> None:
    folder = repo / where / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8")


def app_of(repo: Path) -> AppInfo:
    return AppInfo(id="a", name=repo.name, path=str(repo), created_at="t", updated_at="t",
                   manifest=build_manifest(repo, which=lambda _: None))


def test_codex_only_repo_is_a_codex_app(tmp_path):
    repo = tmp_path / "r"
    (repo / "AGENTS.md").parent.mkdir(parents=True)
    (repo / "AGENTS.md").write_text("Use $scan to scan.", encoding="utf-8")
    skill(repo, ".agents/skills", "scan", "Scan the market")
    skill(repo, ".agents/skills", "report", "Write a report")
    m = app_of(repo).manifest
    assert m.tier == 1 and m.primary_agent == "codex"
    buttons = {c.name: c for c in m.commands}
    assert set(buttons) == {"scan", "report"}           # no commands at all: every skill is a button
    assert buttons["scan"].agent == "codex" and buttons["scan"].invoke == "$scan"


def test_codex_run_uses_dollar_and_claude_run_gets_instructions(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    skill(repo, ".agents/skills", "scan", "Scan the market", body="Scan $ARGUMENTS carefully.")
    app = app_of(repo)
    command = app.manifest.commands[0]
    assert running.build_prompt(app, command, None, {"value": "AAPL"}, "codex") == "$scan AAPL"
    prompt = running.build_prompt(app, command, None, {"value": "AAPL"}, "claude-code")
    assert "written for Codex" in prompt and "Scan AAPL carefully." in prompt


def test_helper_codex_skill_stays_off_the_list(tmp_path):
    repo = tmp_path / "r"
    (repo / ".claude" / "commands").mkdir(parents=True)
    (repo / ".claude" / "commands" / "apply.md").write_text("---\ndescription: Apply\n---\nApply\n", encoding="utf-8")
    (repo / "README.md").write_text("Run /apply, or in Codex type $tailor.", encoding="utf-8")
    skill(repo, ".agents/skills", "tailor", "Tailor a CV")
    skill(repo, ".agents/skills", "helper", "Used by other skills")
    m = app_of(repo).manifest
    assert m.primary_agent == "claude"
    assert sorted(c.name for c in m.commands) == ["apply", "tailor"]   # $tailor is in the README


def test_same_skill_for_both_agents(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    skill(repo, ".claude/skills", "scan", "Scan")
    skill(repo, ".agents/skills", "scan", "Scan")
    app = app_of(repo)
    [command] = app.manifest.commands
    assert command.agent == "claude" and command.invoke == "/scan"
    assert running.build_prompt(app, command, None, {"value": "x"}, "claude-code") == "/scan x"
    assert running.build_prompt(app, command, None, {"value": "x"}, "codex") == "$scan x"

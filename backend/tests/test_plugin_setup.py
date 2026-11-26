"""Fixes 1-5 for repos packaged differently from ai-job-search:
plugins, loose skills, setup forms, MCP connections and money safety.

The `trader` fixture is shaped like a public day-trading skills repo: a
Claude Code plugin whose skills sit in top-level folders, an example config
inside one skill folder that the docs place at trader/config.json, an
example MCP config for a broker, and a skill that places orders.
"""

import json
import subprocess
from pathlib import Path

import pytest

from aihub.apps.install import MARKER, install_skills, loose_skills
from aihub.apps.manifest import build_manifest
from aihub.apps.plugins import find_plugins
from aihub.apps.setup_values import SetupError, SetupValues
from aihub.db import Database
from aihub.models import AppInfo, SetupSave
from aihub.secrets_store import MemoryStore


def write(path: Path, text) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")


def skill(name, description, body="Run the tool."):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


NO = lambda p: None  # noqa: E731  - fake `which`: nothing installed


@pytest.fixture
def trader(tmp_path) -> Path:
    repo = tmp_path / "day-trader-skill"
    write(repo / ".claude-plugin" / "plugin.json",
          {"name": "day-trader-skill", "description": "Day trading skills",
           "skills": ["./trader-scanner", "./trader-broker", "./trader-budget"]})
    write(repo / "trader-scanner" / "SKILL.md", skill("trader-scanner", "Scans charts for setups",
                                                      "`$ARGUMENTS` may contain a ticker list."))
    write(repo / "trader-scanner" / "scan.py", "print('scan')\n")
    write(repo / "trader-broker" / "SKILL.md", skill("trader-broker", "Order executor: places orders through Alpaca"))
    write(repo / "trader-broker" / "claude_desktop_config.example.json", {"mcpServers": {"alpaca": {
        "command": "uvx", "args": ["alpaca-mcp-server", "serve"],
        "env": {"ALPACA_API_KEY": "your_key", "ALPACA_SECRET_KEY": "your_secret", "ALPACA_PAPER_TRADE": "True"}}}})
    write(repo / "trader-budget" / "SKILL.md", skill("trader-budget", "Position sizing"))
    write(repo / "trader-budget" / "config.example.json",
          {"sec_user_agent": "Your Name you@example.com", "paper": True, "risk": {"max_loss": 100},
           "flat_by": "15:55"})
    write(repo / "README.md", "# Day trader\n\nEleven skills for day trading.\n\n"
          "Set your name and email in `trader/config.json` or the SEC_USER_AGENT environment variable.\n"
          "Keep `\"paper\": true`.\n")
    return repo


# ---------- fix 1: finding skills in a plugin ----------

def test_find_plugin_skills(trader):
    [plugin] = find_plugins(trader)
    assert plugin.name == "day-trader-skill" and plugin.root == trader.resolve()
    assert sorted(f.parent.name for f in plugin.skill_files) == ["trader-broker", "trader-budget", "trader-scanner"]


def test_marketplace_with_local_source(tmp_path):
    repo = tmp_path / "m"
    write(repo / ".claude-plugin" / "marketplace.json",
          {"name": "m", "plugins": [{"name": "tools", "source": "./plugins/tools"},
                                    {"name": "remote", "source": {"source": "github", "repo": "x/y"}}]})
    write(repo / "plugins" / "tools" / "skills" / "hello" / "SKILL.md", skill("hello", "Says hello"))
    write(repo / "plugins" / "tools" / "commands" / "greet.md", "Greet $ARGUMENTS\n")
    [plugin] = find_plugins(repo)   # the GitHub source is another repo: skipped
    assert plugin.name == "tools" and [f.parent.name for f in plugin.skill_files] == ["hello"]
    assert [n for n, _, _ in plugin.command_files] == ["greet"]


def test_plugin_manifest_becomes_buttons(trader):
    m = build_manifest(trader, which=NO)
    assert m.tier == 1 and m.agents == ["claude"]
    assert [(p.name, p.root) for p in m.plugins] == [("day-trader-skill", ".")]
    by_name = {c.name: c for c in m.commands}
    # no commands of its own -> every skill is a button, named like the plugin loads it
    assert set(by_name) == {"trader-scanner", "trader-broker", "trader-budget"}
    assert by_name["trader-scanner"].invoke == "/day-trader-skill:trader-scanner"
    assert by_name["trader-scanner"].input == "optional"


def test_helper_skills_stay_hidden_when_there_are_commands(tmp_path):
    repo = tmp_path / "r"
    write(repo / ".claude" / "commands" / "go.md", "Go.\n")
    write(repo / ".claude" / "skills" / "helper" / "SKILL.md", skill("helper", "Used by /go"))
    assert [c.name for c in build_manifest(repo, which=NO).commands] == ["go"]


# ---------- fix 2: loose skills are copied where Claude Code looks ----------

def test_loose_skills_are_installed(tmp_path):
    repo = tmp_path / "loose"
    write(repo / "skills" / "summarize" / "SKILL.md", skill("summarize", "Summarize a page"))
    write(repo / "skills" / "summarize" / "run.py", "print(1)\n")
    write(repo / "translate" / "SKILL.md", skill("translate", "Translate text"))
    write(repo / ".claude" / "skills" / "translate" / "SKILL.md", skill("translate", "The repo's own"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    assert install_skills(repo) == ["summarize"]   # the repo's own translate is left alone
    copy = repo / ".claude" / "skills" / "summarize"
    assert (copy / "run.py").is_file() and (copy / MARKER).is_file()
    assert "/.claude/skills/summarize/" in (repo / ".git" / "info" / "exclude").read_text()
    assert "own" in (repo / ".claude" / "skills" / "translate" / "SKILL.md").read_text()

    write(repo / "skills" / "summarize" / "SKILL.md", skill("summarize", "Summarize, updated"))
    install_skills(repo)                            # refreshed on rescan
    assert "updated" in (copy / "SKILL.md").read_text()
    m = build_manifest(repo, which=NO)
    assert m.setup.installed_skills == ["summarize"] and m.tier == 1


def test_plugins_are_not_copied(trader):
    assert loose_skills(trader) == [] and install_skills(trader) == []


# ---------- fixes 3 and 4: the setup form ----------

def test_setup_form(trader):
    setup = build_manifest(trader, which=NO).setup
    [config] = setup.files
    assert (config.source, config.target, config.editable) == ("trader-budget/config.example.json", "trader/config.json", True)
    f = {x.id: x for x in setup.fields}
    agent = f["file:trader/config.json:sec_user_agent"]
    assert agent.required and not agent.advanced and agent.default is None   # a placeholder in the example
    paper = f["file:trader/config.json:paper"]
    assert (paper.safety, paper.type, paper.default) == (True, "toggle", "true")
    assert not f["file:trader/config.json:risk.max_loss"].advanced   # loss limits: shown to beginners
    assert f["file:trader/config.json:flat_by"].advanced              # sensible example value: "More settings"
    assert "env:SEC_USER_AGENT" not in f            # same setting as sec_user_agent in the config file
    key = f["env:ALPACA_API_KEY"]
    assert (key.type, key.required) == ("secret", True)
    assert f["env:ALPACA_PAPER_TRADE"].safety

    [server] = setup.servers
    assert (server.name, server.money, server.plugin) == ("alpaca", True, None)
    assert server.env == ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_PAPER_TRADE"]


def test_env_example_and_plugin_user_config(tmp_path):
    repo = tmp_path / "e"
    write(repo / ".env.example", "# Your OpenAI key, from platform.openai.com\nOPENAI_API_KEY=\nMODEL=gpt-4o-mini  # which model\n"
                                 "DRY_RUN=false\n")
    write(repo / ".claude-plugin" / "plugin.json", {"name": "p", "userConfig": {
        "region": {"type": "string", "title": "Region", "description": "Where", "options": ["eu", "us"]},
        "token": {"type": "string", "title": "Token", "description": "API token", "sensitive": True}}})
    f = {x.id: x for x in build_manifest(repo, which=NO).setup.fields}
    key = f["env:OPENAI_API_KEY"]
    assert (key.type, key.required, key.help) == ("secret", True, "Your OpenAI key, from platform.openai.com")
    assert (f["env:MODEL"].default, f["env:MODEL"].help) == ("gpt-4o-mini", "which model")
    dry = f["env:DRY_RUN"]
    assert (dry.safety, dry.default) == (True, "true")    # starts safe even though the example says false
    assert f["plugin:p:region"].type == "choice" and f["plugin:p:region"].choices == ["eu", "us"]
    assert f["plugin:p:token"].type == "secret"


@pytest.fixture
def connected(tmp_path, trader):
    db = Database(tmp_path / "t.db")
    app = AppInfo(id="a1", name="t", path=str(trader), created_at="t", updated_at="t",
                  manifest=build_manifest(trader, which=NO))
    db.insert_app(app)
    yield app, SetupValues(db, MemoryStore()), trader
    db.close()


class TestSaving:
    def test_view_before_saving(self, connected):
        app, values, _ = connected
        view = values.view(app)
        assert not view.saved
        assert "file:trader/config.json:sec_user_agent" in view.missing and "env:ALPACA_API_KEY" in view.missing
        assert view.values["file:trader/config.json:paper"] == "true" and view.unsafe == []
        assert view.servers_ready == {"alpaca": False}

    def test_save_writes_config_secrets_and_mcp(self, connected):
        app, values, repo = connected
        view = values.save(app, SetupSave(values={
            "file:trader/config.json:sec_user_agent": "Asha Rao asha@mail.com",
            "file:trader/config.json:risk.max_loss": "250",
            "env:ALPACA_API_KEY": "PK123", "env:ALPACA_SECRET_KEY": "SK456",
        }))
        config = json.loads((repo / "trader" / "config.json").read_text())
        assert config == {"sec_user_agent": "Asha Rao asha@mail.com", "paper": True, "risk": {"max_loss": 250},
                          "flat_by": "15:55"}
        assert view.missing == [] and set(view.secrets_set) == {"env:ALPACA_API_KEY", "env:ALPACA_SECRET_KEY"}
        assert "PK123" not in json.dumps(view.model_dump())          # secrets never come back
        mcp = json.loads((repo / ".mcp.json").read_text())
        assert mcp["mcpServers"]["alpaca"]["env"]["ALPACA_API_KEY"] == "${ALPACA_API_KEY}"
        assert "PK123" not in (repo / ".mcp.json").read_text()         # keys stay off disk
        env = values.run_env(app)
        assert env["ALPACA_API_KEY"] == "PK123" and env["ALPACA_PAPER_TRADE"] == "true"

    def test_user_changes_in_the_config_are_kept(self, connected):
        app, values, repo = connected
        values.save(app, SetupSave(values={"file:trader/config.json:sec_user_agent": "A a@b.c"}))
        path = repo / "trader" / "config.json"
        data = json.loads(path.read_text())
        data["my_own_setting"] = 1
        path.write_text(json.dumps(data))
        values.save(app, SetupSave(values={"file:trader/config.json:sec_user_agent": "B b@c.d"}))
        data = json.loads(path.read_text())
        assert data["my_own_setting"] == 1 and data["sec_user_agent"] == "B b@c.d"

    def test_turning_paper_off_needs_confirmation(self, connected):
        app, values, repo = connected
        with pytest.raises(SetupError, match="safety switch"):
            values.save(app, SetupSave(values={"file:trader/config.json:paper": "false"}))
        view = values.save(app, SetupSave(values={"file:trader/config.json:paper": "false"}, confirm_unsafe=True))
        assert view.unsafe == ["file:trader/config.json:paper"]
        assert json.loads((repo / "trader" / "config.json").read_text())["paper"] is False

    def test_the_repos_own_mcp_server_is_not_replaced(self, connected):
        app, values, repo = connected
        write(repo / ".mcp.json", {"mcpServers": {"alpaca": {"command": "mine"}}})
        values.save(app, SetupSave(values={}))
        assert json.loads((repo / ".mcp.json").read_text())["mcpServers"]["alpaca"] == {"command": "mine"}

    def test_clearing_a_secret(self, connected):
        app, values, _ = connected
        values.save(app, SetupSave(values={"env:ALPACA_API_KEY": "PK"}))
        view = values.save(app, SetupSave(values={"env:ALPACA_API_KEY": ""}))
        assert "env:ALPACA_API_KEY" not in view.secrets_set


# ---------- fix 5: money ----------

def test_money_commands_and_servers(trader):
    m = build_manifest(trader, which=NO)
    money = {c.name for c in m.commands if c.money}
    assert money == {"trader-broker"}
    assert m.setup.always_ask == ["alpaca"]


def test_money_only_from_the_first_sentence(tmp_path):
    repo = tmp_path / "r"
    write(repo / ".claude-plugin" / "plugin.json", {"name": "p", "skills": "./"})
    write(repo / "risk" / "SKILL.md", skill("risk", "Risk Manager. Checks every trade before your broker places orders."))
    write(repo / "pay" / "SKILL.md", skill("pay", "Payments. Sends invoices."))
    assert build_manifest(repo, which=NO).setup.money_commands == ["pay"]


def test_server_variable_with_a_real_example_value(trader):
    spec = json.loads((trader / "trader-broker" / "claude_desktop_config.example.json").read_text())
    spec["mcpServers"]["alpaca"]["env"]["ALPACA_TOOLSETS"] = "account,trading"
    write(trader / "trader-broker" / "claude_desktop_config.example.json", spec)
    f = {x.id: x for x in build_manifest(trader, which=NO).setup.fields}
    tools = f["env:ALPACA_TOOLSETS"]
    assert (tools.required, tools.advanced, tools.default) == (False, True, "account,trading")

"""Apps tab, step 1: reading a repo (manifest) and connecting it (clone + save).

The fake repo below is shaped like ai-job-search: CLAUDE.md, AGENTS.md,
slash commands, skills, a settings.json, LaTeX templates and Bun tools.
Cloning is tested against a local git repo, so no internet is needed.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from aihub import config
from aihub.apps import connect as apps
from aihub.apps.manifest import build_manifest, describe_rule, input_mode, parse_frontmatter
from aihub.db import Database
from aihub.models import AppConnect

README = """<p align="center"><img src="hero.svg" alt="hero"></p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a> &nbsp; <a href="#commands"><b>Commands</b></a>
</p>

> [!NOTE]
> Not affiliated with anyone.

<p align="center">
  The job search that runs on <a href="https://x">your machine</a>.<br>
  Evaluate postings, tailor CVs &amp; write cover letters.
</p>

## Usage

1. Run `/setup` to build your profile.
2. Run `/scrape` to search portals, then `/rank` to shortlist.
3. Run `/apply <url>` for a job.
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def job_repo(tmp_path) -> Path:
    repo = tmp_path / "ai-job-search"
    write(repo / "README.md", README)
    write(repo / "CLAUDE.md", "# Candidate profile\n")
    write(repo / "AGENTS.md", "# Entry point for other agents\n")
    write(repo / "SETUP.md", "Install a LaTeX distribution with lualatex.\n")
    cmds = repo / ".claude" / "commands"
    write(cmds / "apply.md", "---\ndescription: Evaluate a job and tailor your CV\n"
                             "argument-hint: <job url or description>\n---\nApply to $ARGUMENTS\n")
    write(cmds / "interview.md", "# /interview - Prepare for an Interview\n\n"
                                 "`$ARGUMENTS` may contain a company name (optionally with a role), e.g. `/interview acme`.\n")
    write(cmds / "setup.md", "---\ndescription: \"Build your profile\"\n---\nInterview the user.\n")
    write(cmds / "scrape.md", "# Search the job portals\n\nUse the portal tools.\n")
    write(cmds / "rank.md", "Shortlist the scraped jobs.\n")
    write(cmds / "reset.md", "---\ndescription: Start over\n---\nDelete everything.\n")
    write(cmds / "upskill.md", "Find courses for skill $1.\n")
    write(repo / ".claude" / "skills" / "job-application-assistant" / "SKILL.md",
          "---\nname: job-application-assistant\ndescription: >\n  Writes CVs\n  and letters. Triggers on: cv, apply\n"
          "allowed-tools: Read\n---\nbody\n")
    write(repo / ".claude" / "skills" / "job-scraper" / "SKILL.md",
          "---\nname: scrape\ndescription: >\n  Finds new jobs. Triggers on: find jobs, /scrape\n---\n"
          "If `$ARGUMENTS` contains a portal name, search only there.\n")
    write(repo / ".agents" / "skills" / "linkedin-search" / "SKILL.md", "# Search LinkedIn\n")
    write(repo / ".claude" / "settings.json", json.dumps({"permissions": {
        "allow": ["Bash(python3 tools/*)", "Bash(python tools/*)", "WebFetch(domain:linkedin.com)", "Edit"],
        "deny": ["Bash(git push:*)"],
    }}))
    write(repo / "tools" / "install_portal_clis.py", "import subprocess\nsubprocess.run(['bun', 'install'])\n")
    write(repo / "templates" / "cover.cls", "% latex class\n")
    return repo


def fake_which(installed: set[str]):
    return lambda name: f"/bin/{name}" if name in installed else None


# ---------- manifest ----------

class TestManifest:
    def test_tier_agents_and_context(self, job_repo):
        m = build_manifest(job_repo, which=fake_which(set()))
        assert m.tier == 1
        assert m.agents == ["claude", "codex"]
        assert m.primary_agent == "claude"
        assert m.context_files == ["CLAUDE.md", "AGENTS.md", "SETUP.md", "README.md"]
        assert m.summary == "The job search that runs on your machine. Evaluate postings, tailor CVs & write cover letters."

    def test_commands_become_buttons_in_workflow_order(self, job_repo):
        m = build_manifest(job_repo, which=fake_which(set()))
        # setup first (start here), then README order, then unmentioned, reset last (danger)
        assert [c.name for c in m.commands] == ["setup", "scrape", "rank", "apply", "interview", "upskill", "reset"]
        by_name = {c.name: c for c in m.commands}
        assert by_name["setup"].kind == "start"
        assert by_name["reset"].kind == "danger"
        apply = by_name["apply"]
        assert apply.description == "Evaluate a job and tailor your CV"
        assert apply.argument_hint == "<job url or description>"
        assert apply.input == "required" and apply.source == ".claude/commands/apply.md"
        assert by_name["setup"].description == "Build your profile"     # quotes removed
        assert by_name["upskill"].input == "required"                     # uses $1
        assert by_name["rank"].input == "none"
        interview = by_name["interview"]
        assert interview.description == "Prepare for an Interview"       # "/interview - " removed
        assert (interview.input, interview.argument_hint) == ("optional", "a company name (optionally with a role)")

    def test_skill_the_readme_tells_you_to_type_is_a_button(self, job_repo):
        by_name = {c.name: c for c in build_manifest(job_repo, which=fake_which(set())).commands}
        # scrape.md (a command) wins over the scrape skill with the same name
        assert not by_name["scrape"].from_skill
        (job_repo / ".claude" / "commands" / "scrape.md").unlink()
        by_name = {c.name: c for c in build_manifest(job_repo, which=fake_which(set())).commands}
        scrape = by_name["scrape"]
        assert scrape.from_skill and scrape.description == "Finds new jobs." and scrape.input == "optional"
        assert "job-application-assistant" not in by_name   # a helper skill, never typed

    def test_skills(self, job_repo):
        skills = {s.name: s for s in build_manifest(job_repo, which=fake_which(set())).skills}
        assert skills["job-application-assistant"].agent == "claude"
        assert skills["job-application-assistant"].description == "Writes CVs and letters."  # multi-line, no "Triggers on"
        assert skills["linkedin-search"].agent == "codex"

    def test_requirements(self, job_repo):
        m = build_manifest(job_repo, which=fake_which({"git", "uv", "bun"}))
        req = {r.id: r for r in m.requirements}
        assert set(req) == {"git", "python", "bun", "latex"}
        assert req["git"].installed and req["bun"].installed
        assert req["python"].auto
        assert not req["latex"].installed   # nothing like lualatex on PATH

    def test_permissions_in_plain_words(self, job_repo):
        p = build_manifest(job_repo, which=fake_which(set())).permissions
        assert p.found
        assert p.allow == ["Run the command: python tools/*", "Open web pages on linkedin.com",
                           "Edit files in this folder"]   # python3/python listed once
        assert p.deny == ["Run the command: git push …"]

    def test_only_instructions_is_tier_2(self, tmp_path):
        write(tmp_path / "r" / "AGENTS.md", "hi")
        m = build_manifest(tmp_path / "r", which=fake_which(set()))
        assert (m.tier, m.agents, m.primary_agent) == (2, ["codex"], "codex")

    def test_plain_repo_is_tier_3(self, tmp_path):
        write(tmp_path / "r" / "main.py", "print(1)")
        m = build_manifest(tmp_path / "r", which=fake_which(set()))
        assert (m.tier, m.agents, m.commands) == (3, [], [])

    def test_bad_settings_json(self, tmp_path):
        write(tmp_path / "r" / ".claude" / "settings.json", "{not json")
        assert build_manifest(tmp_path / "r", which=fake_which(set())).permissions.error


def test_frontmatter_block_values():
    fields, body = parse_frontmatter("---\ndescription: >\n  one\n  two\nnotes: |\n  a\n  b\nname: x\n---\nbody")
    assert fields == {"description": "one two", "notes": "a\nb", "name": "x"} and body == "body"


@pytest.mark.parametrize("body,expected", [
    ("Nothing to type here.", ("none", None)),
    ("The posting is provided below as `$ARGUMENTS` (either a URL or pasted text).",
     ("required", "either a URL or pasted text")),
    ("`$ARGUMENTS` may contain a subcommand, a file path, or nothing.",
     ("optional", "a subcommand, a file path, or nothing")),
    ("If `$ARGUMENTS` is empty, ask which scope.", ("optional", None)),
    ("If `$ARGUMENTS` looks like a URL, fetch it.", ("required", None)),
    ("## Parse Arguments\n- No argument -> default file", ("optional", None)),
])
def test_input_mode(body, expected):
    assert input_mode(None, body) == expected


def test_frontmatter_without_closing_line_is_body():
    fields, body = parse_frontmatter("---\ndescription: x\nno end")
    assert fields == {} and "no end" in body


@pytest.mark.parametrize("rule,text", [
    ("Bash", "Run any terminal command"),
    ("WebSearch", "Search the web"),
    ("mcp__notion__search", "Use the notion connection (search)"),
    ("Read(./documents/**)", "Read files: ./documents/**"),
    ("Skill(job-application-assistant)", "Use its skill: job-application-assistant"),
    ("Bash(git push:*)", "Run the command: git push …"),
    ("SomethingNew", "SomethingNew"),
])
def test_describe_rule(rule, text):
    assert describe_rule(rule) == text


# ---------- links ----------

@pytest.mark.parametrize("url", [
    "https://github.com/ai-job-search-dev/ai-job-search",
    "https://github.com/ai-job-search-dev/ai-job-search.git",
    "https://github.com/ai-job-search-dev/ai-job-search/tree/main/.claude",
    "github.com/ai-job-search-dev/ai-job-search/",
    "ai-job-search-dev/ai-job-search",
])
def test_parse_repo_url(url):
    assert apps.parse_repo_url(url) == ("https://github.com/ai-job-search-dev/ai-job-search.git", "ai-job-search")


def test_parse_repo_url_rejects_junk():
    with pytest.raises(apps.AppError):
        apps.parse_repo_url("hello world")


# ---------- connect (real git, local "remote") ----------

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def origin(job_repo) -> Path:
    """job_repo as a git repo, standing in for GitHub."""
    git(job_repo, "init")
    git(job_repo, "config", "user.email", "t@t")
    git(job_repo, "config", "user.name", "t")
    git(job_repo, "add", ".")
    git(job_repo, "commit", "-m", "init")
    return job_repo


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def from_local(monkeypatch, origin):
    """Make any pasted link clone from the local origin instead of GitHub."""
    monkeypatch.setattr(apps, "parse_repo_url", lambda url: (str(origin), "ai-job-search"))
    return origin


class TestConnect:
    def test_clone_save_and_push_disabled(self, tmp_path, db, from_local):
        folder = tmp_path / "repos"
        app = asyncio.run(apps.connect(AppConnect(url="x", folder=str(folder)), db))
        dest = folder / "ai-job-search"
        assert Path(app.path) == dest.resolve()
        assert (dest / ".claude" / "commands" / "apply.md").is_file()
        assert app.manifest.tier == 1 and app.name == "ai-job-search"
        assert git(dest, "remote", "get-url", "--push", "origin") == apps.PUSH_DISABLED
        assert git(dest, "remote", "get-url", "origin") == str(from_local)  # pull still works
        assert [a.id for a in db.list_apps()] == [app.id]

    def test_default_folder(self, tmp_path, db, from_local, monkeypatch):
        monkeypatch.setattr(config, "APPS_DIR", tmp_path / "aihub" / "repos")
        app = asyncio.run(apps.connect(AppConnect(url="x"), db))
        assert Path(app.path) == (tmp_path / "aihub" / "repos" / "ai-job-search").resolve()

    def test_connecting_again_reuses_the_folder(self, tmp_path, db, from_local):
        req = AppConnect(url="x", folder=str(tmp_path / "repos"))
        first = asyncio.run(apps.connect(req, db))
        second = asyncio.run(apps.connect(req, db))
        assert second.id == first.id and len(db.list_apps()) == 1

    def test_folder_with_something_else_is_refused(self, tmp_path, db, from_local):
        write(tmp_path / "repos" / "ai-job-search" / "mine.txt", "not a clone")
        with pytest.raises(apps.AppError, match="already exists"):
            asyncio.run(apps.connect(AppConnect(url="x", folder=str(tmp_path / "repos")), db))

    def test_existing_local_folder(self, db, origin):
        app = asyncio.run(apps.connect(AppConnect(path=str(origin)), db))
        assert app.manifest.tier == 1 and app.url is None  # no remote in this test repo

    def test_rescan_picks_up_new_commands(self, db, origin):
        app = asyncio.run(apps.connect(AppConnect(path=str(origin)), db))
        write(origin / ".claude" / "commands" / "interview.md", "Prepare for an interview.\n")
        again = apps.rescan(app.id, db)
        assert "interview" in [c.name for c in again.manifest.commands]

    def test_link_and_folder_together_is_refused(self, db):
        with pytest.raises(apps.AppError, match="not both"):
            asyncio.run(apps.connect(AppConnect(url="x", path="y"), db))

    def test_nothing_given(self, db):
        with pytest.raises(apps.AppError):
            asyncio.run(apps.connect(AppConnect(), db))

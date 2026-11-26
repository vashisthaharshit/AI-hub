"""Read a repo folder and describe it as an "app" (an AppManifest).

Everything here is plain file reading: no AI and no tokens. That's on
purpose. Working out "what commands does this repo have" is the same every
time, so code does it; the agent is only used when the user clicks a button.

What we look for
  .claude/commands/**/*.md     Claude Code slash commands  -> buttons
  .claude/skills/*/SKILL.md    Claude Code skills; also buttons when the repo tells
                               users to type them (`/scrape` in ai-job-search)
  .agents/skills/*/SKILL.md    Codex skills; buttons by the same rule, started as
                               `$<name>` (how Codex runs a skill on purpose)
  .claude-plugin/*.json        a Claude Code plugin: its skills/, commands/, .mcp.json
                               (buttons become /<plugin>:<name>; see plugins.py)
  CLAUDE.md / AGENTS.md        instructions each agent reads at start
  .claude/settings.json        what the repo allows the agent to do
  README / SETUP / tools/      hints about the programs it needs (Bun, LaTeX...)

Tiers
  1  has commands       -> full buttons UI
  2  only instructions  -> "Ask this repo" chat (later step)
  3  neither            -> not an app; open it in Manual mode instead
"""

import html
import json
import re
from collections.abc import Callable
from pathlib import Path

from aihub.apps import tools
from aihub.apps.install import MARKER
from aihub.apps.plugins import PluginInfo, find_plugins
from aihub.apps.setup import read_setup
from aihub.models import AppCommand, AppManifest, AppPermissions, AppPlugin, AppRequirement, AppSkill

# Folders never worth walking into.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache"}
_MAX_DEPTH = 4
_MAX_FILES = 5000
_MAX_TEXT = 200_000  # bytes read per file when searching for hints

# A command whose name says "start here" or "careful". Names are matched as
# whole words of the command name (split on - and _).
_START_WORDS = {"setup", "init", "onboard", "onboarding", "install", "configure", "start", "getting"}
_DANGER_WORDS = {"reset", "delete", "clean", "wipe", "purge", "uninstall", "destroy", "nuke", "remove", "clear"}


# ---------- small helpers ----------

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a Markdown file into (frontmatter fields, body).

    Frontmatter is the block between two `---` lines at the very top:
        ---
        description: Tailor a CV for a job
        argument-hint: <job url>
        ---
    Only simple `key: value` lines are read; that's all command and skill
    files use, so we don't need a YAML library for it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    fields: dict[str, str] = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            return fields, "\n".join(lines[i + 1:])
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        i += 1
        if not match:
            continue
        value = match.group(2).strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            # Multi-line value: the indented lines below belong to it.
            #   description: >
            #     Finds new job postings
            #     matching your profile
            # ">" joins them into one line, "|" keeps the line breaks.
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                if lines[i].strip() == "---":
                    break
                block.append(lines[i].strip())
                i += 1
            value = (" " if value[0] == ">" else "\n").join(b for b in block if b)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[match.group(1).lower()] = value
    return {}, text  # no closing ---: treat it all as body


def _first_line(body: str, limit: int = 200) -> str | None:
    """First real sentence of a Markdown body (skips blank lines and code fences)."""
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith(("```", "<!--", "|", "![", "[!")):
            continue
        line = line.lstrip("#>*- ").strip()
        # "$ARGUMENTS" / "$1" are placeholders for what the user types; don't show them.
        line = re.sub(r"\$(ARGUMENTS|[1-9])", "…", line)
        if line:
            return line[:limit]
    return None


def _clean_description(text: str | None, name: str) -> str | None:
    """Make a description readable for a beginner.
    '/apply - Drafter-Reviewer Workflow' -> 'Drafter-Reviewer Workflow'
    '... Triggers on: job scrape, find jobs' -> '...' (keywords meant for the agent)"""
    if not text:
        return None
    text = re.sub(rf"^/?{re.escape(name)}\s*[-–—:]\s*", "", text.strip())
    text = re.sub(r"\s*Triggers on\b.*$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    return text[:300] or None


# Phrases on a $ARGUMENTS line that mean "you may leave it empty".
_OPTIONAL_INPUT = re.compile(
    r"may contain|or nothing|is empty|optional|\bif\W{0,3}\$ARGUMENTS\W{0,3}\s+(contains|names|has|includes)",
    re.IGNORECASE,
)


def input_mode(hint: str | None, body: str) -> tuple[str, str | None]:
    """Does the button need a text field? Returns (mode, hint).

    mode: "none" | "optional" | "required". Most commands in ai-job-search
    accept input but don't need it ("`$ARGUMENTS` may contain a company
    name, or nothing"); /apply needs it ("the job posting is provided as
    `$ARGUMENTS` (either a URL or pasted text)"). Only the lines that mention
    $ARGUMENTS are read, so words elsewhere in a long file don't mislead us.
    """
    arg_lines = [ln for ln in body.split("\n") if "$ARGUMENTS" in ln or re.search(r"\$[1-9]\b", ln)]
    if not arg_lines:
        if hint or re.search(r"\bargument", body, re.IGNORECASE):
            return "optional", hint
        return "none", None
    if not hint:  # find a hint in the text itself
        for line in arg_lines:
            m = (re.search(r"may contain\s+(.{3,100}?)(?:[.:]|,? e\.g\.|$)", line)
                 or re.search(r"\$ARGUMENTS`?\s*\(([^)]{3,100})\)", line))
            if m and m.group(1).strip(" `"):
                hint = m.group(1).strip(" `")
                break
    optional = any(_OPTIONAL_INPUT.search(ln) for ln in arg_lines)
    return ("optional" if optional else "required"), hint


def _title(name: str) -> str:
    """'html-report' -> 'Html report'. A better wording comes later (helper model, cached)."""
    words = re.split(r"[-_\s]+", name)
    text = " ".join(w for w in words if w)
    return text[:1].upper() + text[1:]


def _read(path: Path) -> str:
    try:
        with path.open("rb") as f:
            return f.read(_MAX_TEXT).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _walk(root: Path):
    """Files under root, skipping heavy folders, at most _MAX_DEPTH deep."""
    count = 0
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and depth < _MAX_DEPTH:
                    stack.append((entry, depth + 1))
            else:
                count += 1
                if count > _MAX_FILES:
                    return
                yield entry


# ---------- commands and skills ----------

def _command_kind(name: str) -> str:
    words = set(re.split(r"[-_:]+", name.lower()))
    if words & _DANGER_WORDS:
        return "danger"
    if words & _START_WORDS:
        return "start"
    return "normal"


def _command(repo: Path, path: Path, name: str, namespace: str | None, plugin: str | None,
             extra: dict | None = None) -> AppCommand:
    fields, body = parse_frontmatter(_read(path))
    extra = extra or {}
    mode, hint = input_mode(fields.get("argument-hint") or extra.get("argumentHint") or None, body)
    return AppCommand(
        name=name, namespace=namespace, title=_title(name),
        description=_clean_description(fields.get("description") or extra.get("description") or _first_line(body), name),
        argument_hint=hint, input=mode, kind=_command_kind(name), agent="claude",
        source=path.relative_to(repo).as_posix(), plugin=plugin,
        invoke=f"/{plugin}:{name}" if plugin else f"/{name}",
    )


def read_plugin_commands(repo: Path, plugins: list[PluginInfo]) -> list[AppCommand]:
    return [_command(repo, path, name, None, p.name, extra) for p in plugins for name, path, extra in p.command_files]


def read_commands(repo: Path) -> list[AppCommand]:
    """Claude Code slash commands: one Markdown file per command.

    .claude/commands/apply.md          -> /apply
    .claude/commands/git/commit.md     -> /commit  (namespace "git")
    """
    folder = repo / ".claude" / "commands"
    if not folder.is_dir():
        return []
    commands = []
    for path in sorted(folder.rglob("*.md")):
        rel = path.relative_to(folder)
        commands.append(_command(repo, path, path.stem, "/".join(rel.parts[:-1]) or None, None))
    return commands


def read_skills(repo: Path, plugins: list[PluginInfo] | None = None) -> list[AppSkill]:
    """Skills: folders with a SKILL.md. The agent picks them up by itself;
    we list them so the user knows what the app can do."""
    places: list[tuple[str, str | None, list[Path]]] = [
        ("claude", None, sorted((repo / ".claude" / "skills").glob("*/SKILL.md"))),
        ("codex", None, sorted((repo / ".agents" / "skills").glob("*/SKILL.md"))),
    ]
    places += [("claude", p.name, p.skill_files) for p in plugins or []]
    skills = []
    for agent, plugin, skill_files in places:
        for skill_md in skill_files:
            fields, body = parse_frontmatter(_read(skill_md))
            name = fields.get("name") or skill_md.parent.name
            skills.append(AppSkill(
                name=name,
                description=_clean_description(fields.get("description") or _first_line(body), name),
                agent=agent,
                source=skill_md.relative_to(repo).as_posix(),
                user_invocable=fields.get("user-invocable", "").lower() != "false",
                plugin=plugin,
                raw_description=fields.get("description") or "",
                body=body,
            ))
    return skills


def _mentions_slash(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w/]){re.escape('/' + name)}(?![\w-])", text) is not None


def _mentions_dollar(text: str, name: str) -> bool:
    """Codex skills are typed as `$name`."""
    return re.search(rf"(?<![\w$]){re.escape('$' + name)}(?![\w-])", text) is not None


def skills_as_commands(skills: list[AppSkill], readme: str, taken: set[str], all_skills: bool = False) -> list[AppCommand]:
    """Claude Code skills can be started like commands (`/scrape`).

    A skill becomes a button when the repo tells people to type it (its
    description or the README mentions `/<name>`), or when the repo has no
    commands at all (`all_skills`): then the skills ARE the app, e.g. a
    plugin made of skills. In a repo that has commands, skills nobody is
    told to type are helpers (job-application-assistant, used by /apply)
    and stay off the button list.
    """
    commands = []
    taken = set(taken)
    # Claude skills first: when both agents have a skill with the same name,
    # the button is the Claude one (and a Codex run still uses `$name`, see
    # running.build_prompt).
    for skill in sorted(skills, key=lambda s: s.agent != "claude"):
        if not skill.user_invocable or skill.name in taken:
            continue
        told = any(f(t, skill.name) for f in (_mentions_slash, _mentions_dollar)
                   for t in (skill.raw_description, readme))
        if not (told or all_skills):
            continue
        mode, hint = input_mode(None, skill.body)
        if skill.agent == "codex":
            invoke = f"${skill.name}"
        else:
            invoke = f"/{skill.plugin}:{skill.name}" if skill.plugin else f"/{skill.name}"
        commands.append(AppCommand(
            name=skill.name, title=_title(skill.name), description=skill.description,
            argument_hint=hint, input=mode, kind=_command_kind(skill.name),
            agent=skill.agent, source=skill.source, from_skill=True, plugin=skill.plugin,
            invoke=invoke,
        ))
        taken.add(skill.name)
    return commands


def order_commands(commands: list[AppCommand], readme: str) -> list[AppCommand]:
    """Put commands in the order a user would use them.

    1. "start here" commands (setup, init...) first
    2. then in the order the README first mentions them (`/scrape`, then `/apply`...)
       READMEs usually explain the workflow top to bottom, so this is a free,
       token-less way to get a sensible order
    3. commands the README never mentions, alphabetically
    4. dangerous commands (reset, delete...) last
    """
    def readme_position(cmd: AppCommand) -> int:
        match = re.search(rf"(?<![\w/]){re.escape('/' + cmd.name)}(?![\w-])", readme)
        return match.start() if match else len(readme) + 1

    rank = {"start": 0, "normal": 1, "danger": 2}
    return sorted(commands, key=lambda c: (rank[c.kind], readme_position(c), c.name))


# ---------- permissions ----------

def describe_rule(rule: str) -> str:
    """Turn a Claude Code permission rule into plain language.
    'Bash(python3 tools/*)' -> 'Run the command: python3 tools/*'"""
    match = re.match(r"^([A-Za-z]+)(?:\((.*)\))?$", rule.strip())
    if rule.startswith("mcp__"):
        parts = rule.split("__")
        server = parts[1] if len(parts) > 1 else "?"
        tool = parts[2] if len(parts) > 2 else None
        return f"Use the {server} connection" + (f" ({tool})" if tool else "")
    if not match:
        return rule
    tool, arg = match.group(1), match.group(2)
    if arg and arg.endswith(":*"):  # "git push:*" = the command, with anything after it
        arg = arg[:-2] + " …"
    if tool == "Skill":
        return f"Use its skill: {arg}" if arg else "Use its skills"
    if tool == "Bash":
        return f"Run the command: {arg}" if arg else "Run any terminal command"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"Edit files: {arg}" if arg else "Edit files in this folder"
    if tool in ("Read", "Glob", "Grep", "LS"):
        return f"Read files: {arg}" if arg else "Read files in this folder"
    if tool == "WebFetch":
        if arg and arg.startswith("domain:"):
            return f"Open web pages on {arg[len('domain:'):]}"
        return "Open web pages"
    if tool == "WebSearch":
        return "Search the web"
    return rule


def read_permissions(repo: Path) -> AppPermissions:
    """The repo's shared Claude Code permissions (.claude/settings.json).
    settings.local.json is the user's own file, so it is not read here."""
    path = repo / ".claude" / "settings.json"
    if not path.is_file():
        return AppPermissions(found=False)
    try:
        data = json.loads(_read(path))
        perms = data.get("permissions") or {}
        lists = {key: [str(r) for r in perms.get(key) or []] for key in ("allow", "ask", "deny")}
    except (ValueError, AttributeError):
        return AppPermissions(found=True, error="settings.json could not be read (not valid JSON)")
    return AppPermissions(
        found=True,
        allow=_plain(lists["allow"]),
        ask=_plain(lists["ask"]),
        deny=_plain(lists["deny"]),
        raw=lists["allow"] + lists["ask"] + lists["deny"],
    )


def _plain(rules: list[str]) -> list[str]:
    """Plain-language rules without repeats. Repos often list every script
    twice (`python x.py` and `python3 x.py`); the user only needs it once."""
    seen, out = set(), []
    for rule in rules:
        text = re.sub(r"\bpython3\b", "python", describe_rule(rule))
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


# ---------- programs it needs ----------

def read_requirements(repo: Path, files: list[Path], hint_text: str,
                      which: Callable[[str], str | None] | None = None) -> list[AppRequirement]:
    """Programs the repo needs on the machine, with the reason we think so.

    This decides WHICH tools are needed; tools.py knows how to find and
    install each one. `which` replaces the search; tests pass a fake one.
    """
    names = {f.name.lower() for f in files}
    suffixes = {f.suffix.lower() for f in files}
    text = hint_text.lower()
    found: list[AppRequirement] = []

    def add(tool_id, why, optional=False, auto=False):
        found.append(tools.status(tool_id, why, optional=optional, auto=auto, which=which))

    add("git", "Needed to download and update the repo")

    has_python = ".py" in suffixes or names & {"pyproject.toml", "requirements.txt", "setup.py", "pipfile"}
    if has_python:
        # uv downloads the right Python version itself, so this one is automatic.
        add("python", "The repo has Python files (AI-Hub sets this up with uv)", auto=True)

    if names & {"bun.lock", "bun.lockb", "bunfig.toml"} or re.search(r"\bbunx?\b", text):
        add("bun", "The repo uses Bun (a JavaScript runtime) for some of its tools")
    elif "package.json" in names:
        add("node", "The repo has a package.json")

    if suffixes & {".tex", ".cls"} or re.search(r"\b(lua|xe|pdf)latex\b", text):
        add("latex", "Used to make PDF documents")

    if names & {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        add("docker", "The repo can also run in Docker (optional)", optional=True)

    return found


# ---------- putting it together ----------

def _readme(repo: Path) -> str:
    for name in ("README.md", "README.MD", "Readme.md", "readme.md", "README.rst", "README.txt", "README"):
        if (repo / name).is_file():
            return _read(repo / name)
    return ""


def _summary(readme: str) -> str | None:
    """First real paragraph of the README.

    Skips titles, badges, images, notes (> ...), code and navigation bars.
    READMEs often wrap the description in HTML (<p align="center">), so tags
    are stripped rather than the whole block skipped.
    """
    for block in re.split(r"\n\s*\n", readme):
        block = block.strip()
        if not block or block.startswith(("#", ">", "```", "|", "---", "===")):
            continue
        text = " ".join(line.strip() for line in block.split("\n"))
        text = re.sub(r"<img\b[^>]*>", " ", text)                  # HTML images
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # Markdown images
        # Text that isn't inside a link: a nav bar or badge row has almost none.
        outside_links = re.sub(r"<a\b.*?</a>|\[[^\]]*\]\([^)]*\)", " ", text, flags=re.DOTALL)
        outside_links = html.unescape(re.sub(r"<[^>]+>", " ", outside_links))
        if len(re.sub(r"\W", "", outside_links)) < 20:
            continue
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # links -> their text
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))          # other HTML tags
        text = re.sub(r"[*_`]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([.,;:!?])", r"\1", text)  # "Claude Code ." -> "Claude Code."
        if len(text) > 20:
            return text[:300] + ("…" if len(text) > 300 else "")
    return None


def build_manifest(repo: Path, which: Callable[[str], str | None] | None = None) -> AppManifest:
    repo = Path(repo)
    readme = _readme(repo)
    files = list(_walk(repo))

    # Text that may mention needed programs: docs + small helper scripts.
    hint_files = [repo / n for n in ("README.md", "SETUP.md", "INSTALL.md", "CLAUDE.md", "AGENTS.md")]
    hint_files += [f for f in files if f.parent.name in ("tools", "scripts") and f.suffix in (".py", ".sh", ".ps1")]
    hint_text = "\n".join(_read(f) for f in hint_files if f.is_file())

    plugins = find_plugins(repo)
    skills = read_skills(repo, plugins)
    commands = read_commands(repo) + read_plugin_commands(repo, plugins)
    commands += skills_as_commands(skills, readme, taken={c.name for c in commands}, all_skills=not commands)
    commands = order_commands(commands, readme)
    context_files = [n for n in ("CLAUDE.md", "AGENTS.md", "SETUP.md", "README.md") if (repo / n).is_file()]

    # Setup form + which commands can move money (fix 5)
    texts = [(c.name, c.description or "") for c in commands]   # name + description only (see setup.py)
    setup = read_setup(repo, plugins, texts)
    setup.installed_skills = sorted(m.parent.name for m in (repo / ".claude" / "skills").glob(f"*/{MARKER}"))
    for c in commands:
        c.money = c.name in setup.money_commands

    agents = []
    if (repo / ".claude").is_dir() or (repo / "CLAUDE.md").is_file() or plugins:
        agents.append("claude")
    if (repo / "AGENTS.md").is_file() or (repo / ".agents" / "skills").is_dir():
        agents.append("codex")

    if commands:
        # Slash commands are Claude Code's format; a repo whose buttons are all
        # Codex skills is a Codex app.
        tier = 1
        primary = "claude" if any(c.agent == "claude" for c in commands) else "codex"
    elif agents:
        tier, primary = 2, agents[0]
    else:
        tier, primary = 3, None

    return AppManifest(
        name=repo.name,
        summary=_summary(readme),
        tier=tier,
        agents=agents,
        primary_agent=primary,
        commands=commands,
        skills=skills,
        requirements=read_requirements(repo, files, hint_text, which),
        permissions=read_permissions(repo),
        context_files=context_files,
        plugins=[AppPlugin(name=p.name, root=p.root.relative_to(repo.resolve()).as_posix() or ".",
                           description=p.description) for p in plugins],
        setup=setup,
    )

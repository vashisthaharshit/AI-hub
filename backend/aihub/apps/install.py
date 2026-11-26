"""Make a repo's skills visible to Claude Code.

Claude Code loads project skills only from .claude/skills/<name>/SKILL.md
(https://code.claude.com/docs/en/skills). Some repos keep them elsewhere:

  plugin repos (.claude-plugin/)  nothing to copy: runs load the repo with
                                  `claude --plugin-dir`, so ${CLAUDE_PLUGIN_ROOT}
                                  and the plugin's MCP servers keep working
  loose skills                    skills/<name>/SKILL.md or <name>/SKILL.md at
                                  the top, no plugin manifest: copied here into
                                  .claude/skills/<name>/ (the "per project"
                                  install such READMEs describe)

Copies are AI-Hub's: each holds a marker file, is refreshed when the repo is
read again, and is listed in .git/info/exclude (a local-only ignore list,
not the repo's .gitignore) so `git status` stays clean. A folder in
.claude/skills without our marker is the repo's own and is never touched.
"""

import shutil
from pathlib import Path

MARKER = ".aihub-copy"


def loose_skills(repo: Path) -> list[Path]:
    """Skill folders outside .claude/skills, in a repo that isn't a plugin."""
    if (repo / ".claude-plugin").is_dir():
        return []
    found = []
    for pattern in ("skills/*/SKILL.md", "*/SKILL.md"):
        for skill in sorted(repo.glob(pattern)):
            folder = skill.parent
            if folder.name.startswith(".") or folder in found:
                continue
            found.append(folder)
    return found


def _exclude(repo: Path, rel: str) -> None:
    info = repo / ".git" / "info"
    if not (repo / ".git").is_dir():
        return
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    if rel not in lines:
        exclude.write_text("\n".join([*lines, rel]) + "\n", encoding="utf-8")


def install_skills(repo: Path) -> list[str]:
    """Copy loose skills into .claude/skills. Returns the names now installed."""
    repo = repo.resolve()
    target_root = repo / ".claude" / "skills"
    installed = []
    for folder in loose_skills(repo):
        dest = target_root / folder.name
        if dest.exists() and not (dest / MARKER).exists():
            continue  # the repo's own skill with that name: leave it
        if dest.exists():
            shutil.rmtree(dest)  # our old copy: refresh it
        shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (dest / MARKER).write_text(f"Copied by AI-Hub from {folder.relative_to(repo).as_posix()}/\n", encoding="utf-8")
        _exclude(repo, f"/.claude/skills/{folder.name}/")
        installed.append(folder.name)
    return installed

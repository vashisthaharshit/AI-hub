"""A short description of a repo for the planner, made by code (no AI, no tokens).

The planner is a cheap model; it shouldn't explore the repo itself (that's
what Claude Code and Codex are good at, and paying twice wastes tokens). So
it gets this digest instead:

  - the files Git knows about (respects .gitignore), grouped by folder
  - the start of the README
  - the project files that tell the stack (pyproject.toml, package.json...)
  - agent instructions (CLAUDE.md / AGENTS.md), shortened

Kept to ~6k characters (~1.5k tokens).
"""

import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

STACK_FILES = ("pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod",
               "pom.xml", "build.gradle", "Gemfile", "composer.json", "Dockerfile", "docker-compose.yml")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".pytest_cache"}
MAX_FILES_PER_FOLDER = 12
MAX_FILES = 3000


@dataclass
class RepoDigest:
    text: str
    files: set[str] = field(default_factory=set)   # all known files, repo-relative


def list_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=repo, capture_output=True, text=True, timeout=20, check=True).stdout
        files = [line for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        files = []
        for path in repo.rglob("*"):
            if len(files) >= MAX_FILES:
                break
            if path.is_file() and not (set(path.relative_to(repo).parts) & SKIP_DIRS):
                files.append(path.relative_to(repo).as_posix())
    return sorted(files)[:MAX_FILES]


def _start(path: Path, chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:chars] + ("\n…" if len(text) > chars else "")


def _tree(files: list[str]) -> str:
    by_folder: dict[str, list[str]] = defaultdict(list)
    for f in files:
        folder, _, name = f.rpartition("/")
        by_folder[folder or "."].append(name)
    lines = []
    for folder in sorted(by_folder):
        names = by_folder[folder]
        shown = ", ".join(names[:MAX_FILES_PER_FOLDER])
        more = f" (+{len(names) - MAX_FILES_PER_FOLDER} more)" if len(names) > MAX_FILES_PER_FOLDER else ""
        lines.append(f"{folder}/: {shown}{more}")
    return "\n".join(lines)


def repo_digest(repo: Path, max_chars: int = 6000) -> RepoDigest:
    repo = Path(repo)
    files = list_files(repo)
    parts = [f"Repository: {repo.name}", f"{len(files)} files."]
    readme = next((repo / n for n in ("README.md", "README.rst", "README.txt", "README") if (repo / n).is_file()), None)
    if readme:
        parts.append(f"README (start):\n{_start(readme, 1200)}")
    for name in ("CLAUDE.md", "AGENTS.md"):
        if (repo / name).is_file():
            parts.append(f"{name} (start):\n{_start(repo / name, 600)}")
    stack = [n for n in STACK_FILES if (repo / n).is_file()]
    if stack:
        parts.append("Project files: " + ", ".join(stack))
        for name in ("pyproject.toml", "package.json"):
            if name in stack:
                parts.append(f"{name} (start):\n{_start(repo / name, 500)}")
    tree = _tree(files)
    budget = max_chars - sum(len(p) for p in parts) - 100
    if len(tree) > budget > 0:
        tree = tree[:budget] + "\n…"
    parts.append(f"Files by folder:\n{tree}")
    return RepoDigest(text="\n\n".join(parts), files=set(files))

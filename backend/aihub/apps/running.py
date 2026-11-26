"""Running an app's command: what is sent to the agent, with which settings,
and what came out.

  build_prompt   a card's answers -> the text the agent gets
                   Claude Code: "/apply https://..." (its own slash command,
                   plugin ones as /<plugin>:<skill>)
                   Codex: its own skills (.agents/skills) as "$skill ...";
                   for Claude-format commands it gets the command file's
                   instructions with $ARGUMENTS filled in (and the other way
                   round for a Codex skill run with Claude Code)
  context        environment (settings + keys + uv on PATH), plugin folders
                 to load, the approval policy (trusted? money tools?)
  snapshot/diff  which files a turn created or changed in the app's folder,
                 so the UI can say "Your CV is ready -> Open"

Money safety (fix 5) is enforced in RunManager.request_approval with
is_money_tool(): those tools ask the user even in a trusted app, and a
money command makes the whole run ask for everything.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from aihub.apps.guide import fill_argument
from aihub.apps.manifest import parse_frontmatter
from aihub.models import AppCommand, AppInfo, CommandForm

AGENT_IDS = {"claude": "claude-code", "codex": "codex"}  # manifest name -> registry id
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mcpb-cache", ".pytest_cache"}
MAX_FILES = 20_000
MAX_LISTED = 50


class AppRunError(RuntimeError):
    pass


# ---------- the prompt ----------

def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def answers_text(form: CommandForm | None, values: dict[str, str]) -> dict[str, str]:
    """Card answers ready for fill_argument: file fields hold one path per
    line (several files), sent as space-separated, quoted if needed."""
    out = dict(values)
    for f in (form.fields if form else []):
        if f.type == "file" and values.get(f.key):
            out[f.key] = " ".join(_quote(p) for p in values[f.key].split("\n") if p.strip())
    return out


def missing_answers(form: CommandForm | None, values: dict[str, str]) -> list[str]:
    return [f.label for f in (form.fields if form else []) if f.required and not str(values.get(f.key) or "").strip()]


def build_prompt(app: AppInfo, command: AppCommand, form: CommandForm | None, values: dict[str, str],
                 agent: str) -> str:
    missing = missing_answers(form, values)
    if missing:
        raise AppRunError(f"Please fill in: {', '.join(missing)}")
    answers = answers_text(form, values)
    argument = fill_argument(form.argument, answers) if form else str(answers.get("value") or "").strip()
    if agent == "claude-code" and command.agent == "claude":
        return f"{command.invoke or '/' + command.name} {argument}".strip()
    if agent == "codex" and command.from_skill and any(
            s.agent == "codex" and s.name == command.name for s in app.manifest.skills):
        # Codex finds the repo's skills in .agents/skills; `$name` starts one.
        return f"${command.name} {argument}".strip()

    # The other agent: the command's own instructions, with its placeholders filled in.
    try:
        _, body = parse_frontmatter((Path(app.path) / command.source).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        raise AppRunError(f"Couldn't read {command.source}") from None
    words = argument.split()
    body = body.replace("$ARGUMENTS", argument)
    body = re.sub(r"\$([1-9])\b", lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else "", body)
    written_for = "Codex" if command.agent == "codex" else "Claude Code"
    return (f"Follow these instructions. They are this repo's `{command.name}` command "
            f"(written for {written_for}; adapt any {written_for}-only steps).\n\n{body.strip()}")


# ---------- the run's settings ----------

@dataclass
class AppRunContext:
    extra_env: dict[str, str] = field(default_factory=dict)
    plugin_dirs: list[str] = field(default_factory=list)
    trusted: bool = False
    always_ask: list[str] = field(default_factory=list)   # MCP servers that move money
    ask_everything: bool = False                           # a money command: every action asks


def uv_folder() -> str | None:
    """MCP servers are often started with `uvx`, which sits next to uv."""
    try:
        from uv import find_uv_bin
        return str(Path(find_uv_bin()).parent)
    except Exception:  # noqa: BLE001
        return None


def build_context(app: AppInfo, env: dict[str, str], trusted: bool, command: str | None) -> AppRunContext:
    m = app.manifest
    extra = dict(env)
    folder = uv_folder()
    if folder:
        extra["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), folder])
    money_command = bool(command) and command in m.setup.money_commands
    return AppRunContext(
        extra_env=extra,
        plugin_dirs=[str((Path(app.path) / p.root).resolve()) for p in m.plugins],
        trusted=trusted and not money_command,
        always_ask=list(m.setup.always_ask),
        ask_everything=money_command,
    )


def is_money_tool(tool_name: str, always_ask: list[str]) -> bool:
    """mcp__alpaca__place_order, or a plugin's server: mcp__plugin_<plugin>_alpaca__x."""
    return any(re.search(rf"(^mcp__|_){re.escape(server)}__", tool_name) for server in always_ask)


# ---------- what came out ----------

def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """path -> (modified time, size) for every file, skipping heavy folders."""
    out: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack and len(out) < MAX_FILES:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat()
                    out[Path(e.path).relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return out


def diff(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> dict:
    files = []
    for path, (mtime, size) in after.items():
        old = before.get(path)
        if old is None:
            files.append({"path": path, "status": "new", "size": size})
        elif old != (mtime, size):
            files.append({"path": path, "status": "changed", "size": size})
    files.sort(key=lambda f: (f["status"] != "new", f["path"]))
    return {"files": files[:MAX_LISTED], "total": len(files)}


# ---------- opening a file for the user ----------

def open_path(app: AppInfo, rel: str, reveal: bool = False) -> None:
    """Open a file with its usual program, or show it in the file manager."""
    root = Path(app.path).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.exists():
        raise AppRunError("That file isn't in the app's folder.")
    import subprocess
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # noqa: S606 - the user's own file, opened at their request
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])

"""Project memory: what agents learned, so later steps and future tasks look
it up instead of exploring the repo again (fewer tokens).

Two kinds of knowledge, one store (SQLite):

  notes       written by agents: decisions, gotchas, where things are.
              task notes   shared by the steps of one Brain task
              repo notes   task notes that became lasting when the task was
                           KEPT (a discarded task's notes describe code that
                           never landed, so they're deleted)
              Each note remembers its files and a hash of each one. When a
              file changes, the note is shown as "maybe outdated" so agents
              double-check instead of trusting old facts.
  code facts  built by code, never by AI: where each function / class is
              defined and which files use it. Always current (rebuilt from
              the files when they change), free, exact.

Agents reach it two ways:
  - their brief already contains the few notes most relevant to their step
  - tools (brain/memory_mcp.py): find_code, search_notes, add_note, task_status
"""

import ast
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from aihub.brain.digest import list_files
from aihub.db import Database
from aihub.models import MemoryNote

STOPWORDS = set("""a an and are as at be but by for from has have in into is it its of on or that the this to
was were will with you your add make use using should can do does not all any some more new each when what
which where how why step steps file files code""".split())
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"}
MAX_CODE_FILES = 4000
MAX_FILE_BYTES = 400_000


def file_hash(folder: Path, rel: str) -> str | None:
    try:
        return hashlib.sha1((folder / rel).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]{3,}", text.lower()) if w not in STOPWORDS}


PATH_RE = re.compile(r"[\w./-]+\.(?:py|tsx?|jsx?|mjs|css|html|md|json|toml|ya?ml|sql|go|rs|java|rb|php|sh)\b")


# ---------- notes ----------

class Memory:
    def __init__(self, db: Database):
        self.db = db
        self.code = CodeIndex()

    def add(self, *, workspace_id: str | None, task_id: str | None, text: str, files: list[str],
            folder: Path, scope: str = "task") -> MemoryNote | None:
        text = " ".join(text.split())[:600]
        if len(text) < 8:
            return None
        files = sorted({f.strip().strip("`").lstrip("./") for f in [*files, *PATH_RE.findall(text)] if f.strip()})[:10]
        entries = [{"path": f, "hash": file_hash(folder, f)} for f in files]
        note_id = uuid.uuid4().hex[:12]
        self.db.add_note(note_id, workspace_id, task_id, scope, text, entries)
        rows = self.db.notes(task_id=task_id) if task_id else self.db.notes(workspace_id=workspace_id)
        return self._note(next(r for r in rows if r["id"] == note_id), folder)

    def _note(self, row: dict, folder: Path | None) -> MemoryNote:
        stale = []
        if folder is not None:
            for f in row["files"]:
                if f.get("hash") is not None and file_hash(folder, f["path"]) != f["hash"]:
                    stale.append(f["path"])
        return MemoryNote(id=row["id"], workspace_id=row["workspace_id"], task_id=row["task_id"], scope=row["scope"],
                          text=row["text"], files=[f["path"] for f in row["files"]], stale_files=stale,
                          created_at=row["created_at"])

    def notes(self, *, workspace_id: str | None, task_id: str | None = None, folder: Path | None = None,
              scope: str | None = None) -> list[MemoryNote]:
        rows = []
        if scope in (None, "repo") and workspace_id:
            rows += self.db.notes(workspace_id=workspace_id, scope="repo")
        if scope in (None, "task") and task_id:
            rows += self.db.notes(task_id=task_id, scope="task")
        return [self._note(r, folder) for r in rows]

    def search(self, query: str, *, workspace_id: str | None, task_id: str | None, folder: Path | None,
               files: list[str] | None = None, limit: int = 5) -> list[MemoryNote]:
        """Notes that share words or files with the query, best first. Repo notes must match;
        this task's own notes are always included (best matches first)."""
        q = words(query)
        wanted = set(files or [])
        scored = []
        for note in self.notes(workspace_id=workspace_id, task_id=task_id, folder=folder):
            score = len(q & words(note.text)) + 2 * len(wanted & set(note.files))
            if any(f.split("/")[0] == w.split("/")[0] for f in note.files for w in wanted):
                score += 0.5
            if note.scope == "task":
                score += 1.0   # this task's own notes are always relevant to its later steps
            if note.stale_files:
                score -= 0.5
            if score > 0.5:
                scored.append((score, note))
        scored.sort(key=lambda s: s[1].created_at, reverse=True)   # newest first among equal scores
        scored.sort(key=lambda s: s[0], reverse=True)
        return [n for _, n in scored[:limit]]

    def promote(self, task_id: str, folder: Path) -> int:
        """The task was kept: its notes become repo notes (hashes taken from the kept work)."""
        rows = self.db.notes(task_id=task_id, scope="task")
        for r in rows:
            entries = [{"path": f["path"], "hash": file_hash(folder, f["path"])} for f in r["files"]]
            self.db.update_note(r["id"], "repo", entries)
        return len(rows)

    def forget_task(self, task_id: str) -> int:
        """The task was discarded: its notes are about code that never landed."""
        return self.db.delete_notes(task_id=task_id, scope="task")


def format_notes(notes: list[MemoryNote]) -> str:
    lines = []
    for n in notes:
        where = f" (files: {', '.join(n.files)})" if n.files else ""
        stale = f" [maybe outdated: {', '.join(n.stale_files)} changed since]" if n.stale_files else ""
        lines.append(f"- {n.text}{where}{stale}")
    return "\n".join(lines)


# ---------- notes from an agent's final message ----------

STEP_RESULT = re.compile(r"STEP RESULT:\s*(done|blocked)\b[:\s-]*(.*)", re.I)


def parse_report(text: str) -> tuple[str | None, str | None, list[str]]:
    """From the agent's last message: ("done"/"blocked"/None, reason, notes).

    The brief asks agents to end with:
        STEP RESULT: done            (or: STEP RESULT: blocked: <why>)
        NOTES:
        - <something later steps should know>
    """
    status = reason = None
    match = None
    for match in STEP_RESULT.finditer(text or ""):
        pass
    if match:
        status = match.group(1).lower()
        reason = match.group(2).strip().splitlines()[0].strip() if match.group(2).strip() else None
    notes: list[str] = []
    part = re.split(r"^\s*\**NOTES\**:?\s*$", text or "", flags=re.I | re.M)
    if len(part) > 1:
        for line in part[-1].splitlines():
            line = line.strip()
            if line.startswith(("-", "*", "•")):
                notes.append(line.lstrip("-*• ").strip())
            elif line and notes and not STEP_RESULT.match(line):
                continue
    return status, reason, [n for n in notes if n][:8]


# ---------- code facts ----------

@dataclass
class Symbol:
    name: str
    kind: str      # function | class | const | type
    path: str
    line: int


_JS_PATTERNS = [
    (re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M), "function"),
    (re.compile(r"^\s*(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", re.M), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M), "class"),
    (re.compile(r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M), "const"),
    (re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M), "type"),
]


def symbols_in(path: str, text: str) -> list[Symbol]:
    found: list[Symbol] = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(Symbol(node.name, "function", path, node.lineno))
            elif isinstance(node, ast.ClassDef):
                found.append(Symbol(node.name, "class", path, node.lineno))
        return found
    seen: set[tuple[str, int]] = set()
    for pattern, kind in _JS_PATTERNS:
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            if (m.group(1), line) not in seen:
                seen.add((m.group(1), line))
                found.append(Symbol(m.group(1), kind, path, line))
    return found


class CodeIndex:
    """Where things are defined, rebuilt per file when the file changes."""

    def __init__(self) -> None:
        self._files: dict[str, dict[str, tuple[float, list[Symbol], str]]] = {}  # folder -> path -> (mtime, symbols, text)

    def _load(self, folder: Path) -> dict[str, tuple[float, list[Symbol], str]]:
        cache = self._files.setdefault(str(folder), {})
        current = [f for f in list_files(folder) if Path(f).suffix in CODE_EXTS][:MAX_CODE_FILES]
        for gone in set(cache) - set(current):
            del cache[gone]
        for rel in current:
            full = folder / rel
            try:
                stat = full.stat()
            except OSError:
                continue
            if rel in cache and cache[rel][0] == stat.st_mtime:
                continue
            if stat.st_size > MAX_FILE_BYTES:
                cache[rel] = (stat.st_mtime, [], "")
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            cache[rel] = (stat.st_mtime, symbols_in(rel, text), text)
        return cache

    def find(self, folder: Path, name: str, max_uses: int = 12) -> str:
        name = name.strip()
        if not name:
            return "Give the name of a function, class or variable."
        cache = self._load(folder)
        exact = [s for _, syms, _ in cache.values() for s in syms if s.name == name]
        close = [s for _, syms, _ in cache.values() for s in syms
                 if s.name != name and name.lower() in s.name.lower()][:10]
        lines = []
        if exact:
            lines.append("Defined in:")
            lines += [f"- {s.path}:{s.line} ({s.kind} {s.name})" for s in exact]
            word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
            uses = []
            for rel, (_, _, text) in sorted(cache.items()):
                for i, line in enumerate(text.splitlines(), start=1):
                    if word.search(line) and not any(s.path == rel and s.line == i for s in exact):
                        uses.append(f"- {rel}:{i}: {line.strip()[:120]}")
                        break
                if len(uses) >= max_uses:
                    break
            lines.append("Used in:" if uses else "Not used anywhere else.")
            lines += uses
        if close:
            lines.append("Similar names:")
            lines += [f"- {s.name} ({s.kind}) {s.path}:{s.line}" for s in close]
        return "\n".join(lines) if lines else f"Nothing named '{name}' found in this repo's code files."

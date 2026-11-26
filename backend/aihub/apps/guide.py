"""The Guide tab: what a repo does and what you can ask it, for beginners.

Two versions:
  basic  built from the manifest (README summary + command descriptions).
         Instant, free, a bit technical. Shown while the AI one is written,
         or when no AI is available.
  ai     written ONCE by the cheapest model available, saved, and reused
         until the repo changes. Plain language, example requests,
         first steps, what the user must provide, and a simple form per
         command (questions instead of flags, file drop for documents).

Keeping the AI version cheap (the user's goal: fewer tokens than doing it
by hand):
  - the model gets a compact digest, not whole files: the command list
    with one-line descriptions, the notes on what each command accepts,
    and the start of the README (~3-4k tokens)
  - the answer is compact JSON (~1.5-2.5k tokens)
  - the model: the summary model from Settings if one is set (the user
    already picked something cheap there), else the user's own agent with
    its smallest model at the lowest effort (Claude Code -> Haiku)
  - a fingerprint of the digest is saved with the guide; it's only
    rewritten when the digest changes (new commands, new README)
"""

import asyncio
import hashlib
import html
import json
import re
from pathlib import Path

from aihub.agents.registry import AgentRegistry
from aihub.db import Database, now_iso
from aihub.models import (
    AgentStatus, AppCommand, AppGuide, AppInfo, AppManifest, CommandForm, FieldChoice, FormField, GuideExample,
    GuideStep, SummarySettings,
)
from aihub.summarizer import Summarizer, SummaryError

README_CHARS = 3500   # how much of the README the model sees
SETUP_CHARS = 1200    # and of SETUP.md / INSTALL.md
MAX_ANSWER_TOKENS = 3500
GUIDE_VERSION = "4"   # bump when INSTRUCTIONS change, so saved guides get rewritten
FIELD_TYPES = {"text", "long_text", "url", "url_or_text", "file", "choice", "toggle", "number", "date"}

INSTRUCTIONS = """You explain a GitHub repo to a beginner. They will use it through an app that \
has one button per command and a box where they type what they want.

Reply with JSON only (no code fences), in this shape:
{"what_it_does": "2-3 short sentences in plain words, no jargon, say what the user gets",
 "examples": [{"request": "something the user could type, first person, short", "command": "command name or null"}],
 "steps": [{"title": "short step title", "text": "one plain sentence", "command": "command name or null"}],
 "needs": ["something the user must provide or have, e.g. 'An OpenAI API key' or 'Your notes as PDF files'"],
 "commands": {"<command name>": {
    "title": "2-4 word button name in plain words",
    "description": "one plain sentence: what the user gets",
    "fields": [{"key": "snake_case", "label": "a plain question", "type": "text|long_text|url|url_or_text|file|choice|toggle|number|date",
                "required": false, "advanced": false, "placeholder": "an example answer", "help": "short tip or null",
                "choices": [{"value": "exact word the command expects", "label": "plain words"}],
                "value": "toggle only: exact text added when switched on, e.g. --all",
                "file_types": [".pdf", ".docx"], "save_to": "file only: folder in the repo", "multiple": false}],
    "argument": "how answers become the text typed after the command, e.g. \"{company} {role}\"; wrap optional parts in [ ] like \"[--limit {limit}]\"; \"\" if it takes nothing"}}}

Rules: 4-7 examples, most useful first, written the way a person would ask \
(e.g. "Summarize this video: <link>", "Check my portfolio for risks"). 3-5 steps to get started, in order. 0-5 needs. \
Only use command names from the list. Only state facts found in the input; \
these instructions' examples are not about this repo.
For "commands": one entry per command in the list. Ask only for what its input notes say it accepts. \
Never make the user type flags or keywords: use "choice" for keywords and "toggle" or "number" for flags. \
Mark rarely needed fields "advanced": true. \
When a command reads files the user must provide (documents, data, a CV, notes) from a folder named in the input, \
its form MUST start with a "file" field (multiple: true) whose "save_to" is that folder (the most fitting subfolder), \
left out of "argument", so a beginner can drop the files on the card instead of finding the folder. \
A link-or-pasted-text input is "url_or_text". Labels are questions a non-technical person understands."""


class GuideError(RuntimeError):
    pass


# ---------- the digest the model sees ----------

def _plain(text: str) -> str:
    """README text without HTML tags, images, badges and extra blank lines."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<img\b[^>]*>|!\[[^\]]*\]\([^)]*\)", " ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _read_start(path: Path, chars: int) -> str:
    try:
        return _plain(path.read_text(encoding="utf-8", errors="replace"))[:chars]
    except OSError:
        return ""


def input_notes(command_file: Path, max_lines: int = 10) -> list[str]:
    """What a command file says about its input: the lines that mention
    $ARGUMENTS plus the bullet list right after them, e.g.
        `$ARGUMENTS` may contain:
        - Nothing -> rank up to 10 jobs
        - `--all` -> re-rank every job
    That's all the model needs to build a form; the rest of the file isn't sent."""
    try:
        lines = command_file.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []
    notes: list[str] = []
    for i, line in enumerate(lines):
        if "$ARGUMENTS" not in line and not re.search(r"\$[1-9]\b", line):
            continue
        block = [line]
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        while j < len(lines) and lines[j].lstrip().startswith(("- ", "* ")) and len(block) < 9:
            block.append(lines[j])
            j += 1
        for item in block:
            item = item.strip()[:180]
            if item and item not in notes:
                notes.append(item)
        if len(notes) >= max_lines:
            break
    return notes[:max_lines]


def _folder_notes(folder: Path) -> list[str]:
    """Top-level folders that explain themselves (have a README), e.g.
    documents/ with cv/, linkedin/... That's where users put their files."""
    out = []
    try:
        dirs = sorted(d for d in folder.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return out
    for d in dirs:
        readme = d / "README.md"
        if not readme.is_file():
            continue
        subs = sorted(s.name for s in d.iterdir() if s.is_dir() and not s.name.startswith("."))[:12]
        out.append(f"{d.name}/" + (f" (subfolders: {', '.join(subs)})" if subs else "")
                   + f": {_read_start(readme, 300)}")
        if len(out) == 3:
            break
    return out


def digest(app: AppInfo) -> str:
    m = app.manifest
    folder = Path(app.path)
    lines = [f"Repo: {m.name}"]
    if m.summary:
        lines.append(f"Summary: {m.summary}")
    lines.append("Commands (name: what it does [input]):")
    for c in m.commands:
        hint = f"; input: {c.argument_hint}" if c.argument_hint else ""
        lines.append(f"- {c.name}: {c.description or c.title} [{c.input}{hint}]")
        if c.input != "none":
            lines += [f"    {note}" for note in input_notes(folder / c.source)]
    notes = _folder_notes(folder)
    if notes:
        lines += ["", "Folders for the user's files:"] + [f"- {n}" for n in notes]
    readme = next((folder / n for n in ("README.md", "readme.md", "README.rst", "README") if (folder / n).is_file()), None)
    if readme:
        lines += ["", "README (start):", _read_start(readme, README_CHARS)]
    for name in ("SETUP.md", "INSTALL.md"):
        if (folder / name).is_file():
            lines += ["", f"{name} (start):", _read_start(folder / name, SETUP_CHARS)]
            break
    return "\n".join(lines)


def fingerprint(text: str) -> str:
    return hashlib.sha256(f"v{GUIDE_VERSION}\n{text}".encode("utf-8")).hexdigest()[:16]


# ---------- command forms ----------

def _safe_folder(value) -> str | None:
    """A folder inside the repo ("documents/cv"), never outside it."""
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def basic_form(c: AppCommand) -> CommandForm:
    """A form without AI, from the command's input hint. Simple but never
    just an unexplained "Optional" box."""
    if c.input == "none":
        return CommandForm()
    hint = c.argument_hint or ""
    low = hint.lower()
    required = c.input == "required"
    if "url" in low and any(w in low for w in ("text", "description", "paste")):
        ftype, label = "url_or_text", "Link or text"
    elif "file" in low or "path" in low:
        ftype, label = "file", "File"
    elif "url" in low or "link" in low:
        ftype, label = "url", "Link"
    else:
        ftype = "long_text" if required else "text"
        label = (hint[:1].upper() + hint[1:]) if hint else "Anything to add?"
    field = FormField(
        key="value", label=label[:80], type=ftype, required=required, advanced=not required,
        placeholder=hint or None, help=None if required else "You can leave this empty.",
        save_to="uploads" if ftype == "file" else None,
    )
    return CommandForm(fields=[field], argument="{value}")


def parse_form(raw, command: AppCommand) -> CommandForm | None:
    """One command's form from the model's answer, made safe:
    known field types only, unique keys, files saved inside the repo,
    and {placeholders} in `argument` that match real fields."""
    if not isinstance(raw, dict):
        return None
    fields: list[FormField] = []
    for f in raw.get("fields") or []:
        if not isinstance(f, dict):
            continue
        key = re.sub(r"[^a-z0-9_]", "_", str(f.get("key", "")).lower()).strip("_")[:30]
        if not key or not key[0].isalpha() or key in {x.key for x in fields}:
            continue
        ftype = f.get("type") if f.get("type") in FIELD_TYPES else "text"
        choices = [FieldChoice(value=str(ch["value"]).strip(), label=str(ch.get("label") or ch["value"]).strip())
                   for ch in f.get("choices") or [] if isinstance(ch, dict) and str(ch.get("value", "")).strip()]
        if ftype == "choice" and not choices:
            ftype = "text"
        if ftype == "toggle" and not str(f.get("value") or "").strip():
            continue  # a switch that adds nothing is useless
        text = lambda name, n=200: (str(f.get(name)).strip()[:n] or None) if f.get(name) else None  # noqa: E731
        fields.append(FormField(
            key=key, label=(text("label", 80) or key.replace("_", " ").capitalize()), type=ftype,
            required=bool(f.get("required")), advanced=bool(f.get("advanced")) and not f.get("required"),
            placeholder=text("placeholder"), help=text("help"), choices=choices[:12],
            value=text("value", 60) if ftype == "toggle" else None,
            file_types=[str(t).strip() for t in f.get("file_types") or [] if str(t).strip().startswith(".")][:10]
            if ftype == "file" else [],
            save_to=(_safe_folder(f.get("save_to")) or "uploads") if ftype == "file" else None,
            multiple=bool(f.get("multiple")) and ftype == "file",
        ))
    keys = {f.key for f in fields}
    argument = re.sub(r"\{(\w+)\}", lambda m: m.group(0) if m.group(1) in keys else "",
                      str(raw.get("argument") or ""))[:300].strip()
    title = str(raw.get("title") or "").strip()[:40] or None
    description = str(raw.get("description") or "").strip()[:200] or None
    return CommandForm(title=title, description=description, fields=fields[:8], argument=argument)


def fill_argument(template: str, values: dict[str, str]) -> str:
    """Turn a form's answers into the text after the command.

    "{focus} [--limit {limit}]" with {"focus": "data science"} -> "data science"
    with {"focus": "", "limit": "5"} -> "--limit 5". A [ ] part is dropped
    when any field inside it is empty, so no dangling flags.
    """
    def fill(text: str) -> str:
        return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1)) or "").strip(), text)

    def optional(m: re.Match) -> str:
        keys = re.findall(r"\{(\w+)\}", m.group(1))
        return fill(m.group(1)) if keys and all(str(values.get(k) or "").strip() for k in keys) else ""

    return re.sub(r"\s+", " ", fill(re.sub(r"\[([^\[\]]*)\]", optional, template))).strip()


# ---------- basic guide (no AI) ----------

def basic_guide(m: AppManifest, print_: str = "") -> AppGuide:
    usable = [c for c in m.commands if c.kind != "danger"]
    examples = [GuideExample(request=c.description or c.title, command=c.name) for c in usable[:6]]
    start = [c for c in usable if c.kind == "start"]
    rest = [c for c in usable if c.kind != "start"]
    steps = [GuideStep(title=c.title, text=c.description, command=c.name) for c in (start + rest)[:4]]
    return AppGuide(
        what_it_does=m.summary or f"{m.name} is a repo made to be used with an AI agent.",
        examples=examples, steps=steps, needs=[], commands={c.name: basic_form(c) for c in m.commands},
        source="basic", fingerprint=print_, created_at=now_iso(),
    )


# ---------- reading the model's answer ----------

def parse_answer(answer: str, commands: set[str] | list[AppCommand]) -> dict:
    """The JSON object in the model's answer, cleaned up.

    Small models sometimes wrap JSON in ``` fences or add a sentence before
    it, so we take the outermost {...}. Command names that aren't real
    commands become null instead of breaking a button later.
    """
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        raise GuideError("The model didn't answer with JSON.")
    try:
        data = json.loads(answer[start:end + 1])
    except ValueError:
        raise GuideError("The model's JSON couldn't be read.") from None
    if not isinstance(data, dict) or not str(data.get("what_it_does", "")).strip():
        raise GuideError("The model's answer is missing 'what_it_does'.")

    by_name = {c.name: c for c in commands} if commands and not isinstance(next(iter(commands)), str) else {}
    names = set(by_name) or set(commands)

    def command(value) -> str | None:
        name = str(value or "").strip().lstrip("/")
        return name if name in names else None

    examples = [{"request": str(e["request"]).strip(), "command": command(e.get("command"))}
                for e in data.get("examples") or [] if isinstance(e, dict) and str(e.get("request", "")).strip()]
    steps = [{"title": str(s["title"]).strip(), "text": (str(s.get("text") or "").strip() or None),
              "command": command(s.get("command"))}
             for s in data.get("steps") or [] if isinstance(s, dict) and str(s.get("title", "")).strip()]
    needs = [str(n).strip() for n in data.get("needs") or [] if str(n).strip()]
    forms = {}
    raw_forms = data.get("commands") if isinstance(data.get("commands"), dict) else {}
    for name, cmd in by_name.items():  # every command gets a form; the basic one if the model skipped it
        form = parse_form(raw_forms.get(name) or raw_forms.get("/" + name), cmd)
        forms[name] = form if form is not None else basic_form(cmd)
    return {"what_it_does": str(data["what_it_does"]).strip(),
            "examples": examples[:8], "steps": steps[:6], "needs": needs[:6], "commands": forms}


# ---------- which model writes it ----------

_CHEAP_WORDS = ("haiku", "mini", "nano", "flash", "lite", "small")
_LOW_EFFORTS = ("minimal", "low")


def cheapest_settings(agent: AgentStatus) -> SummarySettings:
    """The agent's smallest model at its lowest effort (None = its defaults)."""
    hidden = set(agent.hidden_models)
    models = [m for m in agent.models if m.id not in hidden]
    model = next((m for word in _CHEAP_WORDS for m in models
                  if word in m.id.lower() or word in m.label.lower()), None)
    efforts = model.efforts if model else agent.default_efforts
    effort = next((e for e in _LOW_EFFORTS if e in efforts), None)
    return SummarySettings(provider="agent", agent_id=agent.id, model=model.id if model else None, effort=effort)


async def pick_writer(summarizer: Summarizer, registry: AgentRegistry,
                      hidden: dict[str, set[str]] | None = None) -> SummarySettings | None:
    chosen = summarizer.settings()
    if chosen.provider != "off":
        return chosen
    agents = await registry.scan(False, hidden or {})
    usable = [a for a in agents if a.state in ("ready", "installed")]
    usable.sort(key=lambda a: a.id != "claude-code")  # Claude Code first: Haiku is cheap and good at this
    return cheapest_settings(usable[0]) if usable else None


# ---------- putting it together ----------

class Guides:
    def __init__(self, db: Database, summarizer: Summarizer, registry: AgentRegistry):
        self.db = db
        self.summarizer = summarizer
        self.registry = registry
        self._locks: dict[str, asyncio.Lock] = {}

    def current(self, app: AppInfo) -> AppGuide:
        """The saved AI guide if it still matches the repo, else the basic one."""
        print_ = fingerprint(digest(app))
        saved = self.db.get_app_guide(app.id)
        if saved and saved.fingerprint == print_:
            return saved
        return basic_guide(app.manifest, print_)

    async def write(self, app: AppInfo, force: bool = False) -> AppGuide:
        """Have the cheapest model write the guide, save it and return it."""
        async with self._locks.setdefault(app.id, asyncio.Lock()):  # one writer per app at a time
            text = digest(app)
            print_ = fingerprint(text)
            saved = self.db.get_app_guide(app.id)
            if saved and saved.fingerprint == print_ and not force:
                return saved
            settings = await pick_writer(self.summarizer, self.registry, self.db.hidden_models())
            if settings is None:
                raise GuideError("No AI is available to write the guide. Sign in to Claude Code or Codex, "
                                 "or set a summary model in Settings.")
            try:
                answer = await self.summarizer.complete(INSTRUCTIONS, text, settings, MAX_ANSWER_TOKENS)
            except SummaryError as exc:
                raise GuideError(f"Couldn't write the guide: {exc}") from None
            parsed = parse_answer(answer, app.manifest.commands)
            guide = AppGuide(
                **parsed, source="ai", made_by=self.summarizer.describe(settings),
                fingerprint=print_, created_at=now_iso(),
            )
            self.db.set_app_guide(app.id, guide)
            return guide

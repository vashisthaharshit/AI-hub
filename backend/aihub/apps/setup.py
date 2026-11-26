"""The setup form of an app: what the user must fill in before it works.

Read from the repo itself (no AI, no tokens), the same way for every repo:

  env      environment variables: .env.example / .env.sample, `${VAR}` used
           by MCP servers, and VAR_NAMES the docs call environment variables
  file     config files made from an example: trader-budget/config.example.json
           -> trader/config.json (the target is the path the docs mention,
           else next to the example), one field per setting
  plugin   a Claude Code plugin's own userConfig
  servers  MCP servers (connections to outside services, e.g. a broker)

Two safety rules (fix 5), also generic:
  - safe-mode switches (paper, sandbox, dry_run, test_mode, testnet, demo)
    start in the safe position; turning one off needs a clear confirmation
  - tools that move money (brokers, payments) are listed in `always_ask`:
    every use asks the user, even if they chose "Trust this repo"
"""

import json
import re
from pathlib import Path

from aihub.apps.plugins import PluginInfo
from aihub.models import AppSetup, McpServerInfo, SetupField, SetupFile

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
_EXAMPLE = re.compile(r"^(?P<base>.+?)\.(example|sample|template|dist)\.(?P<ext>json|ya?ml|toml|ini|cfg|txt)$", re.I)
_ENV_EXAMPLE = re.compile(r"^\.env\.(example|sample|template|dist)$|^env\.example$", re.I)
_SECRET = re.compile(r"(api_?key|(^|_)key$|^key$|token|secret|password|passwd|private|credential)", re.I)
_SAFETY = re.compile(r"(^|_)(paper|sandbox|dry_?run|test_?mode|testnet|simulat\w*|demo)($|_)", re.I)
_LIVE = re.compile(r"(^|_)(live|real_?money|production)($|_)", re.I)
_ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_ENV_SKIP = re.compile(r"^(PATH|HOME|USER|SHELL|TERM|PWD|CLAUDE_|ANTHROPIC_|AIHUB_|GITHUB_|CI$|NODE_ENV|PYTHON)", re.I)
# Settings a user should see first even when the example has a value
# (a trading budget, a loss limit...). Everything else with a sensible
# example value goes under "More settings".
_IMPORTANT = re.compile(r"budget|loss|risk|limit", re.I)
_PLACEHOLDER = re.compile(r"your|example|changeme|xxx|<[^>]*>|todo|replace|^$", re.I)
_MONEY_SERVER = re.compile(r"alpaca|broker|trading|stripe|paypal|coinbase|binance|kraken|ibkr|robinhood|schwab|"
                           r"payment|bank|plaid|wallet|exchange", re.I)
# Read on a command's NAME and DESCRIPTION only (its full instructions mention
# all sorts of words: "git checkout" is not a shop checkout).
_MONEY_SKILL = re.compile(r"broker|order[-_ ]?(exec|entry|placement)|payments?\b|"
                          r"place[sd]? (real )?orders?|execut\w* (an? )?(orders?|trades?)|transfer money|buy or sell", re.I)


def humanize(key: str) -> str:
    """'sec_user_agent' / 'ALPACA_API_KEY' / 'maxLoss' -> 'Sec user agent' / 'Alpaca API key' / 'Max loss'."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", key.split(".")[-1]).replace("_", " ").replace("-", " ").lower()
    text = re.sub(r"\b(api|url|id|sec|mcp|ai|llm|sms|pdt)\b", lambda m: m.group(1).upper(), text)
    return text[:1].upper() + text[1:]


def _walk(repo: Path, depth: int = 3):
    stack = [(repo, 0)]
    while stack:
        folder, d = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in _SKIP_DIRS and d < depth and not (e.name.startswith(".") and e.name not in (".claude-plugin",)):
                    stack.append((e, d + 1))
            else:
                yield e


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _sentence_about(name: str, docs: str) -> str | None:
    """The first line in the docs that mentions `name`, as help text."""
    for line in docs.split("\n"):
        if name in line:
            text = re.sub(r"[`*_>#|]", "", line).strip(" -")
            text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
            if len(text) > 15:
                return text[:180]
    return None


def _kind_of(key: str, value=None) -> str:
    if isinstance(value, bool):
        return "toggle"
    if isinstance(value, (int, float)):
        return "number"
    if _SECRET.search(key):
        return "secret"
    if "email" in key.lower():
        return "email"
    return "text"


def _safety(key: str, value) -> tuple[bool, str | None]:
    """(is a safe-mode switch, the safe value)."""
    if _SAFETY.search(key):
        return True, "true"
    if _LIVE.search(key) and (isinstance(value, bool) or str(value).lower() in ("true", "false", "0", "1", "")):
        return True, "false"
    return False, None


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------- environment variables ----------

def _env_examples(repo: Path, files: list[Path], docs: str) -> list[SetupField]:
    fields: dict[str, SetupField] = {}
    for f in files:
        if not _ENV_EXAMPLE.match(f.name):
            continue
        comment = []
        for raw in _read(f).split("\n"):
            line = raw.strip()
            if line.startswith("#"):
                comment.append(line.lstrip("# ").strip())
                continue
            m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m and m.group(1) not in fields:
                name, value = m.group(1), m.group(2).split(" #")[0].strip().strip("\"'")
                inline = m.group(2).split(" #", 1)[1].strip() if " #" in m.group(2) else None
                safe, safe_value = _safety(name, value)
                fields[name] = SetupField(
                    id=f"env:{name}", kind="env", target=name, key=name, label=humanize(name),
                    type="toggle" if safe else _kind_of(name), required=bool(_PLACEHOLDER.search(value)) and not safe,
                    default=safe_value if safe else (None if _PLACEHOLDER.search(value) else value),
                    help=inline or (" ".join(comment)[:180] or None) or _sentence_about(name, docs),
                    safety=safe, safe_value=safe_value,
                )
            comment = []
    return list(fields.values())


def _is_env_var(name: str, line: str) -> bool:
    """Does this line present `name` as an environment variable the USER sets?
    "the `SEC_USER_AGENT` environment variable", "export KEY=...",
    "$env:KEY", "set KEY=". A plain shell variable (TINYTEX_BIN=...) or a
    constant name in code docs doesn't count."""
    n = re.escape(name)
    patterns = (
        rf"{n}\W{{0,4}}(environment|env)[ -]var",
        rf"(environment|env)[ -]variables?\W{{0,6}}{n}\b",
        rf"\bexport\s+{n}\s*=",
        rf"\$env:{n}\b",
        rf"\bset\s+{n}\s*=",
        rf"\bsetx\s+{n}\b",
    )
    return any(re.search(p, line, re.I) for p in patterns)


def _env_from_docs(docs: str) -> list[str]:
    names: list[str] = []
    for line in docs.split("\n"):
        for name in _ENV_NAME.findall(line):
            if (name not in names and len(name) <= 40 and not _ENV_SKIP.match(name)
                    and _is_env_var(name, line)):
                names.append(name)
    return names


# ---------- config files from examples ----------

def _target_for(example: Path, repo: Path, docs: str) -> str:
    base = _EXAMPLE.match(example.name).group("base") + "." + _EXAMPLE.match(example.name).group("ext")
    mentions = re.findall(rf"[\w.\-/]*\b{re.escape(base)}\b", docs)
    for m in sorted(set(mentions), key=lambda s: (-s.count("/"), s)):
        rel = m.strip("./").replace("\\", "/")
        if rel and ".." not in rel.split("/") and not rel.startswith("~"):
            return rel
    return (example.parent / base).relative_to(repo).as_posix()


def _leaves(data, prefix: str = "", depth: int = 0):
    if isinstance(data, dict) and depth < 3:
        for k, v in data.items():
            if str(k).startswith(("_", "$")):
                continue  # comments / schema keys
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k), depth + 1)
    elif not isinstance(data, (dict, list)):
        yield prefix, data


def _config_files(repo: Path, files: list[Path], docs: str) -> tuple[list[SetupFile], list[SetupField]]:
    out_files: list[SetupFile] = []
    out_fields: list[SetupField] = []
    for f in files:
        m = _EXAMPLE.match(f.name)
        if not m or "mcp" in f.name.lower() or "claude_desktop_config" in f.name.lower():
            continue
        target = _target_for(f, repo, docs)
        source = f.relative_to(repo).as_posix()
        editable = m.group("ext").lower() == "json"
        out_files.append(SetupFile(source=source, target=target, editable=editable,
                                   exists=(repo / target).is_file()))
        if not editable:
            continue
        try:
            data = json.loads(_read(f))
        except ValueError:
            continue
        for path, value in list(_leaves(data))[:25]:
            key = path.split(".")[-1]
            safe, safe_value = _safety(key, value)
            kind = "toggle" if safe else _kind_of(key, value)
            placeholder = isinstance(value, str) and bool(_PLACEHOLDER.search(value))
            out_fields.append(SetupField(
                id=f"file:{target}:{path}", kind="file", target=target, key=path, label=humanize(key),
                type=kind, required=placeholder and kind not in ("toggle", "number"),
                advanced=not (placeholder or safe or kind == "secret" or _IMPORTANT.search(key)),
                # a safe-mode switch always starts safe, even if the example has it off
                default=safe_value if safe else _as_text(None if placeholder else value),
                help=_sentence_about(key, docs), safety=safe, safe_value=safe_value,
            ))
    return out_files, out_fields


# ---------- MCP servers ----------

def _var_refs(value) -> list[str]:
    text = json.dumps(value)
    return [n for n in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}", text) if not _ENV_SKIP.match(n)]


def _server(name: str, spec: dict, source: str, plugin: str | None) -> McpServerInfo:
    env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
    needs = list(dict.fromkeys([k for k in env if not _ENV_SKIP.match(k)] + _var_refs(spec)))
    blob = " ".join([name, str(spec.get("command") or ""), " ".join(map(str, spec.get("args") or [])),
                     str(spec.get("url") or "")])
    return McpServerInfo(
        name=name, command=spec.get("command"), args=[str(a) for a in spec.get("args") or []],
        url=spec.get("url"), env=needs, source=source, plugin=plugin, money=bool(_MONEY_SERVER.search(blob)),
        spec=spec,
    )


def _servers(repo: Path, files: list[Path], plugins: list[PluginInfo]) -> list[McpServerInfo]:
    servers: dict[str, McpServerInfo] = {}
    for p in plugins:
        for name, spec in p.mcp_servers.items():
            servers[name] = _server(name, spec, p.root.relative_to(repo.resolve()).as_posix() or ".", p.name)
    for f in files:
        low = f.name.lower()
        if not low.endswith(".json") or not ("mcp" in low or "claude_desktop_config" in low):
            continue
        if f.name == ".mcp.json" and f.parent == repo:
            continue  # the repo's own project config: Claude Code loads it by itself
        try:
            data = json.loads(_read(f))
        except ValueError:
            continue
        block = data.get("mcpServers") if isinstance(data, dict) else None
        for name, spec in (block or {}).items():
            if isinstance(spec, dict) and name not in servers:
                servers[name] = _server(name, spec, f.relative_to(repo).as_posix(), None)
    return list(servers.values())


# ---------- plugin userConfig ----------

_UC_TYPES = {"string": "text", "number": "number", "boolean": "toggle", "directory": "text", "file": "text"}


def _plugin_fields(plugins: list[PluginInfo]) -> list[SetupField]:
    out = []
    for p in plugins:
        for key, spec in p.user_config.items():
            kind = "secret" if spec.get("sensitive") else _UC_TYPES.get(str(spec.get("type")), "text")
            options = [str(o) for o in spec.get("options") or []] if kind == "text" else []
            safe, safe_value = _safety(key, spec.get("default"))
            out.append(SetupField(
                id=f"plugin:{p.name}:{key}", kind="plugin", target=p.name, key=key,
                label=str(spec.get("title") or humanize(key))[:80], type="choice" if options else ("toggle" if safe else kind),
                choices=options, required=bool(spec.get("required")), help=str(spec.get("description") or "")[:200] or None,
                default=_as_text(spec.get("default")) if spec.get("default") is not None else (safe_value if safe else None),
                safety=safe, safe_value=safe_value,
            ))
    return out


# ---------- putting it together ----------

def read_setup(repo: Path, plugins: list[PluginInfo], skill_texts: list[tuple[str, str]]) -> AppSetup:
    """skill_texts: (command name, its description + body) for every button,
    to spot the ones that move money."""
    repo = repo.resolve()
    files = list(_walk(repo))
    doc_files = [f for f in files if f.suffix.lower() in (".md", ".txt") and f.stat().st_size < 300_000]
    docs = "\n".join(_read(f) for f in doc_files)

    fields = _env_examples(repo, files, docs)
    setup_files, file_fields = _config_files(repo, files, docs)
    servers = _servers(repo, files, plugins)
    fields += _plugin_fields(plugins)

    known = {f.key.upper() for f in fields if f.kind == "env"}
    file_keys = {f.key.split(".")[-1].upper() for f in file_fields}
    # env vars that MCP servers need, then ones the docs mention
    # the example value each server gives a variable ("YOUR_KEY_HERE" or a real default)
    examples: dict[str, str] = {}
    for s in servers:
        env = s.spec.get("env") if isinstance(s.spec.get("env"), dict) else {}
        examples.update({k: str(v) for k, v in env.items() if k not in examples})
    for name in [n for s in servers for n in s.env] + _env_from_docs(docs):
        if name.upper() in known or name.upper() in file_keys:
            continue  # same setting already asked (e.g. sec_user_agent in config.json)
        known.add(name.upper())
        safe, safe_value = _safety(name, examples.get(name))
        example = examples.get(name)
        placeholder = example is None or bool(_PLACEHOLDER.search(example)) or bool(_var_refs(example))
        kind = "toggle" if safe else _kind_of(name)
        fields.append(SetupField(
            id=f"env:{name}", kind="env", target=name, key=name, label=humanize(name),
            type=kind, required=placeholder and not safe and name in examples,
            advanced=not (placeholder or safe or kind == "secret"),
            help=_sentence_about(name, docs), safety=safe, safe_value=safe_value,
            default=safe_value if safe else (None if placeholder else example),
        ))
    fields += file_fields

    always_ask = [s.name for s in servers if s.money]
    # name + FIRST SENTENCE only: long descriptions mention brokers and trades in passing
    # ("checks every trade against your limits") without moving money themselves.
    money_commands = [name for name, text in skill_texts
                      if _MONEY_SKILL.search(name) or _MONEY_SKILL.search(re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0])]
    return AppSetup(fields=fields, files=setup_files, servers=servers, always_ask=always_ask,
                    money_commands=money_commands)

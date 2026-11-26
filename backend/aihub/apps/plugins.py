"""Claude Code plugins inside a repo: where their skills, commands and MCP
servers are, following the official layout
(https://code.claude.com/docs/en/plugins/manifest-reference).

A repo can be a plugin itself (.claude-plugin/plugin.json at the top) or a
"marketplace" listing one or more plugins (.claude-plugin/marketplace.json,
each entry with a local `source` folder). Without a manifest a plugin still
works from its default folders, which we look at too:

  skills/<name>/SKILL.md     skills          ("skills" key ADDS folders)
  commands/*.md              commands        ("commands" key REPLACES this)
  .mcp.json                  MCP servers     ("mcpServers" key MERGES into it)
  userConfig (manifest)      values Claude Code asks the user for

When AI-Hub runs a plugin repo, it loads it with `claude --plugin-dir <root>`
for that run only: nothing is installed or copied, and paths like
${CLAUDE_PLUGIN_ROOT} inside the skills keep working. Its commands are then
named /<plugin>:<skill>.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginInfo:
    name: str
    root: Path                                    # absolute
    skill_files: list[Path] = field(default_factory=list)      # SKILL.md files
    command_files: list[tuple[str, Path, dict]] = field(default_factory=list)  # (name, file, extra fields)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    user_config: dict[str, dict] = field(default_factory=dict)
    description: str | None = None


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _inside(root: Path, rel) -> Path | None:
    """A manifest path ("./x", "." ) resolved inside root, or None."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    path = (root / rel.strip()).resolve()
    return path if path.is_relative_to(root.resolve()) and path.exists() else None


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _skills_in(folder: Path) -> list[Path]:
    """A folder holding SKILL.md directly is one skill; otherwise each
    <name>/SKILL.md inside it is a skill."""
    if (folder / "SKILL.md").is_file():
        return [folder / "SKILL.md"]
    return sorted(folder.glob("*/SKILL.md"))


def _mcp_from(value, root: Path) -> dict[str, dict]:
    servers: dict[str, dict] = {}
    for item in _as_list(value):
        if isinstance(item, dict):
            # inline map, or a file's content with an "mcpServers" key
            block = item.get("mcpServers") if isinstance(item.get("mcpServers"), dict) else item
            servers.update({k: v for k, v in block.items() if isinstance(v, dict)})
        elif isinstance(item, str) and item.endswith(".json"):
            path = _inside(root, item)
            if path:
                servers.update(_mcp_from(_json(path), root))
    return servers


def read_plugin(root: Path, entry: dict | None = None, fallback_name: str | None = None) -> PluginInfo:
    """One plugin at `root`. `entry` = its marketplace entry, whose component
    fields combine with plugin.json (entry fields win)."""
    manifest = {**_json(root / ".claude-plugin" / "plugin.json"), **(entry or {})}
    info = PluginInfo(
        name=str(manifest.get("name") or fallback_name or root.name),
        root=root.resolve(),
        description=manifest.get("description") if isinstance(manifest.get("description"), str) else None,
    )

    # skills: default skills/ plus any listed folders
    skill_dirs = [root / "skills"] if (root / "skills").is_dir() else []
    skill_dirs += [p for p in (_inside(root, s) for s in _as_list(manifest.get("skills"))) if p]
    seen: set[Path] = set()
    for folder in skill_dirs:
        for skill in _skills_in(folder):
            if skill.resolve() not in seen:
                seen.add(skill.resolve())
                info.skill_files.append(skill)
    # "a plugin with SKILL.md at its root, no skills/ and no skills key loads as a single skill"
    if not info.skill_files and (root / "SKILL.md").is_file() and "skills" not in manifest:
        info.skill_files.append(root / "SKILL.md")

    # commands: the manifest REPLACES the default commands/ folder
    commands = manifest.get("commands")
    if isinstance(commands, dict):
        for name, spec in commands.items():
            if isinstance(spec, dict) and (path := _inside(root, spec.get("source"))):
                info.command_files.append((str(name), path, spec))
    else:
        sources = _as_list(commands) if commands is not None else (["./commands"] if (root / "commands").is_dir() else [])
        for item in sources:
            path = _inside(root, item)
            if path and path.is_dir():
                info.command_files += [(f.stem, f, {}) for f in sorted(path.glob("*.md"))]
            elif path and path.suffix == ".md":
                info.command_files.append((path.stem, path, {}))

    # MCP servers: .mcp.json first, then the manifest's (later names replace earlier)
    if (root / ".mcp.json").is_file():
        info.mcp_servers.update(_mcp_from(_json(root / ".mcp.json"), root))
    info.mcp_servers.update(_mcp_from(manifest.get("mcpServers"), root))

    if isinstance(manifest.get("userConfig"), dict):
        info.user_config = {k: v for k, v in manifest["userConfig"].items() if isinstance(v, dict)}
    return info


def find_plugins(repo: Path) -> list[PluginInfo]:
    """Plugins this repo provides (a plugin itself and/or a marketplace)."""
    repo = repo.resolve()
    plugins: list[PluginInfo] = []
    roots: set[Path] = set()

    market = _json(repo / ".claude-plugin" / "marketplace.json")
    for entry in market.get("plugins") or []:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        # Only local sources; a GitHub/URL source is another repo, not this one.
        root = _inside(repo, source) if isinstance(source, str) else None
        if root and root.is_dir() and root not in roots:
            roots.add(root)
            fields = {k: v for k, v in entry.items() if k in ("name", "description", "skills", "commands", "mcpServers", "userConfig")}
            plugins.append(read_plugin(root, fields, entry.get("name")))

    if (repo / ".claude-plugin" / "plugin.json").is_file() and repo not in roots:
        plugins.append(read_plugin(repo))
    return plugins

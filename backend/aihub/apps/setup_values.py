"""Saving an app's setup form and applying it.

Where answers go
  secrets (keys, tokens)  the OS keychain, as "app:<app id>:<field id>";
                          never AI-Hub's database, never .mcp.json
  everything else         AI-Hub's database (settings table)

Applying (on every save)
  config files   created from their example; only the fields the form
                 covers are set, anything else the user changed is kept
  .mcp.json      servers found in example configs are added to the repo's
                 .mcp.json with "${NAME}" in place of each value. Claude Code
                 fills those in from the environment AI-Hub gives the run
                 (https://code.claude.com/docs/en/mcp), so keys stay off disk
  environment    run_env() hands env answers + secrets to the agent's process

Safe-mode switches (paper trading, sandbox...) can only be turned off with
confirm_unsafe=True: the UI asks first, in plain words.
"""

import json
from pathlib import Path

from aihub.apps.install import _exclude
from aihub.db import Database
from aihub.models import AppInfo, SetupField, SetupSave, SetupView
from aihub.secrets_store import SecretStore


class SetupError(RuntimeError):
    pass


def _coerce(value: str, like):
    """Store "true"/"5" as true/5 when the example had a bool/number there."""
    if isinstance(like, bool) or value.lower() in ("true", "false"):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(like, (int, float)) and not isinstance(like, bool):
        try:
            return int(value) if float(value).is_integer() and "." not in value else float(value)
        except ValueError:
            return value
    return value


def _get(data, path: str):
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _set(data: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
        if not isinstance(data, dict):
            return
    data[parts[-1]] = value


def _inside(repo: Path, rel: str) -> Path:
    path = (repo / rel).resolve()
    if not path.is_relative_to(repo.resolve()):
        raise SetupError(f"{rel} is outside the app's folder.")
    return path


class SetupValues:
    def __init__(self, db: Database, secrets: SecretStore):
        self.db = db
        self.secrets = secrets

    def _stored(self, app_id: str) -> dict:
        return self.db.get_setting(f"app_setup:{app_id}", {}) or {}

    def _secret_name(self, app_id: str, field: SetupField) -> str:
        return f"app:{app_id}:{field.id}"

    def _value(self, app: AppInfo, field: SetupField, stored: dict) -> str | None:
        if field.type == "secret":
            return self.secrets.get(self._secret_name(app.id, field))
        value = stored.get("values", {}).get(field.id)
        return value if value not in (None, "") else field.default

    # ---------- reading ----------

    def view(self, app: AppInfo) -> SetupView:
        setup = app.manifest.setup
        stored = self._stored(app.id)
        values, secrets_set, missing, unsafe = {}, [], [], []
        current: dict[str, str | None] = {}
        for f in setup.fields:
            value = self._value(app, f, stored)
            current[f.id] = value
            if f.type == "secret":
                if value:
                    secrets_set.append(f.id)
            elif value is not None:
                values[f.id] = value
            if f.required and not value:
                missing.append(f.id)
            if f.safety and value is not None and f.safe_value is not None and value.lower() != f.safe_value:
                unsafe.append(f.id)
        env_values = {f.key: current[f.id] for f in setup.fields if f.kind == "env"}
        ready = {s.name: all(env_values.get(n) for n in s.env) for s in setup.servers}
        return SetupView(setup=setup, values=values, secrets_set=secrets_set, missing=missing,
                         servers_ready=ready, unsafe=unsafe, saved=bool(stored.get("saved")),
                         trusted=self.trusted(app.id))

    def trusted(self, app_id: str) -> bool:
        return bool(self.db.get_setting(f"app_trust:{app_id}", False))

    def set_trusted(self, app_id: str, trusted: bool) -> None:
        self.db.set_setting(f"app_trust:{app_id}", bool(trusted))

    def run_env(self, app: AppInfo) -> dict[str, str]:
        """Environment variables for the agent's process (secrets included)."""
        stored = self._stored(app.id)
        env = {}
        for f in app.manifest.setup.fields:
            if f.kind == "env":
                value = self._value(app, f, stored)
                if value:
                    env[f.key] = value
        return env

    # ---------- saving ----------

    def save(self, app: AppInfo, body: SetupSave) -> SetupView:
        fields = {f.id: f for f in app.manifest.setup.fields}
        stored = self._stored(app.id)
        values = dict(stored.get("values", {}))

        for field_id, raw in body.values.items():
            f = fields.get(field_id)
            if not f:
                continue
            value = (raw or "").strip()
            if f.safety and f.safe_value is not None and value and value.lower() != f.safe_value and not body.confirm_unsafe:
                raise SetupError(f"\"{f.label}\" is a safety switch. Turning it off needs your confirmation.")
            if f.type == "secret":
                name = self._secret_name(app.id, f)
                try:
                    self.secrets.set(name, value) if value else self.secrets.delete(name)
                except RuntimeError as exc:
                    raise SetupError(str(exc)) from None
            elif value:
                values[field_id] = value
            else:
                values.pop(field_id, None)

        self.db.set_setting(f"app_setup:{app.id}", {**stored, "values": values, "saved": True})
        self.apply_files(app)
        self.write_mcp(app)
        return self.view(app)

    def apply_files(self, app: AppInfo) -> list[str]:
        """Create / update the config files. Returns the files written."""
        repo = Path(app.path)
        stored = self._stored(app.id)
        written = []
        for sf in app.manifest.setup.files:
            source, target = _inside(repo, sf.source), _inside(repo, sf.target)
            if not source.is_file():
                continue
            if not sf.editable:
                if not target.exists():  # other formats: copy once, the user edits it
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
                    written.append(sf.target)
                continue
            try:
                example = json.loads(source.read_text(encoding="utf-8"))
                data = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else json.loads(json.dumps(example))
            except ValueError:
                continue  # a broken JSON file is left alone
            changed = not target.is_file()
            for f in app.manifest.setup.fields:
                if f.kind != "file" or f.target != sf.target:
                    continue
                value = self._value(app, f, stored)
                if value is None:
                    continue
                new = _coerce(value, _get(example, f.key))
                if _get(data, f.key) != new:
                    _set(data, f.key, new)
                    changed = True
            if changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                written.append(sf.target)
        return written

    def write_mcp(self, app: AppInfo) -> bool:
        """Add servers from example configs to the repo's .mcp.json, with
        ${NAME} placeholders instead of values. Plugin servers are skipped:
        they load with the plugin."""
        servers = [s for s in app.manifest.setup.servers if not s.plugin]
        if not servers:
            return False
        repo = Path(app.path)
        path = repo / ".mcp.json"
        created = not path.exists()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            return False  # the repo's own file is broken: don't touch it
        block = data.setdefault("mcpServers", {})
        ours = set(self._stored(app.id).get("mcp_written", []))
        changed = False
        for s in servers:
            if s.name in block and s.name not in ours:
                continue  # the repo defines it itself
            spec = json.loads(json.dumps(s.spec))
            if isinstance(spec.get("env"), dict):
                spec["env"] = {k: f"${{{k}}}" for k in spec["env"]}
            if block.get(s.name) != spec:
                block[s.name] = spec
                changed = True
            ours.add(s.name)
        if changed:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            if created:
                _exclude(repo, "/.mcp.json")
        stored = self._stored(app.id)
        self.db.set_setting(f"app_setup:{app.id}", {**stored, "mcp_written": sorted(ours)})
        return changed

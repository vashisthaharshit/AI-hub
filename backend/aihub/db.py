"""SQLite storage.

Main tables:
  runs        one row per run (who, what, state, latest result and metrics)
  run_events  every event of every run, in order (an append-only log)
  apps        connected repos (Apps tab) with their manifest

Keeping the full event log, not just the final state, is called event
sourcing. It lets the UI replay any old run, and later the router and
evaluator can compute anything they need from it.

Uses Python's built-in sqlite3 with plain SQL so you can see exactly what
happens. Calls are quick, so running them on the event loop is fine for one
user; switch to aiosqlite or SQLAlchemy if that ever becomes a bottleneck.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aihub.models import AgentEvent, AppGuide, AppInfo, AppManifest, RunInfo, RunMetrics, RunResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    repo_path     TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    state         TEXT NOT NULL,
    branch        TEXT,
    worktree_path TEXT,
    created_at    TEXT NOT NULL,
    result_json   TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id     TEXT    NOT NULL REFERENCES runs(id),
    seq        INTEGER NOT NULL,   -- 0, 1, 2... order within the run
    kind       TEXT    NOT NULL,
    data_json  TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- Small app settings, stored as JSON (e.g. which summary model to use).
-- API keys are NOT stored here: they go to the OS keychain (secrets_store.py).
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

-- Models hidden from the pickers, e.g. after a run failed because the model
-- needs usage credits the account doesn't have. The user can reset them.
CREATE TABLE IF NOT EXISTS model_flags (
    agent_id   TEXT NOT NULL,
    model      TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, model)
);

-- Connected apps (Apps tab). manifest_json is what manifest.py found in the
-- folder; it is rebuilt on "rescan" and after updates.
CREATE TABLE IF NOT EXISTS apps (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    url           TEXT,
    path          TEXT NOT NULL UNIQUE,
    manifest_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# Columns added after the first version. A tiny "migration": on startup, any
# column that's missing from an existing database is added. Real projects use
# a migration tool (Alembic) once there are many changes; this is enough here.
ADDED_COLUMNS = {
    "base_commit": "TEXT",
    "session_id": "TEXT",
    "supervised": "INTEGER NOT NULL DEFAULT 0",
    "test_command": "TEXT",
    "metrics_json": "TEXT",
    "outcome": "TEXT",
    "model": "TEXT",
    "effort": "TEXT",
    "kind": "TEXT NOT NULL DEFAULT 'task'",
    "app_id": "TEXT",
    "label": "TEXT",
}

# Same idea for the apps table.
ADDED_APP_COLUMNS = {
    "guide_json": "TEXT",   # the AI-written guide (apps/guide.py)
}

# Only these fields may be changed by update_run (protects against SQL injection).
_UPDATABLE = {
    "state", "branch", "worktree_path", "base_commit", "session_id",
    "result", "metrics", "outcome", "model", "effort", "agent_id",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")  # safer concurrent reads/writes
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(runs)")}
        with self.conn:
            for column, sql_type in ADDED_COLUMNS.items():
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {sql_type}")
            app_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(apps)")}
            for column, sql_type in ADDED_APP_COLUMNS.items():
                if column not in app_columns:
                    self.conn.execute(f"ALTER TABLE apps ADD COLUMN {column} {sql_type}")

    def close(self) -> None:
        self.conn.close()

    # ---------- runs ----------

    def insert_run(self, run: RunInfo) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, agent_id, repo_path, prompt, state, branch, worktree_path,"
                " created_at, base_commit, supervised, test_command, model, effort, kind, app_id, label)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run.id, run.agent_id, run.repo_path, run.prompt, run.state, run.branch,
                 run.worktree_path, run.created_at, run.base_commit, int(run.supervised),
                 run.test_command, run.model, run.effort, run.kind, run.app_id, run.label),
            )

    def update_run(self, run_id: str, **fields) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Cannot update columns: {unknown}")
        # Pydantic objects are stored as JSON text.
        for name in ("result", "metrics"):
            if name in fields:
                value = fields.pop(name)
                fields[f"{name}_json"] = value.model_dump_json() if value else None
        sets = ", ".join(f"{col} = ?" for col in fields)
        with self.conn:
            self.conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))

    def get_run(self, run_id: str) -> RunInfo | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, limit: int = 50, kind: str | None = "task", app_id: str | None = None) -> list[RunInfo]:
        """Newest first. Manual mode lists kind="task"; an app lists its own runs."""
        where, args = [], []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if app_id:
            where.append("app_id = ?")
            args.append(app_id)
        sql = "SELECT * FROM runs" + (f" WHERE {' AND '.join(where)}" if where else "")
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
        return [_row_to_run(r) for r in rows]

    def mark_interrupted_runs(self) -> None:
        """On startup: runs still marked running were cut off when AI-Hub stopped."""
        with self.conn:
            self.conn.execute("UPDATE runs SET state = 'failed' WHERE state IN ('running', 'pending')")

    # ---------- events ----------

    def append_event(self, run_id: str, seq: int, event: AgentEvent) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO run_events (run_id, seq, kind, data_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, event.kind, json.dumps(event.data), now_iso()),
            )

    def events(self, run_id: str, after_seq: int = -1) -> list[tuple[int, AgentEvent]]:
        rows = self.conn.execute(
            "SELECT seq, kind, data_json FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after_seq),
        ).fetchall()
        return [(r["seq"], AgentEvent(kind=r["kind"], data=json.loads(r["data_json"]))) for r in rows]

    # ---------- hidden models ----------

    def hide_model(self, agent_id: str, model: str, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO model_flags (agent_id, model, reason, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, model, reason, now_iso()),
            )

    def unhide_models(self, agent_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM model_flags WHERE agent_id = ?", (agent_id,))

    def hidden_models(self) -> dict[str, set[str]]:
        hidden: dict[str, set[str]] = {}
        for row in self.conn.execute("SELECT agent_id, model FROM model_flags"):
            hidden.setdefault(row["agent_id"], set()).add(row["model"])
        return hidden

    # ---------- settings ----------

    def get_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (key, json.dumps(value)),
            )

    def usage_events(self) -> list[tuple[str, dict]]:
        """(agent_id, usage data) for every saved usage event, oldest first.
        Used at startup so the usage bars aren't empty until the next run."""
        rows = self.conn.execute(
            "SELECT r.agent_id, e.data_json FROM run_events e JOIN runs r ON r.id = e.run_id"
            " WHERE e.kind = 'usage' ORDER BY e.created_at, e.seq"
        ).fetchall()
        return [(r["agent_id"], json.loads(r["data_json"])) for r in rows]

    def delete_run(self, run_id: str) -> None:
        with self.conn:  # one transaction: both go, or neither
            self.conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            self.conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    # ---------- apps ----------

    def insert_app(self, app: AppInfo) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO apps (id, name, url, path, manifest_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app.id, app.name, app.url, app.path, app.manifest.model_dump_json(),
                 app.created_at, app.updated_at),
            )

    def update_app_manifest(self, app_id: str, manifest: AppManifest) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE apps SET manifest_json = ?, updated_at = ? WHERE id = ?",
                (manifest.model_dump_json(), now_iso(), app_id),
            )

    def get_app(self, app_id: str) -> AppInfo | None:
        row = self.conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        return _row_to_app(row) if row else None

    def get_app_by_path(self, path: str) -> AppInfo | None:
        row = self.conn.execute("SELECT * FROM apps WHERE path = ?", (path,)).fetchone()
        return _row_to_app(row) if row else None

    def list_apps(self) -> list[AppInfo]:
        rows = self.conn.execute("SELECT * FROM apps ORDER BY created_at DESC, rowid DESC").fetchall()
        return [_row_to_app(r) for r in rows]

    def get_app_guide(self, app_id: str) -> AppGuide | None:
        row = self.conn.execute("SELECT guide_json FROM apps WHERE id = ?", (app_id,)).fetchone()
        return AppGuide.model_validate_json(row["guide_json"]) if row and row["guide_json"] else None

    def set_app_guide(self, app_id: str, guide: AppGuide) -> None:
        with self.conn:
            self.conn.execute("UPDATE apps SET guide_json = ? WHERE id = ?", (guide.model_dump_json(), app_id))

    def delete_app(self, app_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))

    def last_seq(self, run_id: str) -> int:
        row = self.conn.execute("SELECT MAX(seq) AS m FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
        return row["m"] if row["m"] is not None else -1


def _row_to_run(row: sqlite3.Row) -> RunInfo:
    return RunInfo(
        id=row["id"], agent_id=row["agent_id"], repo_path=row["repo_path"],
        prompt=row["prompt"], state=row["state"], created_at=row["created_at"],
        branch=row["branch"], worktree_path=row["worktree_path"],
        base_commit=row["base_commit"], session_id=row["session_id"],
        supervised=bool(row["supervised"]), test_command=row["test_command"],
        result=RunResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
        metrics=RunMetrics.model_validate_json(row["metrics_json"]) if row["metrics_json"] else None,
        outcome=row["outcome"], model=row["model"], effort=row["effort"],
        kind=row["kind"] or "task", app_id=row["app_id"], label=row["label"],
    )


def _row_to_app(row: sqlite3.Row) -> AppInfo:
    return AppInfo(
        id=row["id"], name=row["name"], url=row["url"], path=row["path"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        manifest=AppManifest.model_validate_json(row["manifest_json"]),
    )

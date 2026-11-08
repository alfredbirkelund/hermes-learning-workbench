"""SQLite store for Learning Workbench.

One database per Hermes profile at ``<HERMES_HOME>/plugin-data/learning-workbench/data.db``
(Hermes's ``plugin_db`` helper). The schema is append-oriented: attempts, assessments, contests,
plan revisions, reference revisions and events are inserted and never updated in place. Only a
study's revision counter, an exercise's state and drafts change.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

PLUGIN_NAME = "learning-workbench"
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studies (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    goal TEXT NOT NULL,
    hermes_session_id TEXT,
    created_by TEXT NOT NULL,
    material_title TEXT NOT NULL,
    material_text TEXT NOT NULL,
    material_source TEXT NOT NULL,
    material_origin TEXT NOT NULL,
    material_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS studies_session ON studies(hermes_session_id, status);
CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    title TEXT NOT NULL,
    why TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_revisions (
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    items_json TEXT NOT NULL,
    PRIMARY KEY (study_id, revision)
);
CREATE TABLE IF NOT EXISTS exercises (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    step_id TEXT,
    step_title TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    prompt TEXT NOT NULL,
    lesson TEXT NOT NULL DEFAULT '',
    reasoning_mode TEXT NOT NULL,
    hints_json TEXT NOT NULL,
    state TEXT NOT NULL,
    state_changed_at TEXT NOT NULL,
    UNIQUE (study_id, sequence)
);
CREATE INDEX IF NOT EXISTS exercises_study ON exercises(study_id, state);
CREATE TABLE IF NOT EXISTS exercise_references (
    exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    rubric TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (exercise_id, revision)
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    answer TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    help_class TEXT NOT NULL,
    hints_seen INTEGER NOT NULL,
    solution_seen INTEGER NOT NULL,
    feedback_seen INTEGER NOT NULL,
    reference_revision INTEGER NOT NULL,
    client_request_id TEXT NOT NULL,
    UNIQUE (exercise_id, number),
    UNIQUE (study_id, client_request_id)
);
CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    verdict TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    gap TEXT NOT NULL,
    reference_revision INTEGER NOT NULL,
    responds_to_contest_id TEXT,
    UNIQUE (attempt_id, revision)
);
CREATE TABLE IF NOT EXISTS contests (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    UNIQUE (study_id, client_request_id)
);
CREATE TABLE IF NOT EXISTS drafts (
    exercise_id TEXT PRIMARY KEY REFERENCES exercises(id) ON DELETE CASCADE,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    answer TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    exercise_id TEXT,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_study ON events(study_id, seq);
CREATE TABLE IF NOT EXISTS delete_tokens (
    study_id TEXT PRIMARY KEY REFERENCES studies(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

_initialized: set[str] = set()
_init_lock = threading.Lock()


class StoreTooNew(Exception):
    """The store was written by a newer plugin version. It is left untouched rather than read by older code."""

    code = "store_too_new"

# Additive, in-place migrations: schema version -> statements that bring version-1 data up to it.
# Attempts, checks and events are never rewritten by a migration.
_MIGRATIONS: dict[int, list[str]] = {
    2: ["ALTER TABLE exercises ADD COLUMN lesson TEXT NOT NULL DEFAULT ''"],
}


def _schema_version(conn: sqlite3.Connection) -> Optional[int]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone() is None:
        return None
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else None


def _too_new(found: int) -> StoreTooNew:
    return StoreTooNew(
        f"Your Learning Workbench records were saved by a newer version of the plugin (data format {found}; "
        f"this version reads up to {SCHEMA_VERSION}). Update the plugin to open them. Nothing was changed.")


def _migrate(conn: sqlite3.Connection) -> None:
    """Refuse a newer store before touching it, upgrade an older one in one write transaction, create a new one."""
    found = _schema_version(conn)
    if found is not None and found > SCHEMA_VERSION:
        raise _too_new(found)
    if found is not None and found < SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            found = _schema_version(conn) or 0  # another process may have upgraded it in the meantime
            if found > SCHEMA_VERSION:
                raise _too_new(found)
            for version in range(found + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS.get(version, []):
                    conn.execute(statement)
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    conn.executescript(_SCHEMA)  # idempotent: builds a new store, adds nothing to an up-to-date one
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def _prepare(conn: sqlite3.Connection, key: str) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # explicit BEGIN IMMEDIATE / COMMIT in core
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")
    with _init_lock:
        if key not in _initialized:
            _migrate(conn)
            _initialized.add(key)
    return conn


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the profile's store (or an explicit file, used by tests and exports)."""
    if db_path is not None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return _prepare(conn, str(path.resolve()))
    from plugins.plugin_storage import plugin_data_dir, plugin_db

    key = str((plugin_data_dir(PLUGIN_NAME) / "data.db").resolve())
    return _prepare(plugin_db(PLUGIN_NAME), key)


def data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME)


def store_exists() -> bool:
    """Whether this profile has a store yet. Creates nothing: hooks that fire on every chat turn check this first,
    so people who never study get no plugin files."""
    from hermes_constants import get_hermes_home

    return (get_hermes_home() / "plugin-data" / PLUGIN_NAME / "data.db").exists()

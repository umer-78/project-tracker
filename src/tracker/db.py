"""SQLite access layer.

Plain `sqlite3` rather than an ORM: the schema is small, the queries are the
interesting part, and every statement is parameterised. Foreign keys are on
(SQLite leaves them off by default, which quietly allows orphan rows), and
`WAL` mode keeps reads from blocking writes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'viewer')),
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    lead_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'on_hold', 'done', 'archived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sprints (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    starts_on  TEXT NOT NULL,
    ends_on    TEXT NOT NULL,
    goal       TEXT NOT NULL DEFAULT '',
    CHECK (ends_on >= starts_on)
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sprint_id   INTEGER REFERENCES sprints(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'in_progress', 'review', 'done')),
    priority    TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    estimate_hours REAL NOT NULL DEFAULT 0 CHECK (estimate_hours >= 0),
    spent_hours    REAL NOT NULL DEFAULT 0 CHECK (spent_hours >= 0),
    due_date    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS tasks_project_idx  ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS tasks_assignee_idx ON tasks(assignee_id, status);
CREATE INDEX IF NOT EXISTS tasks_sprint_idx   ON tasks(sprint_id);
CREATE INDEX IF NOT EXISTS activity_task_idx  ON activity(task_id, created_at);
"""


class Database:
    def __init__(self, path: str | Path = "tracker.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn = sqlite3.connect(self.path) if self.path == ":memory:" else None
        if self._memory_conn:
            self._memory_conn.row_factory = sqlite3.Row
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        if self._memory_conn:
            return self._memory_conn
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def cursor(self, commit: bool = False):
        conn = self.connect()
        try:
            yield conn
            if commit:
                conn.commit()
        finally:
            if conn is not self._memory_conn:
                conn.close()

    def migrate(self) -> None:
        with self.cursor(commit=True) as conn:
            conn.executescript(SCHEMA)

    # --------------------------------------------------------------- helpers
    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.cursor() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.cursor(commit=True) as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def log(self, action: str, *, task_id: int | None = None, project_id: int | None = None,
            user_id: int | None = None, detail: str = "") -> None:
        self.execute(
            "INSERT INTO activity (task_id, project_id, user_id, action, detail) VALUES (?, ?, ?, ?, ?)",
            (task_id, project_id, user_id, action, detail),
        )

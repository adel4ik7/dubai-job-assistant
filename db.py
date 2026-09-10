import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Applied',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS ai_usage (
    telegram_id INTEGER NOT NULL,
    usage_day TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (telegram_id, usage_day)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._init()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_user(self, telegram_id: int, username: str | None, first_name: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(telegram_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name
                """,
                (telegram_id, username, first_name),
            )

    def add_resume(self, telegram_id: int, filename: str, file_path: str, extracted_text: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO resumes(telegram_id, filename, file_path, extracted_text)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, filename, file_path, extracted_text),
            )
            return int(cur.lastrowid)

    def latest_resume(self, telegram_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM resumes
                WHERE telegram_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (telegram_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_application(self, telegram_id: int, company: str, role: str, notes: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO applications(telegram_id, company, role, notes)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, company, role, notes),
            )
            return int(cur.lastrowid)

    def list_applications(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM applications
                WHERE telegram_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_application_status(self, telegram_id: int, app_id: int, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE applications SET status=?
                WHERE id=? AND telegram_id=?
                """,
                (status, app_id, telegram_id),
            )
            return cur.rowcount > 0

    def reserve_ai_attempt(self, telegram_id: int, day: str, limit: int) -> bool:
        """Atomically count dispatched attempts, including failures, to bound costs."""
        if limit <= 0:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO ai_usage(telegram_id, usage_day, attempts)
                VALUES (?, ?, 1)
                ON CONFLICT(telegram_id, usage_day) DO UPDATE
                SET attempts=attempts+1 WHERE attempts < ?""",
                (telegram_id, day, limit),
            )
            return cur.rowcount > 0

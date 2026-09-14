import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from datetime import date

from statuses import STATUSES, LEGACY_STATUSES, normalize_status
from vacancy_store import SCHEMA as VACANCY_SCHEMA
from services.job_alerts import SCHEMA as ALERT_SCHEMA
from services.application_tracker import initialize as initialize_tracker
PROFILE_FIELDS = ("full_name", "desired_role", "desired_salary", "current_location",
                  "visa_status", "years_experience", "english_level", "notes")


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cv_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    resume_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS apply_preparations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    origin_kind TEXT NOT NULL,
    origin_id INTEGER NOT NULL,
    role TEXT,
    company TEXT,
    recipient_email TEXT,
    resume_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'prepared',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_id, origin_kind, origin_id)
);

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
    status TEXT NOT NULL DEFAULT 'applied',
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

CREATE TABLE IF NOT EXISTS profiles (
    telegram_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL DEFAULT '',
    desired_role TEXT NOT NULL DEFAULT '',
    desired_salary TEXT NOT NULL DEFAULT '',
    current_location TEXT NOT NULL DEFAULT '',
    visa_status TEXT NOT NULL DEFAULT '',
    years_experience TEXT NOT NULL DEFAULT '',
    english_level TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS active_resumes (
    telegram_id INTEGER PRIMARY KEY,
    resume_id INTEGER NOT NULL
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
        conn.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        conn.execute("PRAGMA secure_delete=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(VACANCY_SCHEMA)
            conn.executescript(ALERT_SCHEMA)
            user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            if "language" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT NULL")
            if "last_name" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT DEFAULT NULL")
            for code, legacy in zip(STATUSES, LEGACY_STATUSES):
                conn.execute("UPDATE applications SET status=? WHERE status=?", (code, legacy))

            columns = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
            for name, kind in {'vacancy_id':'INTEGER', 'selected_cv_id':'INTEGER',
                               'recipient_email':'TEXT', 'email_subject':'TEXT', 'email_body':'TEXT',
                               'applied_at':'TEXT', 'last_contact_at':'TEXT', 'follow_up_at':'TEXT'}.items():
                if name not in columns:
                    conn.execute(f'ALTER TABLE applications ADD COLUMN {name} {kind}')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS application_vacancy_owner ON applications(telegram_id,vacancy_id) WHERE vacancy_id IS NOT NULL')
            for name in ("source", "salary", "date_applied", "vacancy_text", "source_url"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE applications ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                    if name == "date_applied":
                        conn.execute("UPDATE applications SET date_applied=substr(created_at,1,10) WHERE status!='saved'")
            conn.execute("""INSERT OR IGNORE INTO active_resumes(telegram_id,resume_id)
                            SELECT telegram_id, MAX(id) FROM resumes GROUP BY telegram_id""")
            initialize_tracker(conn)
            from services.growth import initialize
            initialize(conn)

    def upsert_user(self, telegram_id: int, username: str | None, first_name: str | None, last_name: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(telegram_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name
                """,
                (telegram_id, username, first_name, last_name),
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
            conn.execute("INSERT OR REPLACE INTO active_resumes VALUES (?, ?)", (telegram_id, cur.lastrowid))
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

    def add_application(self, telegram_id: int, company: str, role: str, notes: str = "", *,
                        status: str = "applied", source: str = "", salary: str = "",
                        date_applied: str | None = None, vacancy_text: str = "", source_url: str = "") -> int:
        status = normalize_status(status)
        if status not in STATUSES:
            raise ValueError("Choose a listed application status.")
        if not company.strip() or not role.strip():
            raise ValueError("Company and role are required.")
        if date_applied is None:
            date_applied = "" if status == "saved" else date.today().isoformat()
        if date_applied:
            if date.fromisoformat(date_applied).isoformat() != date_applied:
                raise ValueError("Use YYYY-MM-DD for date applied.")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO applications(telegram_id, company, role, notes, status, source, salary, date_applied, vacancy_text, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (telegram_id, company.strip(), role.strip(), notes, status, source, salary, date_applied, vacancy_text, source_url),
            )
            return int(cur.lastrowid)

    def list_applications(self, telegram_id: int, limit: int = 20, *, status: str = "", search: str = "", offset: int = 0) -> list[dict[str, Any]]:
        status = normalize_status(status)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM applications
                WHERE telegram_id=?
                  AND (?='' OR status=?)
                  AND (?='' OR instr(casefold(company),casefold(?))>0 OR instr(casefold(role),casefold(?))>0)
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (telegram_id, status, status, search, search, search, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_application_status(self, telegram_id: int, app_id: int, status: str) -> bool:
        status = normalize_status(status)
        if status not in STATUSES:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE applications SET status=?, date_applied=CASE
                  WHEN date_applied='' AND ? NOT IN ('saved', 'withdrawn') THEN ? ELSE date_applied END
                WHERE id=? AND telegram_id=?
                """,
                (status, status, date.today().isoformat(), app_id, telegram_id),
            )
            return cur.rowcount > 0

    def get_profile(self, telegram_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE telegram_id=?", (telegram_id,)).fetchone()
            return dict(row) if row else None

    def save_profile(self, telegram_id: int, **fields) -> None:
        if not fields or any(name not in PROFILE_FIELDS for name in fields):
            raise ValueError("Unknown profile field.")
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO profiles(telegram_id) VALUES (?)", (telegram_id,))
            assignments = ", ".join(f"{name}=?" for name in fields)
            conn.execute(f"UPDATE profiles SET {assignments} WHERE telegram_id=?", (*fields.values(), telegram_id))

    def delete_profile(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE telegram_id=?", (telegram_id,))

    def list_resumes(self, telegram_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("""SELECT r.id,r.filename,r.created_at,
                (a.resume_id=r.id) AS active FROM resumes r
                LEFT JOIN active_resumes a ON a.telegram_id=r.telegram_id
                WHERE r.telegram_id=? ORDER BY r.id DESC LIMIT ? OFFSET ?""", (telegram_id, limit, offset))]

    def get_resume(self, telegram_id: int, resume_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM resumes WHERE id=? AND telegram_id=?", (resume_id, telegram_id)).fetchone()
            return dict(row) if row else None

    def active_resume(self, telegram_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""SELECT r.* FROM resumes r JOIN active_resumes a ON a.resume_id=r.id
                                  AND a.telegram_id=r.telegram_id WHERE r.telegram_id=?""", (telegram_id,)).fetchone()
            return dict(row) if row else None

    def select_resume(self, telegram_id: int, resume_id: int) -> bool:
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM resumes WHERE id=? AND telegram_id=?", (resume_id, telegram_id)).fetchone():
                return False
            conn.execute("INSERT OR REPLACE INTO active_resumes VALUES (?,?)", (telegram_id, resume_id))
            return True

    def delete_resume_record(self, telegram_id: int, resume_id: int) -> bool:
        """Called by privacy service after file cleanup; never deletes another user's CV."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM resumes WHERE telegram_id=? AND id=?", (telegram_id, resume_id))
            conn.execute("DELETE FROM active_resumes WHERE telegram_id=? AND resume_id=?", (telegram_id, resume_id))
            conn.execute("""INSERT OR IGNORE INTO active_resumes SELECT telegram_id,MAX(id)
                            FROM resumes WHERE telegram_id=? GROUP BY telegram_id""", (telegram_id,))
            return cur.rowcount > 0

    def get_application(self, telegram_id: int, app_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM applications WHERE id=? AND telegram_id=?", (app_id, telegram_id)).fetchone()
            return dict(row) if row else None

    def dashboard(self, telegram_id: int) -> dict:
        with self._connect() as conn:
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status,COUNT(*) n FROM applications WHERE telegram_id=? GROUP BY status", (telegram_id,))}
        total = sum(counts.values())
        submitted = sum(counts.get(s, 0) for s in STATUSES if s not in {"saved", "withdrawn"})
        interviews = sum(counts.get(s, 0) for s in ("interview", "test_task", "final_interview"))
        offers, rejections = counts.get("offer", 0), counts.get("rejected", 0)
        return {"total": total, "active": sum(counts.get(s, 0) for s in
                ("applied", "hr_screening", "interview", "test_task", "final_interview")),
                "interviews": interviews, "offers": offers, "rejections": rejections,
                "submitted": submitted, "interview_rate": round(100 * interviews / submitted, 1) if submitted else 0,
                "response_rate": round(100 * sum(counts.get(s,0) for s in ('hr_screening','interview','test_task','final_interview','offer','rejected')) / submitted,1) if submitted else 0,
                "offer_rate": round(100 * offers / submitted, 1) if submitted else 0}

    def resume_paths(self, telegram_id: int) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT file_path FROM resumes WHERE telegram_id=?", (telegram_id,))]

    def other_resume_paths(self, telegram_id: int) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT file_path FROM resumes WHERE telegram_id!=?", (telegram_id,))]

    def path_references(self, path: str, excluding_id: int | None = None) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM resumes WHERE file_path=? AND (? IS NULL OR id!=?)",
                                (path, excluding_id, excluding_id)).fetchone()[0]

    def delete_user_records(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute('DELETE FROM user_saved_vacancies WHERE user_id=?', (telegram_id,))
            for table in ("alert_attempts", "alert_deliveries", "alert_preferences", "apply_preparations", "cv_drafts", "profiles", "active_resumes", "resumes", "applications", "ai_usage", "users"):
                conn.execute(f"DELETE FROM {table} WHERE telegram_id=?", (telegram_id,))

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

    def language_selected(self, telegram_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT language FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            return bool(row and row[0] in {"en", "ru"})

    def get_language(self, telegram_id: int) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT language FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            return row[0] if row and row[0] in {"en", "ru"} else "en"

    def set_language(self, telegram_id: int, language: str) -> None:
        if language not in {"en", "ru"}:
            raise ValueError("Unsupported language")
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES (?)", (telegram_id,))
            conn.execute("UPDATE users SET language=? WHERE telegram_id=?", (language, telegram_id))

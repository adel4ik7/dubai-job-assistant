"""Additive collector storage, shared by the independent bot and collector processes."""
import json
import re
from pathlib import Path


SCHEMA = '''
CREATE TABLE IF NOT EXISTS vacancy_sources (
    id INTEGER PRIMARY KEY,
    telegram_username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    title TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_type TEXT NOT NULL DEFAULT 'telegram_channel',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES vacancy_sources(id),
    source_message_id INTEGER NOT NULL,
    source_url TEXT,
    published_at TEXT,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    combined_text TEXT NOT NULL DEFAULT '',
    role TEXT, company TEXT, location TEXT,
    salary_min REAL, salary_max REAL, salary_currency TEXT,
    experience TEXT, skills_json TEXT, languages_json TEXT,
    email TEXT, phone TEXT, telegram_contact TEXT, application_url TEXT,
    detection_score INTEGER NOT NULL DEFAULT 0,
    detection_status TEXT NOT NULL DEFAULT 'pending',
    content_hash TEXT,
    duplicate_of INTEGER REFERENCES vacancies(id),
    ocr_status TEXT NOT NULL DEFAULT 'not_needed',
    processing_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS vacancies_hash ON vacancies(content_hash);
CREATE INDEX IF NOT EXISTS vacancies_recent ON vacancies(published_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS user_saved_vacancies (
    user_id INTEGER NOT NULL,
    vacancy_id INTEGER NOT NULL REFERENCES vacancies(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, vacancy_id)
);
'''

FIELDS = ('source_url', 'published_at', 'raw_text', 'ocr_text', 'combined_text',
          'role', 'company', 'location', 'salary_min', 'salary_max', 'salary_currency',
          'experience', 'skills_json', 'languages_json', 'email', 'phone',
          'telegram_contact', 'application_url', 'detection_score', 'detection_status',
          'content_hash', 'ocr_status', 'processing_error')


class VacancyStore:
    def __init__(self, database):
        self.db = database
        with self.db._connect() as conn:
            conn.executescript(SCHEMA)

    def add_source(self, username, title=None, enabled=True, source_type='telegram_channel'):
        username = username.removeprefix('@')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{3,31}', username) or source_type != 'telegram_channel':
            raise ValueError('Use a public Telegram channel username.')
        with self.db._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO vacancy_sources(telegram_username,title,enabled,source_type) VALUES (?,?,?,?)',
                         (username.lower(), title or username, int(enabled), source_type))
            return conn.execute('SELECT id FROM vacancy_sources WHERE telegram_username=?', (username,)).fetchone()[0]

    def seed_sources(self, path: Path):
        for source in json.loads(path.read_text(encoding='utf-8')):
            self.add_source(source['telegram_username'], source.get('title'),
                            source.get('enabled', True), source.get('source_type', 'telegram_channel'))

    def sources(self, enabled_only=False):
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM vacancy_sources' +
                    (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY id')]

    def enable_source(self, source_id, enabled):
        with self.db._connect() as conn:
            conn.execute('UPDATE vacancy_sources SET enabled=? WHERE id=?', (int(enabled), source_id))

    def checkpoint(self, source_id, message_id):
        with self.db._connect() as conn:
            conn.execute('UPDATE vacancy_sources SET last_message_id=MAX(last_message_id,?), last_checked_at=CURRENT_TIMESTAMP WHERE id=?',
                         (message_id, source_id))

    def has_message(self, source_id, message_id):
        with self.db._connect() as conn:
            return conn.execute('SELECT 1 FROM vacancies WHERE source_id=? AND source_message_id=?',
                                (source_id, message_id)).fetchone() is not None

    def insert(self, source_id, message_id, **values):
        unknown = set(values) - set(FIELDS)
        if unknown:
            raise ValueError('Unknown vacancy field')
        with self.db._connect() as conn:
            # Serialize hash lookup with insertion across collector instances.
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT id FROM vacancies WHERE source_id=? AND source_message_id=?',
                                    (source_id, message_id)).fetchone()
            if previous:
                return previous[0], False
            duplicate = None
            if values.get('content_hash'):
                row = conn.execute('SELECT id FROM vacancies WHERE content_hash=? AND duplicate_of IS NULL ORDER BY id LIMIT 1',
                                   (values['content_hash'],)).fetchone()
                duplicate = row[0] if row else None
            columns = ['source_id', 'source_message_id', *values, 'duplicate_of']
            cur = conn.execute(f"INSERT INTO vacancies({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                               (source_id, message_id, *values.values(), duplicate))
            return cur.lastrowid, True

    def get(self, vacancy_id):
        with self.db._connect() as conn:
            row = conn.execute('SELECT v.*,s.title AS source_title FROM vacancies v JOIN vacancy_sources s ON s.id=v.source_id WHERE v.id=?',
                               (vacancy_id,)).fetchone()
            return dict(row) if row else None

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
CREATE INDEX IF NOT EXISTS vacancies_duplicate ON vacancies(duplicate_of);
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

    def list(self, *, limit=100, offset=0, search='', location='', salary_min=None,
             source_id=None, days=None, saved_user=None):
        clauses = ["v.detection_status IN ('vacancy','probably_vacancy')", 'v.duplicate_of IS NULL']
        if saved_user is None:
            clauses.append('(s.enabled=1 OR EXISTS(SELECT 1 FROM vacancies d JOIN vacancy_sources ds ON ds.id=d.source_id WHERE d.duplicate_of=v.id AND ds.enabled=1))')
        values = []
        if search:
            clauses.append("(instr(casefold(v.role),casefold(?))>0 OR instr(casefold(v.company),casefold(?))>0 OR instr(casefold(v.combined_text),casefold(?))>0)")
            values.extend([search] * 3)
        if location:
            clauses.append('instr(casefold(v.location),casefold(?))>0')
            values.append(location)
        if salary_min is not None:
            clauses.append("v.salary_currency='AED' AND v.salary_min>=?")
            values.append(salary_min)
        if source_id is not None:
            clauses.append('(v.source_id=? OR EXISTS(SELECT 1 FROM vacancies d WHERE d.duplicate_of=v.id AND d.source_id=?))')
            values.extend([source_id, source_id])
        if days is not None:
            clauses.append("julianday(v.published_at)>=julianday('now', ?)")
            values.append(f'-{int(days)} days')
        if saved_user is not None:
            clauses.append('EXISTS(SELECT 1 FROM user_saved_vacancies u JOIN vacancies saved ON saved.id=u.vacancy_id WHERE (saved.id=v.id OR saved.duplicate_of=v.id) AND u.user_id=?)')
            values.append(saved_user)
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute(
                'SELECT v.*,s.title AS source_title FROM vacancies v JOIN vacancy_sources s ON s.id=v.source_id WHERE ' +
                ' AND '.join(clauses) + ' ORDER BY v.published_at DESC,v.id DESC LIMIT ? OFFSET ?',
                (*values, min(100, max(1, limit)), max(0, offset)))]

    def save(self, user_id, vacancy_id):
        row = self.get(vacancy_id)
        if not row or row['detection_status'] not in {'vacancy', 'probably_vacancy'}:
            return False
        with self.db._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO user_saved_vacancies(user_id,vacancy_id) VALUES (?,?)',
                         (user_id, row['duplicate_of'] or vacancy_id))
        return True

    def unsave(self, user_id, vacancy_id):
        with self.db._connect() as conn:
            conn.execute('DELETE FROM user_saved_vacancies WHERE user_id=? AND vacancy_id IN (SELECT id FROM vacancies WHERE id=? OR duplicate_of=?)',
                         (user_id, vacancy_id, vacancy_id))

    def is_saved(self, user_id, vacancy_id):
        with self.db._connect() as conn:
            return conn.execute('SELECT 1 FROM user_saved_vacancies u JOIN vacancies v ON v.id=u.vacancy_id WHERE u.user_id=? AND (v.id=? OR v.duplicate_of=?)',
                                (user_id, vacancy_id, vacancy_id)).fetchone() is not None

    def stats(self):
        with self.db._connect() as conn:
            return dict(conn.execute("""SELECT
                (SELECT COUNT(*) FROM vacancy_sources) AS sources,
                COUNT(*) AS collected,
                SUM(collected_at>=datetime('now','-1 day')) AS last_24h,
                SUM(ocr_status IN ('processed','empty')) AS ocr_processed,
                SUM(ocr_status='failed') AS ocr_failures,
                SUM(detection_status IN ('vacancy','probably_vacancy')) AS detected,
                SUM(duplicate_of IS NOT NULL) AS duplicates FROM vacancies""").fetchone())

    def retry_candidates(self, limit=100, ocr_only=False):
        condition = "v.ocr_status IN ('failed','unavailable','disabled','empty')" if ocr_only else "v.detection_status='pending' OR v.processing_error IS NOT NULL"
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('SELECT v.* FROM vacancies v JOIN vacancy_sources s ON s.id=v.source_id WHERE s.enabled=1 AND (' + condition + ') ORDER BY v.id LIMIT ?',
                                                 (min(500, max(1, limit)),))]

    def update_pipeline(self, vacancy_id, values):
        if not values or set(values) - set(FIELDS):
            raise ValueError('Unknown pipeline fields')
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT content_hash FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            if not old:
                return
            conn.execute('UPDATE vacancies SET ' + ','.join(k + '=?' for k in values) + ',processing_error=NULL WHERE id=?',
                         (*values.values(), vacancy_id))
            conn.execute('UPDATE vacancies SET duplicate_of=NULL WHERE id=?', (vacancy_id,))
            for fingerprint in {old[0], values.get('content_hash')} - {None}:
                canonical = conn.execute('SELECT MIN(id) FROM vacancies WHERE content_hash=?', (fingerprint,)).fetchone()[0]
                conn.execute('UPDATE vacancies SET duplicate_of=CASE WHEN id=? THEN NULL ELSE ? END WHERE content_hash=?',
                             (canonical, canonical, fingerprint))


def salary_label(vacancy):
    if vacancy.get('salary_min') is None:
        return ''
    low, high = vacancy['salary_min'], vacancy.get('salary_max')
    amount = f'{low:g}' + (f'–{high:g}' if high is not None and high != low else '')
    return amount + (' ' + vacancy['salary_currency'] if vacancy.get('salary_currency') else '')


def application_defaults(vacancy):
    """Prefill a reviewable Saved draft; collecting a post is not an application."""
    return dict(company=vacancy.get('company') or '', role=vacancy.get('role') or '',
                status='saved', source=vacancy.get('source_title') or '', salary=salary_label(vacancy),
                date_applied='', notes='', source_url=vacancy.get('source_url') or '',
                vacancy_text=vacancy['combined_text'])

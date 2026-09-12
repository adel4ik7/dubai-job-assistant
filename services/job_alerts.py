"""Opt-in local outbox. No Telegram calls inside database transactions."""
import json
import re
import time
from datetime import datetime

from services.vacancy_search import relevance_scorer, normalize_search
from services.vacancy_locations import location_matcher

SCHEMA = '''
CREATE TABLE IF NOT EXISTS alert_preferences (
 telegram_id INTEGER PRIMARY KEY, roles TEXT NOT NULL DEFAULT '[]',
 keywords TEXT NOT NULL DEFAULT '[]', location TEXT NOT NULL DEFAULT '',
 uae_only INTEGER NOT NULL DEFAULT 0, salary_min INTEGER,
 enabled INTEGER NOT NULL DEFAULT 0, enabled_at REAL NOT NULL DEFAULT 0,
 after_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alert_deliveries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, vacancy_id INTEGER NOT NULL,
 content_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 created_at REAL NOT NULL, available_at REAL NOT NULL, attempted_at REAL,
 delivered_at REAL, message_id INTEGER,
 UNIQUE(telegram_id,vacancy_id), UNIQUE(telegram_id,content_key)
);
CREATE INDEX IF NOT EXISTS alert_pending ON alert_deliveries(state,available_at);
CREATE INDEX IF NOT EXISTS alert_user_attempts ON alert_deliveries(telegram_id,attempted_at);
CREATE TABLE IF NOT EXISTS alert_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
 delivery_id INTEGER NOT NULL, attempted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS alert_attempt_times ON alert_attempts(telegram_id,attempted_at);
CREATE TABLE IF NOT EXISTS alert_rate_limit (
 id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO alert_rate_limit(id) VALUES(1);
'''


def phrases(value):
    values = list(dict.fromkeys(normalize_search(v) for v in re.split(r'[,;\n]', value) if normalize_search(v)))
    if not 1 <= len(values) <= 12 or any(len(v) > 80 for v in values):
        raise ValueError('invalid_phrases')
    return values


def fresh(vacancy, preferences, now):
    try:
        published = datetime.fromisoformat(vacancy['published_at'].replace('Z', '+00:00'))
        if published.tzinfo is None:
            return False
        timestamp = published.timestamp()
    except (ValueError, TypeError, AttributeError):
        return False
    return (vacancy['id'] > preferences['after_id'] and
            max(preferences['enabled_at'], now - 86400) <= timestamp <= now)


def matching_reasons(preferences, vacancy):
    """Roles/extra aliases are OR; location and minimum AED salary are AND."""
    terms = json.loads(preferences['roles']) + json.loads(preferences['keywords'])
    scores = [relevance_scorer(term)(vacancy['role'], vacancy['skills_json'],
        vacancy['combined_text'], vacancy['ocr_text'], vacancy['raw_text'],
        vacancy['company'], vacancy['location']) for term in terms]
    # More conservative than interactive search: exclude unrelated-role body
    # mentions (45) and company-only evidence (35) from unsolicited alerts.
    score = max(scores, default=0)
    if score < 55:
        return []
    if not location_matcher(preferences['location'], preferences['uae_only'])(
            vacancy['location'], vacancy['combined_text'], vacancy['ocr_text'], vacancy['raw_text']):
        return []
    minimum = preferences['salary_min']
    if minimum is not None and (vacancy['salary_currency'] != 'AED' or
            vacancy['salary_min'] is None or vacancy['salary_min'] < minimum):
        return []
    reasons = ['al_reason_role' if score >= 70 else 'al_reason_keywords']
    if preferences['location']:
        reasons.append('al_reason_location')
    if preferences['uae_only']:
        reasons.append('al_reason_uae')
    if minimum is not None:
        reasons.append('al_reason_salary')
    return reasons


def enqueue_new(conn, vacancy_id, now=None):
    """Called in the vacancy insertion transaction; retries/updates never call it."""
    now = time.time() if now is None else now
    vacancy = dict(conn.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone())
    if vacancy['duplicate_of'] or vacancy['detection_status'] not in {'vacancy', 'probably_vacancy'}:
        return
    source = conn.execute('SELECT enabled FROM vacancy_sources WHERE id=?', (vacancy['source_id'],)).fetchone()
    if not source or not source[0]:
        return
    for row in conn.execute('SELECT * FROM alert_preferences WHERE enabled=1'):
        preferences = dict(row)
        if fresh(vacancy, preferences, now) and matching_reasons(preferences, vacancy):
            conn.execute('''INSERT OR IGNORE INTO alert_deliveries
                (telegram_id,vacancy_id,content_key,created_at,available_at) VALUES(?,?,?,?,?)''',
                (row['telegram_id'], vacancy_id, vacancy['content_hash'] or f'id:{vacancy_id}', now, now))


class JobAlerts:
    def __init__(self, db):
        self.db = db

    def preferences(self, user):
        with self.db._connect() as conn:
            row = conn.execute('SELECT * FROM alert_preferences WHERE telegram_id=?', (user,)).fetchone()
        return dict(row) if row else dict(telegram_id=user, roles='[]', keywords='[]',
            location='', uae_only=0, salary_min=None, enabled=0, enabled_at=0, after_id=0)

    def update(self, user, field, value, now=None):
        if field not in {'roles', 'keywords', 'location', 'uae_only', 'salary_min', 'enabled'}:
            raise ValueError('invalid_field')
        if field in {'roles', 'keywords'}:
            value = json.dumps([] if value == '-' else phrases(value), ensure_ascii=False)
        elif field == 'location':
            value = '' if value == '-' else value.strip()
            if len(value) > 100:
                raise ValueError('invalid_location')
        elif field == 'salary_min':
            value = None if value == '-' else int(value)
            if value is not None and not 0 <= value <= 1000000:
                raise ValueError('invalid_salary')
        else:
            if value not in (0, 1, False, True):
                raise ValueError('invalid_toggle')
            value = int(value)
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO alert_preferences(telegram_id) VALUES(?)', (user,))
            pref = dict(conn.execute('SELECT * FROM alert_preferences WHERE telegram_id=?', (user,)).fetchone())
            pref[field] = value
            if pref['enabled'] and not (json.loads(pref['roles']) or json.loads(pref['keywords'])):
                raise ValueError('roles_required')
            # Every actual settings change starts a new future-only subscription.
            old = conn.execute('SELECT ' + field + ' FROM alert_preferences WHERE telegram_id=?', (user,)).fetchone()[0]
            if old == value:
                return
            conn.execute('UPDATE alert_preferences SET ' + field + '=?,enabled_at=?,after_id=(SELECT COALESCE(MAX(id),0) FROM vacancies) WHERE telegram_id=?', (value, now, user))
            conn.execute("UPDATE alert_deliveries SET state='cancelled' WHERE telegram_id=? AND state='pending'", (user,))

    def claim(self, now=None):
        """Atomic global/per-user rate reservation; stale sending is never retried."""
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE alert_deliveries SET state='unknown' WHERE state='sending' AND attempted_at<?", (now-300,))
            conn.execute("UPDATE alert_deliveries SET state='expired' WHERE state='pending' AND created_at<?", (now-86400,))
            if conn.execute('SELECT next_at FROM alert_rate_limit WHERE id=1').fetchone()[0] > now:
                return None
            rows = conn.execute('''SELECT d.* FROM alert_deliveries d
                JOIN alert_preferences p ON p.telegram_id=d.telegram_id
                WHERE d.state='pending' AND d.available_at<=? AND p.enabled=1
                AND NOT EXISTS(SELECT 1 FROM alert_attempts h WHERE h.telegram_id=d.telegram_id AND h.attempted_at>?)
                AND (SELECT COUNT(*) FROM alert_attempts h WHERE h.telegram_id=d.telegram_id AND h.attempted_at>?)<10
                ORDER BY d.id LIMIT 100''', (now, now-60, now-86400)).fetchall()
            for candidate in rows:
                delivery = dict(candidate)
                pref = dict(conn.execute('SELECT * FROM alert_preferences WHERE telegram_id=?', (delivery['telegram_id'],)).fetchone())
                row = conn.execute('SELECT v.* FROM vacancies v JOIN vacancy_sources s ON s.id=v.source_id WHERE v.id=? AND s.enabled=1', (delivery['vacancy_id'],)).fetchone()
                vacancy = dict(row) if row else None
                if not vacancy or vacancy['duplicate_of'] or vacancy['detection_status'] not in {'vacancy', 'probably_vacancy'} or not fresh(vacancy, pref, now) or not matching_reasons(pref, vacancy):
                    conn.execute("UPDATE alert_deliveries SET state='cancelled' WHERE id=?", (delivery['id'],))
                    continue
                # Hash may have changed after OCR reprocessing. Check both the
                # stored delivery key and current vacancy hashes before sending.
                seen = conn.execute('''SELECT 1 FROM alert_deliveries d JOIN vacancies v ON v.id=d.vacancy_id
                    WHERE d.telegram_id=? AND d.id!=? AND d.attempted_at IS NOT NULL
                    AND (d.content_key=? OR v.content_hash=?)''',
                    (delivery['telegram_id'], delivery['id'], vacancy['content_hash'], vacancy['content_hash'])).fetchone()
                if seen:
                    conn.execute("UPDATE alert_deliveries SET state='cancelled' WHERE id=?", (delivery['id'],))
                    continue
                conn.execute("UPDATE alert_deliveries SET state='sending',attempted_at=? WHERE id=?", (now, delivery['id']))
                conn.execute('INSERT INTO alert_attempts(telegram_id,delivery_id,attempted_at) VALUES(?,?,?)', (delivery['telegram_id'], delivery['id'], now))
                conn.execute('UPDATE alert_rate_limit SET next_at=? WHERE id=1', (now+2,))
                return delivery, pref, vacancy

    def finish(self, delivery, state, *, message_id=None, retry_after=None, now=None):
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            if retry_after is not None:
                delay = max(2, retry_after)
                conn.execute('UPDATE alert_rate_limit SET next_at=MAX(next_at,?) WHERE id=1', (now+delay,))
                conn.execute("""UPDATE alert_deliveries SET state=CASE WHEN EXISTS(
                    SELECT 1 FROM alert_preferences p WHERE p.telegram_id=alert_deliveries.telegram_id
                    AND p.enabled=1 AND p.enabled_at<=alert_deliveries.created_at)
                    THEN 'pending' ELSE 'cancelled' END,available_at=? WHERE id=? AND state='sending'""", (now+delay, delivery['id']))
            else:
                conn.execute("UPDATE alert_deliveries SET state=?,message_id=?,delivered_at=? WHERE id=? AND state='sending'", (state, message_id, now if state == 'sent' else None, delivery['id']))

    def disable_existing(self, user):
        # A late Forbidden response after delete-my-data must not recreate data.
        with self.db._connect() as conn:
            conn.execute('UPDATE alert_preferences SET enabled=0 WHERE telegram_id=?', (user,))
            conn.execute("UPDATE alert_deliveries SET state='cancelled' WHERE telegram_id=? AND state='pending'", (user,))

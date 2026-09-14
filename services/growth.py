"""Local first-user telemetry and moderation. Never stores arbitrary event metadata."""
import json
from datetime import datetime, timezone, timedelta

EVENTS = {'user_started','onboarding_completed','profile_completed','cv_uploaded','cv_created',
          'cv_generated','vacancy_viewed','vacancy_searched','vacancy_saved','vacancy_matched',
          'job_alert_enabled','job_alert_delivered','apply_pack_opened','application_created',
          'application_status_changed','vacancy_shared','user_returned'}
FEEDBACK = ('bug','feature','vacancy','cv','search','other')
REASONS = ('expired','fraud','not_vacancy','incorrect','duplicate','other')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS product_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_name TEXT NOT NULL,
 entity_type TEXT, entity_id INTEGER, metadata_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, event_key TEXT,
 UNIQUE(user_id,event_name,event_key));
CREATE INDEX IF NOT EXISTS product_event_time ON product_events(created_at,event_name);
CREATE INDEX IF NOT EXISTS product_event_user ON product_events(user_id,event_name);
CREATE TABLE IF NOT EXISTS product_activity (
 user_id INTEGER NOT NULL, day TEXT NOT NULL, last_at TEXT NOT NULL, PRIMARY KEY(user_id,day));
CREATE TABLE IF NOT EXISTS onboarding (
 user_id INTEGER PRIMARY KEY, step INTEGER NOT NULL DEFAULT -1, state TEXT NOT NULL DEFAULT 'new');
CREATE TABLE IF NOT EXISTS feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, category TEXT NOT NULL,
 message TEXT NOT NULL, vacancy_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status TEXT NOT NULL DEFAULT 'open', notification_state TEXT NOT NULL DEFAULT 'pending', notice_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS feedback_gate (id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO feedback_gate(id) VALUES(1);
CREATE INDEX IF NOT EXISTS product_activity_last ON product_activity(last_at);
CREATE TABLE IF NOT EXISTS admin_daily_reports (
 day TEXT NOT NULL, admin_id INTEGER NOT NULL, part INTEGER NOT NULL, cutoff TEXT NOT NULL,
 user_ids TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'pending',
 attempted_at REAL, next_at REAL NOT NULL DEFAULT 0, message_id INTEGER,
 PRIMARY KEY(day,admin_id,part));
CREATE TRIGGER IF NOT EXISTS daily_report_delete_user AFTER DELETE ON users BEGIN
 DELETE FROM admin_daily_reports WHERE admin_id=OLD.telegram_id;
 UPDATE admin_daily_reports SET user_ids=(SELECT json_group_array(value)
   FROM json_each(admin_daily_reports.user_ids) WHERE value!=OLD.telegram_id);
END;
CREATE INDEX IF NOT EXISTS feedback_pending ON feedback(notification_state,notice_at);
CREATE TABLE IF NOT EXISTS vacancy_reports (
 user_id INTEGER NOT NULL, vacancy_id INTEGER NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,vacancy_id));
CREATE TABLE IF NOT EXISTS vacancy_moderation (
 vacancy_id INTEGER PRIMARY KEY, hidden INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE VIEW IF NOT EXISTS visible_vacancies AS SELECT v.* FROM vacancies v
 WHERE NOT EXISTS(SELECT 1 FROM vacancy_moderation m JOIN vacancies h ON h.id=m.vacancy_id
 WHERE m.hidden=1 AND (h.id=v.id OR h.id=v.duplicate_of OR
 (h.content_hash IS NOT NULL AND h.content_hash!='' AND h.content_hash=v.content_hash)));
CREATE TRIGGER IF NOT EXISTS growth_new_user AFTER INSERT ON users BEGIN
 INSERT OR IGNORE INTO onboarding(user_id) VALUES(NEW.telegram_id); END;
CREATE TRIGGER IF NOT EXISTS growth_delete_user AFTER DELETE ON users BEGIN
 DELETE FROM product_events WHERE user_id=OLD.telegram_id;
 DELETE FROM product_activity WHERE user_id=OLD.telegram_id;
 DELETE FROM onboarding WHERE user_id=OLD.telegram_id;
 DELETE FROM feedback WHERE user_id=OLD.telegram_id;
 DELETE FROM vacancy_reports WHERE user_id=OLD.telegram_id; END;
'''


def initialize(conn):
    conn.executescript(SCHEMA)
    # Capture successful durable transitions, including legacy commands and Apply Pack.
    triggers = [
        ('cv_created','cv_drafts','INSERT','NEW.telegram_id',"'cv'",'NEW.id','1'),
        ('application_created','applications','INSERT','NEW.telegram_id',"'application'",'NEW.id','1'),
        ('application_status_changed','applications','UPDATE OF status','NEW.telegram_id',"'application'",'NEW.id','OLD.status!=NEW.status'),
        ('vacancy_saved','user_saved_vacancies','INSERT','NEW.user_id',"'vacancy'",'NEW.vacancy_id','1'),
        ('job_alert_enabled','alert_preferences','UPDATE OF enabled','NEW.telegram_id','NULL','NULL','OLD.enabled=0 AND NEW.enabled=1'),
        ('job_alert_delivered','alert_deliveries','UPDATE OF state','NEW.telegram_id',"'vacancy'",'NEW.vacancy_id',"OLD.state!='sent' AND NEW.state='sent'"),
    ]
    for name, table, operation, user, kind, entity, condition in triggers:
        conn.executescript(f'''CREATE TRIGGER IF NOT EXISTS growth_{name} AFTER {operation} ON {table}
         WHEN {condition} AND EXISTS(SELECT 1 FROM users WHERE telegram_id={user}) BEGIN
         INSERT INTO product_events(user_id,event_name,entity_type,entity_id)
         VALUES({user},'{name}',{kind},{entity}); END;''')
    conn.executescript('''CREATE TRIGGER IF NOT EXISTS growth_profile AFTER UPDATE ON profiles
     WHEN NEW.full_name!='' AND NEW.desired_role!='' AND EXISTS(SELECT 1 FROM users WHERE telegram_id=NEW.telegram_id) BEGIN
     INSERT OR IGNORE INTO product_events(user_id,event_name,event_key) VALUES(NEW.telegram_id,'profile_completed','once'); END;''')


def stamp(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


class Growth:
    def __init__(self, db, admin_id=None):
        self.db, self.admin_id = db, admin_id

    def track(self, user, name, entity_type=None, entity_id=None, metadata=None, key=None, now=None):
        if name not in EVENTS or entity_type not in {None,'vacancy','cv','application'}:
            raise ValueError('event')
        if entity_id is not None and (type(entity_id) is not int or entity_id <= 0):
            raise ValueError('entity')
        # Only a closed enum is allowed. No search terms, paths, free text or contacts.
        safe = {}
        if isinstance(metadata, dict) and metadata.get('format') in ('pdf','docx','txt'):
            safe['format'] = metadata['format']
        with self.db._connect() as conn:
            if not conn.execute('SELECT 1 FROM users WHERE telegram_id=?',(user,)).fetchone():
                return
            conn.execute('''INSERT OR IGNORE INTO product_events
              (user_id,event_name,entity_type,entity_id,metadata_json,event_key,created_at) VALUES(?,?,?,?,?,?,?)''',
              (user,name,entity_type,entity_id,json.dumps(safe),key,stamp(now)))

    def touch(self, user, now=None):
        value = stamp(now); day = value[:10]
        with self.db._connect() as conn:
            if not conn.execute('SELECT 1 FROM users WHERE telegram_id=?',(user,)).fetchone():
                return
            prior = conn.execute('SELECT MIN(day) FROM product_activity WHERE user_id=?',(user,)).fetchone()[0]
            conn.execute('INSERT INTO product_activity VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET last_at=excluded.last_at',
                         (user,day,value))
            if prior and prior < day:
                conn.execute('''INSERT OR IGNORE INTO product_events(user_id,event_name,event_key,created_at)
                                VALUES(?,'user_returned',?,?)''',(user,day,value))

    def onboarding(self, user):
        with self.db._connect() as conn:
            row=conn.execute('SELECT * FROM onboarding WHERE user_id=?',(user,)).fetchone()
            return dict(row) if row else None

    def step(self, user, step, state='active'):
        if state not in {'active','paused','completed'} or not -1 <= step <= 7:
            raise ValueError('step')
        with self.db._connect() as conn:
            conn.execute('UPDATE onboarding SET step=?,state=? WHERE user_id=? AND state!=\'completed\'',(step,state,user))
        if state=='completed':
            self.track(user,'onboarding_completed',key='once')

    def feedback(self, user, category, message, vacancy_id=None):
        message=message.strip()
        if category not in FEEDBACK or not 1 <= len(message) <= 2000:
            raise ValueError('feedback')
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM users WHERE telegram_id=?',(user,)).fetchone():
                raise ValueError('user')
            if vacancy_id and not conn.execute('SELECT 1 FROM vacancies WHERE id=?',(vacancy_id,)).fetchone():
                raise ValueError('vacancy')
            if conn.execute("SELECT COUNT(*) FROM feedback WHERE user_id=? AND created_at>=datetime('now','-1 day')",(user,)).fetchone()[0]>=5:
                raise ValueError('limit')
            return conn.execute('INSERT INTO feedback(user_id,category,message,vacancy_id) VALUES(?,?,?,?)',
                                (user,category,message,vacancy_id)).lastrowid

    def report(self, user, vacancy_id, reason):
        if reason not in REASONS:
            raise ValueError('reason')
        with self.db._connect() as conn:
            if not conn.execute('SELECT 1 FROM users WHERE telegram_id=?',(user,)).fetchone():
                raise ValueError('user')
            row=conn.execute('SELECT COALESCE(duplicate_of,id) FROM vacancies WHERE id=?',(vacancy_id,)).fetchone()
            if not row:
                raise ValueError('vacancy')
            conn.execute('INSERT OR IGNORE INTO vacancy_reports(user_id,vacancy_id,reason) VALUES(?,?,?)',(user,row[0],reason))

    def admin(self, user):
        if not self.admin_id or user!=self.admin_id:
            raise PermissionError('admin')

    def hide(self, admin, vacancy_id, hidden):
        self.admin(admin)
        with self.db._connect() as conn:
            if not conn.execute('SELECT 1 FROM vacancies WHERE id=?',(vacancy_id,)).fetchone():
                raise ValueError('vacancy')
            conn.execute('''INSERT INTO vacancy_moderation(vacancy_id,hidden) VALUES(?,?)
              ON CONFLICT(vacancy_id) DO UPDATE SET hidden=excluded.hidden,updated_at=CURRENT_TIMESTAMP''',(vacancy_id,int(hidden)))

    def feedback_rows(self, admin, offset=0):
        self.admin(admin)
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT 5 OFFSET ?",(max(0,offset),))]

    def reports(self, admin, offset=0):
        self.admin(admin)
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('''SELECT v.id,v.role,v.source_id,COUNT(DISTINCT r.user_id) reports,
              COALESCE(m.hidden,0) hidden FROM vacancies v LEFT JOIN vacancy_reports r ON r.vacancy_id=v.id
              LEFT JOIN vacancy_moderation m ON m.vacancy_id=v.id WHERE r.vacancy_id IS NOT NULL OR m.hidden=1
              GROUP BY v.id ORDER BY (COUNT(DISTINCT r.user_id)>=3) DESC,COUNT(DISTINCT r.user_id) DESC,v.id DESC
              LIMIT 5 OFFSET ?''',(max(0,offset),))]

    def stats(self, admin, now=None):
        self.admin(admin)
        now=now or datetime.now(timezone.utc)
        with self.db._connect() as conn:
            result={'total_users':conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]}
            for days, suffix in ((1,'24h'),(7,'7d')):
                cutoff=stamp(now-timedelta(days=days))
                result['new_'+suffix]=conn.execute('SELECT COUNT(*) FROM users WHERE julianday(created_at)>=julianday(?)',(cutoff,)).fetchone()[0]
                result['active_'+suffix]=conn.execute('SELECT COUNT(DISTINCT user_id) FROM product_activity WHERE last_at>=?',(cutoff,)).fetchone()[0]
            result['active_today']=conn.execute('SELECT COUNT(*) FROM product_activity WHERE day=?',(stamp(now)[:10],)).fetchone()[0]
            result['returning']=conn.execute('SELECT COUNT(*) FROM (SELECT user_id FROM product_activity GROUP BY user_id HAVING COUNT(*)>=2)').fetchone()[0]
            result['usage']={r[0]:r[1] for r in conn.execute('SELECT event_name,COUNT(*) FROM product_events GROUP BY event_name')}
            sets={name:{r[0] for r in conn.execute('SELECT DISTINCT user_id FROM product_events WHERE event_name=?',(name,))} for name in EVENTS}
            available={r[0] for r in conn.execute('SELECT DISTINCT telegram_id FROM resumes')}
            stages=[sets['user_started'],sets['onboarding_completed'],available|sets['cv_generated']|sets['cv_uploaded'],
                    sets['vacancy_viewed'],sets['vacancy_saved']|sets['vacancy_matched'],sets['application_created']]
            cohort=set(stages[0]); funnel=[]
            for stage in stages:
                cohort &= stage
                funnel.append(len(cohort))
            result['funnel']=funnel
            result['conversion']=[round(100*n/funnel[0],1) if funnel[0] else 0 for n in funnel]
            return result

    def source_usage(self, admin):
        self.admin(admin)
        with self.db._connect() as conn:
            rows=conn.execute('''SELECT v.source_id,e.event_name,COUNT(DISTINCT e.id) n FROM product_events e
              LEFT JOIN applications a ON e.entity_type='application' AND a.id=e.entity_id
              JOIN vacancies v ON (e.entity_type='vacancy' AND v.id=e.entity_id) OR
              (e.entity_type='application' AND (v.id=a.vacancy_id OR (a.vacancy_id IS NULL AND a.source_url!='' AND v.source_url=a.source_url)))
              WHERE e.event_name IN ('vacancy_viewed','vacancy_saved','application_created')
              GROUP BY v.source_id,e.event_name''')
            return {(r[0],r[1]):r[2] for r in rows}

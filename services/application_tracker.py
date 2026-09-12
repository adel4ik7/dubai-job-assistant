"""Owner-scoped scheduling/history. All timestamps UTC; UI uses Dubai UTC+04."""
import time
from datetime import datetime, timedelta, timezone

DUBAI = timezone(timedelta(hours=4))
ACTIVE = ('applied','hr_screening','interview','test_task','final_interview')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS application_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER NOT NULL,
 event_type TEXT NOT NULL, old_status TEXT, new_status TEXT, note TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);
CREATE INDEX IF NOT EXISTS application_event_order ON application_events(application_id,created_at,id);
CREATE TABLE IF NOT EXISTS application_reminders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER NOT NULL,
 kind TEXT NOT NULL, schedule_key TEXT NOT NULL, due_at REAL NOT NULL,
 available_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 claimed_at REAL, message_id INTEGER,
 UNIQUE(application_id,kind,schedule_key)
);
CREATE INDEX IF NOT EXISTS application_reminder_due ON application_reminders(state,available_at);
CREATE TRIGGER IF NOT EXISTS application_created_event AFTER INSERT ON applications BEGIN
 INSERT INTO application_events(application_id,event_type,new_status) VALUES(NEW.id,'created',NEW.status);
END;
CREATE TRIGGER IF NOT EXISTS application_status_event AFTER UPDATE OF status ON applications
 WHEN OLD.status!=NEW.status BEGIN
 INSERT INTO application_events(application_id,event_type,old_status,new_status) VALUES(NEW.id,'status',OLD.status,NEW.status);
 UPDATE application_reminders SET state='cancelled' WHERE application_id=NEW.id AND state='pending'
 AND NEW.status IN ('offer','rejected','withdrawn');
END;
CREATE TRIGGER IF NOT EXISTS application_cleanup AFTER DELETE ON applications BEGIN
 DELETE FROM application_events WHERE application_id=OLD.id;
 DELETE FROM application_reminders WHERE application_id=OLD.id;
 DELETE FROM apply_preparations WHERE telegram_id=OLD.telegram_id AND origin_kind='application' AND origin_id=OLD.id;
END;
'''


def initialize(conn):
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(applications)')}
    for field in ('interview_at','interview_format','interview_location','interview_notes'):
        if field not in columns:
            conn.execute(f'ALTER TABLE applications ADD COLUMN {field} TEXT')
    conn.executescript(SCHEMA)
    # Snapshot current legacy status, never invent earlier transitions.
    conn.execute("""INSERT INTO application_events(application_id,event_type,new_status)
        SELECT a.id,'snapshot',a.status FROM applications a
        WHERE NOT EXISTS(SELECT 1 FROM application_events e WHERE e.application_id=a.id)""")


def iso(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).isoformat()


def parse_local(value, now=None):
    now = time.time() if now is None else now
    result = datetime.strptime(value.strip(),'%Y-%m-%d %H:%M').replace(tzinfo=DUBAI).timestamp()
    if result <= now:
        raise ValueError('at_invalid_date')
    return result


def display_date(value):
    if not value:
        return ''
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(DUBAI).strftime('%Y-%m-%d %H:%M')


class ApplicationTracker:
    def __init__(self, db):
        self.db = db

    def owned(self, conn, user, app_id):
        row = conn.execute('SELECT * FROM applications WHERE telegram_id=? AND id=?',(user,app_id)).fetchone()
        if not row:
            raise ValueError('application_not_found')
        return dict(row)

    def event(self, conn, app_id, kind, note=None):
        conn.execute('INSERT INTO application_events(application_id,event_type,note) VALUES(?,?,?)',(app_id,kind,note))

    def follow_up(self,user,app_id,due=None,now=None):
        now = time.time() if now is None else now
        if due is not None and due <= now:
            raise ValueError('at_invalid_date')
        value = iso(due) if due is not None else None
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            app = self.owned(conn,user,app_id)
            if due is not None and app['status'] in {'offer','rejected','withdrawn'}:
                raise ValueError('at_closed')
            if app['follow_up_at'] == value:
                return
            conn.execute('UPDATE applications SET follow_up_at=? WHERE id=?',(value,app_id))
            conn.execute("UPDATE application_reminders SET state='cancelled' WHERE application_id=? AND kind='follow_up' AND state='pending'",(app_id,))
            if due is not None:
                conn.execute("""INSERT INTO application_reminders(application_id,kind,schedule_key,due_at,available_at) VALUES(?,'follow_up',?,?,?)
                    ON CONFLICT(application_id,kind,schedule_key) DO UPDATE SET state='pending',available_at=excluded.available_at
                    WHERE application_reminders.state='cancelled' AND application_reminders.claimed_at IS NULL""",(app_id,value,due,due))
            self.event(conn,app_id,'follow_up' if value else 'follow_cancel',value)

    def interview(self,user,app_id,when,format,location='',notes='',now=None):
        now = time.time() if now is None else now
        if when<=now or format not in {'onsite','phone','video'} or len(location)>500 or len(notes)>1000:
            raise ValueError('at_invalid_interview')
        value = iso(when)
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            app = self.owned(conn,user,app_id)
            if app['status'] in {'offer','rejected','withdrawn'}:
                raise ValueError('at_closed')
            if tuple(app[k] for k in ('interview_at','interview_format','interview_location','interview_notes')) == (value,format,location,notes):
                return
            conn.execute('UPDATE applications SET interview_at=?,interview_format=?,interview_location=?,interview_notes=? WHERE id=?', (value,format,location,notes,app_id))
            if app['interview_at'] != value:
                conn.execute("UPDATE application_reminders SET state='cancelled' WHERE application_id=? AND kind IN ('interview_24','interview_2') AND state='pending'",(app_id,))
                for hours in (24,2):
                    due = when-hours*3600
                    if due>now:
                        conn.execute("""INSERT INTO application_reminders(application_id,kind,schedule_key,due_at,available_at) VALUES(?,?,?,?,?)
                            ON CONFLICT(application_id,kind,schedule_key) DO UPDATE SET state='pending',available_at=excluded.available_at
                            WHERE application_reminders.state='cancelled' AND application_reminders.claimed_at IS NULL""",(app_id,f'interview_{hours}',value,due,due))
            self.event(conn,app_id,'interview',value)

    def note(self,user,app_id,value):
        if len(value)>1000:
            raise ValueError('at_invalid_note')
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            app = self.owned(conn,user,app_id)
            if (app['notes'] or '')!=value:
                conn.execute('UPDATE applications SET notes=? WHERE id=?',(value,app_id))
                self.event(conn,app_id,'note',value)

    def replied(self,user,app_id):
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            app = self.owned(conn,user,app_id)
            if app['follow_up_at']:
                conn.execute('UPDATE applications SET follow_up_at=NULL,last_contact_at=? WHERE id=?',(iso(time.time()),app_id))
                conn.execute("UPDATE application_reminders SET state='cancelled' WHERE application_id=? AND kind='follow_up' AND state='pending'",(app_id,))
                self.event(conn,app_id,'reply')

    def delete(self,user,app_id):
        with self.db._connect() as conn:
            self.owned(conn,user,app_id)
            conn.execute('DELETE FROM applications WHERE telegram_id=? AND id=?',(user,app_id))

    def timeline(self,user,app_id,offset=0):
        with self.db._connect() as conn:
            self.owned(conn,user,app_id)
            return [dict(r) for r in conn.execute('SELECT * FROM application_events WHERE application_id=? ORDER BY created_at,id LIMIT 11 OFFSET ?',(app_id,max(0,offset)))]

    def today(self,user,now=None,offset=0):
        now = time.time() if now is None else now
        start = datetime.fromtimestamp(now,DUBAI).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('''SELECT a.*,
              (julianday(interview_at)>=julianday(?) AND julianday(interview_at)<julianday(?)) AS interview_today,
              (julianday(follow_up_at)>=julianday(?) AND julianday(follow_up_at)<julianday(?)) AS follow_today,
              (julianday(follow_up_at)<julianday(?)) AS overdue,
              (a.status!='saved' AND julianday(COALESCE((SELECT MAX(created_at) FROM application_events e WHERE e.application_id=a.id
                AND e.event_type IN ('status','created','snapshot')),a.created_at))<julianday(?)) AS stale
              FROM applications a WHERE telegram_id=? AND status IN ('saved','applied','hr_screening','interview','test_task','final_interview')
              AND (interview_today OR follow_today OR overdue OR stale)
              ORDER BY interview_today DESC,overdue DESC,follow_today DESC,a.id LIMIT 11 OFFSET ?''',
              (iso(start),iso(start+86400),iso(start),iso(start+86400),iso(now),iso(now-14*86400),user,max(0,offset)))]

    def claim(self,now=None):
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE application_reminders SET state='unknown' WHERE state='sending' AND claimed_at<?",(now-300,))
            if conn.execute('SELECT next_at FROM alert_rate_limit WHERE id=1').fetchone()[0]>now:
                return
            rows = conn.execute('''SELECT r.*,a.telegram_id FROM application_reminders r JOIN applications a ON a.id=r.application_id
                WHERE r.state='pending' AND r.available_at<=? AND NOT EXISTS(SELECT 1 FROM application_reminders h
                    JOIN applications ha ON ha.id=h.application_id WHERE ha.telegram_id=a.telegram_id AND h.claimed_at>?)
                ORDER BY r.available_at,r.id LIMIT 100''',(now,now-60)).fetchall()
            for row in rows:
                reminder = dict(row)
                app = self.owned(conn,reminder['telegram_id'],reminder['application_id'])
                scheduled = app['follow_up_at'] if reminder['kind']=='follow_up' else app['interview_at']
                expired = reminder['kind']!='follow_up' and scheduled and datetime.fromisoformat(scheduled).timestamp()<=now
                if reminder['kind']=='interview_24' and scheduled and datetime.fromisoformat(scheduled).timestamp()-now<=7200:
                    expired=True  # If offline across both deadlines, deliver only the 2h reminder.
                if app['status'] in {'offer','rejected','withdrawn'} or scheduled!=reminder['schedule_key'] or expired:
                    conn.execute("UPDATE application_reminders SET state='cancelled' WHERE id=?",(reminder['id'],))
                    continue
                conn.execute("UPDATE application_reminders SET state='sending',claimed_at=? WHERE id=?",(now,reminder['id']))
                conn.execute('UPDATE alert_rate_limit SET next_at=? WHERE id=1',(now+2,))
                return reminder,app

    def finish(self,reminder,state,message_id=None,retry_after=None,now=None):
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            if retry_after is not None:
                available = now+max(2,retry_after)
                conn.execute('UPDATE alert_rate_limit SET next_at=MAX(next_at,?) WHERE id=1',(available,))
                conn.execute("UPDATE application_reminders SET state='pending',available_at=? WHERE id=? AND state='sending'",(available,reminder['id']))
            else:
                conn.execute("UPDATE application_reminders SET state=?,message_id=? WHERE id=? AND state='sending'",(state,message_id,reminder['id']))

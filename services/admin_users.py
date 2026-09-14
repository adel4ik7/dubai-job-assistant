"""Allowlisted local user directory shared by admin UI and trusted local CLI."""
from datetime import datetime, timezone, timedelta
import unicodedata
from services.growth import stamp


QUERY = '''WITH activity AS (
 SELECT user_id,MAX(last_at) last_activity FROM product_activity GROUP BY user_id
), events AS (
 SELECT user_id,COUNT(*) events,
 SUM(event_name='vacancy_searched') searches,
 MIN(CASE WHEN event_name='user_started' THEN created_at END) first_start
 FROM product_events GROUP BY user_id
)
SELECT u.telegram_id,u.username,u.first_name,u.last_name,COALESCE(u.language,'en') language,
 u.created_at registered_at,e.first_start,a.last_activity,
 COALESCE(e.events,0) events,COALESCE(e.searches,0) searches,
 (SELECT COUNT(*) FROM resumes r WHERE r.telegram_id=u.telegram_id) +
 (SELECT COUNT(*) FROM cv_drafts d WHERE d.telegram_id=u.telegram_id AND NOT EXISTS
   (SELECT 1 FROM resumes r WHERE r.id=d.resume_id AND r.telegram_id=u.telegram_id)) cvs,
 (SELECT COUNT(*) FROM applications p WHERE p.telegram_id=u.telegram_id) applications,
 COALESCE((SELECT enabled FROM alert_preferences p WHERE p.telegram_id=u.telegram_id),0) alerts
FROM users u LEFT JOIN activity a ON a.user_id=u.telegram_id LEFT JOIN events e ON e.user_id=u.telegram_id
ORDER BY a.last_activity DESC,u.telegram_id ASC LIMIT ? OFFSET ?'''


def directory(db, offset=0, limit=5, now=None):
    now = now or datetime.now(timezone.utc)
    today = stamp(now.replace(hour=0,minute=0,second=0,microsecond=0))
    end = stamp(now); week = stamp(now-timedelta(days=7))
    with db._connect() as conn:
        counts = {'total':conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]}
        for name, cutoff in (('today',today),('7d',week)):
            counts['new_'+name] = conn.execute('SELECT COUNT(*) FROM users WHERE julianday(created_at) BETWEEN julianday(?) AND julianday(?)',(cutoff,end)).fetchone()[0]
            counts['active_'+name] = conn.execute('''SELECT COUNT(DISTINCT a.user_id) FROM product_activity a
             JOIN users u ON u.telegram_id=a.user_id WHERE a.last_at BETWEEN ? AND ?''',(cutoff,end)).fetchone()[0]
        rows = [dict(r) for r in conn.execute(QUERY,(min(100,max(1,limit)),max(0,offset)))]
    return counts, rows


def clean(value):
    # Telegram identity fields are untrusted: remove terminal controls/newlines.
    return ''.join(c for c in str(value) if not unicodedata.category(c).startswith('C')) if value is not None else 'вЂ”'


def render(counts, rows, tr):
    lines = [tr('au_title'),tr('au_summary',**counts),tr('au_dates')]
    for row in rows:
        safe = {k:clean(v) for k,v in row.items()}
        safe['lang'] = safe.pop('language')
        safe['alerts'] = tr('g_yes' if row['alerts'] else 'g_no')
        lines.append(tr('au_row',**safe))
    if not rows: lines.append(tr('g_empty'))
    return '\n\n'.join(lines)


def print_users(db):
    counts, rows = directory(db,limit=100)
    print('Users: {total} | active today: {active_today} | active 7d: {active_7d} | new today: {new_today} | new 7d: {new_7d}'.format(**counts))
    print('UTC dates; first_start unknown for untracked legacy starts. CVs include unexported drafts.')
    fields = ('telegram_id','username','first_name','last_name','language','first_start','last_activity','events','searches','cvs','applications','alerts')
    print(' | '.join(fields))
    offset = 0
    while rows:
        for row in rows: print(' | '.join(clean(row[k]) for k in fields))
        offset += len(rows)
        _, rows = directory(db,offset,100)

"""22:00 Dubai admin digest. Only activity metadata; persistent per-part delivery."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, Forbidden, BadRequest
from locales import text
from services.growth import stamp, EVENTS
from services.admin_users import clean

# Dubai is UTC+4 with no daylight saving; independent of Windows timezone data.
DUBAI = timezone(timedelta(hours=4))
USER_EVENTS = sorted(EVENTS-{'job_alert_delivered','user_returned'})
STAGES = (
    ('application_status_changed','dr_stage_status'),
    ('application_created','dr_stage_application'),
    ('apply_pack_opened','dr_stage_apply'),
    ('vacancy_matched','dr_stage_match'),
    ('vacancy_saved','dr_stage_save'),
    ('vacancy_viewed','dr_stage_view'),
    ('vacancy_searched','dr_stage_search'),
    ('cv_generated','dr_stage_cv'),
    ('cv_uploaded','dr_stage_cv'),
    ('cv_created','dr_stage_draft'),
    ('onboarding_completed','dr_stage_setup'),
    ('user_started','dr_stage_start'),
)


def bounds(day):
    start=datetime.fromisoformat(day).replace(tzinfo=DUBAI)
    return stamp(start),stamp(start+timedelta(hours=22))


def claim(store, now):
    local=datetime.fromtimestamp(now,DUBAI)
    if local.hour<22 or not store.admin_id:
        return None
    day=local.date().isoformat();start,_=bounds(day);end=stamp(datetime.fromtimestamp(now,timezone.utc))
    with store.db._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM admin_daily_reports WHERE day<?',((local.date()-timedelta(days=7)).isoformat(),))
        conn.execute("UPDATE admin_daily_reports SET state='unknown' WHERE state='sending' AND attempted_at<?",(now-300,))
        if not conn.execute('SELECT 1 FROM admin_daily_reports WHERE day=? AND admin_id=?',(day,store.admin_id)).fetchone():
            # Snapshot participants, not personal content. Background deliveries do not count.
            ids=[r[0] for r in conn.execute('''SELECT a.user_id FROM product_activity a
              JOIN users u ON u.telegram_id=a.user_id WHERE a.last_at>=? AND a.last_at<=?
              GROUP BY a.user_id ORDER BY MAX(a.last_at) DESC,a.user_id''',(start,end))]
            chunks=[[]]+[ids[i:i+5] for i in range(0,len(ids),5)]
            for part, users in enumerate(chunks):
                conn.execute('INSERT INTO admin_daily_reports(day,admin_id,part,cutoff,user_ids) VALUES(?,?,?,?,?)',
                             (day,store.admin_id,part,end,json.dumps(users)))
        if conn.execute('SELECT next_at FROM alert_rate_limit WHERE id=1').fetchone()[0]>now:
            return None
        row=conn.execute("SELECT * FROM admin_daily_reports WHERE day=? AND admin_id=? AND state='pending' AND next_at<=? ORDER BY part LIMIT 1",
                         (day,store.admin_id,now)).fetchone()
        if not row: return None
        conn.execute("UPDATE admin_daily_reports SET state='sending',attempted_at=? WHERE day=? AND admin_id=? AND part=?",
                     (now,day,store.admin_id,row['part']))
        conn.execute('UPDATE alert_rate_limit SET next_at=? WHERE id=1',(now+2,))
        return dict(row)


def render_report(store, row):
    language=store.db.get_language(row['admin_id'])
    tr=lambda key,**kw:text(language,key,**kw)
    start,_=bounds(row['day']);end=row['cutoff']
    local_end=datetime.fromisoformat(end).replace(tzinfo=timezone.utc).astimezone(DUBAI)
    lines=[tr('dr_title',day=row['day'],until=local_end.strftime('%H:%M'))]
    with store.db._connect() as conn:
        if row['part']==0:
            active=conn.execute('''SELECT COUNT(DISTINCT j.value) FROM admin_daily_reports r,
             json_each(r.user_ids) j JOIN users u ON u.telegram_id=j.value
             WHERE r.day=? AND r.admin_id=?''',(row['day'],row['admin_id'])).fetchone()[0]
            new=conn.execute('SELECT COUNT(*) FROM users WHERE julianday(created_at) BETWEEN julianday(?) AND julianday(?)',(start,end)).fetchone()[0]
            counts={r[0]:r[1] for r in conn.execute('''SELECT event_name,COUNT(*) FROM product_events e
              JOIN users u ON u.telegram_id=e.user_id WHERE e.created_at BETWEEN ? AND ? GROUP BY event_name''',(start,end))}
            actions=sum(counts.get(k,0) for k in USER_EVENTS)
            lines.append(tr('dr_summary',active=active,new=new,actions=actions))
            for name in USER_EVENTS+['job_alert_delivered']:
                if counts.get(name): lines.append(tr('dr_event_'+name)+': '+str(counts[name]))
            lines.append(tr('dr_scope'))
        else:
            for uid in json.loads(row['user_ids']):
                user=conn.execute('SELECT username,first_name FROM users WHERE telegram_id=?',(uid,)).fetchone()
                if not user: continue
                counts={r[0]:r[1] for r in conn.execute('SELECT event_name,COUNT(*) FROM product_events WHERE user_id=? AND created_at BETWEEN ? AND ? GROUP BY event_name',(uid,start,end))}
                identity='@'+clean(user['username'])[:40] if user['username'] else clean(user['first_name'])[:40]
                lines.append(f'{identity} · ID: {uid}')
                actions=[tr('dr_event_'+name)+': '+str(counts[name]) for name in USER_EVENTS if counts.get(name)]
                lines.append('; '.join(actions) if actions else tr('dr_no_events'))
                stage=next((label for name,label in STAGES if counts.get(name)), 'dr_stage_activity')
                lines.append(tr('dr_reached',stage=tr(stage)))
    return '\n\n'.join(lines),InlineKeyboardMarkup([[InlineKeyboardButton(tr('au_title'),callback_data='g:users:0')]])


async def deliver_daily_report(bot, store, now=None):
    if not store.admin_id: return False
    now=time.time() if now is None else now
    row=claim(store,now)
    if not row: return False
    state='unknown';delay=None;message_id=None
    try:
        body,markup=render_report(store,row)
        result=await bot.send_message(chat_id=row['admin_id'],text=body,reply_markup=markup,disable_web_page_preview=True)
        state='sent';message_id=result.message_id
    except RetryAfter as exc:
        delay=exc.retry_after.total_seconds() if isinstance(exc.retry_after,timedelta) else exc.retry_after
        state='pending'
    except (Forbidden,BadRequest):
        state='failed'
    except asyncio.CancelledError:
        raise
    except Exception:
        # Ambiguous delivery: do not resend a potentially delivered part.
        pass
    finally:
        with store.db._connect() as conn:
            conn.execute('''UPDATE admin_daily_reports SET state=?,next_at=?,message_id=?
              WHERE day=? AND admin_id=? AND part=? AND state='sending' ''',
              (state,now+(delay or 0),message_id,row['day'],row['admin_id'],row['part']))
            if delay is not None:
                conn.execute('UPDATE alert_rate_limit SET next_at=MAX(next_at,?) WHERE id=1',(now+delay,))
    return True

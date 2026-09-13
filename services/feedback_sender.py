"""Admin-only feedback notice. Never forwards private feedback text into a push."""
import asyncio
import time
from datetime import timedelta
from telegram.error import RetryAfter
from locales import text


async def deliver_feedback(bot, store, now=None):
    if not store.admin_id:
        return False
    now=time.time() if now is None else now
    with store.db._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE feedback SET notification_state='unknown' WHERE notification_state='sending' AND notice_at<?",(now-300,))
        if conn.execute('SELECT next_at FROM alert_rate_limit WHERE id=1').fetchone()[0]>now:
            return False
        if conn.execute('SELECT next_at FROM feedback_gate WHERE id=1').fetchone()[0]>now:
            return False
        row=conn.execute("SELECT id,category FROM feedback WHERE notification_state='pending' AND notice_at<=? ORDER BY id LIMIT 1",(now,)).fetchone()
        if not row:
            return False
        row=dict(row)
        conn.execute("UPDATE feedback SET notification_state='sending',notice_at=? WHERE id=?",(now,row['id']))
        conn.execute('UPDATE feedback_gate SET next_at=? WHERE id=1',(now+60,))
        conn.execute('UPDATE alert_rate_limit SET next_at=? WHERE id=1',(now+2,))
    state='sent'; delay=None
    try:
        language=store.db.get_language(store.admin_id)
        await bot.send_message(chat_id=store.admin_id,
            text=text(language,'g_feedback_notification',id=row['id'],category=text(language,'g_fb_'+row['category'])))
    except RetryAfter as exc:
        delay=exc.retry_after.total_seconds() if isinstance(exc.retry_after,timedelta) else exc.retry_after
        state='pending'
    except asyncio.CancelledError:
        state='unknown'
        raise
    except Exception:
        state='unknown'
    finally:
        with store.db._connect() as conn:
            conn.execute("UPDATE feedback SET notification_state=?,notice_at=? WHERE id=? AND notification_state='sending'",(state,now+(delay or 0),row['id']))
            if delay is not None:
                conn.execute('UPDATE alert_rate_limit SET next_at=MAX(next_at,?) WHERE id=1',(now+delay,))
    return True

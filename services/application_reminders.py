"""User-only reminder delivery, sharing the existing bot worker and global rate gate."""
import asyncio
from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, Forbidden, TelegramError
from locales import translator, status_label
from services.application_tracker import ApplicationTracker, display_date, DUBAI


def render_reminder(language,reminder,app,now=None):
    tr=translator(language);app_id=app['id']
    if reminder['kind']=='follow_up':
        lines=[tr('at_reminder'),app['role']+' — '+app['company'],status_label(language,app['status'])]
        applied=app.get('applied_at') or app.get('date_applied')
        if applied:
            date=datetime.fromisoformat(applied).date()
            today=datetime.fromtimestamp(now,DUBAI).date() if now is not None else datetime.now(DUBAI).date()
            lines.append(tr('at_applied_days',days=max(0,(today-date).days)))
        lines.append(tr('at_follow_hint'))
        actions=[('at_pack',f'ap:application:{app_id}'),('at_status',f'at:status:{app_id}'),
                 ('at_snooze',f'at:follow:{app_id}'),('at_reply',f'at:reply:{app_id}')]
    else:
        lines=[tr('at_interview_reminder',hours=24 if reminder['kind']=='interview_24' else 2),
               app['role']+' — '+app['company'],display_date(app['interview_at']),tr('at_timezone'),
               tr('at_'+app['interview_format']),app.get('interview_location') or '',app.get('interview_notes') or '']
        actions=[('at_open',f'at:open:{app_id}')]
    return '\n\n'.join(lines)[:3900],InlineKeyboardMarkup([[InlineKeyboardButton(tr(k),callback_data=v)] for k,v in actions])


async def deliver_reminder(bot,db,now=None):
    store=ApplicationTracker(db)
    claimed=store.claim(now)
    if not claimed:return False
    reminder,app=claimed
    try:
        text,markup=render_reminder(db.get_language(app['telegram_id']),reminder,app,now)
        message=await bot.send_message(chat_id=app['telegram_id'],text=text,reply_markup=markup,disable_web_page_preview=True)
    except RetryAfter as exc:
        delay=exc.retry_after.total_seconds() if isinstance(exc.retry_after,timedelta) else exc.retry_after
        store.finish(reminder,'pending',retry_after=delay,now=now)
    except Forbidden:
        store.finish(reminder,'failed',now=now)
        with db._connect() as conn:
            conn.execute("UPDATE application_reminders SET state='cancelled' WHERE state='pending' AND application_id IN (SELECT id FROM applications WHERE telegram_id=?)",(app['telegram_id'],))
    except asyncio.CancelledError:
        store.finish(reminder,'unknown',now=now);raise
    except Exception:
        # Ambiguous sends are not retried; never log employer/user details.
        store.finish(reminder,'unknown',now=now)
    else:
        store.finish(reminder,'sent',message_id=message.message_id,now=now)
    return True

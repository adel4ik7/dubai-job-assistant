"""Bounded bot-side delivery. Ambiguous network outcomes are not auto-retried."""
import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from telegram.error import RetryAfter, Forbidden, BadRequest, TelegramError
from services.job_alerts import JobAlerts
from alerts_ui import notification
from services.application_reminders import deliver_reminder
from services.feedback_sender import deliver_feedback

log = logging.getLogger(__name__)


async def deliver_one(bot, store, now=None):
    claimed = store.claim(now)
    if not claimed:
        return False
    delivery, preferences, vacancy = claimed
    try:
        text, markup = notification(store.db.get_language(delivery['telegram_id']), preferences, vacancy)
        result = await bot.send_message(chat_id=delivery['telegram_id'], text=text,
                                        reply_markup=markup, disable_web_page_preview=True)
    except RetryAfter as exc:
        delay = exc.retry_after.total_seconds() if isinstance(exc.retry_after, timedelta) else exc.retry_after
        store.finish(delivery, 'pending', retry_after=delay, now=now)
    except Forbidden:
        store.finish(delivery, 'failed', now=now)
        store.disable_existing(delivery['telegram_id'])
    except BadRequest:
        store.finish(delivery, 'failed', now=now)
    except (TelegramError, asyncio.CancelledError):
        store.finish(delivery, 'unknown', now=now)
        if asyncio.current_task().cancelling():
            raise
    except Exception:
        # Do not print RPC exceptions, recipient IDs, CV content or message text.
        store.finish(delivery, 'unknown', now=now)
        log.warning('Job alert delivery outcome unknown; automatic retry suppressed.')
    else:
        store.finish(delivery, 'sent', message_id=result.message_id, now=now)
    return True


async def start_sender(application):
    store = application.bot_data['job_alerts']
    async def loop():
        while True:
            growth=application.bot_data.get('growth')
            jobs=[]
            if growth:jobs.append(lambda:deliver_feedback(application.bot,growth))
            jobs.extend((lambda:deliver_reminder(application.bot,store.db),lambda:deliver_one(application.bot,store)))
            for job in jobs:
                try:
                    if await job():break
                except asyncio.CancelledError:raise
                except Exception as exc:
                    health=application.bot_data.get('runtime_health')
                    if health:health.error(exc,'background_job')
                    else:log.warning('Background job failed; error_type=%s',type(exc).__name__)
                    # Another job can proceed through the existing shared rate gate.
            await asyncio.sleep(2)
    application.bot_data['alert_task'] = asyncio.create_task(loop())


async def stop_sender(application):
    task = application.bot_data.pop('alert_task', None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

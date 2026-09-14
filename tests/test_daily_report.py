import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from telegram.error import RetryAfter, TimedOut
from db import Database
from services.growth import Growth, stamp
from services.daily_report import deliver_daily_report, claim, render_report, DUBAI, USER_EVENTS


class DailyReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.db';self.db=Database(self.path)
        self.store=Growth(self.db,99)
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=8)))
        self.now=datetime(2026,9,15,22,tzinfo=DUBAI).timestamp()
        self.db.upsert_user(99,'admin','Admin');self.db.set_language(99,'ru')
        for uid in range(1,7):
            self.db.upsert_user(uid,'person_'+str(uid),'First')
            self.store.touch(uid,datetime.fromtimestamp(self.now-60,timezone.utc))
        self.store.track(1,'vacancy_searched',now=datetime.fromtimestamp(self.now-10,timezone.utc))
        self.store.track(1,'application_created',now=datetime.fromtimestamp(self.now-9,timezone.utc))

    def state(self):
        with self.db._connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM admin_daily_reports ORDER BY part')]

    async def test_dubai_schedule_once_parts_and_restart(self):
        self.assertFalse(await deliver_daily_report(self.bot,self.store,self.now-1))
        self.assertTrue(await deliver_daily_report(self.bot,self.store,self.now))
        body=self.bot.send_message.call_args.kwargs['text']
        self.assertIn('22:00',body);self.assertIn('пользователей: 6',body)
        self.assertEqual(self.bot.send_message.call_args.kwargs['chat_id'],99)
        self.assertFalse(await deliver_daily_report(self.bot,self.store,self.now+1))
        fresh=Growth(Database(self.path),99)
        self.assertTrue(await deliver_daily_report(self.bot,fresh,self.now+2))
        body=self.bot.send_message.call_args.kwargs['text']
        self.assertIn('@person_1',body);self.assertIn('создания отклика',body)
        self.assertTrue(await deliver_daily_report(self.bot,fresh,self.now+4))
        self.assertFalse(await deliver_daily_report(self.bot,fresh,self.now+6))
        self.assertEqual([r['state'] for r in self.state()],['sent']*3)

    async def test_midnight_boundary_and_background_not_activity(self):
        self.db.upsert_user(10,None,None)
        self.db.upsert_user(11,None,None)
        midnight=datetime(2026,9,15,0,tzinfo=DUBAI)
        self.store.touch(10,midnight-timedelta(seconds=1))
        self.store.track(11,'job_alert_delivered',now=midnight+timedelta(hours=1))
        row=claim(self.store,self.now)
        body,_=render_report(self.store,row)
        self.assertIn('пользователей: 6',body)
        self.assertIn('Уведомлений доставлено: 1',body)
        ids=[uid for r in self.state() for uid in json.loads(r['user_ids'])]
        self.assertNotIn(10,ids);self.assertNotIn(11,ids)

    async def test_retry_after_and_ambiguous_timeout(self):
        self.bot.send_message.side_effect=RetryAfter(60)
        await deliver_daily_report(self.bot,self.store,self.now)
        self.assertEqual(self.state()[0]['state'],'pending')
        self.assertFalse(await deliver_daily_report(self.bot,self.store,self.now+59))
        self.bot.send_message.side_effect=TimedOut()
        await deliver_daily_report(self.bot,self.store,self.now+60)
        self.assertEqual(self.state()[0]['state'],'unknown')
        self.bot.send_message.side_effect=None
        await deliver_daily_report(self.bot,self.store,self.now+62)
        self.assertNotIn('Активных пользователей:',self.bot.send_message.call_args.kwargs['text'])

    async def test_no_admin_no_send_and_new_day(self):
        self.assertFalse(await deliver_daily_report(self.bot,Growth(self.db),self.now))
        self.assertFalse(await deliver_daily_report(self.bot,self.store,self.now+7200))
        await deliver_daily_report(self.bot,self.store,self.now+86400)
        self.assertEqual(len(self.state()),1)
        self.assertIn('пользователей: 0',self.bot.send_message.call_args.kwargs['text'])

    async def test_privacy_delete_and_cyrillic_english_size(self):
        self.db.save_profile(1,notes='PRIVATE NOTE',full_name='PRIVATE NAME')
        self.db.add_resume(1,'private.pdf','private-path','PRIVATE CV')
        for uid in range(1,6):
            for name in USER_EVENTS:
                self.store.track(uid,name,now=datetime.fromtimestamp(self.now-5,timezone.utc))
        claim(self.store,self.now)
        for lang in ('ru','en'):
            self.db.set_language(99,lang)
            for row in self.state():
                body,_=render_report(self.store,row)
                self.assertNotIn('PRIVATE',body)
                self.assertLessEqual(len(body.encode('utf-16-le'))//2,4096)
        self.db.delete_user_records(1)
        for row in self.state():
            self.assertNotIn(1,json.loads(row['user_ids']))
            self.assertNotIn('@person_1',render_report(self.store,row)[0])
        self.db.delete_user_records(99)
        self.assertEqual(self.state(),[])

    async def test_late_same_day_includes_activity_after_22(self):
        self.store.touch(1,datetime.fromtimestamp(self.now+600,timezone.utc))
        row=claim(self.store,self.now+1200)
        body,_=render_report(self.store,row)
        self.assertIn('22:20',body);self.assertIn('пользователей: 6',body)

    async def test_cancelled_send_and_crashed_claim_not_retried(self):
        self.bot.send_message.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await deliver_daily_report(self.bot,self.store,self.now)
        self.assertEqual(self.state()[0]['state'],'unknown')
        claim(self.store,self.now+2)
        claim(self.store,self.now+400)
        self.assertEqual(self.state()[1]['state'],'unknown')

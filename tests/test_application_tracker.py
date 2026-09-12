import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from db import Database
from services.application_tracker import ApplicationTracker, iso, parse_local, display_date
from services.application_reminders import deliver_reminder, render_reminder
from telegram.error import RetryAfter, TimedOut


class ApplicationTrackerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.db=Database(Path(temp.name)/'test.db');self.store=ApplicationTracker(self.db)
        self.aid=self.db.add_application(1,'Company','Chef',status='applied')
        self.now=2000000000
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=10)))

    def rows(self):
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM application_reminders ORDER BY id')]

    def test_history_status_idempotence_order_and_migration(self):
        self.db.update_application_status(1,self.aid,'hr_screening')
        self.db.update_application_status(1,self.aid,'hr_screening')
        self.db.update_application_status(1,self.aid,'interview')
        events=self.store.timeline(1,self.aid)
        self.assertEqual([r['new_status'] for r in events],['applied','hr_screening','interview'])
        Database(self.db.path)
        self.assertEqual(self.store.timeline(1,self.aid),events)
        with self.db._connect() as conn:
            conn.execute('DELETE FROM application_events WHERE application_id=?',(self.aid,))
        Database(self.db.path);Database(self.db.path)
        events=self.store.timeline(1,self.aid)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['event_type'],'snapshot')
        self.assertEqual(self.db.get_application(1,self.aid)['role'],'Chef')

    async def test_followup_fires_once_snooze_reply_and_privacy(self):
        self.store.follow_up(1,self.aid,self.now+100,now=self.now)
        self.store.follow_up(1,self.aid,self.now+100,now=self.now)
        self.assertEqual(len(self.rows()),1)
        self.assertFalse(await deliver_reminder(self.bot,self.db,self.now+99))
        self.assertTrue(await deliver_reminder(self.bot,self.db,self.now+100))
        self.assertFalse(await deliver_reminder(self.bot,self.db,self.now+200))
        self.assertEqual(self.rows()[0]['state'],'sent')
        self.store.follow_up(1,self.aid,self.now+300,now=self.now+200)
        self.assertTrue(await deliver_reminder(self.bot,self.db,self.now+300))
        self.store.replied(1,self.aid);self.store.replied(1,self.aid)
        self.assertEqual(sum(r['event_type']=='reply' for r in self.store.timeline(1,self.aid)),1)
        self.assertIsNone(self.db.get_application(1,self.aid)['follow_up_at'])
        self.db.delete_user_records(1)
        self.assertEqual(self.rows(),[])
        with self.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM application_events').fetchone()[0],0)

    async def test_interview_24h_2h_once_and_reschedule(self):
        when=self.now+48*3600
        for _ in range(2): self.store.interview(1,self.aid,when,'video','https://example.com','Bring CV',now=self.now)
        self.assertEqual(len(self.rows()),2)
        self.assertFalse(await deliver_reminder(self.bot,self.db,when-24*3600-1))
        self.assertTrue(await deliver_reminder(self.bot,self.db,when-24*3600))
        self.assertFalse(await deliver_reminder(self.bot,self.db,when-24*3600+100))
        self.assertTrue(await deliver_reminder(self.bot,self.db,when-2*3600))
        self.assertFalse(await deliver_reminder(self.bot,self.db,when-2*3600+100))
        self.store.interview(1,self.aid,when+86400,'phone',now=when-3600)
        self.assertTrue(any(r['state']=='pending' for r in self.rows()))

    def test_late_interview_no_old_reminders_and_closed_status(self):
        self.store.interview(1,self.aid,self.now+3600,'onsite',now=self.now)
        self.assertEqual(self.rows(),[])
        self.store.follow_up(1,self.aid,self.now+100,now=self.now)
        self.db.update_application_status(1,self.aid,'rejected')
        self.assertEqual(self.rows()[0]['state'],'cancelled')
        self.assertIsNone(self.store.claim(self.now+200))

    def test_cancel_then_restore_future_schedule_no_duplicate(self):
        due=self.now+1000
        self.store.follow_up(1,self.aid,due,now=self.now)
        self.store.follow_up(1,self.aid,None,now=self.now)
        self.store.follow_up(1,self.aid,due,now=self.now)
        self.assertEqual(len(self.rows()),1);self.assertEqual(self.rows()[0]['state'],'pending')

    async def test_floodwait_unknown_and_restart_claim(self):
        self.store.follow_up(1,self.aid,self.now+1,now=self.now)
        self.bot.send_message.side_effect=RetryAfter(120)
        await deliver_reminder(self.bot,self.db,self.now+2)
        self.assertFalse(await deliver_reminder(self.bot,self.db,self.now+121))
        self.bot.send_message.side_effect=TimedOut()
        await deliver_reminder(self.bot,self.db,self.now+123)
        self.assertEqual(self.rows()[0]['state'],'unknown')
        self.assertIsNone(ApplicationTracker(Database(self.db.path)).claim(self.now+1000))

    def test_today_timezone_and_status_staleness(self):
        self.store.interview(1,self.aid,self.now+60,'phone',now=self.now-100000)
        self.store.follow_up(1,self.aid,self.now-100,now=self.now-200)
        rows=self.store.today(1,self.now)
        self.assertEqual(rows[0]['id'],self.aid);self.assertTrue(rows[0]['interview_today']);self.assertTrue(rows[0]['overdue'])
        self.assertEqual(self.store.today(2,self.now),[])
        self.assertEqual(display_date(iso(parse_local('2035-01-01 12:00',now=self.now))),'2035-01-01 12:00')

    def test_dashboard_only_supported_status_denominator(self):
        for status in ('saved','hr_screening','interview','offer','rejected','withdrawn'):
            self.db.add_application(1,'C','R',status=status)
        with self.db._connect() as conn:
            conn.execute("INSERT INTO applications(telegram_id,company,role,status) VALUES(1,'C','R','legacy')")
        d=self.db.dashboard(1)
        self.assertEqual(d['submitted'],5);self.assertEqual(d['response_rate'],80)
        self.assertEqual(d['interview_rate'],20);self.assertEqual(d['offer_rate'],20)
        self.assertEqual(self.db.dashboard(2)['response_rate'],0)

    def test_owner_checks_delete_and_ru_en_reminder(self):
        for call in (lambda:self.store.follow_up(2,self.aid,self.now+1,now=self.now),lambda:self.store.delete(2,self.aid),lambda:self.store.timeline(2,self.aid)):
            with self.assertRaises(ValueError):call()
        app=self.db.get_application(1,self.aid)
        for lang in ('ru','en'):
            text,markup=render_reminder(lang,{'kind':'follow_up'},app,self.now)
            self.assertIn('Chef',text)
            self.assertIn('Напоминание' if lang=='ru' else 'follow-up',text)
            self.assertEqual(markup.inline_keyboard[0][0].callback_data,f'ap:application:{self.aid}')
        self.store.delete(1,self.aid)
        self.assertIsNone(self.db.get_application(1,self.aid))

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from config import Settings
from db import Database
from locales import text
from services.growth import Growth
from services.feedback_sender import deliver_feedback
from services.job_alerts import JobAlerts
from services.matching_jobs import matching_jobs
from services.apply_flow import ApplyFlow
from vacancy_store import VacancyStore
from growth_ui import WAIT_GROWTH


class GrowthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.db=Database(self.root/'test.db')
        self.g=Growth(self.db,99); self.v=VacancyStore(self.db)
        self.source=self.v.add_source('test_jobs')
        for uid in (1,2,3): self.db.upsert_user(uid,None,None)
        self.vid=self.v.insert(self.source,1,role='Cook',location='Dubai',detection_status='vacancy',
            content_hash='same',published_at=datetime.now(timezone.utc).isoformat(),source_url='https://t.me/test_jobs/1')[0]

    def events(self, name):
        with self.db._connect() as c: return [dict(r) for r in c.execute('SELECT * FROM product_events WHERE event_name=?',(name,))]

    def test_events_closed_metadata_and_user_erasure(self):
        self.g.track(1,'cv_generated','cv',10,{'format':'pdf','cv':'secret','email':'secret','query':'secret','phone':'secret'})
        self.g.track(1,'vacancy_searched',metadata={'format':'secret'})
        self.assertEqual(json.loads(self.events('cv_generated')[0]['metadata_json']),{'format':'pdf'})
        self.assertEqual(json.loads(self.events('vacancy_searched')[0]['metadata_json']),{})
        with self.assertRaises(ValueError): self.g.track(1,'private arbitrary event')
        self.db.delete_user_records(1)
        self.assertFalse(self.events('cv_generated'))
        self.g.track(1,'user_returned')
        self.assertFalse(self.events('user_returned'))

    def test_deleted_user_cannot_recreate_feedback_or_reports(self):
        self.db.delete_user_records(1)
        with self.assertRaises(ValueError): self.g.feedback(1,'bug','Example')
        with self.assertRaises(ValueError): self.g.report(1,self.vid,'expired')

    def test_durable_mutations_and_idempotence(self):
        self.g.track(1,'user_started',key='once'); self.g.track(1,'user_started',key='once')
        self.assertEqual(len(self.events('user_started')),1)
        self.v.save(1,self.vid); self.v.save(1,self.vid)
        self.assertEqual(len(self.events('vacancy_saved')),1)
        aid=self.db.add_application(1,'Example','Cook')
        self.db.update_application_status(1,aid,'interview'); self.db.update_application_status(1,aid,'interview')
        self.assertEqual(len(self.events('application_created')),1)
        self.assertEqual(len(self.events('application_status_changed')),1)
        self.db.save_profile(1,full_name='Test',desired_role='Cook'); self.db.save_profile(1,notes='private note')
        self.assertEqual(len(self.events('profile_completed')),1)
        alerts=JobAlerts(self.db); alerts.update(1,'roles','Cook');alerts.update(1,'enabled',1);alerts.update(1,'enabled',1)
        self.assertEqual(len(self.events('job_alert_enabled')),1)

    def test_funnel_unique_intersections_and_safe_empty_rates(self):
        for uid in (1,2,3): self.g.track(uid,'user_started',key='once')
        for uid in (1,2): self.g.step(uid,7,'completed'); self.db.add_resume(uid,'x.txt','synthetic','SQL')
        self.g.track(1,'vacancy_viewed','vacancy',self.vid);self.v.save(1,self.vid);self.db.add_application(1,'Example','Cook')
        result=self.g.stats(99)
        self.assertEqual(result['funnel'],[3,2,2,1,1,1]);self.assertEqual(result['conversion'][-1],33.3)
        self.db.delete_user_records(1);self.db.delete_user_records(2);self.db.delete_user_records(3)
        self.assertEqual(self.g.stats(99)['conversion'],[0]*6)

    def test_retention_counts_days_not_background_deliveries(self):
        now=datetime(2026,9,13,12,tzinfo=timezone.utc)
        self.g.touch(1,now-timedelta(days=2));self.g.touch(1,now);self.g.touch(1,now)
        self.g.touch(2,now-timedelta(days=8));self.g.track(3,'job_alert_delivered',now=now)
        result=self.g.stats(99,now)
        self.assertEqual(result['active_today'],1);self.assertEqual(result['active_7d'],1)
        self.assertEqual(result['returning'],1);self.assertEqual(len(self.events('user_returned')),1)

    def test_admin_authorization(self):
        for call in (lambda:self.g.stats(1),lambda:self.g.hide(1,self.vid,True),lambda:self.g.reports(1),lambda:self.g.feedback_rows(1),lambda:self.g.source_usage(1)):
            with self.assertRaises(PermissionError): call()
        with self.assertRaises(PermissionError): Growth(self.db).stats(99)

    def test_feedback_limit_and_privacy_cleanup(self):
        for _ in range(5): self.g.feedback(1,'bug','Private feedback',self.vid)
        with self.assertRaises(ValueError): self.g.feedback(1,'bug','Sixth')
        self.g.feedback(2,'cv','Other user');self.g.report(1,self.vid,'fraud');self.g.touch(1)
        self.db.delete_user_records(1)
        self.assertEqual(len(self.g.feedback_rows(99)),1)
        self.assertFalse(self.g.reports(99))
        self.assertIsNone(self.g.onboarding(1))

    def test_reports_are_independent_and_do_not_auto_hide(self):
        for _ in range(4):self.g.report(1,self.vid,'fraud')
        for uid in (2,3):self.g.report(uid,self.vid,'fraud')
        self.assertEqual(self.g.reports(99)[0]['reports'],3)
        self.assertIsNotNone(self.v.get_visible(self.vid))

    def test_hidden_across_search_saved_reposts_matching_and_apply(self):
        sid=self.v.add_source('second_jobs'); duplicate=self.v.insert(sid,1,role='Cook',detection_status='vacancy',content_hash='same')[0]
        self.v.save(1,self.vid)
        alerts=JobAlerts(self.db);alerts.update(1,'roles','Cook')
        self.g.hide(99,self.vid,True)
        self.assertFalse(self.v.list(search='Cook'));self.assertFalse(self.v.list(saved_user=1))
        self.assertIsNone(self.v.get_visible(duplicate));self.assertFalse(self.v.save(1,duplicate))
        self.assertFalse(matching_jobs(self.db,alerts.preferences(1))[0])
        with self.assertRaises(ValueError): ApplyFlow(self.db,self.root).prepare(1,'vacancy',self.vid,'en')
        self.g.hide(99,self.vid,False)
        self.assertTrue(self.v.list(search='Cook'))

    def test_hidden_pending_alert_cancelled(self):
        alerts=JobAlerts(self.db);alerts.update(1,'roles','Cook',now=100);alerts.update(1,'enabled',1,now=100)
        vid=self.v.insert(self.source,2,role='Cook',detection_status='vacancy',published_at=datetime.now(timezone.utc).isoformat(),content_hash='new')[0]
        self.g.hide(99,vid,True)
        self.assertIsNone(alerts.claim())
        with self.db._connect() as c:self.assertEqual(c.execute('SELECT state FROM alert_deliveries WHERE vacancy_id=?',(vid,)).fetchone()[0],'cancelled')

    def test_source_usage_links_actions(self):
        self.g.track(1,'vacancy_viewed','vacancy',self.vid);self.v.save(1,self.vid)
        self.db.add_application(1,'Example','Cook',source_url='https://t.me/test_jobs/1')
        usage=self.g.source_usage(99)
        for event in ('vacancy_viewed','vacancy_saved','application_created'):
            self.assertEqual(usage[(self.source,event)],1)

    def test_old_users_not_enrolled_and_migration_repeat_safe(self):
        path=self.root/'old.db'
        with sqlite3.connect(path) as c:
            c.executescript("CREATE TABLE users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at TEXT); INSERT INTO users VALUES(7,'old','Name','2020-01-01');")
        c.close()
        old=Database(path)
        self.assertIsNone(Growth(old).onboarding(7))
        old.upsert_user(8,None,None)
        self.assertEqual(Growth(Database(path)).onboarding(8)['state'],'new')


class GrowthUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.config=Settings('123:'+'x'*30,99,None,root/'test.db',root)
        self.app=bot.build_application(self.config)
        self.message=SimpleNamespace(reply_text=AsyncMock(),text='',document=None)
        self.query=SimpleNamespace(answer=AsyncMock(),data='',message=self.message)
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=1,username='test',first_name='Test',language_code='ru'),
            effective_message=self.message,message=self.message,callback_query=self.query,effective_chat=SimpleNamespace(type='private'))
        self.context=SimpleNamespace(user_data={},args=[])

    async def click(self,data):
        self.query.data=data;return await bot.buttons(self.update,self.context)

    def reply(self):return self.message.reply_text.call_args.args[0]
    def labels(self):return [b.text for row in self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]

    async def answer(self,value):
        self.message.text=value;return await bot.growth_ui.receive(self.update,self.context)

    async def test_new_onboarding_persists_existing_preferences(self):
        await bot.start(self.update,self.context);self.assertIn('Добро пожаловать',self.reply())
        await self.click('g:resume');await self.click('g:value:0:ru')
        self.assertEqual(await self.answer('повар'),WAIT_GROWTH)
        await self.answer('Dubai');await self.click('g:value:3:1');await self.answer('5000')
        await self.click('g:skip:5');await self.click('g:value:6:1')
        pref=JobAlerts(bot.db).preferences(1)
        self.assertTrue(pref['enabled']);self.assertEqual(pref['salary_min'],5000);self.assertTrue(pref['uae_only'])
        self.assertEqual(bot.db.get_profile(1)['desired_role'],'повар')
        self.assertEqual(bot.growth_ui.store.onboarding(1)['state'],'completed')
        self.assertEqual(len(self.labels()),7)
        await bot.start(self.update,self.context);self.assertNotIn('Добро пожаловать',self.reply())

    async def test_skip_all_and_later_resume(self):
        await bot.start(self.update,self.context);await self.click('g:resume');await self.click('g:value:0:en')
        await self.answer('Cook');await self.click('g:later')
        bot.build_application(self.config);self.context.user_data.clear()
        await self.click('g:settings');self.assertIn(text('en','g_resume'),self.labels())
        await self.click('g:resume');self.assertIn(text('en','g_step_2'),self.reply())
        await self.click('g:finish');self.assertEqual(bot.growth_ui.store.onboarding(1)['state'],'completed')
        self.assertFalse(JobAlerts(bot.db).preferences(1)['enabled'])

    async def test_old_user_not_forced(self):
        bot.db.set_language(1,'en');bot.db.save_profile(1,full_name='Existing')
        await bot.start(self.update,self.context)
        self.assertNotIn(text('en','g_begin'),self.labels())
        self.assertEqual(bot.db.get_profile(1)['full_name'],'Existing')

    async def test_back_invalid_salary_and_cancel(self):
        await bot.start(self.update,self.context);await self.click('g:resume');await self.click('g:skip:0')
        await self.click('g:skip:1');await self.click('g:skip:2');await self.click('g:skip:3')
        self.assertEqual(await self.answer('private@example.com'),WAIT_GROWTH)
        self.assertEqual(bot.growth_ui.store.onboarding(1)['step'],4)
        await self.click('g:back:4');self.assertEqual(bot.growth_ui.store.onboarding(1)['step'],3)
        await self.click('g:skip:3');await bot.cancel(self.update,self.context)
        self.assertEqual(bot.growth_ui.store.onboarding(1)['state'],'paused')

    async def test_optional_cv_handoff(self):
        await bot.start(self.update,self.context);await self.click('g:resume')
        for step in range(5):await self.click(f'g:skip:{step}')
        await self.click('g:cv:create')
        self.assertEqual(bot.growth_ui.store.onboarding(1)['step'],6)
        self.assertIn(text('en','cb_intro'),self.reply())
        await self.click('g:resume');await self.click('g:value:6:0')
        self.assertEqual(bot.growth_ui.store.onboarding(1)['state'],'completed')

    async def test_ru_en_home_settings_feedback_and_admin_denial(self):
        for lang in ('ru','en'):
            bot.db.set_language(1,lang)
            labels=[b.text for row in bot.make_main_menu(lang).inline_keyboard for b in row]
            self.assertEqual(len(labels),7);self.assertIn(text(lang,'g_settings'),labels)
            await self.click('g:settings');self.assertNotIn(text(lang,'g_admin'),self.labels())
            await self.click('g:admin');self.assertEqual(self.reply(),text(lang,'g_denied'))
            await self.click('g:feedback');self.assertIn(text(lang,'g_fb_bug'),self.labels())
            await self.click('g:category:bug');await self.answer('Synthetic issue')
            await bot.growth_ui.data(self.update,self.context);self.assertIn('/delete_my_data',self.reply())

    async def test_admin_review_sources_and_hidden_stale_card(self):
        await bot.ensure_user(self.update)
        source=bot.vacancies.store.add_source('test_jobs');vid=bot.vacancies.store.insert(source,1,role='Cook',detection_status='vacancy')[0]
        await self.click(f'g:report:{vid}');await self.click(f'g:reason:{vid}:fraud')
        await self.click('g:category:bug');await self.answer('Synthetic admin issue')
        self.update.effective_user.id=99
        await self.click('g:admin');self.assertIn('Total users',self.reply())
        await self.click('g:feedbacks:0');await self.click('g:reports:0');await self.click(f'g:review:{vid}')
        await self.click('g:sources:0');self.assertIn('Source quality',self.reply())
        await self.click(f'g:hide:{vid}:1')
        self.update.effective_user.id=1
        await self.click(f'v:open:{vid}');self.assertEqual(self.reply(),text('en','v_stale'))
        await self.click(f'g:hide:{vid}:0');self.assertIsNone(bot.vacancies.store.get_visible(vid))
        self.update.effective_user.id=99
        await self.click(f'g:hide:{vid}:0');self.assertIsNotNone(bot.vacancies.store.get_visible(vid))

    async def test_feedback_notification_private_idempotent_and_rate_limited(self):
        await bot.ensure_user(self.update);g=bot.growth_ui.store
        g.feedback(1,'bug','PRIVATE CV EMAIL BODY')
        transport=SimpleNamespace(send_message=AsyncMock())
        self.assertTrue(await deliver_feedback(transport,g,now=100))
        transport.send_message.assert_awaited_once()
        values=transport.send_message.call_args.kwargs
        self.assertEqual(values['chat_id'],99);self.assertNotIn('PRIVATE',values['text'])
        self.assertFalse(await deliver_feedback(transport,g,now=101))
        g.feedback(1,'cv','Other issue');self.assertFalse(await deliver_feedback(transport,g,now=110))
        self.assertTrue(await deliver_feedback(transport,g,now=161))

    async def test_feedback_retry_and_deleted_feedback_not_recreated(self):
        from telegram.error import RetryAfter
        await bot.ensure_user(self.update);g=bot.growth_ui.store;g.feedback(1,'bug','Issue')
        transport=SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(70)))
        await deliver_feedback(transport,g,now=100)
        self.assertFalse(await deliver_feedback(transport,g,now=150))
        bot.db.delete_user_records(1)
        transport.send_message.side_effect=None
        self.assertFalse(await deliver_feedback(transport,g,now=200))

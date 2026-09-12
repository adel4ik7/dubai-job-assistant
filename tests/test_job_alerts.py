import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import RetryAfter, TimedOut, Forbidden
from db import Database
from vacancy_store import VacancyStore
from services.job_alerts import JobAlerts, matching_reasons
from services.alert_sender import deliver_one, start_sender, stop_sender
from alerts_ui import notification


class JobAlertsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / 'test.db')
        self.vacancies = VacancyStore(self.db)
        self.source = self.vacancies.add_source('test_jobs')
        self.store = JobAlerts(self.db)
        self.now = 1700000000.0
        self.number = 0
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))

    def configure(self, user=1, **settings):
        for key, value in dict(roles='Повар', location='Dubai', salary_min='5000', uae_only=True, **settings).items():
            self.store.update(user, key, value, now=self.now)
        self.store.update(user, 'enabled', True, now=self.now)

    def add(self, **overrides):
        self.number += 1
        values = dict(role='Chef de Partie', location='Dubai', salary_min=5000,
                      salary_max=6000, salary_currency='AED', detection_status='vacancy',
                      content_hash=f'hash-{self.number}',
                      published_at=datetime.fromtimestamp(self.now+1, timezone.utc).isoformat())
        values.update(overrides)
        with patch('services.job_alerts.time.time', return_value=self.now+2):
            return self.vacancies.insert(self.source, self.number, **values)[0]

    def deliveries(self):
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM alert_deliveries ORDER BY id')]

    def test_preferences_default_off_persist_validate_and_user_isolation(self):
        self.assertFalse(self.store.preferences(1)['enabled'])
        with self.assertRaises(ValueError):
            self.store.update(1, 'enabled', True)
        self.configure()
        self.assertEqual(JobAlerts(Database(self.db.path)).preferences(1)['salary_min'], 5000)
        self.assertEqual(json.loads(self.store.preferences(1)['roles']), ['повар'])
        self.assertFalse(self.store.preferences(2)['enabled'])
        for field, value in [('salary_min', '-1'), ('roles', 'x'*81), ('location', 'x'*101), ('enabled', 3)]:
            with self.assertRaises(ValueError):
                self.store.update(1, field, value)
        self.assertTrue(self.store.preferences(1)['enabled'])

    def test_no_old_missing_date_future_posts_or_existing_rows(self):
        self.add()
        self.configure()
        self.add(published_at=datetime.fromtimestamp(self.now-1, timezone.utc).isoformat())
        self.add(published_at=None)
        self.add(published_at='2023-01-01')
        self.add(published_at=datetime.fromtimestamp(self.now+100, timezone.utc).isoformat())
        self.assertEqual(self.deliveries(), [])
        self.add()
        self.assertEqual(len(self.deliveries()), 1)

    def test_synonyms_multiple_roles_ocr_and_combined_filters(self):
        self.configure()
        self.store.update(1, 'keywords', 'barista; cold kitchen', now=self.now)
        for role in ('Cook', 'Commis Chef', 'CDP', 'Barista'):
            self.add(role=role)
        self.add(role=None, ocr_text='Hiring Chef de Partie in Dubai')
        self.add(role='Restaurant Manager', combined_text='Our restaurant has a chef')
        self.add(role='Accountant')
        self.add(location='Qatar')
        self.add(location='Dubai', raw_text='Location: Maldives\nContact Dubai')
        self.add(salary_min=None)
        self.add(salary_min=4999)
        self.add(salary_currency='USD')
        self.assertEqual(len(self.deliveries()), 5)

    def test_duplicates_updates_and_reenable_do_not_replay(self):
        self.configure()
        vid = self.add(content_hash='same')
        self.vacancies.update_pipeline(vid, {'role': 'Cook'})
        self.source = self.vacancies.add_source('other_jobs')
        self.add(content_hash='same')
        self.assertEqual(len(self.deliveries()), 1)
        self.store.update(1, 'enabled', False, now=self.now+3)
        self.assertEqual(self.deliveries()[0]['state'], 'cancelled')
        self.store.update(1, 'enabled', True, now=self.now+4)
        self.assertIsNone(self.store.claim(self.now+5))
        self.now += 10
        self.add()
        self.assertIsNotNone(self.store.claim(self.now+3))

    def test_global_user_rate_limits_and_daily_cap_are_persistent(self):
        self.configure()
        self.configure(user=2)
        for _ in range(12):
            self.add()
        claimed = self.store.claim(self.now+3)
        self.assertEqual(claimed[0]['telegram_id'], 1)
        self.assertIsNone(JobAlerts(self.db).claim(self.now+4))
        self.assertEqual(self.store.claim(self.now+5)[0]['telegram_id'], 2)
        self.assertIsNone(self.store.claim(self.now+6))
        self.store.disable_existing(2)
        for index in range(1, 10):
            self.assertIsNotNone(self.store.claim(self.now+3+61*index))
        self.assertIsNone(self.store.claim(self.now+3+61*10))
        self.assertEqual(sum(r['attempted_at'] is not None and r['telegram_id']==1 for r in self.deliveries()), 10)

    async def test_delivery_success_and_all_notification_actions_ru_en(self):
        self.configure()
        vid = self.add()
        for lang in ('ru', 'en'):
            text, markup = notification(lang, self.store.preferences(1), self.vacancies.get(vid))
            self.assertIn('Chef de Partie', text)
            self.assertIn('Профессия совпадает' if lang=='ru' else 'Profession matches', text)
            self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row],
                [f'v:open:{vid}', f'ap:vacancy:{vid}', f'v:analyse:{vid}', f'v:save:{vid}', f'v:convert:{vid}', 'al:off'])
        self.db.set_language(1, 'ru')
        await deliver_one(self.bot, self.store, self.now+3)
        self.assertIn('Новая вакансия', self.bot.send_message.call_args.kwargs['text'])
        self.assertEqual(self.deliveries()[0]['message_id'], 123)
        self.assertEqual(self.deliveries()[0]['state'], 'sent')
        self.assertFalse(await deliver_one(self.bot, JobAlerts(self.db), self.now+100))

    async def test_floodwait_persists_global_delay_and_retries_only_rejected_send(self):
        self.configure()
        self.configure(user=2)
        self.add()
        self.bot.send_message.side_effect = RetryAfter(120)
        await deliver_one(self.bot, self.store, self.now+3)
        self.assertEqual(self.deliveries()[0]['state'], 'pending')
        self.assertFalse(await deliver_one(self.bot, JobAlerts(self.db), self.now+122))
        self.bot.send_message.side_effect = None
        self.assertTrue(await deliver_one(self.bot, self.store, self.now+124))

    async def test_ambiguous_network_error_never_retried(self):
        self.configure()
        self.add()
        self.bot.send_message.side_effect = TimedOut('SECRET_FIXTURE')
        await deliver_one(self.bot, self.store, self.now+3)
        self.assertEqual(self.deliveries()[0]['state'], 'unknown')
        self.assertIsNone(JobAlerts(self.db).claim(self.now+100))
        self.bot.send_message.assert_awaited_once()

    async def test_repeated_floodwait_counts_every_attempt_against_daily_limit(self):
        self.configure()
        self.add()
        self.bot.send_message.side_effect = RetryAfter(2)
        for index in range(10):
            self.assertTrue(await deliver_one(self.bot, self.store, self.now+3+61*index))
        self.assertFalse(await deliver_one(self.bot, self.store, self.now+3+61*10))
        self.assertEqual(self.bot.send_message.await_count, 10)

    async def test_blocked_user_disabled_and_privacy_erases_all_alert_records(self):
        self.configure()
        self.add()
        self.add()
        self.bot.send_message.side_effect = Forbidden('private')
        await deliver_one(self.bot, self.store, self.now+3)
        self.assertFalse(self.store.preferences(1)['enabled'])
        self.assertEqual(self.deliveries()[1]['state'], 'cancelled')
        self.db.delete_user_records(1)
        self.store.disable_existing(1)
        self.assertEqual(self.deliveries(), [])
        with self.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM alert_preferences').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM alert_attempts').fetchone()[0], 0)

    def test_late_receipt_after_privacy_delete_cannot_modify_new_delivery(self):
        self.configure()
        self.add()
        old, _, _ = self.store.claim(self.now+3)
        self.db.delete_user_records(1)
        self.now += 10
        self.configure()
        self.add()
        new, _, _ = self.store.claim(self.now+3)
        self.assertNotEqual(old['id'], new['id'])
        self.store.finish(old, 'sent', message_id=123, now=self.now+4)
        self.assertEqual(self.deliveries()[0]['state'], 'sending')

    def test_dispatch_revalidates_source_changes_and_queue_expiry(self):
        self.configure()
        vid = self.add()
        self.vacancies.enable_source(self.source, False)
        self.assertIsNone(self.store.claim(self.now+3))
        self.assertEqual(self.deliveries()[0]['state'], 'cancelled')
        self.vacancies.enable_source(self.source, True)
        self.add()
        self.assertIsNone(self.store.claim(self.now+86403))
        self.assertEqual(self.deliveries()[1]['state'], 'expired')

    def test_two_workers_cannot_claim_same_delivery_or_recover_sending(self):
        self.configure()
        self.add()
        self.assertIsNotNone(self.store.claim(self.now+3))
        self.assertIsNone(JobAlerts(self.db).claim(self.now+100))
        self.assertIsNone(JobAlerts(self.db).claim(self.now+400))
        self.assertEqual(self.deliveries()[0]['state'], 'unknown')

    async def test_sender_lifecycle_cancels_background_task(self):
        app = SimpleNamespace(bot=self.bot, bot_data={'job_alerts': self.store})
        await start_sender(app)
        task = app.bot_data['alert_task']
        await stop_sender(app)
        self.assertTrue(task.done())
        self.assertNotIn('alert_task', app.bot_data)

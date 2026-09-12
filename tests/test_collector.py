import argparse
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import types, errors
from collector import Collector, bounded_backfill, reprocess_backfill
from collector_config import load_collector_settings
from db import Database
from vacancy_store import VacancyStore


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.db')
        self.store = VacancyStore(self.db)
        self.source_id = self.store.add_source('test_jobs')
        self.entity = types.Channel(id=1, title='Test', photo=types.ChatPhotoEmpty(),
            date=datetime.now(timezone.utc), broadcast=True, username='test_jobs')
        self.client = SimpleNamespace(get_entity=AsyncMock(return_value=self.entity), get_messages=AsyncMock())
        self.sleep = AsyncMock()
        self.collector = Collector(self.client, self.store, sleep=self.sleep)

    def message(self, number, text='Hiring analyst'):
        return SimpleNamespace(id=number, message=text, date=datetime.now(timezone.utc))

    async def test_first_poll_baseline_then_only_new_messages(self):
        self.client.get_messages.return_value = [self.message(900)]
        await self.collector.run_once()
        self.assertFalse(self.store.has_message(self.source_id, 900))
        async def messages(*args, **kwargs):
            self.assertEqual(kwargs['min_id'], 900)
            self.assertTrue(kwargs['reverse'])
            self.assertEqual(kwargs['limit'], 100)
            yield self.message(901)
            yield self.message(902, None)
        self.client.iter_messages = messages
        await self.collector.run_once()
        self.assertTrue(self.store.has_message(self.source_id, 902))
        self.assertEqual(self.store.sources()[0]['last_message_id'], 902)

    async def test_backfill_idempotence_and_persistent_source_settings(self):
        self.client.get_messages.return_value = [self.message(3), self.message(2)]
        await self.collector.run_once(backfill=2)
        await self.collector.run_once(backfill=2)
        with self.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM vacancies').fetchone()[0], 2)
        self.store.enable_source(self.source_id, False)
        self.assertEqual(self.store.add_source('test_jobs'), self.source_id)
        self.assertEqual(self.store.sources(enabled_only=True), [])

    async def test_new_enabled_sources_picked_up_on_next_poll(self):
        self.client.get_messages.return_value = [self.message(10)]
        await self.collector.run_once()
        self.store.enable_source(self.source_id, False)
        other_id = self.store.add_source('russian_jobs', 'Работа в Дубае')
        self.store.add_source('disabled_jobs', enabled=False)
        self.entity.username = 'russian_jobs'
        self.client.get_entity.reset_mock()
        await self.collector.run_once()
        self.client.get_entity.assert_awaited_once_with('russian_jobs')
        self.assertEqual(self.store.resolve_source(other_id)['last_message_id'], 10)

    async def test_processing_failure_does_not_lose_later_messages(self):
        self.client.get_messages.return_value = [self.message(3), self.message(2)]
        self.collector.processor = AsyncMock(side_effect=[ValueError('private-content'), {}])
        with self.assertLogs('collector') as logs:
            await self.collector.run_once(backfill=2)
        self.assertNotIn('private-content', ''.join(logs.output))
        self.assertTrue(self.store.has_message(self.source_id, 3))
        self.assertEqual(self.store.get(1)['processing_error'], 'pipeline_failed')

    async def test_new_collected_vacancy_enqueues_alert_but_retry_does_not(self):
        from services.job_alerts import JobAlerts
        from services.vacancy_pipeline import analyze_text
        now = datetime.now(timezone.utc).timestamp()
        alerts = JobAlerts(self.db)
        alerts.update(1, 'roles', 'Analyst', now=now-2)
        alerts.update(1, 'enabled', True, now=now-2)
        async def process(client, source, message):
            return analyze_text(message.message)
        self.collector.processor = process
        source = self.store.sources()[0]
        message = self.message(1, 'Role: Analyst\nHiring in Dubai. Salary 6000 AED. Send CV jobs@example.com')
        await self.collector.process_message(source, message)
        await self.collector.process_message(source, message, force=True)
        with self.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM alert_deliveries').fetchone()[0], 1)

    async def test_flood_wait_respected_and_private_group_refused(self):
        self.client.get_entity.side_effect = [errors.FloodWaitError(None, capture=12), self.entity]
        self.client.get_messages.return_value = [self.message(1)]
        await self.collector.run_once()
        self.sleep.assert_any_await(12)
        self.client.get_entity.side_effect = None
        self.entity.broadcast = False
        self.client.get_messages.reset_mock()
        await self.collector.run_once(backfill=5)
        self.client.get_messages.assert_not_awaited()

    def test_additive_migration_preserves_v03(self):
        self.db.set_language(1, 'ru')
        self.db.save_profile(1, full_name='Test')
        app_id = self.db.add_application(1, 'Acme', 'Analyst')
        cv_id = self.db.add_resume(1, 'test.txt', 'unused', 'SQL')
        VacancyStore(Database(self.db.path))
        self.assertEqual(self.db.get_language(1), 'ru')
        self.assertEqual(self.db.get_profile(1)['full_name'], 'Test')
        self.assertEqual(self.db.active_resume(1)['id'], cv_id)
        self.assertEqual(self.db.get_application(1, app_id)['role'], 'Analyst')

    def test_backfill_bounds_and_settings_do_not_require_bot_token(self):
        for value in ('0', '501', 'bad', '-1'):
            with self.assertRaises(argparse.ArgumentTypeError):
                bounded_backfill(value)
        self.assertEqual(bounded_backfill('50'), 50)
        with patch.dict('os.environ', {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32}, clear=True):
            settings = load_collector_settings()
            self.assertNotIn('a' * 32, repr(settings))
            self.assertEqual(settings.session_path.name, 'dubai_job_collector')
        with patch.dict('os.environ', {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32,
                                     'TELEGRAM_SESSION_NAME': '../outside'}, clear=True):
            with self.assertRaises(ValueError):
                load_collector_settings()

    async def test_cancelled_poll_does_not_advance_uncommitted_message(self):
        import asyncio
        self.client.get_messages.return_value = [self.message(3)]
        self.collector.processor = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.collector.run_once(backfill=1)
        self.assertEqual(self.store.sources()[0]['last_message_id'], 0)
        self.assertFalse(self.store.has_message(self.source_id, 3))

    async def test_storage_failure_keeps_checkpoint_for_retry(self):
        self.client.get_messages.return_value = [self.message(2), self.message(1)]
        original = self.store.insert
        def insert(source, message, **values):
            if message == 2:
                raise OSError('sensitive')
            return original(source, message, **values)
        with patch.object(self.store, 'insert', side_effect=insert):
            await self.collector.run_once(backfill=2)
        self.assertEqual(self.store.sources()[0]['last_message_id'], 1)
        await self.collector.run_once(backfill=2)
        self.assertTrue(self.store.has_message(self.source_id, 2))

    async def test_malformed_post_does_not_block_valid_backfill(self):
        self.client.get_messages.return_value = [self.message('bad'), self.message(2)]
        await self.collector.run_once(backfill=2)
        self.assertTrue(self.store.has_message(self.source_id, 2))

    def test_pending_reprocess_repairs_duplicate_links_and_stats(self):
        from services.vacancy_pipeline import analyze_text
        values = analyze_text('Hiring analyst in Dubai. Send CV jobs@example.com')
        first, _ = self.store.insert(self.source_id, 1, raw_text=values['raw_text'])
        second, _ = self.store.insert(self.source_id, 2, **values, ocr_status='failed')
        self.assertEqual(len(self.store.retry_candidates()), 1)
        self.store.update_pipeline(first, values)
        self.assertEqual(self.store.get(second)['duplicate_of'], first)
        self.assertEqual(self.store.stats()['duplicates'], 1)
        self.assertEqual(self.store.stats()['detected'], 2)
        self.assertEqual(self.store.stats()['ocr_failures'], 1)
        self.assertEqual(len(self.store.retry_candidates(ocr_only=True)), 1)
        # Canonical content can change during OCR retry without orphaning duplicates.
        changed = analyze_text('Hiring waiter in Sharjah. Send CV other@example.com')
        self.store.update_pipeline(first, changed)
        self.assertIsNone(self.store.get(second)['duplicate_of'])

    async def test_reprocess_backfill_updates_photos_and_reuses_dedup(self):
        from services.vacancy_pipeline import analyze_text
        content = 'Hiring analyst in Dubai. SQL required. Send CV jobs@example.com'
        old, _ = self.store.insert(self.source_id, 1, ocr_status='disabled', detection_status='not_vacancy')
        other = self.store.add_source('other_jobs')
        duplicate, _ = self.store.insert(other, 1, **analyze_text(content))
        self.store.save(2, duplicate)
        self.store.checkpoint(self.source_id, 50)
        before = self.store.sources()[0]
        self.client.get_messages.return_value = [self.message(2), self.message(1, '')]
        self.collector.processor = AsyncMock(return_value=dict(**analyze_text('', content), ocr_status='processed'))
        for _ in range(2):
            await reprocess_backfill(self.collector, 'test_jobs', 2)
        self.assertEqual(self.store.message_record(self.source_id, 1)['id'], old)
        self.assertEqual(self.store.get(old)['ocr_status'], 'processed')
        self.assertEqual(self.store.get(duplicate)['duplicate_of'], old)
        self.assertTrue(self.store.is_saved(2, old))
        self.assertEqual(self.store.sources()[0], before)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.stats()['collected'], 3)  # Three source-post records, one vacancy.
        self.client.get_messages.assert_awaited_with(self.entity, limit=2, wait_time=1)
        self.assertEqual(self.collector.processor.await_count, 4)

    async def test_reprocess_backfill_flood_wait_and_bad_message_continue(self):
        from services.vacancy_pipeline import analyze_text
        self.client.get_messages.side_effect = [errors.FloodWaitError(None, capture=3),
                                                [self.message(3), self.message(2), self.message(1)]]
        self.collector.processor = AsyncMock(side_effect=[
            errors.FloodWaitError(None, capture=4), ValueError('secret-details'),
            analyze_text('Hiring analyst in Dubai @hr_dubai'), analyze_text('Hiring driver in Dubai @hr_dubai')])
        with self.assertLogs('collector') as logs:
            await reprocess_backfill(self.collector, 'test_jobs', 3)
        self.sleep.assert_any_await(3)
        self.sleep.assert_any_await(4)
        self.assertNotIn('secret-details', ''.join(logs.output))
        self.assertTrue(self.store.has_message(self.source_id, 2))
        self.assertTrue(self.store.has_message(self.source_id, 3))
        self.assertFalse(self.store.has_message(self.source_id, 1))
        self.assertIsNone(self.store.sources()[0]['last_checked_at'])

    async def test_reprocess_backfill_refuses_invalid_limits_and_sources(self):
        for limit in (0, 501, 'bad'):
            with self.assertRaises(argparse.ArgumentTypeError):
                await reprocess_backfill(self.collector, 'test_jobs', limit)
        await reprocess_backfill(self.collector, 'unknown_jobs', 5)
        self.client.get_entity.assert_not_awaited()
        self.store.enable_source(self.source_id, False)
        await reprocess_backfill(self.collector, 'test_jobs', 5)
        self.client.get_entity.assert_not_awaited()
        self.store.enable_source(self.source_id, True)
        self.entity.broadcast = False
        await reprocess_backfill(self.collector, 'test_jobs', 5)
        self.client.get_messages.assert_not_awaited()

    async def test_reprocess_backfill_cancel_preserves_cursor(self):
        import asyncio
        self.client.get_messages.return_value = [self.message(1)]
        self.collector.processor = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await reprocess_backfill(self.collector, 'test_jobs', 1)
        self.assertIsNone(self.store.sources()[0]['last_checked_at'])

    async def test_reprocess_backfill_logs_ocr_updates_and_duplicates(self):
        from services.vacancy_pipeline import analyze_text
        self.store.insert(self.source_id, 1, ocr_status='disabled')
        photo = self.message(1, '')
        photo.photo = True
        self.client.get_messages.return_value = [self.message(2), photo]
        self.collector.processor = AsyncMock(return_value=dict(
            **analyze_text('', 'Hiring analyst in Dubai. Send CV jobs@example.com'), ocr_status='processed'))
        with self.assertLogs('collector') as logs:
            await reprocess_backfill(self.collector, 'test_jobs', 2)
        output = '\n'.join(logs.output)
        for label in ('OCR rerun requested', 'Vacancy updated', 'Duplicate', 'processed=2', 'skipped=0', 'failed=0'):
            self.assertIn(label, output)
        self.assertNotIn('jobs@example.com', output)

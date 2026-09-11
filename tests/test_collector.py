import argparse
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import types, errors
from collector import Collector, bounded_backfill
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

    async def test_processing_failure_does_not_lose_later_messages(self):
        self.client.get_messages.return_value = [self.message(3), self.message(2)]
        self.collector.processor = AsyncMock(side_effect=[ValueError('private-content'), {}])
        with self.assertLogs('collector') as logs:
            await self.collector.run_once(backfill=2)
        self.assertNotIn('private-content', ''.join(logs.output))
        self.assertTrue(self.store.has_message(self.source_id, 3))
        self.assertEqual(self.store.get(1)['processing_error'], 'pipeline_failed')

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

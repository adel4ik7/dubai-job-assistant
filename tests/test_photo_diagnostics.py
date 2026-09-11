"""Regression for Telethon filename rewriting and safe per-stage diagnostics."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
from telethon.client.downloads import DownloadMethods
from collector import Collector, reprocess_message
from db import Database
from services.collector_diagnostics import Diagnostics, safe_preview
from services.vacancy_pipeline import VacancyPipeline, analyze_text
from vacancy_store import VacancyStore

TEXT = 'HIRING ANALYST in Dubai. SQL required. Salary 5000 AED. Contact Alice Smith alice@example.com +971501234567 @alice_hr'


class PhotoDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = VacancyStore(Database(self.root / 'test.db'))
        self.store.add_source('test_jobs')
        self.source = self.store.sources()[0]
        self.settings = SimpleNamespace(ocr_enabled=True, keep_media=False, media_dir=self.root / 'media')
        self.message = SimpleNamespace(id=7, message='', date=datetime.now(timezone.utc), photo=True)
        async def download(message, file):
            actual = DownloadMethods._get_proper_filename(file, 'photo', '.jpg')
            self.assertNotEqual(actual, file)  # Precisely the old failure condition.
            Image.new('RGB', (800, 400), 'white').save(actual)
            return actual
        self.client = SimpleNamespace(download_media=AsyncMock(side_effect=download))
        self.engine = SimpleNamespace(read=lambda path: TEXT)
        self.diagnostics = Diagnostics(True)
        self.pipeline = VacancyPipeline(self.settings, self.engine, self.diagnostics)
        self.collector = Collector(self.client, self.store, self.pipeline, diagnostics=self.diagnostics)

    async def test_extension_download_to_db_and_visibility_and_masked_logs(self):
        with self.assertLogs('collector') as logs:
            row = await self.collector.process_message(self.source, self.message)
        self.assertEqual(row['ocr_status'], 'processed')
        self.assertEqual(row['detection_status'], 'vacancy')
        self.assertEqual(row['salary_min'], 5000)
        self.assertEqual(row['email'], 'alice@example.com')
        self.assertEqual(len(self.store.list()), 1)
        events = [json.loads(line.split('vacancy_debug ', 1)[1]) for line in logs.output if 'vacancy_debug ' in line]
        ocr = next(e for e in events if e['stage'] == 'ocr')
        self.assertTrue(ocr['media_downloaded'])
        self.assertTrue(ocr['temp_path_exists'])
        self.assertTrue(ocr['ocr_called'])
        self.assertEqual(ocr['ocr_chars'], len(TEXT))
        self.assertLessEqual(len(ocr['ocr_preview']), 120)
        self.assertTrue(events[-1]['ui_eligible'])
        self.assertTrue(events[-1]['vacancy_saved'])
        all_logs = '\n'.join(logs.output)
        for private in ('Alice', 'Smith', 'alice@example.com', '971501234567', 'alice_hr', str(self.root)):
            self.assertNotIn(private, all_logs)
        self.assertEqual(list(self.settings.media_dir.iterdir()), [])

    async def test_disabled_and_missing_download_diagnostics(self):
        self.settings.ocr_enabled = False
        with self.assertLogs('collector') as logs:
            result = await self.pipeline(self.client, self.source, self.message)
        self.assertEqual(result['ocr_status'], 'disabled')
        self.assertIn('ocr_disabled', ''.join(logs.output))
        self.client.download_media.assert_not_awaited()
        self.settings.ocr_enabled = True
        self.client.download_media.side_effect = None
        self.client.download_media.return_value = None
        with self.assertLogs('collector') as logs:
            result = await self.pipeline(self.client, self.source, self.message)
        self.assertEqual(result['ocr_status'], 'failed')
        self.assertIn('"ocr_called": false', ''.join(logs.output))

    async def test_single_retry_replaces_disabled_row_without_advancing_cursor(self):
        from telethon import types, errors
        existing, _ = self.store.insert(self.source['id'], 7, ocr_status='disabled', detection_status='not_vacancy')
        self.store.checkpoint(self.source['id'], 10)
        entity = types.Channel(id=1, title='Test', photo=types.ChatPhotoEmpty(), date=datetime.now(timezone.utc), broadcast=True, username='test_jobs')
        self.client.get_entity = AsyncMock(side_effect=[errors.FloodWaitError(None, capture=4), entity])
        self.client.get_messages = AsyncMock(return_value=self.message)
        self.collector.sleep = AsyncMock()
        result = await reprocess_message(self.collector, 'test_jobs', '7')
        self.assertEqual(result['id'], existing)
        self.assertEqual(result['ocr_status'], 'processed')
        self.assertEqual(self.store.sources()[0]['last_message_id'], 10)
        self.collector.sleep.assert_awaited_once_with(4)
        self.client.get_messages.assert_awaited_once_with(entity, ids=7)
        self.assertEqual(len(self.store.list()), 1)

    async def test_retry_does_not_erase_successful_ocr_and_regular_poll_still_skips(self):
        row = await self.collector.process_message(self.source, self.message)
        self.client.download_media.reset_mock()
        await self.collector.process_message(self.source, self.message)
        self.client.download_media.assert_not_awaited()
        self.settings.ocr_enabled = False
        await self.collector.process_message(self.source, self.message, force=True)
        self.assertEqual(self.store.get(row['id'])['ocr_text'], TEXT)

    async def test_unconfigured_source_refused_before_network(self):
        self.client.get_entity = AsyncMock()
        await reprocess_message(self.collector, 'unknown_jobs', '7')
        self.client.get_entity.assert_not_awaited()

    def test_preview_masks_unknown_names_contacts_codes_and_tokens(self):
        for secret in ('Alice Smith', 'secret_hash_a1b2c3', '123456', '+971501234567', 'https://example.com/secret', '@privateuser'):
            self.assertNotIn(secret, safe_preview('Hiring SQL ' + secret))
        self.assertEqual(safe_preview('Hiring SQL'), 'Hiring SQL')

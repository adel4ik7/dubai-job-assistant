import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from collector import main
from db import Database
from vacancy_store import VacancyStore, normalize_source


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.path = self.root / 'data' / 'bot.sqlite3'
        self.db = Database(self.path)
        self.store = VacancyStore(self.db)
        self.seed = self.root / 'sources.json'
        self.seed.write_text(json.dumps([{'telegram_username': 'jobs_in_dubai', 'title': 'Dubai jobs'}]), encoding='utf-8')

    def test_links_and_usernames_are_one_source(self):
        variants = ['jobs_in_dubai', '@JOBS_IN_DUBAI', 't.me/jobs_in_dubai',
                    'https://t.me/jobs_in_dubai/', 'http://www.t.me/jobs_in_dubai',
                    'https://t.me/s/jobs_in_dubai']
        ids = {self.store.add_source(value) for value in variants}
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(normalize_source(variants[1]), 'jobs_in_dubai')

    def test_invalid_links_rejected(self):
        for value in ['https://example.com/jobs_in_dubai', 'https://t.me/+invite',
                      'https://t.me/joinchat/invite', 'https://t.me/jobs_in_dubai/12',
                      'https://t.me/jobs_in_dubai?q=1', 'https://t.me/jobs_in_dubai#x',
                      'https://user@t.me/jobs_in_dubai', 'https://t.me:443/jobs_in_dubai',
                      '../channel', 'bad name', '@@@channel']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.add_source(value)

    def test_titles_and_enabled_preferences_survive_seed(self):
        sid = self.store.add_source('jobs_in_dubai', 'Работа в Дубае / Dubai jobs')
        self.store.enable_source('https://t.me/jobs_in_dubai', False)
        self.store.seed_sources(self.seed)
        row = self.store.resolve_source(sid)
        self.assertEqual(row['title'], 'Работа в Дубае / Dubai jobs')
        self.assertEqual(row['enabled'], 0)
        self.store.enable_source(str(sid), True)
        self.assertEqual(len(self.store.sources(True)), 1)
        for title in ['', 'x' * 201, 'bad\nname']:
            with self.assertRaises(ValueError):
                self.store.add_source('jobs_in_dubai', title)
        with self.assertRaises(ValueError):
            self.store.enable_source('missing_channel', True)

    def test_safe_removal_retains_saved_posts_and_cursor(self):
        sid = self.store.add_source('jobs_in_dubai')
        self.store.checkpoint(sid, 33267)
        vid, _ = self.store.insert(sid, 33267, detection_status='vacancy', raw_text='Hiring analyst')
        self.store.save(42, vid)
        self.store.remove_source('@jobs_in_dubai')
        self.store.remove_source(sid)
        store = VacancyStore(Database(self.path))
        store.seed_sources(self.seed)
        self.assertEqual(store.sources(), [])
        self.assertEqual(store.sources(True), [])
        self.assertEqual(len(store.sources(include_removed=True)), 1)
        self.assertEqual(store.get(vid)['source_message_id'], 33267)
        self.assertTrue(store.is_saved(42, vid))
        self.assertEqual(store.list(saved_user=42)[0]['id'], vid)
        with self.assertRaises(ValueError):
            store.enable_source(sid, True)
        self.assertEqual(store.add_source('https://t.me/jobs_in_dubai'), sid)
        self.assertEqual(store.sources(True)[0]['last_message_id'], 33267)

    def test_additive_migration_preserves_old_source(self):
        legacy = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as conn:
            conn.executescript('''CREATE TABLE vacancy_sources (
                id INTEGER PRIMARY KEY, telegram_username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                title TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                source_type TEXT NOT NULL DEFAULT 'telegram_channel',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_message_id INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT);
                INSERT INTO vacancy_sources(id,telegram_username,title,enabled,last_message_id)
                VALUES(7,'jobs_in_dubai','Old title',0,33267);''')
        store = VacancyStore(Database(legacy))
        row = store.resolve_source(7)
        self.assertIsNone(row['removed_at'])
        self.assertEqual((row['title'], row['enabled'], row['last_message_id']), ('Old title', 0, 33267))

    def test_cli_management_needs_no_telegram_or_settings(self):
        def command(*args):
            output = io.StringIO()
            with patch('collector.BASE_DIR', self.root), patch('sys.argv', ['collector.py', *args]), \
                    patch('collector.load_collector_settings') as settings, \
                    patch('collector.TelegramClient') as client, redirect_stdout(output):
                main()
                settings.assert_not_called()
                client.assert_not_called()
            return output.getvalue()
        command('--add-source', 'https://t.me/russian_jobs', '--source-title', 'Работа / Jobs')
        self.assertIn('Работа / Jobs enabled', command('--list-sources'))
        command('--disable-source', '@russian_jobs')
        self.assertIn('Работа / Jobs disabled', command('--list-sources'))
        command('--enable-source', 'russian_jobs')
        command('--remove-source', 'russian_jobs')
        self.assertIn('Работа / Jobs removed', command('--list-sources'))
        command('--add-source', 'russian_jobs')
        self.assertEqual(len(self.store.sources(True)), 2)

    def test_cli_title_requires_add(self):
        with patch('sys.argv', ['collector.py', '--source-title', 'Title']), redirect_stdout(io.StringIO()), \
                patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 2)

    def test_source_quality_counts_hidden_duplicate_and_ocr_outcomes(self):
        first = self.store.add_source('english_jobs')
        second = self.store.add_source('russian_jobs', 'Работа')
        self.store.add_source('empty_jobs', enabled=False)
        vid, _ = self.store.insert(first, 1, detection_status='vacancy',
                                  detection_score=80, content_hash='same')
        self.store.insert(first, 2, detection_status='probably_vacancy',
                          detection_score=60, ocr_status='processed')
        self.store.insert(first, 3, detection_status='not_vacancy',
                          detection_score=20, ocr_status='failed')
        self.store.insert(first, 4, ocr_status='unavailable')
        self.store.insert(second, 1, detection_status='vacancy',
                          detection_score=80, content_hash='same', ocr_status='empty')
        self.store.remove_source(second)
        rows = {r['telegram_username']: r for r in self.store.source_quality()}
        row = rows['english_jobs']
        self.assertEqual([row[k] for k in ('processed', 'vacancies', 'probably_vacancy',
                         'not_vacancy', 'pending', 'ocr_messages', 'ocr_failures',
                         'ocr_unavailable', 'duplicates')], [4, 1, 1, 1, 1, 3, 1, 1, 0])
        self.assertEqual(row['avg_detection_score'], 40)
        self.assertEqual(row['useful_rate'], 0.5)
        self.assertEqual(rows['russian_jobs']['duplicates'], 1)
        self.assertTrue(rows['russian_jobs']['removed_at'])
        self.assertEqual(rows['empty_jobs']['processed'], 0)
        self.assertEqual(rows['empty_jobs']['duplicates'], 0)
        self.assertEqual(rows['empty_jobs']['avg_detection_score'], 0)
        self.assertEqual(rows['empty_jobs']['useful_rate'], 0)
        # Duplicate insert and reprocessing replace outcomes, never inflate counts.
        self.store.insert(first, 1, detection_status='vacancy', detection_score=80)
        self.store.update_pipeline(vid, dict(detection_status='not_vacancy', detection_score=0))
        updated = next(r for r in self.store.source_quality() if r['id'] == first)
        self.assertEqual(updated['processed'], 4)
        self.assertEqual(updated['vacancies'], 0)
        self.assertEqual(updated['useful_rate'], 0.25)

    def test_source_quality_cli_is_offline_and_does_not_expose_post_content(self):
        sid = self.store.add_source('russian_jobs', 'Работа / Jobs')
        self.store.insert(sid, 1, raw_text='PRIVATE_FIXTURE_CONTENT',
                          detection_status='vacancy', detection_score=80)
        output = io.StringIO()
        with patch('collector.BASE_DIR', self.root), \
                patch('sys.argv', ['collector.py', '--source-quality']), \
                patch('collector.load_collector_settings') as settings, \
                patch('collector.TelegramClient') as client, redirect_stdout(output):
            main()
        settings.assert_not_called()
        client.assert_not_called()
        rendered = output.getvalue()
        self.assertIn('SOURCE QUALITY', rendered)
        self.assertIn('russian_jobs | Работа / Jobs | enabled', rendered)
        self.assertIn('processed=1 vacancies=1', rendered)
        self.assertIn('useful_rate=100.0%', rendered)
        self.assertNotIn('PRIVATE_FIXTURE_CONTENT', rendered)

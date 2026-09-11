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

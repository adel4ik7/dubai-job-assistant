import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
from db import Database
from vacancy_store import VacancyStore
from services.vacancy_detector import detect_vacancy
from services.vacancy_parser import parse_vacancy, salary, contacts
from services.vacancy_dedup import content_hash
from services.ocr import preprocess
from services.vacancy_pipeline import VacancyPipeline, analyze_text


class VacancyParsingTests(unittest.TestCase):
    def test_vacancy_detection_english_russian(self):
        for value in ('Hiring accountant in Dubai. Send CV to jobs@example.com. Salary 3500 AED.',
                      'Требуется бухгалтер в ОАЭ. Зарплата 5000 AED. Отправьте резюме @hr_dubai.'):
            self.assertEqual(detect_vacancy(value)['detection_status'], 'vacancy')
        self.assertEqual(detect_vacancy('Accountant required in Dubai. Contact jobs@example.com')['detection_status'], 'probably_vacancy')

    def test_not_vacancy_repeated_keyword_seeker_and_advert(self):
        for value in ('', 'hiring ' * 20, 'salary', 'работа', 'Good morning Dubai!',
                      'Looking for a job as accountant in Dubai. My CV @candidate salary 5000 AED',
                      'Recruitment training course in Dubai for manager. Contact @school salary 5000 AED'):
            self.assertEqual(detect_vacancy(value)['detection_status'], 'not_vacancy', value)

    def test_salary_patterns_without_inventing_currency(self):
        for value, low, high, currency in (
            ('3500 AED', 3500, 3500, 'AED'), ('AED 3500', 3500, 3500, 'AED'),
            ('3,500-5,000 AED', 3500, 5000, 'AED'), ('5k AED', 5000, 5000, 'AED'),
            ('salary 8000 + accommodation', 8000, 8000, None), ('Salary competitive', None, None, None)):
            with self.subTest(value=value):
                self.assertEqual(salary(value), dict(salary_min=low, salary_max=high, salary_currency=currency))

    def test_contacts_and_locations(self):
        result = parse_vacancy('Company: Acme\nRole: Analyst\nDubai / UAE / Abu Dhabi / Sharjah\n'
            'jobs@Example.com +971 50 123 4567 Telegram @hr_dubai\nApply https://example.com/jobs/1')
        self.assertEqual(result['email'], 'jobs@example.com')
        self.assertEqual(result['phone'], '+971501234567')
        self.assertEqual(result['telegram_contact'], '@hr_dubai')
        self.assertEqual(result['company'], 'Acme')
        self.assertIn('Sharjah', result['location'])
        self.assertEqual(result['application_url'], 'https://example.com/jobs/1')
        self.assertEqual(contacts('050-123-4567')['phone'], result['phone'])

    def test_missing_fields_and_or_requirement_preserved(self):
        value = 'Power BI or Tableau required. SQL. Fluent English.'
        parsed = parse_vacancy(value)
        self.assertIsNone(parsed['company'])
        self.assertIsNone(parsed['role'])
        self.assertIsNone(parsed['salary_min'])
        self.assertTrue(any('Power BI' in s and 'Tableau' in s and ' or ' in s for s in parsed['skills']))
        self.assertEqual(parsed['raw_text'], value)
        self.assertEqual(parse_vacancy('Role: Accountant or Analyst')['role'], 'Accountant or Analyst')

    def test_hash_normalization_and_contact_differences(self):
        first = 'HIRING Analyst! Email: JOBS@example.com. +971 50 123 4567\nSource: @channel_a'
        second = 'Hiring   analyst Email jobs@example.com 050-123-4567\nИсточник: https://t.me/channel_b/12'
        self.assertEqual(content_hash(first), content_hash(second))
        self.assertNotEqual(content_hash(first), content_hash(first.replace('JOBS@', 'OTHER@')))
        self.assertIsNone(content_hash(''))

    def test_duplicate_and_source_message_unique(self):
        with tempfile.TemporaryDirectory() as root:
            store = VacancyStore(Database(Path(root) / 'test.db'))
            a, b = store.add_source('channel_a'), store.add_source('channel_b')
            values = analyze_text('Hiring analyst in Dubai. SQL required. Send CV @hr_dubai')
            first, inserted = store.insert(a, 1, **values)
            same, inserted_again = store.insert(a, 1, **values)
            second, _ = store.insert(b, 1, **values)
            self.assertTrue(inserted)
            self.assertFalse(inserted_again)
            self.assertEqual(same, first)
            self.assertEqual(store.get(second)['duplicate_of'], first)
            self.assertEqual(store.get(second)['source_id'], b)

    def test_preprocessing_keeps_original_and_bounds_size(self):
        with tempfile.TemporaryDirectory() as root:
            original, prepared = Path(root) / 'original.png', Path(root) / 'prepared.png'
            Image.new('RGB', (300, 150), 'white').save(original)
            before = original.read_bytes()
            preprocess(original, prepared)
            self.assertEqual(original.read_bytes(), before)
            with Image.open(prepared) as result:
                self.assertEqual(result.mode, 'L')
                self.assertEqual(result.size, (600, 300))

    def test_media_retention_only_removes_expired_generated_files(self):
        import os
        import time
        from services.vacancy_pipeline import prune_media
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for name in ('1_2.image', '1_3.image', 'personal.png'):
                (root / name).write_bytes(b'fixture')
            for name in ('1_2.image', 'personal.png'):
                os.utime(root / name, (time.time() - 10 * 86400,) * 2)
            prune_media(root, 7)
            self.assertFalse((root / '1_2.image').exists())
            self.assertTrue((root / '1_3.image').exists())
            self.assertTrue((root / 'personal.png').exists())

    def test_phone_variants_and_source_footer_not_a_contact(self):
        for value in ('+971 (0)50 123 4567', '00971 50 123 4567', '971501234567'):
            self.assertEqual(contacts(value)['phone'], '+971501234567')
        self.assertIsNone(contacts('Source: @channel_a')['telegram_contact'])
        self.assertEqual(contacts('Contact https://t.me/hr_dubai')['telegram_contact'], '@hr_dubai')


class OCRPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_fixture_pipeline_cleanup_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            settings = SimpleNamespace(ocr_enabled=True, keep_media=False, media_dir=Path(root))
            message = SimpleNamespace(id=1, message='', photo=True, file=SimpleNamespace(size=100))
            async def download(message, file):
                # Match Telethon's extension-appending download contract.
                actual = file + '.png'
                Image.new('RGB', (400, 200), 'white').save(actual, format='PNG')
                return actual
            client = SimpleNamespace(download_media=AsyncMock(side_effect=download))
            class FixtureEngine:
                def read(self, path):
                    with Image.open(path) as image:
                        assert image.mode == 'L'
                    return 'Hiring analyst in Dubai. Send CV jobs@example.com'
            pipeline = VacancyPipeline(settings, FixtureEngine())
            result = await pipeline(client, {'id': 1}, message)
            self.assertEqual(result['ocr_status'], 'processed')
            self.assertEqual(result['detection_status'], 'vacancy')
            self.assertEqual(list(Path(root).iterdir()), [])
            client.download_media.side_effect = ValueError('sensitive')
            with self.assertLogs('collector') as logs:
                result = await pipeline(client, {'id': 1}, message)
            self.assertEqual(result['ocr_status'], 'failed')
            self.assertNotIn('sensitive', ''.join(logs.output))
            self.assertEqual(list(Path(root).iterdir()), [])

    async def test_disabled_ocr_and_non_image_do_not_download(self):
        settings = SimpleNamespace(ocr_enabled=False)
        client = SimpleNamespace(download_media=AsyncMock())
        message = SimpleNamespace(id=1, message='Hiring analyst in Dubai @hr_dubai', photo=True)
        result = await VacancyPipeline(settings)(client, {'id': 1}, message)
        self.assertEqual(result['ocr_status'], 'disabled')
        self.assertEqual(result['detection_status'], 'vacancy')
        client.download_media.assert_not_awaited()

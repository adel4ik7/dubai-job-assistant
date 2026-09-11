import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from db import Database
from services.vacancy_search import PROFESSIONS, normalize_search, search_terms
from vacancy_store import VacancyStore


class VacancySearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = VacancyStore(Database(Path(temp.name) / 'test.sqlite3'))
        self.source = self.store.add_source('test_jobs')
        self.message_id = 0

    def add(self, **fields):
        self.message_id += 1
        fields.setdefault('detection_status', 'vacancy')
        return self.store.insert(self.source, self.message_id, **fields)[0]

    def ids(self, query, **filters):
        return [row['id'] for row in self.store.list(search=query, **filters)]

    def test_cook_query_finds_english_roles(self):
        for role in ('Chef de Partie', 'Cook', 'CDP', 'Commis', 'Demi-chef', 'Line cook'):
            with self.subTest(role=role):
                vid = self.add(role=role)
                self.assertIn(vid, self.ids('повар'))

    def test_english_chef_finds_russian(self):
        vid = self.add(role='Повар')
        self.assertIn(vid, self.ids('chef'))

    def test_searches_each_text_column_independently(self):
        for field in ('ocr_text', 'raw_text', 'combined_text'):
            with self.subTest(field=field):
                vid = self.add(**{field: 'Hiring a Chef de Partie'})
                self.assertIn(vid, self.ids('повар'))

    def test_company_and_location_search(self):
        vid = self.add(company='Acme Hospitality', location='Abu Dhabi')
        self.assertEqual(self.ids('ACME'), [vid])
        self.assertEqual(self.ids('hospital'), [vid])
        self.assertEqual(self.ids('abu-dhabi'), [vid])

    def test_normalization_and_cdp_equivalence(self):
        self.assertEqual(normalize_search('  СУ‑ШЕФ!!!  '), 'су шеф')
        vid = self.add(role='CHEF—DE   PARTIE')
        self.assertIn(vid, self.ids('CDP'))
        other = self.add(role='cdp')
        self.assertIn(other, self.ids(' chef-de-partie! '))
        sous = self.add(role='Sous Chef')
        self.assertIn(sous, self.ids('Су–шеф'))

    def test_all_groups_expand_both_directions_without_recursive_broadening(self):
        for group in PROFESSIONS:
            for term in group:
                self.assertTrue(set(map(normalize_search, group)) <= set(search_terms(term)))
        self.assertNotIn('cook', search_terms('повар холодного цеха'))
        self.assertNotIn('повар', search_terms('sous chef'))

    def test_filters_pagination_and_saved_scope(self):
        now = datetime.now(timezone.utc).isoformat()
        first = self.add(role='Cook', location='Dubai', salary_min=5000,
                         salary_currency='AED', published_at=now)
        second = self.add(role='Chef', location='Dubai', salary_min=6000,
                          salary_currency='AED', published_at=now)
        self.add(role='Cook', location='Sharjah', salary_min=6000, salary_currency='AED', published_at=now)
        self.add(role='Cook', location='Dubai', salary_min=1000, salary_currency='AED', published_at=now)
        self.add(role='Cook', location='Dubai', salary_min=6000, salary_currency='AED', published_at='2000-01-01')
        filters = dict(location='Dubai', salary_min=4000, source_id=self.source, days=7, limit=1)
        self.assertEqual(self.ids('повар', **filters), [second])
        self.assertEqual(self.ids('повар', offset=1, **filters), [first])
        self.assertEqual(self.ids('повар', offset=2, **filters), [])
        self.store.save(42, first)
        self.assertEqual(self.ids('chef', saved_user=42), [first])
        self.assertEqual(self.ids('chef', saved_user=99), [])

    def test_empty_unknown_and_sql_like_queries(self):
        self.add(role='Cook')
        self.assertEqual(self.ids('!!!'), [])
        self.assertEqual(self.ids("' OR 1=1 --"), [])
        self.assertEqual(self.ids('unknownprofession'), [])
        self.assertEqual(len(self.ids('')), 1)

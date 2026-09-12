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

    def test_title_synonyms_outrank_body_and_company(self):
        manager = self.add(role='Restaurant Manager', combined_text='Hiring manager to work with chef team.')
        company = self.add(company='повар staffing')
        ocr = self.add(ocr_text='Hiring Cook in Dubai')
        skills = self.add(skills_json='["cook"]')
        cdp = self.add(role='Chef de Partie')
        commis = self.add(role='Commis Chef')
        cook = self.add(role='Cook')
        order = self.ids('повар')
        for strong in (cdp, commis, cook):
            self.assertLess(order.index(strong), order.index(ocr))
            self.assertLess(order.index(strong), order.index(manager))
            self.assertLess(order.index(strong), order.index(company))
        self.assertLess(order.index(skills), order.index(ocr))

    def test_specific_title_and_seniority(self):
        generic = self.add(role='Chef')
        exact = self.add(role='Chef de Partie')
        alias = self.add(role='CDP')
        senior = self.add(role='Executive Chef')
        self.assertEqual(self.ids('chef de partie')[:2], [exact, alias])
        self.assertLess(self.ids('повар').index(generic), self.ids('повар').index(senior))
        self.assertIn(senior, self.ids('повар'))

    def test_random_mention_threshold_and_word_boundaries(self):
        weak = self.add(role='Manager', combined_text='General business administration ' * 40 + ' chef')
        accidental = self.add(role='Cookbook author', raw_text='Review cookbooks')
        self.assertNotIn(weak, self.ids('повар'))
        self.assertNotIn(accidental, self.ids('повар'))
        found = self.add(raw_text='Wanted: a cook for restaurant')
        self.assertIn(found, self.ids('повар'))

    def test_relevance_then_publication_with_more_than_thirty_results(self):
        newest = self.add(role='Cook', published_at='2026-09-12T08:00:00+00:00')
        older = self.add(role='Cook', published_at='2026-09-11T08:00:00+00:00')
        weak = self.add(raw_text='Wanted cook', published_at='2026-09-13T08:00:00+00:00')
        for i in range(32):
            self.add(role='Cook', published_at='2026-09-10T08:00:00+00:00')
        results = self.ids('cook')
        self.assertEqual(len(results), 35)
        self.assertEqual(results[:2], [newest, older])
        self.assertEqual(results[-1], weak)
        paged = [vid for offset in range(0, 35, 7) for vid in self.ids('cook', limit=7, offset=offset)]
        self.assertEqual(paged, results)

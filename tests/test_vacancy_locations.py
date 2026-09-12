import tempfile
import unittest
from pathlib import Path

from db import Database
from services.vacancy_locations import UAE_LOCATIONS, location_matcher, parse_location
from services.vacancy_parser import parse_vacancy
from vacancy_store import VacancyStore


class VacancyLocationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = VacancyStore(Database(Path(temp.name) / 'test.db'))
        self.sid = self.store.add_source('test_jobs')
        self.number = 0

    def add(self, **fields):
        self.number += 1
        return self.store.insert(self.sid, self.number, detection_status='vacancy',
                                 role='Cook', **fields)[0]

    def test_all_emirates_aliases_and_country_deduplication(self):
        for canonical, aliases in UAE_LOCATIONS.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(parse_location('Location: ' + alias), canonical)
                    self.assertTrue(location_matcher('ОАЭ')(alias, '', '', ''))
        self.assertEqual(parse_location('Location: Дубай / Dubai / UAE / United Arab Emirates'), 'Dubai')
        self.assertEqual(parse_location('Location: Абу-Даби'), 'Abu Dhabi')

    def test_workplace_overrides_foreign_contact_or_nationality(self):
        text = 'Hiring Cook\nLocation: Maldives\nRecruiter contact: Dubai, UAE'
        self.assertEqual(parse_vacancy(text)['location'], 'Maldives')
        strict = location_matcher(uae_only=True)
        # Applies even to legacy rows which previously parsed the contact's Dubai.
        self.assertFalse(strict('Dubai', text, '', ''))
        self.assertTrue(strict('', 'Location: Dubai\nCandidates from India welcome.', '', ''))
        self.assertFalse(strict('', 'Location: Unknown Island\nContact: Dubai', '', ''))
        self.assertFalse(strict('', 'UAE experience required\nSend CV hr@dubai.example', '', ''))
        self.assertFalse(strict('UAE', 'UAE experience required\nSend CV hr@dubai.example', '', ''))
        self.assertFalse(strict('', 'Location: Dubai / Qatar', '', ''))
        self.assertEqual(parse_vacancy('Hiring Cook in Qatar. UAE experience preferred')['location'], 'Qatar')
        self.assertIsNone(parse_vacancy('Hiring Cook\nLocation: Unknown Island')['location'])

    def test_strict_filter_legacy_ocr_and_existing_filters(self):
        first = self.add(location='Дубай', salary_min=5000, salary_currency='AED', published_at='2099-01-01')
        second = self.add(ocr_text='Job location: Ras Al Khaimah', salary_min=6000,
                          salary_currency='AED', published_at='2099-01-02')
        self.add(location='Dubai', raw_text='Location: Saudi Arabia\nRecruiter in Dubai')
        self.add(location='India')
        self.add(location=None)
        self.add(location='Sharjah', salary_min=1000, salary_currency='AED', published_at='2099-01-03')
        filters = dict(search='повар', uae_only=True, salary_min=4000, days=7, source_id=self.sid)
        self.assertEqual([r['id'] for r in self.store.list(**filters)], [second, first])
        self.assertEqual(self.store.list(limit=1, offset=1, **filters)[0]['id'], first)
        self.assertEqual([r['id'] for r in self.store.list(location='Dubai')], [first])
        self.assertEqual(len(self.store.list(location='United Arab Emirates')), 3)
        self.assertEqual(len(self.store.list()), 6)

    def test_profile_preference_does_not_replace_explicit_filter(self):
        abroad = self.add(location='India', published_at='2099-01-01')
        local = self.add(location='Ajman', published_at='2000-01-01')
        self.assertEqual(self.store.list(search='повар')[0]['id'], abroad)
        self.assertEqual(self.store.list(search='повар', preferred_location='Дубай')[0]['id'], local)
        self.assertEqual(self.store.list(preferred_location='UAE')[0]['id'], local)
        self.assertEqual([r['id'] for r in self.store.list(location='India', preferred_location='Dubai')], [abroad])

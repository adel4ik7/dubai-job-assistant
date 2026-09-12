import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from db import Database
from vacancy_store import VacancyStore
from services.job_alerts import JobAlerts
from services.matching_jobs import matching_jobs


class MatchingJobsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name)/'test.db')
        self.store = VacancyStore(self.db)
        self.source = self.store.add_source('test_jobs')
        self.alerts = JobAlerts(self.db)
        for field,value in dict(roles='повар', location='Dubai', uae_only=True, salary_min='5000').items():
            self.alerts.update(1,field,value)
        self.now = 1700000000
        self.number = 0

    def add(self, days=1, **fields):
        self.number += 1
        values = dict(role='Cook', location='Dubai', salary_min=6000, salary_currency='AED',
                      detection_status='vacancy', published_at=datetime.fromtimestamp(self.now-days*86400,timezone.utc).isoformat(),
                      source_url=f'https://t.me/test_jobs/{self.number}')
        values.update(fields)
        return self.store.insert(self.source,self.number,**values)[0]

    def results(self, **kwargs):
        return matching_jobs(self.db,self.alerts.preferences(1),now=self.now,**kwargs)

    def test_saved_preferences_synonyms_location_salary_unknown_and_no_side_effects(self):
        strong = self.add(role='Chef de Partie')
        unknown = self.add(role='Commis Chef',salary_min=None)
        partial = self.add(salary_min=4000,salary_max=5500)
        self.add(salary_min=4000,salary_max=4500)
        self.add(location='Qatar')
        self.add(raw_text='Location: India',location='Dubai')
        self.add(role='Accountant')
        rows,_ = self.results()
        self.assertEqual({r['id'] for r in rows},{strong,unknown,partial})
        self.assertFalse(self.alerts.preferences(1)['enabled'])
        with self.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM alert_deliveries').fetchone()[0],0)

    def test_exact_role_before_body_and_cv_is_secondary_then_date(self):
        self.alerts.update(1,'roles','chef de partie')
        exact = self.add(days=4,role='Chef de Partie',combined_text='exact')
        same_cv_best = self.add(days=5,role='Chef de Partie',combined_text='best')
        same_newer = self.add(days=2,role='Chef de Partie',combined_text='exact')
        generic = self.add(role='Chef',combined_text='best')
        body = self.add(role=None,ocr_text='Hiring chef de partie',combined_text='best')
        with patch('services.matching_jobs.analyse_match',side_effect=lambda cv,t: {'score':99 if t=='best' else 1}):
            rows,_ = self.results(cv_text='test CV')
        self.assertEqual([r['id'] for r in rows],[same_cv_best,same_newer,exact,generic,body])

    def test_freshness_expansion_dedup_and_nonvacancy(self):
        first = self.add(content_hash='same')
        self.source = self.store.add_source('other_jobs')
        self.add(content_hash='same')
        older = self.add(days=10)
        self.add(days=15)
        self.add(days=-1)
        self.add(detection_status='not_vacancy')
        rows,days = self.results()
        self.assertEqual(days,14)
        self.assertEqual({r['id'] for r in rows},{first,older})
        for _ in range(4):
            self.add()
        rows,days = self.results()
        self.assertEqual(days,7)
        self.assertNotIn(older,[r['id'] for r in rows])

    def test_bounded_candidates_and_fifty_results(self):
        for _ in range(305):
            self.add(combined_text='SQL')
        with patch('services.matching_jobs.analyse_match',return_value={'score':50}) as match:
            rows,_ = self.results(cv_text='SQL')
        self.assertEqual(len(rows),50)
        self.assertEqual(match.call_count,300)

    def test_no_professions_no_implicit_all_jobs_or_alert_mutation(self):
        self.add()
        self.assertEqual(matching_jobs(self.db,self.alerts.preferences(2),now=self.now),([],7))
        self.alerts.update(1,'enabled',True)
        before = self.alerts.preferences(1)
        self.results()
        self.assertEqual(self.alerts.preferences(1),before)

import unittest
from functools import partial
from urllib.parse import parse_qs, urlsplit

from locales import text
from services.vacancy_share import share_vacancy


class VacancyShareTests(unittest.TestCase):
    def setUp(self):
        self.vacancy = dict(role='Chef de Partie', company='Example Company', location='Dubai',
                            salary_min=5000, salary_max=6000, salary_currency='AED',
                            source_url='https://t.me/test_jobs/12')

    def render(self, language='en', username=None):
        return share_vacancy(self.vacancy, partial(text, language), username)

    def test_ru_en_public_text_and_share_url(self):
        for language in ('ru', 'en'):
            with self.subTest(language=language):
                body, url = self.render(language)
                self.assertIn('🔥 Chef de Partie — Dubai', body)
                self.assertIn('🏢 Example Company', body)
                self.assertIn('💰 5000–6000 AED', body)
                self.assertIn(text(language, 'share_source'), body)
                params = parse_qs(urlsplit(url).query)
                self.assertEqual(params['url'], [self.vacancy['source_url']])
                self.assertIn('Chef de Partie', params['text'][0])
                self.assertNotIn(self.vacancy['source_url'], params['text'][0])

    def test_missing_salary(self):
        self.vacancy['salary_min'] = None
        self.assertNotIn('💰', self.render()[0])

    def test_missing_company_and_location(self):
        self.vacancy.update(company=None, location=None)
        body, _ = self.render()
        self.assertNotIn('🏢', body)
        self.assertNotIn('📍', body)
        self.assertNotIn('Dubai', body)
        self.assertTrue(body.startswith('🔥 Chef de Partie\n'))

    def test_missing_source_and_bot_fallback(self):
        self.vacancy['source_url'] = None
        body, url = self.render()
        self.assertIsNone(url)
        self.assertNotIn('https://', body)
        self.assertNotIn(text('en', 'share_source'), body)
        body, url = self.render(username='DubaiJobsBot')
        self.assertEqual(parse_qs(urlsplit(url).query)['url'],
                         ['https://t.me/DubaiJobsBot?start=vacancy_share'])

    def test_bot_deep_link_and_private_fields_ignored(self):
        self.vacancy.update(raw_text='PRIVATE BODY', email='private@example.com', phone='PRIVATE PHONE')
        for language in ('ru', 'en'):
            body, url = self.render(language, 'DubaiJobsBot')
            self.assertIn('https://t.me/DubaiJobsBot?start=vacancy_share', body)
            self.assertIn(text(language, 'share_bot'), body)
            self.assertNotIn('PRIVATE', body)
            self.assertNotIn('private@example.com', body)
            self.assertIn('?start=vacancy_share', parse_qs(urlsplit(url).query)['text'][0])

    def test_invalid_links_and_empty_metadata(self):
        for source in ('javascript:alert(1)', 'https://user:pass@example.com', 'https://[', ''):
            self.vacancy = {'source_url': source}
            body, url = self.render('ru', 'invalid/user')
            self.assertEqual(body, '🔥 Вакансия')
            self.assertIsNone(url)

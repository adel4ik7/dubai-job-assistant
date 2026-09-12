import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from config import Settings
from locales import text
from services.vacancy_pipeline import analyze_text
from vacancy_store import application_defaults
from vacancy_ui import WAIT_VACANCY_INPUT


class VacancyUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = bot.build_application(Settings('123:' + 'x' * 30, None, None, root / 'test.db', root, vacancy_match_window=2))
        self.store = bot.vacancies.store
        self.source = self.store.add_source('test_jobs', 'Test Jobs')
        self.message = SimpleNamespace(reply_text=AsyncMock(), text='')
        self.query = SimpleNamespace(answer=AsyncMock(), data='', message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username='test', first_name='Test'),
            effective_message=self.message, message=self.message, callback_query=self.query,
            effective_chat=SimpleNamespace(type='private'))
        self.context = SimpleNamespace(user_data={}, args=[])

    def add(self, index=1, text='Role: Analyst\nCompany: Acme\nHiring in Dubai. SQL required. Salary 5000 AED. Send CV jobs@example.com', days=0):
        return self.store.insert(self.source, index, **analyze_text(text),
            source_url=f'https://t.me/test_jobs/{index}', published_at=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat())[0]

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    def reply(self):
        return self.message.reply_text.call_args.args[0]

    def labels(self):
        return [b.text for row in self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]

    async def test_search_relevance_navigation_over_thirty_cards(self):
        for index in range(1, 36):
            self.store.insert(self.source, index, role='Cook', detection_status='vacancy', published_at=f'2026-08-{(index % 28)+1:02d}')
        self.context.user_data['vacancy_filters'] = {'search': 'повар'}
        bot.db.set_language(1, 'ru')
        await self.click('v:latest')
        self.assertIn(text('ru', 'v_search_high'), self.reply())
        visited = []
        for index in range(35):
            visited.append(self.context.user_data['vacancy_navigation']['id'])
            if index < 34:
                await self.click('v:next:' + self.context.user_data['vacancy_page'][0])
        self.assertEqual(len(set(visited)), 35)
        self.assertNotIn(text('ru', 'v_next'), self.labels())
        await self.click('v:previous:' + self.context.user_data['vacancy_page'][0])
        self.assertEqual(self.context.user_data['vacancy_navigation']['id'], visited[-2])
        bot.db.set_language(1, 'en')
        await self.click('v:previous:' + self.context.user_data['vacancy_page'][0])
        self.assertIn(text('en', 'v_search_high'), self.reply())
        self.assertIn(text('en', 'v_back_search'), self.labels())
        self.assertEqual(await self.click('v:search'), WAIT_VACANCY_INPUT)

    async def test_menu_cards_ru_en_and_no_empty_fields(self):
        self.add(text='Hiring analyst in Dubai. Send CV jobs@example.com')
        for language in ('ru', 'en'):
            bot.db.set_language(1, language)
            await self.click('v:home')
            for key in ('v_latest', 'v_best', 'v_search', 'v_filters', 'v_saved'):
                self.assertIn(text(language, key), self.labels())
            await self.click('v:latest')
            self.assertIn('analyst', self.reply())
            self.assertNotIn(text(language, 'v_salary') + ':', self.reply())
            self.assertNotIn(text(language, 'v_company') + ':', self.reply())
            self.assertIn(text(language, 'v_open'), self.labels())

    async def test_saved_user_isolation_and_privacy(self):
        vacancy = self.add()
        await self.click(f'v:save:{vacancy}')
        await self.click(f'v:save:{vacancy}')
        self.assertEqual(len(self.store.list(saved_user=1)), 1)
        self.assertEqual(self.store.list(saved_user=2), [])
        self.store.unsave(2, vacancy)
        self.assertTrue(self.store.is_saved(1, vacancy))
        self.store.save(2, vacancy)
        bot.db.delete_user_records(1)
        self.assertFalse(self.store.is_saved(1, vacancy))
        self.assertTrue(self.store.is_saved(2, vacancy))
        self.assertIsNotNone(self.store.get(vacancy))

    async def test_conversion_review_edit_preserves_source_and_cancel(self):
        vacancy = self.add()
        await self.click(f'v:convert:{vacancy}')
        self.assertEqual(bot.db.list_applications(1), [])
        self.assertEqual(self.context.user_data['form']['values'], application_defaults(self.store.get(vacancy)))
        self.message.text = 'Corrected company'
        await bot.product.form_received(self.update, self.context)
        while self.context.user_data.get('form'):
            form = self.context.user_data['form']
            field = form['fields'][form['index']]
            await self.click(('p:keep:' if form['values'][field] else 'p:skip:') + form['nonce'])
        record = bot.db.list_applications(1)[0]
        self.assertEqual(record['company'], 'Corrected company')
        self.assertEqual(record['role'], 'Analyst')
        self.assertEqual(record['status'], 'saved')
        self.assertEqual(record['date_applied'], '')
        self.assertEqual(record['salary'], '5000 AED')
        self.assertEqual(record['source_url'], 'https://t.me/test_jobs/1')
        await self.click(f'v:convert:{vacancy}')
        await bot.cancel(self.update, self.context)
        self.assertEqual(len(bot.db.list_applications(1)), 1)

    async def test_search_combined_filters_and_validation(self):
        self.add()
        self.add(2, 'Hiring accountant in Sharjah. Salary 2000 AED. Contact jobs@example.com', days=30)
        self.assertEqual(await self.click('v:search'), WAIT_VACANCY_INPUT)
        self.message.text = 'aCmE'
        await bot.vacancies.input_received(self.update, self.context)
        self.assertIn('Acme', self.reply())
        self.assertEqual(len(self.store.list(location='Dubai', salary_min=3000, days=7, source_id=self.source)), 1)
        self.assertEqual(self.store.list(salary_min=9000), [])
        await self.click('v:input:days')
        self.message.text = '-10'
        self.assertEqual(await bot.vacancies.input_received(self.update, self.context), WAIT_VACANCY_INPUT)
        self.message.text = '7'
        await bot.vacancies.input_received(self.update, self.context)
        self.assertEqual(self.context.user_data['vacancy_filters']['days'], 7)
        await self.click('v:source:' + str(self.source))
        await self.click('v:filters')
        self.assertIn('Source ID', self.reply())

    async def test_best_matches_bounded_lazy_and_active_cv_analysis(self):
        first = self.add()
        self.add(2, 'Hiring analyst in Dubai. Python required. Send CV jobs@example.com')
        self.add(3, 'Hiring analyst in Dubai. Excel required. Send CV jobs@example.com')
        cv_id = bot.db.add_resume(1, 'test.txt', 'unused', 'SQL experience. Currently based in Dubai.')
        with patch('vacancy_ui.analyse_match', wraps=bot.analyse_match) as matcher:
            await self.click('v:latest')
            matcher.assert_not_called()
            await self.click('v:best')
            self.assertEqual(matcher.call_count, 2)
        await self.click(f'v:analyse:{first}')
        self.assertIn('Overall match score', self.reply())
        self.assertEqual(self.context.user_data['analysis']['resume_id'], cv_id)
        self.assertFalse(bot.ai.available)

    async def test_missing_cv_stale_next_and_private_chat(self):
        self.add()
        await self.click('v:best')
        self.assertIn('Upload a CV', self.reply())
        await self.click('v:next:stale')
        self.assertIn('expired', self.reply())
        self.update.effective_chat.type = 'group'
        await self.click('v:latest')
        self.assertIn('private chat', self.reply())

    async def test_admin_stats_only_for_configured_owner(self):
        bot.vacancies.admin_id = 2
        self.add()
        await self.click('v:home')
        self.assertNotIn(text('en', 'v_admin'), self.labels())
        before = self.message.reply_text.await_count
        await self.click('v:admin')
        self.assertEqual(self.message.reply_text.await_count, before)
        self.update.effective_user.id = 2
        bot.db.set_language(2, 'ru')
        await self.click('v:home')
        self.assertIn('Статистика сбора', self.labels())
        await self.click('v:admin')
        self.assertIn('Собрано публикаций: 1', self.reply())
        self.assertIn('Дубликаты: 0', self.reply())

    async def test_duplicate_source_filter_and_saved_disabled_source(self):
        vacancy = self.add()
        other = self.store.add_source('other_jobs')
        self.store.insert(other, 1, **analyze_text(self.store.get(vacancy)['raw_text']))
        self.assertEqual(len(self.store.list(source_id=other)), 1)
        self.store.save(1, vacancy)
        self.store.enable_source(self.source, False)
        self.store.enable_source(other, False)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(len(self.store.list(saved_user=1)), 1)

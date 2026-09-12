import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from alerts_ui import WAIT_ALERTS
from config import Settings
from locales import text


class AlertsUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.app = bot.build_application(Settings('123:'+'x'*30, None, None, root/'test.db', root))
        self.message = SimpleNamespace(text='', reply_text=AsyncMock())
        self.query = SimpleNamespace(answer=AsyncMock(), data='', message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username='test', first_name='Test'),
            effective_message=self.message, message=self.message, callback_query=self.query,
            effective_chat=SimpleNamespace(type='private'))
        self.context = SimpleNamespace(user_data={}, args=[])

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    def reply(self):
        return self.message.reply_text.call_args.args[0]

    async def test_ru_en_menu_settings_enable_disable_persistence(self):
        for lang in ('ru', 'en'):
            bot.db.set_language(1, lang)
            self.assertIn('al:home', [b.callback_data for r in bot.make_main_menu(lang).inline_keyboard for b in r])
            await self.click('al:home')
            self.assertIn(text(lang, 'al_menu'), self.reply())
            self.assertIn(text(lang, 'al_off'), self.reply())
        await self.click('al:on')
        self.assertFalse(bot.alerts_ui.store.preferences(1)['enabled'])
        for field, value in [('roles', 'Повар, Бариста'), ('keywords', 'cold kitchen\ncommis'),
                             ('location', 'Dubai'), ('salary_min', '5000')]:
            self.assertEqual(await self.click('al:edit:'+field), WAIT_ALERTS)
            self.message.text = value
            await bot.alerts_ui.receive(self.update, self.context)
        await self.click('al:uae')
        await self.click('al:on')
        pref = bot.alerts_ui.store.preferences(1)
        self.assertEqual((pref['location'], pref['salary_min'], pref['enabled'], pref['uae_only']), ('Dubai', 5000, 1, 1))
        self.assertIn('5,000 AED', self.reply())
        await self.click('al:off')
        self.assertFalse(bot.alerts_ui.store.preferences(1)['enabled'])

    async def test_validation_cancel_and_navigation_clear_pending_input(self):
        await self.click('al:edit:salary_min')
        self.message.text = 'wrong'
        self.assertEqual(await bot.alerts_ui.receive(self.update, self.context), WAIT_ALERTS)
        self.assertIsNone(bot.alerts_ui.store.preferences(1)['salary_min'])
        await bot.cancel(self.update, self.context)
        self.assertIn(text('en', 'al_menu'), self.reply())
        self.assertNotIn('alert_field', self.context.user_data)
        await self.click('al:edit:roles')
        await self.click('p:profile')
        self.assertNotIn('alert_field', self.context.user_data)

    async def test_notification_open_analyse_save_convert_use_existing_flow(self):
        source = bot.vacancies.store.add_source('test_jobs')
        vid, _ = bot.vacancies.store.insert(source, 1, role='Chef', company='Test company', location='Dubai',
            detection_status='vacancy', combined_text='Hiring Chef with kitchen experience in Dubai.',
            published_at=datetime.now(timezone.utc).isoformat(), source_url='https://t.me/test_jobs/1')
        self.context.user_data['vacancy_page'] = ('old', 'latest', 10)
        await self.click(f'v:open:{vid}')
        self.assertIn('Chef', self.reply())
        self.assertNotIn('vacancy_page', self.context.user_data)
        await self.click(f'v:save:{vid}')
        self.assertTrue(bot.vacancies.store.is_saved(1, vid))
        await self.click(f'v:analyse:{vid}')
        self.assertIn(text('en', 'upload_a_cv_first'), self.reply())
        await self.click(f'v:convert:{vid}')
        self.assertEqual(self.context.user_data['form']['kind'], 'application')

    async def test_delete_my_data_cleans_alert_preferences(self):
        bot.alerts_ui.store.update(1, 'roles', 'Cook')
        bot.alerts_ui.store.update(1, 'enabled', True)
        await bot.product.delete_my_data(self.update, self.context)
        buttons = self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
        confirmation = next(b.callback_data for row in buttons for b in row if b.callback_data.startswith('p:erase:'))
        await self.click(confirmation)
        self.assertFalse(bot.alerts_ui.store.preferences(1)['enabled'])
        with bot.db._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM alert_preferences').fetchone()[0], 0)

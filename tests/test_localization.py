"""Offline localization, migration and shared-handler regression tests."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from config import Settings
from db import Database
from locales import CATALOGS, status_label, text
from services.matcher import analyse_match, format_analysis
from services.product import validate_field
from statuses import STATUSES, LEGACY_STATUSES


class LocalizationTests(unittest.TestCase):
    def test_catalogs_have_matching_keys_and_placeholders(self):
        self.assertEqual(CATALOGS['en'].keys(), CATALOGS['ru'].keys())
        for key, english in CATALOGS['en'].items():
            with self.subTest(key=key):
                fields = lambda s: sorted((field, spec, conv) for _, field, spec, conv in Formatter().parse(s) if field is not None)
                self.assertEqual(fields(english), fields(CATALOGS['ru'][key]))
                self.assertTrue(CATALOGS['ru'][key].strip())

    def test_language_persistence_isolation_and_deletion(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.db'
            db = Database(path)
            self.assertEqual(db.get_language(1), 'en')
            self.assertFalse(db.language_selected(1))
            db.set_language(1, 'ru')
            db.upsert_user(1, 'tester', 'Test')
            db = Database(path)
            self.assertEqual(db.get_language(1), 'ru')
            self.assertTrue(db.language_selected(1))
            self.assertEqual(db.get_language(2), 'en')
            with self.assertRaises(ValueError):
                db.set_language(1, 'de')
            self.assertEqual(db.get_language(1), 'ru')
            db.set_language(1, 'en')
            self.assertTrue(Database(path).language_selected(1))
            db.delete_user_records(1)
            self.assertFalse(db.language_selected(1))

    def test_old_schema_users_and_statuses_migrate_without_data_loss(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'old.db'
            with sqlite3.connect(path) as conn:
                conn.executescript('''
                    CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT);
                    INSERT INTO users VALUES (1, 'tester', 'Test', '2026-01-01');
                    CREATE TABLE applications (id INTEGER PRIMARY KEY, telegram_id INTEGER, company TEXT, role TEXT,
                        status TEXT, notes TEXT, created_at TEXT);
                ''')
                for index, status in enumerate((*LEGACY_STATUSES, 'Custom legacy'), 1):
                    conn.execute('INSERT INTO applications VALUES (?,1,?,?,?,?,?)',
                                 (index, 'Profile', 'Interview', status, 'Saved', '2026-01-01'))
            conn.close()
            db = Database(path)
            self.assertEqual(db.get_language(1), 'en')
            self.assertFalse(db.language_selected(1))
            for index, status in enumerate((*STATUSES, 'Custom legacy'), 1):
                record = db.get_application(1, index)
                self.assertEqual(record['status'], status)
                self.assertEqual((record['company'], record['role'], record['notes']), ('Profile', 'Interview', 'Saved'))
            self.assertEqual(db.get_application(1, 1)['date_applied'], '')
            self.assertEqual(db.dashboard(1)['total'], 10)
            self.assertEqual(len(db.list_applications(1, status='Interview')), 1)
            self.assertEqual(len(db.list_applications(1, status='Интервью')), 1)
            self.assertEqual(Database(path).get_application(1, 4)['status'], 'interview')

    def test_status_labels_and_legacy_inputs(self):
        russian = ('Сохранено', 'Отклик отправлен', 'Скрининг HR', 'Интервью', 'Тестовое задание',
                   'Финальное интервью', 'Оффер', 'Отказ', 'Отозвано')
        for code, en, ru in zip(STATUSES, LEGACY_STATUSES, russian):
            self.assertEqual(status_label('en', code), en)
            self.assertEqual(status_label('ru', code), ru)
            for value in (code, en, ru):
                self.assertEqual(validate_field('status', value, 'ru'), code)
        self.assertEqual(status_label('ru', 'Custom legacy'), 'Custom legacy')

    def test_localized_analysis_preserves_score_logic_and_technologies(self):
        cv = 'SQL. Python. Power BI. Excel. Currently based in Dubai.'
        vacancy = 'SQL. Python. Power BI or Tableau. Excel. Fluent English. 3 years experience. Communication.'
        en = analyse_match(cv, vacancy)
        ru = analyse_match(cv, vacancy, language='ru')
        for key in ('score', 'breakdown', 'effective_weights'):
            self.assertEqual(en[key], ru[key])
        report = format_analysis(en, language='ru')
        self.assertEqual(report, format_analysis(ru, language='ru'))
        self.assertEqual(format_analysis(en), format_analysis(ru))
        for phrase in ('Общая оценка соответствия', 'Важные пробелы', 'Рекомендации',
                       'не официальный ATS score', 'SQL', 'Python', 'Power BI', 'Excel'):
            self.assertIn(phrase, report)
        self.assertNotIn('Strong matches', report)
        self.assertTrue(all('CV' not in row['reason'] for row in ru['requirements']))
        self.assertIn('Стаж от 3 лет', report)
        self.assertIn('Overall match score', format_analysis(en))

    def test_menus_have_shared_callbacks_and_localized_labels(self):
        menus = {lang: [b for row in bot.make_main_menu(lang).inline_keyboard for b in row] for lang in ('en', 'ru')}
        self.assertEqual([b.callback_data for b in menus['en']], [b.callback_data for b in menus['ru']])
        self.assertIn('👤 Профиль', [b.text for b in menus['ru']])
        self.assertIn('👤 Profile', [b.text for b in menus['en']])


class LocalizedHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = bot.build_application(Settings('123:' + 'x' * 30, None, None, self.root / 'test.db', self.root))
        self.message = SimpleNamespace(reply_text=AsyncMock(), text='', document=None)
        self.query = SimpleNamespace(answer=AsyncMock(), data='', message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username='tester', first_name='Test'),
            effective_message=self.message, message=self.message, callback_query=self.query,
            effective_chat=SimpleNamespace(type='private'))
        self.context = SimpleNamespace(user_data={}, args=[])

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    def reply(self):
        return self.message.reply_text.call_args.args[0]

    def labels(self):
        return [b.text for row in self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]

    async def test_onboarding_and_language_change_preserve_profile(self):
        bot.db.upsert_user(1, 'old', 'Old')
        bot.db.save_profile(1, full_name='Profile', desired_role='Interview', notes='Saved')
        await bot.start(self.update, self.context)
        self.assertEqual(self.labels(), ['English 🇬🇧', 'Русский 🇷🇺'])
        await self.click('p:lang:ru')
        self.assertEqual(Database(bot.db.path).get_language(1), 'ru')
        await bot.start(self.update, self.context)
        self.assertIn('Загрузите резюме', self.reply())
        self.assertIn('👤 Профиль', self.labels())
        await self.click('p:profile')
        self.assertIn('Полное имя: Profile', self.reply())
        self.assertIn('Желаемая должность: Interview', self.reply())
        self.assertIn('Примечания: Saved', self.reply())
        await self.click('p:language')
        await self.click('p:lang:en')
        self.assertIn('👤 Profile', self.labels())
        self.assertEqual(bot.db.get_profile(1)['notes'], 'Saved')
        await bot.start(self.update, self.context)
        self.assertIn('Upload your CV', self.reply())

    async def test_language_command_registered_and_clears_pending_forms(self):
        from telegram.ext import ConversationHandler
        conv = next(h for h in self.app.handlers[0] if isinstance(h, ConversationHandler))
        handler = next(h for h in conv.entry_points if 'language' in getattr(h, 'commands', ()))
        self.context.user_data.update(form={'private': 'value'}, ai_action='review')
        await handler.callback(self.update, self.context)
        self.assertNotIn('form', self.context.user_data)
        self.assertNotIn('ai_action', self.context.user_data)
        self.assertEqual(self.labels(), ['English 🇬🇧', 'Русский 🇷🇺'])

    async def test_localized_status_picker_filters_dashboard_and_raw_values(self):
        bot.db.set_language(1, 'ru')
        app_id = bot.db.add_application(1, 'Profile', 'Interview', notes='Saved', status='Applied')
        await self.click(f'p:status:{app_id}')
        self.assertEqual(self.labels(), [status_label('ru', s) for s in STATUSES])
        self.context.args = [str(app_id), 'Интервью']
        await bot.status_command(self.update, self.context)
        self.assertEqual(bot.db.get_application(1, app_id)['status'], 'interview')
        await self.click(f'p:app:{app_id}')
        for phrase in ('Компания: Profile', 'Должность: Interview', 'Примечания: Saved', 'Статус: Интервью'):
            self.assertIn(phrase, self.reply())
        await self.click('p:filter')
        self.assertIn('Финальное интервью', self.labels())
        await self.click('p:filterstatus:3')
        self.assertIn('Статус: Интервью', self.reply())
        self.message.text = 'Profile'
        await bot.product.search_received(self.update, self.context)
        self.assertIn('Поиск: Profile', self.reply())
        await self.click('p:dashboard')
        self.assertIn('Всего откликов: 1', self.reply())
        self.assertIn('100.0%', self.reply())

    async def test_russian_forms_validation_and_language_level_choices(self):
        bot.db.set_language(1, 'ru')
        await self.click('p:edit:english_level')
        self.assertIn('Продвинутый (C1)', self.labels())
        self.message.text = 'Продвинутый (C1)'
        await bot.product.form_received(self.update, self.context)
        self.assertEqual(bot.db.get_profile(1)['english_level'], 'Advanced (C1)')
        self.assertIn('Продвинутый (C1)', self.reply())
        await self.click('p:edit:years_experience')
        self.message.text = 'invalid'
        await bot.product.form_received(self.update, self.context)
        self.assertEqual(self.reply(), text('ru', 'validation_years'))

    async def test_russian_report_gaps_help_and_ai_fallback(self):
        bot.db.set_language(1, 'ru')
        bot.db.add_resume(1, 'Profile.txt', 'unused', 'SQL analyst. ' * 10)
        self.message.text = 'SQL required. Power BI or Tableau required. Fluent English. Based in Dubai. Communication required. ' * 2
        with patch('services.ai.OpenAIProvider.generate', new_callable=AsyncMock) as provider:
            await bot.vacancy_received(self.update, self.context)
            self.assertIn('Общая оценка соответствия', self.reply())
            self.assertIn('Profile.txt', self.reply())
            token = self.context.user_data['analysis']['token']
            await self.click('p:gaps:' + token)
            self.assertIn('Главные пробелы', self.reply())
            self.assertIn('Power BI', self.reply())
            await self.click('p:profilegaps:' + token)
            self.assertIn('Сначала создайте профиль', self.reply())
            await self.click('ai_menu')
            self.assertIn('AI отключён', self.reply())
            provider.assert_not_awaited()
        await bot.help_command(self.update, self.context)
        self.assertIn('/language', self.reply())
        self.assertIn('Команды:', self.reply())

    async def test_russian_privacy_confirmation_and_error(self):
        bot.db.set_language(1, 'ru')
        bot.db.save_profile(1, full_name='Name')
        await bot.product.delete_my_data(self.update, self.context)
        self.assertIn('Да, удалить мои данные', self.labels())
        await self.click('p:home')
        self.assertIsNotNone(bot.db.get_profile(1))
        await bot.product.delete_my_data(self.update, self.context)
        token = self.context.user_data['delete_confirmation'][0]
        await self.click('p:erase:' + token)
        self.assertIn('данные и файлы резюме удалены', self.reply())
        self.assertFalse(bot.db.language_selected(1))
        await bot.start(self.update, self.context)
        self.assertEqual(self.labels(), ['English 🇬🇧', 'Русский 🇷🇺'])
        bot.db.set_language(1, 'ru')
        self.context.error = RuntimeError('sensitive')
        from telegram import Update
        update = Update.de_json({'update_id': 1, 'message': {'message_id': 1, 'date': 0,
            'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1, 'is_bot': False, 'first_name': 'Test'},
            'text': '/menu'}}, self.app.bot)
        with patch('telegram.Message.reply_text', new_callable=AsyncMock) as reply:
            await bot.error_handler(update, self.context)
        self.assertIn('Произошла ошибка', reply.call_args.args[0])
        self.assertNotIn('sensitive', reply.call_args.args[0])

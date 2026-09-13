import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from config import Settings
from cv_builder_ui import WAIT_CV_BUILDER
from locales import text


class CVBuilderUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = Settings('123:' + 'x'*30, None, 'unused', self.root / 'test.db', self.root)
        bot.build_application(self.config)
        self.message = SimpleNamespace(text='', photo=[], reply_text=AsyncMock(), reply_document=AsyncMock(), reply_photo=AsyncMock())
        self.query = SimpleNamespace(data='', answer=AsyncMock(), message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username='test', first_name='Test'),
            message=self.message, effective_message=self.message, callback_query=self.query, effective_chat=SimpleNamespace(type='private'))
        self.context = SimpleNamespace(user_data={})

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    async def answer(self, value):
        self.message.text = value
        return await bot.builder_ui.receive(self.update, self.context)

    async def test_persistent_form_and_ru_en_menu(self):
        for language in ('ru', 'en'):
            bot.db.set_language(1, language)
            labels = [b.text for row in bot.make_main_menu(language).inline_keyboard for b in row]
            self.assertIn(text(language, 'g_my_cv'), labels)
            await self.click('g:cvs')
            self.assertIn(text(language, 'cb_menu'), [b.text for row in self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row])
            await self.click('cb:home')
            self.assertIn(text(language, 'cb_intro'), self.message.reply_text.call_args.args[0])
        self.assertEqual(await self.click('cb:new'), WAIT_CV_BUILDER)
        await self.answer('Test Name')
        did = self.context.user_data['builder_form']
        await self.click(f'cb:view:{did}')
        self.context.user_data.clear()
        bot.build_application(self.config)
        await self.click(f'cb:resume:{did}')
        self.assertIn('Target role', self.message.reply_text.call_args.args[0])
        for value in ['Analyst', '+971 000', 'test@example.com', 'Dubai', '', '']:
            await self.answer(value)
        self.assertEqual(bot.builder_ui.builder.get(1, did)['data']['full_name'], 'Test Name')
        await self.click(f'cb:docx:{did}')
        self.assertEqual(self.message.reply_document.call_args.kwargs['filename'], 'Test_Name_CV.docx')

    async def test_multiple_entry_flow_and_cancel_delete(self):
        await self.click('cb:new')
        did = self.context.user_data['builder_form']
        for i in range(2):
            await self.click(f'cb:add:{did}:experience:0')
            for value in [f'Company {i}', 'Cook', 'Dubai', '2020', 'Present', 'Prepared meals']:
                await self.answer(value)
        self.assertEqual(len(bot.builder_ui.builder.get(1, did)['data']['experience']), 2)
        await self.click(f'cb:delete:{did}')
        token = self.context.user_data['builder_delete'][2]
        await self.click(f'cb:view:{did}')
        await self.click(f'cb:confirm:{did}:{token}')
        self.assertIsNotNone(bot.builder_ui.builder.get(1, did))

    async def test_skip_replay_and_other_user(self):
        await self.click('cb:new')
        did = self.context.user_data['builder_form']
        nonce = bot.builder_ui.builder.get(1, did)['data']['_wizard']['nonce']
        await self.click(f'cb:skip:{did}:{nonce}')
        await self.click(f'cb:skip:{did}:{nonce}')
        self.assertEqual(bot.builder_ui.builder.get(1, did)['data']['_wizard']['position'], 1)
        self.update.effective_user.id = 2
        await self.click(f'cb:view:{did}')
        self.assertIn(text('en', 'cb_missing'), self.message.reply_text.call_args.args[0])

    async def test_full_wizard_with_two_education_entries(self):
        await self.click('cb:new')
        did = self.context.user_data['builder_form']
        for value in ['Test Name', 'Cook', '', 'test@example.com', 'Dubai', '', '', 'Prepared meals']:
            await self.answer(value)
        self.assertEqual(bot.builder_ui.builder.get(1, did)['data']['_next_section'], 'experience')
        await self.click(f'cb:next:{did}:experience')
        for i in range(2):
            await self.click(f'cb:add:{did}:education:1')
            for value in [f'College {i}', 'Diploma', 'Culinary arts', '2020–2022', 'Dubai']:
                await self.answer(value)
        await self.click(f'cb:next:{did}:education')
        for value in ['Cooking', 'English B2', '', '', '', '', '', '']:
            await self.answer(value)
        data = bot.builder_ui.builder.get(1, did)['data']
        self.assertEqual(data['_wizard']['section'], 'photo')
        await self.click(f"cb:skip:{did}:{data['_wizard']['nonce']}")
        data = bot.builder_ui.builder.get(1, did)['data']
        self.assertNotIn('_wizard', data)
        self.assertEqual(len(data['education']), 2)
        await self.click(f'cb:active:{did}')
        self.assertIn('Cooking', bot.db.active_resume(1)['extracted_text'])

    async def test_template_and_separate_language_versions(self):
        await self.click('cb:new')
        did = self.context.user_data['builder_form']
        await self.answer('Original Name')
        await self.click(f'cb:templates:{did}')
        await self.click(f'cb:template:{did}:template_1')
        await self.click(f'cb:version:{did}:ru')
        clone = bot.builder_ui.builder.list(1)[0]
        self.assertNotEqual(clone['id'], did)
        self.assertEqual(clone['data']['_language'], 'ru')
        self.assertEqual(clone['data']['full_name'], 'Original Name')
        bot.builder_ui.builder.set_section(1, clone['id'], 'basics', {'full_name': 'Новое имя', 'target_role': 'Аналитик'})
        self.assertEqual(bot.builder_ui.builder.get(1, did)['data']['full_name'], 'Original Name')
        bot.db.set_language(1, 'en')
        await self.click(f"cb:view:{clone['id']}")
        self.assertIn('RU · Modern', self.message.reply_text.call_args.args[0])

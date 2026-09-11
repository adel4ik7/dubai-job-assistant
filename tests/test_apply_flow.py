import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from config import Settings
from db import Database
from services.apply_flow import ApplyFlow
from vacancy_store import VacancyStore
from locales import text


class ApplyFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = Database(self.root / 'test.db')
        self.db.upsert_user(1, 'test', 'Test')
        self.store = VacancyStore(self.db)
        self.source = self.store.add_source('test_jobs')
        self.vid, _ = self.store.insert(self.source, 1, role='Cook', company='Example', email='jobs@example.com', detection_status='vacancy')
        path = self.root / '1_test.txt'
        path.write_text('Cook experience', encoding='utf-8')
        self.rid = self.db.add_resume(1, 'Test_CV.txt', str(path), 'Cook experience')
        self.flow = ApplyFlow(self.db, self.root)

    def count(self):
        with self.db._connect() as conn:
            return conn.execute('SELECT COUNT(*) FROM apply_preparations').fetchone()[0]

    def test_confirm_is_local_idempotent_and_does_not_apply(self):
        appid = self.db.add_application(1, 'Example', 'Cook', status='saved', vacancy_text='Send CV to jobs@example.com')
        with patch('socket.create_connection', side_effect=AssertionError('No network')):
            draft = self.flow.prepare(1, 'application', appid, 'en')
            self.assertEqual(draft['recipient_email'], 'jobs@example.com')
            self.assertEqual(self.count(), 0)
            first = self.flow.confirm(1, draft)
            self.assertEqual(first, self.flow.confirm(1, draft))
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.get_application(1, appid)['status'], 'saved')
        self.assertFalse(self.db.get_application(1, appid)['date_applied'])

    def test_missing_email_not_invented_and_edits_persist(self):
        appid = self.db.add_application(1, 'Unknown company', 'Cook', status='saved')
        draft = self.flow.prepare(1, 'application', appid, 'ru')
        self.assertIsNone(draft['recipient_email'])
        draft['subject'] = 'Моя тема'
        draft['message'] = 'Моё сообщение'
        self.flow.confirm(1, draft)
        again = self.flow.prepare(1, 'application', appid, 'en')
        self.assertEqual(again['message'], 'Моё сообщение')
        self.assertEqual(again['subject'], 'Моя тема')

    def test_owner_deleted_cv_and_changed_vacancy(self):
        appid = self.db.add_application(1, 'Example', 'Cook')
        with self.assertRaises(ValueError):
            self.flow.prepare(2, 'application', appid, 'en')
        draft = self.flow.prepare(1, 'vacancy', self.vid, 'en')
        with self.assertRaises(ValueError):
            self.flow.confirm(2, draft)
        with self.db._connect() as conn:
            conn.execute('UPDATE vacancies SET email=? WHERE id=?', ('other@example.com', self.vid))
        with self.assertRaises(ValueError):
            self.flow.confirm(1, draft)
        draft = self.flow.prepare(1, 'vacancy', self.vid, 'en')
        self.db.delete_resume_record(1, self.rid)
        with self.assertRaises(ValueError):
            self.flow.confirm(1, draft)

    def test_validation_and_privacy(self):
        for field, value in [('subject', 'a\r\nBcc: unsafe'), ('subject', 'x'*201), ('message', '')]:
            with self.assertRaises(ValueError):
                self.flow.validate(field, value)
        self.flow.confirm(1, self.flow.prepare(1, 'vacancy', self.vid, 'en'))
        self.db.delete_user_records(1)
        self.assertEqual(self.count(), 0)


class ApplyUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        bot.build_application(Settings('123:'+'x'*30, None, 'unused', self.root/'test.db', self.root))
        bot.db.upsert_user(1, 'test', 'Test')
        path = self.root/'1_cv.txt'
        path.write_text('Cook', encoding='utf-8')
        bot.db.add_resume(1, 'Test_CV.txt', str(path), 'Cook')
        store = bot.vacancies.store
        sid = store.add_source('test_jobs')
        self.vid, _ = store.insert(sid, 1, role='Cook', detection_status='vacancy')
        self.message = SimpleNamespace(reply_text=AsyncMock(), text='')
        self.query = SimpleNamespace(answer=AsyncMock(), data='', message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username='test', first_name='Test'),
            effective_chat=SimpleNamespace(type='private'), effective_message=self.message, message=self.message, callback_query=self.query)
        self.context = SimpleNamespace(user_data={})

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    async def test_ru_en_review_edit_cancel_and_confirm(self):
        for language in ('ru', 'en'):
            bot.db.set_language(1, language)
            await self.click(f'ap:vacancy:{self.vid}')
            reply = self.message.reply_text.call_args.args[0]
            self.assertIn(text(language, 'ap_no_email'), reply)
            self.assertIn(text(language, 'ap_no_send'), reply)
            token = self.context.user_data['apply_token']
            await self.click(f'ap:cancel:{token}')
            with bot.db._connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM apply_preparations').fetchone()[0], 0)
        await self.click(f'ap:vacancy:{self.vid}')
        token = self.context.user_data['apply_token']
        await self.click(f'ap:edit:{token}:message')
        self.message.text = 'My actual message'
        await bot.apply_ui.receive(self.update, self.context)
        token = self.context.user_data['apply_token']
        await self.click(f'ap:confirm:{token}')
        await self.click(f'ap:confirm:{token}')
        with bot.db._connect() as conn:
            rows = conn.execute('SELECT message,state FROM apply_preparations').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(rows[0]), ('My actual message', 'prepared'))

    async def test_private_chat_and_reset(self):
        self.update.effective_chat.type='group'
        await self.click(f'ap:vacancy:{self.vid}')
        self.assertNotIn('apply_draft', self.context.user_data)
        self.update.effective_chat.type='private'
        await self.click(f'ap:vacancy:{self.vid}')
        bot.reset_pending(self.context)
        self.assertNotIn('apply_draft', self.context.user_data)

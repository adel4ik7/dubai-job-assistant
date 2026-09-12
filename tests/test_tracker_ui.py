import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from config import Settings
from locales import text
from tracker_ui import WAIT_TRACKER


class TrackerUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        bot.build_application(Settings('123:'+'x'*30,None,None,root/'test.db',root))
        self.aid=bot.db.add_application(1,'Company','Chef',source_url='https://t.me/test_jobs/1')
        self.message=SimpleNamespace(text='',reply_text=AsyncMock(),reply_document=AsyncMock())
        self.query=SimpleNamespace(data='',answer=AsyncMock(),message=self.message)
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=1,username='test',first_name='Test'),
            effective_chat=SimpleNamespace(type='private'),effective_message=self.message,message=self.message,callback_query=self.query)
        self.context=SimpleNamespace(user_data={})

    async def click(self,data):
        self.query.data=data
        return await bot.buttons(self.update,self.context)

    def reply(self):return self.message.reply_text.call_args.args[0]

    async def test_details_status_timeline_ru_en_and_missing_owner(self):
        for lang in ('ru','en'):
            bot.db.set_language(1,lang)
            await self.click(f'p:app:{self.aid}')
            self.assertIn('Chef',self.reply());self.assertIn('UTC+04',self.reply())
            labels=[b.text for row in self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
            self.assertIn(text(lang,'at_follow'),labels)
            await self.click(f'at:status:{self.aid}')
            await self.click(f'at:set:{self.aid}:interview')
            await self.click(f'at:history:{self.aid}:0')
            self.assertIn(text(lang,'at_timeline'),self.reply())
        self.update.effective_user.id=2
        await self.click(f'p:app:{self.aid}')
        self.assertEqual(self.reply(),text('en','application_not_found'))

    async def test_follow_preset_repeat_today_snooze_and_reply(self):
        await self.click(f'at:follow:{self.aid}')
        token=self.context.user_data['tracker_action'][1]
        await self.click(f'at:days:{self.aid}:2:{token}')
        due=bot.db.get_application(1,self.aid)['follow_up_at']
        await self.click(f'at:days:{self.aid}:2:{token}')
        self.assertEqual(bot.db.get_application(1,self.aid)['follow_up_at'],due)
        await self.click('at:today:0')
        self.assertIn(text('en','at_today'),self.reply())
        await self.click(f'at:reply:{self.aid}')
        self.assertIsNone(bot.db.get_application(1,self.aid)['follow_up_at'])

    async def test_interview_wizard_notes_cancel_and_confirmed_delete(self):
        self.assertEqual(await self.click(f'at:interview:{self.aid}'),WAIT_TRACKER)
        for value in ('2035-01-01 12:00','video','https://example.com','Bring CV'):
            self.message.text=value
            await bot.tracker_ui.receive(self.update,self.context)
        app=bot.db.get_application(1,self.aid)
        self.assertEqual(app['interview_format'],'video')
        self.assertEqual(app['interview_notes'],'Bring CV')
        await self.click(f'at:note:{self.aid}')
        self.message.text='My note'
        await bot.tracker_ui.receive(self.update,self.context)
        self.assertEqual(bot.db.get_application(1,self.aid)['notes'],'My note')
        await self.click(f'at:note:{self.aid}')
        await bot.cancel(self.update,self.context)
        self.assertNotIn('tracker_form',self.context.user_data)
        await self.click(f'at:delete:{self.aid}')
        token=self.context.user_data['tracker_delete'][1]
        await self.click(f'at:erase:{self.aid}:{token}')
        self.assertIsNone(bot.db.get_application(1,self.aid))

    async def test_apply_pack_still_opens_from_details(self):
        await self.click(f'at:open:{self.aid}')
        await self.click(f'ap:application:{self.aid}')
        self.assertEqual(self.context.user_data['apply_draft']['origin_id'],self.aid)

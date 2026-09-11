import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from config import Settings
from product_ui import WAIT_FORM, WAIT_SEARCH, END
from db import PROFILE_FIELDS
from locales import text


class ProductUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = Settings("123:" + "x" * 30, None, "unused-placeholder", self.root / "test.db", self.root)
        self.app = bot.build_application(config)
        self.message = SimpleNamespace(reply_text=AsyncMock(), text="", document=None)
        self.query = SimpleNamespace(answer=AsyncMock(), data="", message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username="tester", first_name="Tester"),
            effective_message=self.message, message=self.message, callback_query=self.query,
            effective_chat=SimpleNamespace(type="private"))
        self.context = SimpleNamespace(user_data={}, args=[])

    async def click(self, data):
        self.query.data = data
        return await bot.buttons(self.update, self.context)

    async def answer(self, value):
        self.message.text = value
        return await bot.product.form_received(self.update, self.context)

    def cv(self, user=1, text="SQL analyst with reports. " * 10):
        path = self.root / f"{user}_{len(bot.db.list_resumes(user))}.txt"
        path.write_text(text)
        return bot.db.add_resume(user, path.name, str(path), text), path

    def reply(self):
        return self.message.reply_text.call_args.args[0]

    async def test_profile_create_view_edit_and_cancel(self):
        self.assertEqual(await self.click("p:create"), WAIT_FORM)
        for value in ("Test Name", "Analyst", "AED 10000 monthly", "Dubai", "Own visa", "4", "Advanced (C1)", "-"):
            await self.answer(value)
        self.assertEqual(bot.db.get_profile(1)["years_experience"], "4")
        await self.click("p:profile")
        self.assertIn("Test Name", self.reply())
        await self.click("p:edit:desired_role")
        await self.answer("Senior Analyst")
        self.assertEqual(bot.db.get_profile(1)["desired_role"], "Senior Analyst")
        await self.click("p:edit:full_name")
        await bot.cancel(self.update, self.context)
        self.assertNotIn("form", self.context.user_data)
        self.assertEqual(bot.db.get_profile(1)["full_name"], "Test Name")

    async def test_stale_form_buttons_and_invalid_values(self):
        await self.click("p:edit:years_experience")
        nonce = self.context.user_data["form"]["nonce"]
        self.assertEqual(await self.answer("not a number"), WAIT_FORM)
        await self.click("p:home")
        await self.click("p:skip:" + nonce)
        self.assertIsNone(bot.db.get_profile(1))
        self.assertIn("expired", self.reply())

    async def test_profile_summary_and_edit_menu_ru_en(self):
        bot.db.save_profile(1, full_name='Test Name', desired_role='Analyst', notes='My notes')
        for language in ('ru', 'en'):
            bot.db.set_language(1, language)
            await self.click('p:profile')
            self.assertIn('Test Name', self.reply())
            self.assertIn('Analyst', self.reply())
            self.assertIn('My notes', self.reply())
            rows = self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertEqual([b.callback_data for row in rows for b in row], ['p:profileedit', 'p:language', 'p:home'])
            self.assertEqual(rows[0][0].text, text(language, 'profile_edit_button'))
            await self.click('p:profileedit')
            self.assertEqual(self.reply(), text(language, 'profile_edit_prompt'))
            rows = self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertEqual([len(row) for row in rows], [2, 2, 2, 2, 1, 1])
            self.assertEqual([b.callback_data for row in rows[:4] for b in row], ['p:edit:'+key for key in PROFILE_FIELDS])
            for key, button in zip(PROFILE_FIELDS, [b for row in rows[:4] for b in row]):
                self.assertEqual(button.text, text(language, 'profile_edit_'+key))
            for navigation in rows[-2:]:
                await self.click(navigation[0].callback_data)
                self.assertIn('Test Name', self.reply())
                self.assertEqual(self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'p:profileedit')
            await self.click('p:home')
            self.assertEqual(self.reply(), text(language, 'choose_a_section'))

    async def test_profile_edit_save_validation_and_cancel_navigation(self):
        bot.db.save_profile(1, full_name='Original', years_experience='3')
        for language in ('ru', 'en'):
            bot.db.set_language(1, language)
            await self.click('p:edit:desired_role')
            await self.answer('New role')
            self.assertEqual(bot.db.get_profile(1)['desired_role'], 'New role')
            replies = [call.args[0] for call in self.message.reply_text.call_args_list]
            self.assertIn(text(language, 'profile_updated'), replies)
            self.assertEqual(self.reply(), text(language, 'profile_edit_prompt'))
            await self.click('p:edit:years_experience')
            self.assertEqual(await self.answer('invalid'), WAIT_FORM)
            self.assertEqual(bot.db.get_profile(1)['years_experience'], '3')
            self.assertEqual(await bot.cancel(self.update, self.context), END)
            self.assertEqual(self.reply(), text(language, 'profile_edit_prompt'))
            self.assertNotIn('form', self.context.user_data)
            await self.click('p:edit:full_name')
            rows = self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
            await self.click(rows[-1][0].callback_data)
            self.assertEqual(self.reply(), text(language, 'profile_edit_prompt'))
            self.assertEqual(bot.db.get_profile(1)['full_name'], 'Original')

    async def test_profile_creation_still_available(self):
        await self.click('p:profile')
        rows = self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[0][0].callback_data, 'p:create')
        self.assertEqual(await self.click('p:create'), WAIT_FORM)

    async def test_cv_selection_and_confirmation_deletion(self):
        first, first_path = self.cv()
        second, second_path = self.cv()
        await self.click(f"p:active:{first}")
        self.assertEqual(bot.db.active_resume(1)["id"], first)
        await self.click(f"p:deletecv:{first}")
        token = self.context.user_data["cv_delete"][0]
        self.assertTrue(first_path.exists())
        await self.click("p:cvs:0")  # cancel
        await self.click("p:erasecv:" + token)
        self.assertTrue(first_path.exists())
        await self.click(f"p:deletecv:{first}")
        await self.click("p:erasecv:" + self.context.user_data["cv_delete"][0])
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())
        self.assertEqual(bot.db.active_resume(1)["id"], second)

    async def test_application_wizard_search_filter_and_status(self):
        await self.click("p:add")
        for value in ("Acme", "Analyst", "Saved", "Referral", "AED 12000/month", "-", "Note"):
            await self.answer(value)
        record = bot.db.list_applications(1)[0]
        self.assertEqual(record["salary"], "AED 12000/month")
        self.assertEqual(record["date_applied"], "")
        await self.click(f"p:setstatus:{record['id']}:3")
        self.assertEqual(bot.db.get_application(1, record["id"])["status"], "interview")
        await self.click("p:filterstatus:3")
        self.assertEqual(await self.click("p:search"), WAIT_SEARCH)
        self.message.text = "acme"
        await bot.product.search_received(self.update, self.context)
        self.assertIn("Acme", self.reply())
        await self.click("p:dashboard")
        self.assertIn("Total applications: 1", self.reply())
        self.assertIn("100.0%", self.reply())

    async def test_analyse_active_compare_save_and_profile_gaps(self):
        first, _ = self.cv(text="Python programming experience. " * 5)
        second, _ = self.cv(text="SQL analyst experience. " * 5)
        bot.db.select_resume(1, first)
        self.message.text = "SQL required. Python preferred. Fluent English required. Based in Dubai. Own visa required. " * 2
        await bot.vacancy_received(self.update, self.context)
        analysis = self.context.user_data["analysis"]
        self.assertEqual(analysis["resume_id"], first)
        token = analysis["token"]
        await self.click("p:gaps:" + token)
        self.assertIn("SQL", self.reply())
        await self.click("p:profilegaps:" + token)
        self.assertIn("Create a Profile", self.reply())
        await self.click(f"p:compare:{second}:{token}")
        self.assertEqual(bot.db.active_resume(1)["id"], first)
        self.assertEqual(self.context.user_data["analysis"]["resume_id"], second)
        await self.click("p:save:" + self.context.user_data["analysis"]["token"])
        for value in ("Acme", "Analyst", "Saved", "LinkedIn", "-", "-", "-"):
            await self.answer(value)
        self.assertIn("SQL required", bot.db.list_applications(1)[0]["vacancy_text"])
        await self.click("p:save:" + self.context.user_data["analysis"]["token"])
        self.assertIn("already been saved", self.reply())

    async def test_delete_all_requires_valid_confirmation_and_clears_memory(self):
        _, path = self.cv()
        bot.db.save_profile(1, full_name="Test")
        bot.db.add_application(1, "Acme", "Role")
        self.context.user_data["analysis"] = {"vacancy": "private text"}
        await bot.product.delete_my_data(self.update, self.context)
        token = self.context.user_data["delete_confirmation"][0]
        self.assertTrue(path.exists())
        await self.click("p:home")
        await self.click("p:erase:" + token)
        self.assertTrue(path.exists())
        await bot.product.delete_my_data(self.update, self.context)
        token = self.context.user_data["delete_confirmation"][0]
        await self.click("p:erase:" + token)
        self.assertFalse(path.exists())
        self.assertEqual(self.context.user_data, {})
        self.assertIsNone(bot.db.get_profile(1))

    async def test_expired_and_other_user_confirmation_cannot_delete(self):
        _, path = self.cv()
        await bot.product.delete_my_data(self.update, self.context)
        token, created = self.context.user_data["delete_confirmation"]
        self.context.user_data["delete_confirmation"] = (token, created - 301)
        await self.click("p:erase:" + token)
        self.assertTrue(path.exists())
        self.update.effective_user.id = 2
        self.context.user_data.clear()
        await self.click("p:erase:" + token)
        self.assertTrue(path.exists())

    async def test_private_chat_and_ai_disabled_with_existing_key(self):
        self.assertFalse(bot.ai.available)
        await self.click("ai_menu")
        self.assertIn("disabled", self.reply())
        self.update.effective_chat.type = "group"
        await self.click("p:profile")
        self.assertIn("private chat", self.reply())

    async def test_pagination_and_foreign_record_buttons(self):
        for _ in range(7):
            self.cv()
        foreign, path = self.cv(user=2)
        await self.click("p:cvs:0")
        markup = self.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertTrue(any(b.text == "Next" for row in markup.inline_keyboard for b in row))
        await self.click(f"p:active:{foreign}")
        self.assertNotEqual(bot.db.active_resume(1)["id"], foreign)
        await self.click(f"p:deletecv:{foreign}")
        self.assertTrue(path.exists())

    async def test_repeated_uploads_use_unique_files_and_failed_parse_is_cleaned(self):
        async def download(*, custom_path):
            custom_path.write_text("Sample SQL analyst with experience. " * 8)
        file = SimpleNamespace(download_to_drive=AsyncMock(side_effect=download))
        self.message.document = SimpleNamespace(file_name="cv.txt", file_size=400, get_file=AsyncMock(return_value=file))
        await bot.cv_received(self.update, self.context)
        await bot.cv_received(self.update, self.context)
        resumes = bot.db.list_resumes(1)
        self.assertEqual(len(resumes), 2)
        paths = bot.db.resume_paths(1)
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(p).exists() for p in paths))
        async def invalid_download(*, custom_path):
            custom_path.write_text("short")
        file.download_to_drive.side_effect = invalid_download
        await bot.cv_received(self.update, self.context)
        self.assertEqual(len(list(self.root.glob("1_*.txt"))), 2)
        self.assertEqual(len(bot.db.list_resumes(1)), 2)

    async def test_upload_size_limit(self):
        doc = SimpleNamespace(file_name="cv.txt", file_size=6 * 1024 * 1024, get_file=AsyncMock())
        self.message.document = doc
        await bot.cv_received(self.update, self.context)
        doc.get_file.assert_not_awaited()
        self.assertIn("5 MB", self.reply())


if __name__ == "__main__":
    unittest.main()

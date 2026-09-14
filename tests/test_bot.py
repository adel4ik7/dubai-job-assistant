import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Update, User
from telegram.ext import ConversationHandler

import bot
from config import Settings, load_settings
from services.ai import AIService
from services.matcher import analyse_match, format_analysis
from services.resume_parser import extract_text


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        # Synthetic, non-authenticating token only for offline Application construction.
        self.config = Settings("123:" + "x" * 30, None, None, root / "test.db", root)
        self.app = bot.build_application(self.config)
        self.message = SimpleNamespace(reply_text=AsyncMock(), text="", document=None)
        self.query = SimpleNamespace(answer=AsyncMock(), data="", message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1, username="test", first_name="Test"),
                                      effective_message=self.message, message=self.message, callback_query=self.query)
        self.context = SimpleNamespace(user_data={}, args=[])

    async def test_startup_without_ai_and_existing_tracker(self):
        self.assertFalse(bot.ai.available)
        self.assertTrue(self.app.handlers)
        await bot.start(self.update, self.context)
        self.message.text = "Company | Analyst | note"
        await bot.application_received(self.update, self.context)
        application = bot.db.list_applications(1)[0]
        self.context.args = [str(application["id"]), "Interview"]
        await bot.status_command(self.update, self.context)
        self.assertEqual(bot.db.list_applications(1)[0]["status"], "interview")
        self.assertFalse(bot.db.update_application_status(2, application["id"], "Other"))
        self.query.data = "ai_menu"
        await bot.buttons(self.update, self.context)
        self.assertIn("AI is disabled", self.message.reply_text.call_args.args[0])

    async def test_ai_action_routes_and_cancel(self):
        bot.db.add_resume(1, "sample.txt", "unused", "Excel analyst with weekly reports. " * 10)
        provider = AsyncMock()
        provider.generate.return_value = "Candidate facts: analyst. Suggestions: clarify reports."
        bot.ai = AIService(provider, bot.db, 10)
        self.query.data = "ai_menu"
        await bot.buttons(self.update, self.context)
        provider.generate.assert_not_awaited()
        for action in ("review", "improve", "letter", "interview"):
            self.query.data = "ai_" + action
            state = await bot.buttons(self.update, self.context)
            if action != "review":
                self.assertEqual(state, bot.WAIT_AI_VACANCY)
                self.message.text = "short"
                self.assertEqual(await bot.ai_vacancy_received(self.update, self.context), bot.WAIT_AI_VACANCY)
                self.message.text = "Seeking Excel analyst to prepare weekly reports. " * 4
                await bot.ai_vacancy_received(self.update, self.context)
        self.assertEqual(provider.generate.await_count, 4)
        self.context.user_data["ai_action"] = "letter"
        await bot.cancel(self.update, self.context)
        self.assertNotIn("ai_action", self.context.user_data)

    async def test_heuristic_and_parser_still_work(self):
        path = Path(self.temp.name) / "sample.txt"
        path.write_text("Excel analyst preparing weekly reports in Dubai. " * 4, encoding="utf-8")
        text = extract_text(path)
        bot.db.add_resume(1, path.name, str(path), text)
        self.message.text = "Seeking Excel analyst to prepare weekly reports. " * 4
        await bot.vacancy_received(self.update, self.context)
        self.assertIn("heuristic score", self.message.reply_text.call_args.args[0])
        self.assertIn("not an ATS guarantee", format_analysis(analyse_match(text, self.message.text)))

    async def test_local_report_is_compact_and_telegram_safe_without_ai(self):
        bot.db.add_resume(1, "sample.txt", "unused", "Warehouse worker handling deliveries. " * 5)
        self.message.text = (
            "Python. SQL. Data analysis. Accounting. Project management. Customer service. "
            "Sales. Power BI. Excel. Tableau. SAP. Salesforce. AutoCAD. Git. AWS. Azure. "
            "3 years Python experience. Bachelor's degree in Computer Science. PMP. ACCA. "
            "Fluent English. Arabic. Hindi. Russian. Based in Dubai. Own visa required. "
            "Communication. Teamwork. Leadership.\nOptional:\nJava. SEO. Machine learning. "
            "Statistics. ETL. Logistics. Jira. QuickBooks."
        )
        with patch("services.ai.OpenAIProvider.generate", new_callable=AsyncMock) as network:
            await bot.vacancy_received(self.update, self.context)
            network.assert_not_awaited()
        sent = [call.args[0] for call in self.message.reply_text.call_args_list]
        self.assertTrue(sent)
        self.assertLessEqual(len(sent), 2)
        self.assertTrue(all(len(text.encode("utf-16-le")) // 2 <= 3500 for text in sent))
        self.assertIn("not an official ATS score", "".join(sent))
        markup = self.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertTrue(any(button.text == "Save vacancy/application" for row in markup.inline_keyboard for button in row))

    async def test_menu_resets_real_conversation_routing(self):
        conversation = next(h for h in self.app.handlers[0] if isinstance(h, ConversationHandler))
        self.app.bot._bot_user = User(123, "Test bot", True, username="test_bot")
        update = Update.de_json({"update_id": 1, "message": {
            "message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "text": "/menu", "entities": [{"type": "bot_command", "offset": 0, "length": 5}]
        }}, self.app.bot)
        conversation._conversations[(1, 1)] = bot.WAIT_AI_VACANCY
        self.context.user_data["ai_action"] = "letter"
        check = conversation.check_update(update)
        self.assertIsNotNone(check)
        with patch("telegram.Message.reply_text", new_callable=AsyncMock):
            await conversation.handle_update(update, self.app, check, self.context)
        self.assertNotIn((1, 1), conversation._conversations)
        self.assertNotIn("ai_action", self.context.user_data)

    async def test_error_logs_omit_sensitive_exception(self):
        self.context.error = RuntimeError("private-cv-and-secret")
        with self.assertLogs("bot", level="WARNING") as captured:
            await bot.error_handler(None, self.context)
        self.assertNotIn("private-cv-and-secret", "".join(captured.output))

    def test_config_missing_token_and_invalid_limit(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN is missing"):
                load_settings()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "synthetic", "AI_DAILY_LIMIT": "bad"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AI_DAILY_LIMIT"):
                load_settings()


if __name__ == "__main__":
    unittest.main()

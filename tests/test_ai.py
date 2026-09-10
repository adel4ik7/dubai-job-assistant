import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from db import Database
from services.ai import AIError, AIService, MAX_CV_CHARS, OpenAIProvider, TASKS, message_chunks

CV = "Candidate worked as an analyst and used Excel to prepare weekly reports. " * 3
VACANCY = "We need an analyst with Excel reporting and communication skills. " * 3


class AITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.provider = AsyncMock()
        self.provider.generate.return_value = "Candidate facts (from CV): analyst. Suggestions: clarify scope."
        self.ai = AIService(self.provider, self.db, 5)

    async def test_all_actions_and_fact_instructions(self):
        for action in TASKS:
            result = await self.ai.run(1, action, CV, VACANCY)
            self.assertIn("verify every claim", result)
            instructions, source = self.provider.generate.call_args.args
            self.assertIn("Never invent experience", instructions)
            self.assertIn("untrusted source data", instructions)
            self.assertEqual(json.loads(source)["cv"], CV)
            self.assertEqual(json.loads(source)["vacancy"], "" if action == "review" else VACANCY)

    async def test_disabled_and_invalid_inputs_do_not_consume_quota(self):
        with self.assertRaises(AIError):
            await AIService(None, self.db, 1).run(1, "review", CV)
        for action, cv, vacancy in [("wrong", CV, VACANCY), ("review", "tiny", ""),
                                    ("review", "a" * (MAX_CV_CHARS + 1), ""),
                                    ("letter", CV, "tiny"), ("letter", CV, "a" * 12001)]:
            with self.assertRaises(AIError):
                await self.ai.run(1, action, cv, vacancy)
        self.provider.generate.assert_not_awaited()
        await AIService(self.provider, self.db, 1).run(1, "review", CV)

    async def test_errors_are_safe_and_count_towards_daily_limit(self):
        self.provider.generate.side_effect = RuntimeError("PRIVATE CV AND KEY")
        ai = AIService(self.provider, self.db, 1)
        with self.assertRaises(AIError) as caught:
            await ai.run(1, "review", CV)
        self.assertNotIn("PRIVATE", str(caught.exception))
        with self.assertRaisesRegex(AIError, "Daily AI limit"):
            await ai.run(1, "review", CV)

    async def test_timeout_and_bad_output(self):
        self.provider.generate.side_effect = TimeoutError()
        with self.assertRaisesRegex(AIError, "temporarily unavailable"):
            await self.ai.run(1, "review", CV)
        self.provider.generate.side_effect = None
        for output in ("", "a" * 12001, None):
            self.provider.generate.return_value = output
            with self.assertRaisesRegex(AIError, "unusable"):
                await self.ai.run(1, "review", CV)

    def test_quota_atomic_persistent_and_isolated(self):
        def reserve(_):
            return Database(self.db.path).reserve_ai_attempt(1, "2026-09-10", 3)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 3)
        self.assertFalse(Database(self.db.path).reserve_ai_attempt(1, "2026-09-10", 3))
        self.assertTrue(self.db.reserve_ai_attempt(2, "2026-09-10", 3))
        self.assertTrue(self.db.reserve_ai_attempt(1, "2026-09-11", 3))
        self.assertFalse(self.db.reserve_ai_attempt(3, "2026-09-10", 0))

    def test_chunking_preserves_text_and_emoji(self):
        text = "abc😀\n" * 3000
        chunks = message_chunks(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c.encode("utf-16-le")) // 2 <= 3500 for c in chunks))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, payload=None, status=200):
        real_client = httpx.AsyncClient

        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body["store"])
            self.assertEqual(body["max_output_tokens"], 2200)
            self.assertEqual(request.url.path, "/v1/responses")
            return httpx.Response(status, json=payload)

        client = real_client(transport=httpx.MockTransport(handler))
        with patch("services.ai.httpx.AsyncClient", return_value=client):
            return await OpenAIProvider("test-only-placeholder", "test-model").generate("rules", "source")

    async def test_response_extraction(self):
        result = await self.request({"status": "completed", "output": [
            {"type": "reasoning"}, {"type": "message", "content": [
                {"type": "output_text", "text": "draft"}]}]})
        self.assertEqual(result, "draft")

    async def test_errors_incomplete_refusal_and_malformed(self):
        for status in (401, 429, 500):
            with self.assertRaises(AIError) as caught:
                await self.request({"error": "PRIVATE"}, status)
            self.assertNotIn("PRIVATE", str(caught.exception))
        for payload in ({"status": "incomplete"}, [], {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal"}]}]}):
            with self.assertRaises(AIError):
                await self.request(payload)


if __name__ == "__main__":
    unittest.main()

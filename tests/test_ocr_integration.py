"""Optional real local-model smoke test; never downloads models or logs source text."""
import os
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from services.ocr import EasyOCREngine
from services.vacancy_pipeline import VacancyPipeline
from collector import Collector
from db import Database
from vacancy_store import VacancyStore
from product_ui import ProductUI
from vacancy_ui import VacancyUI
from telethon import TelegramClient, types


@unittest.skipUnless(os.getenv('RUN_OCR_INTEGRATION') == '1', 'Enable explicitly after preparing local OCR models.')
class RealOCRTests(unittest.TestCase):
    def test_local_english_vacancy_fixture_without_network(self):
        model_dir = Path(__file__).resolve().parents[1] / 'ocr_models'
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'fixture.png'
            image = Image.new('RGB', (1100, 380), 'white')
            font = ImageFont.load_default(size=40)
            ImageDraw.Draw(image).multiline_text((35, 25),
                'HIRING ANALYST\nDubai SQL Python\nSalary 5000 AED\njobs@example.com',
                fill='black', font=font, spacing=25)
            image.save(source)
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG')
            with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden in OCR test')):
                engine = EasyOCREngine(model_dir, download=False)
                async def full_pipeline():
                    # Actual Telethon cached-photo downloader, no network or login.
                    photo = types.Photo(1, 0, b'', datetime.now(timezone.utc),
                        [types.PhotoCachedSize('x', 1100, 380, buffer.getvalue())], 1)
                    message = types.Message(id=7, peer_id=types.PeerChannel(1), date=datetime.now(timezone.utc),
                        message='', media=types.MessageMediaPhoto(photo=photo))
                    client = TelegramClient(None, 12345, 'a' * 32)
                    db = Database(Path(root) / 'test.db')
                    store = VacancyStore(db)
                    store.add_source('test_jobs')
                    settings = SimpleNamespace(ocr_enabled=True, keep_media=False, media_dir=Path(root) / 'media')
                    with patch.object(client, 'connect', side_effect=AssertionError('No Telegram network')):
                        row = await Collector(client, store, VacancyPipeline(settings, engine)).process_message(store.sources()[0], message)
                    self.assertEqual(row['ocr_status'], 'processed')
                    self.assertIn('SQL', row['ocr_text'])
                    self.assertEqual(row['detection_status'], 'vacancy')
                    self.assertEqual(row['salary_min'], 5000)
                    self.assertTrue(row['role'])
                    self.assertIn('Dubai', row['location'])
                    # OCR can misread @; do not infer a contact that was not extracted.
                    self.assertEqual(len(store.list()), 1)
                    update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                        effective_message=SimpleNamespace(reply_text=AsyncMock()))
                    ui = VacancyUI(ProductUI(db, Path(root), lambda language: None))
                    await ui.listing(update, SimpleNamespace(user_data={}))
                    card = update.effective_message.reply_text.call_args.args[0]
                    self.assertIn('5000 AED', card)
                    self.assertIn('Dubai', card)
                    self.assertEqual(list(settings.media_dir.iterdir()), [])
                asyncio.run(full_pipeline())

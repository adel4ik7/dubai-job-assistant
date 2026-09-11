"""Optional real local-model smoke test; never downloads models or logs source text."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from services.ocr import EasyOCREngine, preprocess
from services.vacancy_pipeline import analyze_text


@unittest.skipUnless(os.getenv('RUN_OCR_INTEGRATION') == '1', 'Enable explicitly after preparing local OCR models.')
class RealOCRTests(unittest.TestCase):
    def test_local_english_vacancy_fixture_without_network(self):
        model_dir = Path(__file__).resolve().parents[1] / 'ocr_models'
        with tempfile.TemporaryDirectory() as root:
            source, prepared = Path(root) / 'fixture.png', Path(root) / 'prepared.png'
            image = Image.new('RGB', (1100, 380), 'white')
            font = ImageFont.load_default(size=40)
            ImageDraw.Draw(image).multiline_text((35, 25),
                'HIRING ANALYST\nDubai SQL Python\nSalary 5000 AED\njobs@example.com',
                fill='black', font=font, spacing=25)
            image.save(source)
            preprocess(source, prepared)
            with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden in OCR test')):
                result = EasyOCREngine(model_dir, download=False).read(prepared)
            self.assertIn('SQL', result)
            self.assertIn('5000', result)
            parsed = analyze_text('', result)
            self.assertEqual(parsed['detection_status'], 'vacancy')
            self.assertEqual(parsed['salary_min'], 5000)

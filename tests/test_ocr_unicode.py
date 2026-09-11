import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from services.ocr import EasyOCREngine


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Requires optional OCR numpy dependency')
class UnicodeOCRTests(unittest.TestCase):
    def test_cyrillic_windows_like_path_passes_rgb_array_not_filename(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'Users' / 'Адель' / 'Резюме вакансии' / 'prepared.png'
            path.parent.mkdir(parents=True)
            Image.new('L', (31, 17), 125).save(path)
            engine = EasyOCREngine.__new__(EasyOCREngine)
            def readtext(value, **kwargs):
                self.assertIsInstance(value, np.ndarray)
                self.assertEqual(value.shape, (17, 31, 3))
                self.assertEqual(value.dtype, np.uint8)
                self.assertTrue((value == 125).all())
                self.assertEqual(kwargs, dict(detail=0, paragraph=False))
                return ['  Hiring   analyst ', 'Dubai']
            engine.reader = Mock(readtext=Mock(side_effect=readtext))
            self.assertEqual(engine.read(path), 'Hiring analyst\nDubai')
            path.unlink()  # Pillow has released the file handle on Windows.

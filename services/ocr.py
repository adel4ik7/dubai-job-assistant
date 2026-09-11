"""Replaceable local OCR engine. No model downloads during collection."""

from pathlib import Path
from typing import Protocol
import re
import warnings

from PIL import Image, ImageEnhance, ImageOps


class OCREngine(Protocol):
    def read(self, path: Path) -> str:
        ...


def preprocess(path: Path, output: Path, threshold: bool = False) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)

        with Image.open(path) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("Image exceeds OCR pixel budget")

            image = ImageOps.exif_transpose(image).convert("L")

            scale = min(2.0, 1000 / image.width) if image.width < 1000 else 1.0
            scale = min(scale, 2400 / max(image.size))

            image = image.resize(
                (
                    max(1, int(image.width * scale)),
                    max(1, int(image.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )

            image = ImageEnhance.Contrast(
                ImageOps.autocontrast(image)
            ).enhance(1.3)

            if threshold:
                image = image.point(lambda p: 255 if p >= 160 else 0)

            image.save(output, format="PNG")


def cleanup_text(text: str) -> str:
    return "\n".join(
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    )


class EasyOCREngine:
    def __init__(self, model_dir: Path, download: bool = False):
        import easyocr

        self.reader = easyocr.Reader(
            ["ru", "en"],
            gpu=False,
            model_storage_directory=str(model_dir),
            user_network_directory=str(model_dir / "user_network"),
            download_enabled=download,
            verbose=False,
        )

    def read(self, path: Path) -> str:
        # Pillow корректно работает с Unicode-путями Windows.
        # Передаём EasyOCR сам массив изображения, а не путь к файлу.
        import numpy as np
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image_array = np.asarray(image)

        results = self.reader.readtext(
            image_array,
            detail=0,
            paragraph=False,
        )

        return cleanup_text("\n".join(results))

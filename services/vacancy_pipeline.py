import asyncio
import json
import logging
from pathlib import Path
import tempfile

from telethon.errors import FloodWaitError

from services.ocr import preprocess, cleanup_text
from services.vacancy_dedup import content_hash
from services.vacancy_detector import detect_vacancy
from services.vacancy_parser import parse_vacancy

log = logging.getLogger('collector')


def analyze_text(raw, ocr=''):
    combined = '\n'.join(dict.fromkeys(t.strip() for t in (raw, ocr) if t.strip()))
    parsed = parse_vacancy(combined)
    parsed.pop('raw_text')
    parsed['skills_json'] = json.dumps(parsed.pop('skills'), ensure_ascii=False)
    parsed['languages_json'] = json.dumps(parsed.pop('languages'), ensure_ascii=False)
    return dict(raw_text=raw, ocr_text=ocr, combined_text=combined, content_hash=content_hash(combined),
                **parsed, **detect_vacancy(combined))


class VacancyPipeline:
    def __init__(self, settings, engine=None):
        self.settings, self.engine = settings, engine

    async def __call__(self, client, source, message):
        raw = getattr(message, 'message', '') or ''
        ocr, ocr_status = '', 'not_needed'
        photo = getattr(message, 'photo', None)
        document = getattr(message, 'document', None)
        is_image = photo or (document and getattr(document, 'mime_type', '') in {'image/jpeg', 'image/png', 'image/webp'})
        if is_image:
            ocr_status = 'disabled' if not self.settings.ocr_enabled else 'unavailable' if self.engine is None else 'pending'
            size = getattr(getattr(message, 'file', None), 'size', 0) or 0
            if size > 10 * 1024 * 1024:
                ocr_status = 'oversized'
            if ocr_status == 'pending':
                self.settings.media_dir.mkdir(exist_ok=True)
                # No source-controlled filenames, and all temporary copies are removed.
                with tempfile.TemporaryDirectory(prefix='ocr_', dir=self.settings.media_dir) as folder:
                    original, prepared = Path(folder) / 'original', Path(folder) / 'prepared.png'
                    try:
                        await client.download_media(message, file=str(original))
                        await asyncio.to_thread(preprocess, original, prepared)
                        ocr = cleanup_text(await asyncio.to_thread(self.engine.read, prepared))
                        ocr_status = 'processed' if ocr else 'empty'
                        if self.settings.keep_media:
                            retained = self.settings.media_dir / f"{source['id']}_{message.id}.image"
                            original.replace(retained)
                    except FloodWaitError:
                        raise
                    except Exception:
                        ocr_status = 'failed'
                        log.warning('OCR failure; source_id=%s message_id=%s', source['id'], message.id)
        return dict(**analyze_text(raw, ocr), ocr_status=ocr_status)

import asyncio
import json
import logging
from pathlib import Path
import tempfile
import re
import time

from telethon.errors import FloodWaitError

from services.ocr import preprocess, cleanup_text
from services.vacancy_dedup import content_hash
from services.vacancy_detector import detect_vacancy
from services.vacancy_parser import parse_vacancy
from services.collector_diagnostics import Diagnostics

log = logging.getLogger('collector')


def prune_media(directory, days):
    """Only collector-generated retained originals, never arbitrary user files."""
    if not directory.exists():
        return
    cutoff = time.time() - days * 86400
    for path in directory.iterdir():
        if re.fullmatch(r'\d+_\d+\.image', path.name) and path.is_file() and not path.is_symlink():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                log.warning('Retained media cleanup failed; retry on next poll.')


def analyze_text(raw, ocr=''):
    combined = '\n'.join(dict.fromkeys(t.strip() for t in (raw, ocr) if t.strip()))
    parsed = parse_vacancy(combined)
    parsed.pop('raw_text')
    parsed['skills_json'] = json.dumps(parsed.pop('skills'), ensure_ascii=False)
    parsed['languages_json'] = json.dumps(parsed.pop('languages'), ensure_ascii=False)
    return dict(raw_text=raw, ocr_text=ocr, combined_text=combined, content_hash=content_hash(combined),
                **parsed, **detect_vacancy(combined))


class VacancyPipeline:
    def __init__(self, settings, engine=None, diagnostics=None):
        self.settings, self.engine = settings, engine
        self.diagnostics = diagnostics or Diagnostics()

    async def __call__(self, client, source, message):
        raw = getattr(message, 'message', '') or ''
        raw = raw if isinstance(raw, str) else ''
        ocr, ocr_status = '', 'not_needed'
        photo = getattr(message, 'photo', None)
        document = getattr(message, 'document', None)
        is_image = photo or (document and getattr(document, 'mime_type', '') in {'image/jpeg', 'image/png', 'image/webp'})
        downloaded, exists, called = False, False, False
        reason = 'no_supported_image' if not is_image else ''
        self.diagnostics.event(source['id'], message.id, 'media_detection',
            has_text=bool(raw.strip()), has_photo=bool(photo), has_image_document=bool(is_image and not photo))
        if is_image:
            ocr_status = 'disabled' if not self.settings.ocr_enabled else 'unavailable' if self.engine is None else 'pending'
            size = getattr(getattr(message, 'file', None), 'size', 0) or 0
            if size > 10 * 1024 * 1024:
                ocr_status = 'oversized'
            reason = {'disabled': 'ocr_disabled', 'unavailable': 'engine_unavailable', 'oversized': 'media_too_large'}.get(ocr_status, '')
            if ocr_status == 'pending':
                self.settings.media_dir.mkdir(exist_ok=True)
                # No source-controlled filenames, and all temporary copies are removed.
                with tempfile.TemporaryDirectory(prefix='ocr_', dir=self.settings.media_dir) as folder:
                    original, prepared = Path(folder) / 'original', Path(folder) / 'prepared.png'
                    try:
                        reason = 'download_failed'
                        downloaded_path = await client.download_media(message, file=str(original))
                        # Telethon may add an extension: use its returned path, not the requested stem.
                        if not downloaded_path:
                            raise ValueError('No media returned')
                        returned = Path(downloaded_path)
                        original = returned.resolve()
                        if original.parent != Path(folder).resolve() or returned.is_symlink():
                            raise ValueError('Unexpected download destination')
                        exists = original.is_file()
                        downloaded = exists
                        reason = 'downloaded_file_missing'
                        if not exists:
                            raise FileNotFoundError
                        self.diagnostics.event(source['id'], message.id, 'download',
                            media_downloaded=True, temp_path_exists=True)
                        reason = 'media_too_large'
                        if original.stat().st_size > 10 * 1024 * 1024:
                            raise ValueError('Image exceeds OCR byte budget')
                        reason = 'preprocess_or_ocr_failed'
                        def recognize():
                            nonlocal called, reason
                            reason = 'preprocess_failed'
                            preprocess(original, prepared)
                            called = True
                            reason = 'ocr_failed'
                            return cleanup_text(self.engine.read(prepared))
                        task = asyncio.create_task(asyncio.to_thread(recognize))
                        try:
                            ocr = await asyncio.shield(task)
                        except asyncio.CancelledError:
                            # Let the local worker release its files before cleanup on Windows.
                            await task
                            raise
                        ocr_status = 'processed' if ocr else 'empty'
                        reason = '' if ocr else 'ocr_empty'
                        if self.settings.keep_media:
                            retained = self.settings.media_dir / f"{source['id']}_{message.id}.image"
                            original.replace(retained)
                    except FloodWaitError:
                        raise
                    except Exception:
                        ocr_status = 'failed'
                        log.warning('OCR failure; source_id=%s message_id=%s', source['id'], message.id)
        self.diagnostics.event(source['id'], message.id, 'ocr', media_downloaded=downloaded,
            temp_path_exists=exists, ocr_called=called, ocr_chars=len(ocr), ocr_preview=ocr, reason=reason)
        result = dict(**analyze_text(raw, ocr), ocr_status=ocr_status)
        self.diagnostics.event(source['id'], message.id, 'detection_and_parsing',
            detection_score=result['detection_score'], detection_status=result['detection_status'],
            role_found=bool(result['role']), location_found=bool(result['location']),
            email_found=bool(result['email']), salary_found=result['salary_min'] is not None)
        return result

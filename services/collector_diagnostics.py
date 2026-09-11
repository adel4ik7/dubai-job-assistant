"""Opt-in, content-minimizing diagnostics; never enable third-party DEBUG logs."""
import json
import logging
import re

log = logging.getLogger('collector')

# A preview cannot reliably redact arbitrary personal names with regex alone.
# Retain only diagnostic job vocabulary; mask all other words, numbers and contacts.
PREVIEW_WORDS = set('hiring vacancy vacancies required wanted looking for job opening career '
    'opportunity send cv apply now salary experience immediate joining recruitment '
    'analyst accountant engineer driver waiter manager receptionist developer '
    'dubai uae abu dhabi sharjah aed sql python power bi excel tableau english '
    'вакансия требуется ищем работа зарплата опыт резюме собеседование дубай оаэ '
    'бухгалтер аналитик менеджер английский'.split())


def safe_preview(value):
    value = re.sub(r'https?://\S+|\S*@\S+|\+?\d[\d\s().-]{4,}\d', '[redacted]', value)
    tokens = re.findall(r'\w+', value)
    return ' '.join(token if token.casefold() in PREVIEW_WORDS else '[redacted]' for token in tokens)[:120]


class Diagnostics:
    def __init__(self, enabled=False):
        self.enabled = enabled

    def event(self, source_id, message_id, stage, **fields):
        if not self.enabled:
            return
        if 'ocr_preview' in fields:
            fields['ocr_preview'] = safe_preview(fields['ocr_preview'])
        log.info('vacancy_debug %s', json.dumps(dict(source_id=source_id, message_id=message_id,
            stage=stage, **fields), ensure_ascii=True))

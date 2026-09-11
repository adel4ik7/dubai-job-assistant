"""Content-only fingerprints; source rows stay available via duplicate_of."""
import hashlib
import re
import unicodedata
from services.vacancy_parser import PHONE, contacts


def normalize_content(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    # Strip only explicitly marked source footers, never arbitrary contact usernames.
    text = re.sub(r'(?im)^\s*(?:source|источник|forwarded from|переслано из)\s*[:–-].*$', '', text)
    text = PHONE.sub(lambda m: contacts(m[0])['phone'] or m[0], text)
    text = re.sub(r'(?<=\d),(?=\d{3}\b)', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.split())


def content_hash(text):
    normalized = normalize_content(text)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else None

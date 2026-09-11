"""Collector settings are independent of the bot token and never repr secrets."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import BASE_DIR


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
        if minimum <= value <= maximum:
            return value
    except ValueError:
        pass
    raise ValueError(f'{name} must be between {minimum} and {maximum}.')


def env_bool(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {'true', 'false'}:
        raise ValueError(f'{name} must be true or false.')
    return value == 'true'


@dataclass(frozen=True)
class CollectorSettings:
    api_id: int = field(repr=False)
    api_hash: str = field(repr=False)
    phone: str = field(repr=False)
    session_path: Path = field(repr=False)
    database_path: Path
    media_dir: Path
    poll_seconds: int = 300
    keep_media: bool = False
    ocr_enabled: bool = False
    media_retention_days: int = 7


def load_collector_settings():
    api_id = env_int('TELEGRAM_API_ID', 0, 1, 2147483647)
    api_hash = os.getenv('TELEGRAM_API_HASH', '').strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise ValueError('Set TELEGRAM_API_HASH in .env.')
    name = os.getenv('TELEGRAM_SESSION_NAME', 'dubai_job_collector').strip()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
        raise ValueError('TELEGRAM_SESSION_NAME must be a simple name (letters, numbers, _ or -).')
    return CollectorSettings(api_id, api_hash, os.getenv('TELEGRAM_PHONE', '').strip(),
        BASE_DIR / 'sessions' / name, BASE_DIR / 'data' / 'bot.sqlite3',
        BASE_DIR / 'collector_media', env_int('VACANCY_POLL_SECONDS', 300, 60, 86400),
        env_bool('VACANCY_KEEP_MEDIA'), env_bool('VACANCY_OCR_ENABLED'),
        env_int('VACANCY_MEDIA_RETENTION_DAYS', 7, 1, 30))

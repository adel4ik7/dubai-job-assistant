from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_telegram_id: int | None
    openai_api_key: str | None
    database_path: Path
    uploads_dir: Path
    openai_model: str = "gpt-4o-mini"
    ai_daily_limit: int = 5


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "PASTE_YOUR_BOTFATHER_TOKEN_HERE":
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add the BotFather token."
        )

    admin_raw = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    admin_id = int(admin_raw) if admin_raw.isdigit() else None

    try:
        daily_limit = int(os.getenv("AI_DAILY_LIMIT", "5"))
        if not 0 <= daily_limit <= 1000:
            raise ValueError
    except ValueError:
        raise RuntimeError("AI_DAILY_LIMIT must be an integer between 0 and 1000.") from None

    data_dir = BASE_DIR / "data"
    uploads_dir = BASE_DIR / "uploads"
    data_dir.mkdir(exist_ok=True)
    uploads_dir.mkdir(exist_ok=True)

    return Settings(
        telegram_bot_token=token,
        admin_telegram_id=admin_id,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        database_path=data_dir / "bot.sqlite3",
        uploads_dir=uploads_dir,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        ai_daily_limit=daily_limit,
    )

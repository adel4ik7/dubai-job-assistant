"""Local validation, profile checks and bounded user-file deletion."""
from locales import text, field_labels
from statuses import normalize_status
import re
from datetime import date
from pathlib import Path

from db import Database, PROFILE_FIELDS, STATUSES
from services.matcher import analyse_match

PROFILE_LABELS = field_labels('en', PROFILE_FIELDS)
APP_LABELS = field_labels('en', ('company','role','status','source','salary','date_applied','notes'))
ENGLISH_LEVELS = tuple(text('en', 'english_'+str(i)) for i in range(8))


def validate_field(field: str, value: str, language: str = "en") -> str:
    value = value.strip()
    if field == 'status':
        value = normalize_status(value)
    if field == 'english_level':
        for i in range(8):
            if value == text(language, 'english_'+str(i)):
                value = ENGLISH_LEVELS[i]
                break
    if value == "-":
        value = ""
    maximum = 1000 if field == "notes" else 200
    if len(value) > maximum:
        raise ValueError(text(language, "validation_length", maximum=maximum))
    if field in {"company", "role", "full_name"} and not value:
        raise ValueError(text(language,"validation_required"))
    if field == "status" and value not in STATUSES:
        raise ValueError(text(language,"validation_status"))
    if field == "english_level" and value and value not in ENGLISH_LEVELS:
        raise ValueError(text(language,"validation_english"))
    if field == "years_experience" and value:
        if not re.fullmatch(r"\d{1,2}(?:\.\d)?", value) or not 0 <= float(value) <= 80:
            raise ValueError(text(language,"validation_years"))
    if field == "date_applied" and value:
        try:
            valid = date.fromisoformat(value)
        except ValueError:
            raise ValueError(text(language,"validation_date")) from None
        if valid.isoformat() != value or valid > date.today():
            raise ValueError(text(language,"validation_future"))
    return value


def profile_gaps(profile: dict | None, vacancy: str, language: str = "en") -> str:
    if not profile:
        return text(language,"profile_create_first")
    missing = [label for key, label in field_labels(language, PROFILE_FIELDS).items() if key != "notes" and not profile.get(key)]
    facts = []
    if profile.get("current_location"):
        facts.append("Currently based in " + profile["current_location"])
    if profile.get("visa_status"):
        facts.append(profile["visa_status"])
    if profile.get("years_experience"):
        facts.append(profile["years_experience"] + " years total experience")
    english = profile.get("english_level", "")
    if english and english != "Not specified":
        facts.append("English " + english)
    comparison = analyse_match(". ".join(facts), vacancy, language=language)
    gaps = [r["label"] for r in comparison["important_gaps"] + comparison["optional_gaps"]
            if r["category"] in {"experience", "languages", "location"}]
    lines = [text(language,"profile_check")]
    if missing:
        lines.append(text(language,"profile_missing",items="; ".join(missing)))
    if gaps:
        lines.append(text(language,"profile_unconfirmed",items="; ".join(dict.fromkeys(gaps))))
    if not missing and not gaps:
        lines.append(text(language,"profile_no_gaps"))
    lines.append(text(language,"profile_limits"))
    return "\n\n".join(lines)


class PrivacyError(Exception):
    pass


class UserFiles:
    def __init__(self, database: Database, uploads_dir: Path):
        self.db = database
        self.root = uploads_dir.resolve()

    def safe_path(self, user_id: int, raw: str) -> Path:
        path = Path(raw).absolute()
        resolved = path.resolve()
        if (path.is_symlink() or resolved.parent != self.root or
                not resolved.name.startswith(f"{user_id}_") or not resolved.suffix.lower() in {".pdf", ".docx", ".txt"}):
            raise PrivacyError("privacy_path")
        return resolved

    def delete_cv(self, user_id: int, resume_id: int) -> bool:
        resume = self.db.get_resume(user_id, resume_id)
        if not resume:
            return False
        path = self.safe_path(user_id, resume["file_path"])
        # Historical repeated uploads may point at one file. Keep it until last reference.
        if not self.db.path_references(resume["file_path"], excluding_id=resume_id):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise PrivacyError("privacy_cv") from None
        return self.db.delete_resume_record(user_id, resume_id)

    def delete_all(self, user_id: int) -> None:
        paths = set(self.db.resume_paths(user_id))
        # Include failed-upload files, but only the generated per-user filename namespace.
        paths.update(str(p) for p in self.root.glob(f"{user_id}_*") if p.suffix.lower() in {".pdf", ".docx", ".txt"})
        checked = [self.safe_path(user_id, raw) for raw in paths]
        other_paths = {Path(raw).resolve() for raw in self.db.other_resume_paths(user_id)}
        if other_paths.intersection(checked):
            raise PrivacyError("privacy_owner")
        try:
            for path in checked:
                path.unlink(missing_ok=True)
        except OSError:
            raise PrivacyError("privacy_retry") from None
        self.db.delete_user_records(user_id)

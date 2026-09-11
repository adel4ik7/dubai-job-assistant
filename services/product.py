"""Local validation, profile checks and bounded user-file deletion."""
import re
from datetime import date
from pathlib import Path

from db import Database, PROFILE_FIELDS, STATUSES
from services.matcher import analyse_match

PROFILE_LABELS = dict(zip(PROFILE_FIELDS, ("Full name", "Desired role", "Desired salary (currency / period)",
    "Current location", "UAE visa status", "Years of experience", "English level", "Optional notes")))
APP_LABELS = {"company": "Company", "role": "Role", "status": "Status", "source": "Source",
              "salary": "Salary (currency / period), if known", "date_applied": "Date applied (YYYY-MM-DD)", "notes": "Notes"}
ENGLISH_LEVELS = ("Not specified", "Beginner (A1)", "Elementary (A2)", "Intermediate (B1)",
                  "Upper intermediate (B2)", "Advanced (C1)", "Proficient (C2)", "Native")


def validate_field(field: str, value: str) -> str:
    value = value.strip()
    if value == "-":
        value = ""
    maximum = 1000 if field == "notes" else 200
    if len(value) > maximum:
        raise ValueError(f"Use at most {maximum} characters.")
    if field in {"company", "role", "full_name"} and not value:
        raise ValueError("This field is required.")
    if field == "status" and value not in STATUSES:
        raise ValueError("Choose one of the status buttons.")
    if field == "english_level" and value and value not in ENGLISH_LEVELS:
        raise ValueError("Choose an English level button, or skip.")
    if field == "years_experience" and value:
        if not re.fullmatch(r"\d{1,2}(?:\.\d)?", value) or not 0 <= float(value) <= 80:
            raise ValueError("Enter years as a number from 0 to 80, e.g. 3 or 2.5.")
    if field == "date_applied" and value:
        try:
            valid = date.fromisoformat(value)
        except ValueError:
            raise ValueError("Use a valid date in YYYY-MM-DD format, or skip if unknown.") from None
        if valid.isoformat() != value or valid > date.today():
            raise ValueError("Use YYYY-MM-DD; date applied cannot be in the future.")
    return value


def profile_gaps(profile: dict | None, vacancy: str) -> str:
    if not profile:
        return "Create a Profile first. Profile information is checked separately and never added to CV facts."
    missing = [label for key, label in PROFILE_LABELS.items() if key != "notes" and not profile.get(key)]
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
    comparison = analyse_match(". ".join(facts), vacancy)
    gaps = [r["label"] for r in comparison["important_gaps"] + comparison["optional_gaps"]
            if r["category"] in {"experience", "languages", "location"}]
    lines = ["Profile check (self-reported; does not change your CV score)"]
    if missing:
        lines.append("Not filled: " + "; ".join(missing))
    if gaps:
        lines.append("Not confirmed by profile: " + "; ".join(dict.fromkeys(gaps)))
    if not missing and not gaps:
        lines.append("No gaps detected by these limited profile checks.")
    lines.append("Desired role and salary need manual comparison. Total experience does not establish specialist experience. Never add unverified claims to your CV.")
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
            raise PrivacyError("File cleanup needs owner assistance. Records were kept so deletion can be retried.")
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
                raise PrivacyError("CV file could not be removed. Close it and retry; the record was kept.") from None
        return self.db.delete_resume_record(user_id, resume_id)

    def delete_all(self, user_id: int) -> None:
        paths = set(self.db.resume_paths(user_id))
        # Include failed-upload files, but only the generated per-user filename namespace.
        paths.update(str(p) for p in self.root.glob(f"{user_id}_*") if p.suffix.lower() in {".pdf", ".docx", ".txt"})
        checked = [self.safe_path(user_id, raw) for raw in paths]
        other_paths = {Path(raw).resolve() for raw in self.db.other_resume_paths(user_id)}
        if other_paths.intersection(checked):
            raise PrivacyError("A file has conflicting ownership records. Ask the owner to resolve this before retrying deletion.")
        try:
            for path in checked:
                path.unlink(missing_ok=True)
        except OSError:
            raise PrivacyError("Some files could not be removed. Close them and retry. Records are kept until cleanup succeeds.") from None
        self.db.delete_user_records(user_id)

"""Catalog lookup only; no user-data translation and no network access."""
import json
import re
from functools import partial
from pathlib import Path

CATALOGS = {lang: json.loads((Path(__file__).parent / f"{lang}.json").read_text(encoding="utf-8-sig"))
            for lang in ("en", "ru")}
REQUIREMENT_LABELS = json.loads((Path(__file__).parent / "requirement_labels_ru.json").read_text(encoding="utf-8"))


def text(language: str, key: str, **values) -> str:
    language = language if language in CATALOGS else "en"
    return CATALOGS[language][key].format(**values)


def translator(language: str):
    return partial(text, language)


def status_label(language: str, status: str) -> str:
    key = "status_" + status
    return text(language, key) if key in CATALOGS["en"] else status


def field_labels(language: str, keys) -> dict:
    return {key: text(language, "field_" + key) for key in keys}


def value_label(language: str, field: str, value: str) -> str:
    if field == "status":
        return status_label(language, value)
    if field == "english_level":
        for i in range(8):
            if value == text("en", "english_" + str(i)):
                return text(language, "english_" + str(i))
    return value


def requirement_label(language: str, label: str, category: str) -> str:
    if language != "ru" or category in {"hard_skills", "tools"}:
        if language == "ru" and " or " in label:
            return label.replace(" or ", text(language, "requirement_or"))
        return label
    if " or " in label:
        return text(language, "requirement_or").join(requirement_label(language, part, category) for part in label.split(" or "))
    if label in REQUIREMENT_LABELS:
        return REQUIREMENT_LABELS[label]
    match = re.fullmatch(r"(\d+)\+ years(?: in (.*)| of experience)", label)
    if match:
        return text(language, "requirement_years_scope", years=match[1], scope=REQUIREMENT_LABELS.get(match[2], match[2])) if match[2] else text(language, "requirement_years", years=match[1])
    if category == "education" and " in " in label:
        degree, subject = label.split(" in ", 1)
        return REQUIREMENT_LABELS.get(degree, degree) + ": " + subject
    return label


def translate_message(language: str, message: str) -> str:
    """Only fixed catalog messages; never call on user-entered content."""
    for key, value in CATALOGS["en"].items():
        if value == message:
            return text(language, key)
    return message

import re
from collections import Counter

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "our",
    "will", "have", "has", "job", "role", "work", "team", "years", "year", "into",
    "but", "not", "all", "who", "what", "when", "where", "can", "any", "their",
    "a", "an", "to", "of", "in", "on", "at", "as", "is", "be", "or", "we", "it",
}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,30}", text.lower())
    return [w.strip(".-") for w in words if w not in STOPWORDS and len(w) > 2]


def analyse_match(resume_text: str, vacancy_text: str) -> dict:
    resume_tokens = set(tokens(resume_text))
    vacancy_counts = Counter(tokens(vacancy_text))

    ranked = [w for w, _ in vacancy_counts.most_common(50)]
    important = ranked[:30] or ranked
    matched = [w for w in important if w in resume_tokens]
    missing = [w for w in important if w not in resume_tokens]

    score = round(100 * len(matched) / max(1, len(important)))

    suggestions = []
    if missing:
        suggestions.append(
            "Consider adding relevant missing keywords naturally: "
            + ", ".join(missing[:8])
            + "."
        )
    if not re.search(r"\b\d+[%+]?\b", resume_text):
        suggestions.append(
            "Add measurable results where truthful: revenue, time saved, volume handled, team size, KPIs."
        )
    if len(resume_text) > 7000:
        suggestions.append("The CV is long. Consider compressing older or less relevant experience.")
    if "dubai" in vacancy_text.lower() and "dubai" not in resume_text.lower():
        suggestions.append("If applicable, make your Dubai/UAE location or relocation readiness explicit.")
    if not suggestions:
        suggestions.append("Keyword alignment is already reasonable; focus on evidence and quantified achievements.")

    return {
        "score": score,
        "matched": matched[:12],
        "missing": missing[:12],
        "suggestions": suggestions[:5],
    }


def format_analysis(result: dict) -> str:
    matched = ", ".join(result["matched"]) or "—"
    missing = ", ".join(result["missing"]) or "—"
    suggestions = "\n".join(f"• {s}" for s in result["suggestions"])

    return (
        f"📊 Match score: {result['score']}%\n\n"
        f"✅ Matching keywords:\n{matched}\n\n"
        f"⚠️ Missing / weak keywords:\n{missing}\n\n"
        f"🛠 Suggested improvements:\n{suggestions}\n\n"
        "This is a heuristic score, not an ATS guarantee."
    )

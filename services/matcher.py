"""Offline requirement matching: a small explicit vocabulary, never word frequency."""
import re
import unicodedata
from dataclasses import dataclass, replace

CATEGORY_LABELS = {
    "hard_skills": "Hard skills", "tools": "Software / tools",
    "experience": "Experience", "education": "Education / certifications",
    "languages": "Languages", "location": "Location / visa", "soft_skills": "Soft skills",
}
WEIGHTS = {"hard_skills": 35, "tools": 20, "experience": 20,
           "education": 10, "languages_location": 10, "soft_skills": 5}
STOPWORDS = set("the and for with that this from your you are our will have has job role work "
                "years year a an to of in on at as is be or we it good looking preferred "
                "advantage basic skills skill knowledge excellent strong required ability".split())


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    aliases = (
        (r"\bpower\s*bi\b", "powerbi"),
        (r"\b(?:ms|microsoft)\s+excel\b", "excel"),
        (r"\b(?:structured query language|t[- ]?sql|pl\s*/\s*sql)\b", "sql"),
        (r"\bpython(?:\s*3(?:\.\d+)*|\s+programming(?:\s+language)?)?\b", "python"),
        (r"\b(?:united arab emirates|u\.?a\.?e\.?)\b", "uae"),
    )
    for pattern, replacement in aliases:
        text = re.sub(pattern, replacement, text)
    for number, word in enumerate(("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"), 1):
        text = re.sub(r"\b" + word + r"(?=\s+(?:years?|yrs?)\b)", str(number), text)
    return text


def tokens(text: str) -> list[str]:
    """Utility only: standalone tokens never become scored requirements."""
    return [w for w in re.findall(r"\b[a-z][a-z0-9+#]*\b", normalize(text)) if w not in STOPWORDS]


# Canonical key, display label, expression. Extend deliberately, with regression tests.
CATALOG = {
    "hard_skills": [
        ("sql", "SQL", r"sql"), ("python", "Python", r"python"),
        ("data_analysis", "Data analysis", r"data analys(?:is|t)|data analytics"),
        ("financial_analysis", "Financial analysis", r"financial analysis|financial modelling|financial modeling"),
        ("accounting", "Accounting", r"accounting|bookkeeping"),
        ("project_management", "Project management", r"project management"),
        ("customer_service", "Customer service", r"customer service|customer support"),
        ("sales", "Sales", r"sales|business development"),
        ("digital_marketing", "Digital marketing", r"digital marketing"),
        ("seo", "SEO", r"seo|search engine optimization"),
        ("machine_learning", "Machine learning", r"machine learning"),
        ("statistics", "Statistics", r"statistics|statistical analysis"),
        ("etl", "ETL", r"etl|extract transform load"),
        ("data_visualization", "Data visualization", r"data visuali[sz]ation"),
        ("java", "Java", r"java"), ("javascript", "JavaScript", r"javascript"),
        ("logistics", "Logistics", r"logistics|supply chain management"),
    ],
    "tools": [
        ("powerbi", "Power BI", r"powerbi"), ("excel", "Microsoft Excel", r"excel"),
        ("tableau", "Tableau", r"tableau"), ("sap", "SAP", r"sap"),
        ("salesforce", "Salesforce", r"salesforce"), ("autocad", "AutoCAD", r"autocad"),
        ("quickbooks", "QuickBooks", r"quickbooks"), ("jira", "Jira", r"jira"),
        ("git", "Git", r"git"), ("aws", "AWS", r"aws|amazon web services"),
        ("azure", "Azure", r"(?:microsoft )?azure"),
        ("postgresql", "PostgreSQL", r"postgresql|postgres"),
        ("mysql", "MySQL", r"mysql"), ("sql_server", "SQL Server", r"(?:microsoft |ms )?sql server"),
    ],
    "education": [
        ("bachelor", "Bachelor's degree", r"bachelor(?:'s|s)?(?: degree)?|bsc|bba|beng"),
        ("master", "Master's degree", r"master(?:'s|s)?(?: degree)?|msc|mba"),
        ("phd", "Doctorate", r"phd|doctorate"),
        ("degree", "University degree", r"university degree|college degree"),
        ("pmp", "PMP certification", r"pmp|project management professional"),
        ("acca", "ACCA", r"acca"), ("cpa", "CPA", r"cpa"),
        ("cfa", "CFA", r"cfa"), ("ielts", "IELTS", r"ielts"),
    ],
    "languages": [(name, name.title(), name) for name in ("english", "arabic", "hindi", "russian", "french", "urdu")],
    "soft_skills": [
        ("communication", "Communication", r"communication|communicator"),
        ("teamwork", "Teamwork", r"teamwork|team player|collaboration|collaborative"),
        ("leadership", "Leadership", r"leadership"),
        ("problem_solving", "Problem solving", r"problem[- ]solving"),
        ("attention_detail", "Attention to detail", r"attention to detail|detail[- ]oriented"),
        ("time_management", "Time management", r"time management"),
        ("adaptability", "Adaptability", r"adaptability|adaptable"),
    ],
    "location": [
        ("dubai", "Dubai location", r"dubai"), ("uae", "UAE location", r"uae"),
        ("abu_dhabi", "Abu Dhabi location", r"abu dhabi"),
        ("own_visa", "Own / valid UAE visa", r"own visa|valid (?:uae |residen(?:ce|t) )?visa|uae residence visa"),
        ("work_authorization", "UAE work authorization", r"(?:uae )?work authori[sz]ation|right to work in (?:the )?uae|authori[sz]ed to work in (?:the )?uae"),
        ("driving_license", "UAE driving licence", r"uae driving licen[cs]e"),
    ],
}
PATTERNS = {key: re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)")
            for entries in CATALOG.values() for key, _, pattern in entries}
OPTIONAL = re.compile(r"\b(?:preferred|preferably|optional|desirable|advantage|nice[- ]to[- ]have|a plus|bonus)\b")
REQUIRED = re.compile(r"\b(?:required|mandatory|essential|must|minimum|at least)\b")
YEARS = re.compile(r"\b(\d{1,2})(?:\s*(?:-|–|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b")
NEGATIVE = re.compile(r"\b(?:no|not|without|lack|lacks|lacking|learning|studying|pursuing|planned|aspiring)\b")
DEGREE_FIELDS = ("computer science", "information technology", "business administration",
                 "accounting", "finance", "engineering", "mathematics", "statistics")


@dataclass(frozen=True)
class Requirement:
    key: str
    label: str
    category: str
    optional: bool = False
    years: int = 0
    scope: str = ""
    advanced: bool = False
    alternatives: tuple[str, ...] = ()


def clauses(text: str):
    """Optional headings persist until the next heading; prose cues stay local."""
    heading_optional = False
    for line in normalize(text).splitlines():
        line = line.strip().lstrip("•*- ")
        if not line:
            continue
        if ":" in line:
            heading, rest = line.split(":", 1)
            if len(heading) < 60 and not re.search(r"[.;]", heading) and re.search(
                r"\b(?:requirements|required|qualifications|skills|preferred|optional|essential|mandatory|nice[- ]to[- ]have)\b", heading):
                heading_optional = bool(OPTIONAL.search(heading))
                line = rest
        if not line or line.endswith(":"):
            if line:
                heading_optional = False
            continue
        for part in re.split(r"[,;]|(?<=[.!?])\s+|\s+but\s+", line):
            mixed_priorities = OPTIONAL.search(part) and REQUIRED.search(part)
            parts = re.split(r"\s+and\s+", part) if mixed_priorities or len(YEARS.findall(part)) > 1 else [part]
            for clause in parts:
                optional = (heading_optional or bool(OPTIONAL.search(clause))) and not REQUIRED.search(clause)
                yield clause.strip(), bool(optional)


def positive_occurrence(clause: str, match: re.Match) -> bool:
    prefix = clause[max(0, match.start() - 60):match.start()]
    suffix = clause[match.end():match.end() + 40]
    return not (NEGATIVE.search(prefix) or re.match(
        r"\s+(?:is |are )?(?:not\b|in progress|planned)\b", suffix))


def extract_requirements(text: str, *, cv: bool = False) -> list[Requirement]:
    found = {}
    for clause, optional in clauses(text):
        items = []
        for category, entries in CATALOG.items():
            for key, label, _ in entries:
                match = PATTERNS[key].search(clause)
                if not match or not positive_occurrence(clause, match):
                    continue
                if category == "location":
                    if "visa" in clause and re.search(r"\b(?:provided|sponsorship|sponsored)\b", clause):
                        continue
                    if key in {"dubai", "uae", "abu_dhabi"}:
                        if re.search(r"\b(?:experience|visa|licen[cs]e|authorization|authorisation|relocat\w*)\b", clause):
                            continue
                        if cv and not re.search(r"\b(?:based|located|living|current(?:ly)?|location|address)\b", clause) and clause.strip(" .") not in {"dubai", "uae", "abu dhabi"}:
                            continue
                advanced = category in {"languages", "hard_skills", "tools"} and bool(
                    re.search(r"\b(?:fluent|fluency|native|advanced|expert|proficient|proficiency)\b", clause))
                scope = ""
                if category == "education" and key in {"bachelor", "master", "phd", "degree"}:
                    scope = next((field for field in DEGREE_FIELDS if re.search(r"\b" + field + r"\b", clause)), "")
                    # Unknown explicitly named fields also remain constraints.
                    if not scope:
                        field = re.match(r"\s+(?:degree\s+)?in\s+([a-z ]+)", clause[match.end():])
                        if field:
                            scope = re.split(r"\s+(?:required|preferred|or|is|and)\b", field[1])[0].strip()[:60]
                    if scope:
                        label += " in " + scope
                items.append(Requirement(key, label, category, optional, scope=scope, advanced=advanced))

        year_match = YEARS.search(clause)
        scope_items = [i for i in items if i.category in {"hard_skills", "tools"}]
        local_experience = re.search(r"\b(?:uae|dubai)\b.*\bexperience\b|\bexperience\b.*\b(?:uae|dubai)\b", clause)
        if year_match and (scope_items or "experience" in clause) and not NEGATIVE.search(clause):
            scopes = [(i.key, i.label) for i in scope_items]
            if local_experience:
                area = "uae" if "uae" in clause else "dubai"
                scopes = [(area + "_experience", area.upper() + " experience")]
            if not scopes:
                # Preserve explicitly named but uncatalogued specializations instead of
                # equating five years in hospitality with five years in engineering.
                tail = clause[year_match.end():].strip()
                specialty = re.search(r"(?:experience\s+(?:in|with|as)|of\s+experience\s+(?:in|with|as))\s+([a-z ]+)", tail)
                if not specialty:
                    specialty = re.match(r"(?:of\s+)?([a-z ]+?)\s+experience\b", tail)
                    if specialty and specialty[1].strip() in {"of", "of relevant", "relevant", "professional", "total", "work"}:
                        specialty = None
                scopes = [(specialty[1].strip()[:80], specialty[1].strip()[:80])] if specialty else [("", "")]
            for scope, scope_label in scopes:
                years = int(year_match[1])
                label = f"{years}+ years" + (f" in {scope_label}" if scope else " of experience")
                items.append(Requirement("years:" + scope, label, "experience", optional, years, scope))
        elif local_experience and not NEGATIVE.search(clause):
            scope = "uae_experience" if "uae" in clause else "dubai_experience"
            items.append(Requirement(scope, scope.replace("_", " ").upper(), "experience", optional, scope=scope))

        # Build adjacent OR chains, even inside a longer list (Excel and Power BI
        # or Tableau). Canonical order makes reversed/repeated OR clauses identical.
        # Complex alternatives involving years remain conservative conditions.
        if not year_match:
            for category in CATALOG:
                choices = sorted((i for i in items if i.category == category),
                                 key=lambda i: PATTERNS[i.key].search(clause).start())
                chains = []
                for choice in choices:
                    if not chains:
                        chains.append([choice])
                        continue
                    first = chains[-1][-1]
                    between = clause[PATTERNS[first.key].search(clause).end():PATTERNS[choice.key].search(clause).start()]
                    if re.fullmatch(r"\s+(?:or)\s+|\s*/\s*", between):
                        chains[-1].append(choice)
                    else:
                        chains.append([choice])
                for chain in chains:
                    if len(chain) < 2:
                        continue
                    chain.sort(key=lambda i: i.key)
                    items = [i for i in items if i not in chain]
                    alternatives = tuple(i.key for i in chain)
                    items.append(replace(chain[0], key="|".join(alternatives),
                                         label=" or ".join(i.label for i in chain),
                                         alternatives=alternatives))
        for item in items:
            previous = found.get(item.key)
            if previous:
                if previous.optional != item.optional:
                    mandatory, preferred = (item, previous) if previous.optional else (previous, item)
                    if preferred.years > mandatory.years or preferred.advanced and not mandatory.advanced:
                        found[item.key + ":preferred"] = preferred
                    item = mandatory
                    found[item.key] = item
                    continue
                selected = item if item.years > previous.years else previous
                item = replace(selected, optional=previous.optional and item.optional,
                               advanced=previous.advanced or item.advanced)
            found[item.key] = item
    requirements = list(found.values())
    return requirements if cv else deduplicate_requirements(requirements)


def deduplicate_requirements(requirements: list[Requirement]) -> list[Requirement]:
    """Repeated constituent mentions don't create an extra penalty beside an OR.

    Keep different levels/scopes separate. Do not merge partially overlapping OR
    groups: (A or B) and (B or C) is not equivalent to (A or B or C).
    """
    removed = set()
    result = list(requirements)
    for index, group in enumerate(result):
        if not group.alternatives:
            continue
        for other_index, item in enumerate(result):
            if other_index == index or other_index in removed:
                continue
            if (not item.alternatives and item.key in group.alternatives and
                    item.category == group.category and item.scope == group.scope and
                    item.advanced == group.advanced and item.years == group.years):
                group = replace(group, optional=group.optional and item.optional)
                removed.add(other_index)
        result[index] = group

    # A city plus its country is one geographic constraint, retaining the city.
    # Visa, work authorization and local experience remain separate requirements.
    cities = [i for i, r in enumerate(result) if r.key in {"dubai", "abu_dhabi"}]
    countries = [i for i, r in enumerate(result) if r.key == "uae"]
    if len(cities) == 1 and countries:
        city_index = cities[0]
        city = result[city_index]
        result[city_index] = replace(city, label=city.label.replace(" location", " / UAE location"),
                                     optional=city.optional and all(result[i].optional for i in countries))
        removed.update(countries)
    return [item for i, item in enumerate(result) if i not in removed]


def evidence_status(requirement: Requirement, evidence: list[Requirement]) -> tuple[bool, str]:
    if requirement.alternatives:
        statuses = [evidence_status(replace(requirement, key=key, alternatives=()), evidence)
                    for key in requirement.alternatives]
        return next((s for s in statuses if s[0]), statuses[0])
    if requirement.category == "experience":
        candidates = [e for e in evidence if e.category == "experience" and
                      (not requirement.scope or e.scope == requirement.scope or
                       requirement.scope == "uae_experience" and e.scope == "dubai_experience")]
        if candidates and max(e.years for e in candidates) >= requirement.years:
            return True, "Explicit experience statement in CV"
        return False, "Required duration / relevant experience not confirmed in CV"
    candidates = [e for e in evidence if e.key == requirement.key]
    if requirement.key == "degree":
        candidates += [e for e in evidence if e.key in {"bachelor", "master", "phd"}]
    if requirement.key == "sql":
        candidates += [e for e in evidence if e.key in {"postgresql", "mysql", "sql_server"}]
    if requirement.key == "uae":
        candidates += [e for e in evidence if e.key in {"dubai", "abu_dhabi"}]
    if requirement.category == "education" and requirement.scope:
        candidates = [e for e in candidates if e.scope == requirement.scope]
    if candidates and (not requirement.advanced or any(e.advanced for e in candidates)):
        return True, "Mentioned in CV; verify proficiency and context"
    if candidates:
        return False, "Mentioned, but requested proficiency is not confirmed"
    return False, "Not confirmed in CV (not proof the candidate lacks it)"


def analyse_match(resume_text: str, vacancy_text: str) -> dict:
    requirements = extract_requirements(vacancy_text)
    evidence = extract_requirements(resume_text, cv=True)
    rows = []
    for requirement in requirements:
        matched, reason = evidence_status(requirement, evidence)
        rows.append({"key": requirement.key, "label": requirement.label,
                     "category": requirement.category, "optional": requirement.optional,
                     "matched": matched, "reason": reason})
    category_scores = {}
    group_fractions, effective_weights = {}, {}
    for group, weight in WEIGHTS.items():
        members = [r for r in rows if (r["category"] in {"languages", "location"}
                   if group == "languages_location" else r["category"] == group)]
        if not members:
            continue
        denominator = sum(0.25 if r["optional"] else 1 for r in members)
        numerator = sum((0.25 if r["optional"] else 1) for r in members if r["matched"])
        # A purely optional category must not outweigh a mandatory category.
        effective_weight = weight * (0.25 if all(r["optional"] for r in members) else 1)
        category_scores[group] = round(100 * numerator / denominator)
        group_fractions[group] = numerator / denominator
        effective_weights[group] = effective_weight
    # Renormalizing sparse vacancies must not turn the nominal 5% soft-skill
    # budget into a 20-50% penalty. Cap the entire group at 5% after normalization.
    core_weight = sum(weight for group, weight in effective_weights.items() if group != "soft_skills")
    if "soft_skills" in effective_weights:
        effective_weights["soft_skills"] = min(effective_weights["soft_skills"], core_weight * 5 / 95)
    active_weight = sum(effective_weights.values())
    total = sum(effective_weights[group] * fraction for group, fraction in group_fractions.items())
    breakdown = {}
    for category in CATEGORY_LABELS:
        members = [r for r in rows if r["category"] == category]
        denominator = sum(0.25 if r["optional"] else 1 for r in members)
        numerator = sum(0.25 if r["optional"] else 1 for r in members if r["matched"])
        breakdown[category] = round(100 * numerator / denominator) if denominator else None
    strong = [r for r in rows if r["matched"]]
    important = [r for r in rows if not r["matched"] and not r["optional"]]
    optional = [r for r in rows if not r["matched"] and r["optional"]]
    recommendations = []
    if strong:
        recommendations.append("Highlight existing CV evidence for " + ", ".join(r["label"] for r in strong[:3]) + "; keep the original facts and proficiency level.")
    # One recommendation per category avoids repeating the same advice for every skill.
    grouped_important = []
    for category in CATEGORY_LABELS:
        members = [r for r in important if r["category"] == category]
        if members:
            grouped_important.append({"category": category, "label": compact_labels(members, 3)})
    for row in grouped_important[:3]:
        if row["category"] == "experience":
            advice = "Compare the requested duration with your actual dated roles; do not round up or count unrelated work."
        elif row["category"] == "education":
            advice = "Check the credential requirement; an unfinished course is not a completed qualification."
        elif row["category"] == "location":
            advice = "Check eligibility with the employer; past work or relocation interest does not establish current location or visa status."
        elif row["category"] == "languages":
            advice = "Assess your actual language level against the vacancy; do not claim unverified fluency."
        else:
            advice = "No sufficient CV evidence was found. Consider learning or practice before claiming this capability."
        recommendations.append(row["label"] + ": " + advice)
    if optional:
        recommendations.append("Lower priority: " + ", ".join(r["label"] for r in optional[:3]) + ". Treat these as optional development goals, not existing qualifications.")
    return {"score": round(total / active_weight * 100) if active_weight else None,
            "requirements": rows, "category_scores": category_scores, "breakdown": breakdown,
            "effective_weights": {k: round(100 * v / active_weight, 4) if active_weight else 0
                                  for k, v in effective_weights.items()},
            "strong_matches": strong, "important_gaps": important, "optional_gaps": optional,
            "matched": [r["label"] for r in strong], "missing": [r["label"] for r in important + optional],
            "suggestions": recommendations,
            "coverage_note": "Based only on recognized requirements in a small English-language dictionary. Unrecognized requirements need manual review."}


def compact_labels(rows: list[dict], limit: int = 4) -> str:
    labels = list(dict.fromkeys(row["label"] for row in rows))
    text = "; ".join(labels[:limit])
    if len(labels) > limit:
        text += f"; +{len(labels) - limit} more"
    return text


def format_analysis(result: dict) -> str:
    score = f"{result['score']}%" if result["score"] is not None else "N/A — insufficient non-soft requirements"
    lines = [f"📊 Overall match score: {score}", ""]
    lines.append("Breakdown (recognized CV evidence)")
    for category, label in CATEGORY_LABELS.items():
        value = result["breakdown"][category]
        label = {"tools": "Tools", "location": "Location", "education": "Education"}.get(category, label)
        lines.append(f"{label}: {str(value) + '%' if value is not None else 'N/A'}")
    lines.append("")
    for title, key in (("✅ Strong matches", "strong_matches"), ("⚠️ Important gaps", "important_gaps"), ("▫️ Optional gaps", "optional_gaps")):
        rows = result[key]
        lines.append(title)
        for category, label in CATEGORY_LABELS.items():
            members = [r for r in rows if r["category"] == category]
            if members:
                lines.append(f"• {label}: {compact_labels(members)}")
        if not rows:
            lines.append("• None identified")
        lines.append("")
    lines.append("🛠 Recommendations")
    lines.extend("• " + s for s in result["suggestions"])
    lines.extend(["", "Gaps mean insufficient CV evidence, including duration or proficiency. N/A means no requirements to assess. Never add skills, experience or credentials you do not have.",
                  result["coverage_note"], "Related items are grouped; +N indicates additional items. Weights: hard skills 35, tools 20, experience 20, education 10, languages/location 10. Absent groups are excluded; optional requirements have quarter weight. Soft skills contribute at most 5% overall.",
                  "This is a heuristic score, not an official ATS score and not an ATS guarantee."])
    return "\n".join(lines)

"""Read-only, bounded retrospective selection; never changes alert delivery state."""
import json
import time
from datetime import datetime, timezone

from services.vacancy_locations import location_matcher
from services.vacancy_search import relevance_scorer
from services.matcher import analyse_match


def matching_jobs(db, preferences, cv_text=None, now=None):
    now = time.time() if now is None else now
    scorers = [relevance_scorer(term) for term in
               json.loads(preferences['roles']) + json.loads(preferences['keywords'])]
    if not scorers:
        return [], 7
    # The existing publication index bounds the date range before normalization.
    # A one-day coarse margin accommodates timezone offsets in stored ISO dates.
    cutoff = datetime.fromtimestamp(now-15*86400, timezone.utc).strftime('%Y-%m-%d')
    with db._connect() as conn:
        conn.create_function('alert_location', 4, location_matcher(preferences['location'], preferences['uae_only']))
        rows = [dict(row) for row in conn.execute('''SELECT v.*,s.title AS source_title,
            (julianday('1970-01-01')+?/86400-julianday(v.published_at)) AS age_days
            FROM visible_vacancies v JOIN vacancy_sources s ON s.id=v.source_id
            WHERE v.published_at>=? AND age_days BETWEEN 0 AND 14
            AND v.detection_status IN ('vacancy','probably_vacancy') AND v.duplicate_of IS NULL
            AND (s.enabled=1 OR EXISTS(SELECT 1 FROM vacancies d JOIN vacancy_sources ds
                ON ds.id=d.source_id WHERE d.duplicate_of=v.id AND ds.enabled=1))
            AND alert_location(v.location,v.combined_text,v.ocr_text,v.raw_text)=1
            AND (? IS NULL OR v.salary_min IS NULL OR v.salary_currency IS NULL
                 OR v.salary_currency!='AED' OR COALESCE(v.salary_max,v.salary_min)>=?)
            ORDER BY julianday(v.published_at) DESC,v.source_url,casefold(v.role),casefold(v.company)
            LIMIT 300''', (now, cutoff, preferences['salary_min'], preferences['salary_min']))]
    matches = []
    for row in rows:
        score = max(scorer(row['role'], row['skills_json'], row['combined_text'],
                          row['ocr_text'], row['raw_text'], row['company'], row['location']) for scorer in scorers)
        if score < 55:
            continue
        # Known sufficient salary breaks equal profession evidence ties without
        # allowing text-only matches to outrank title/skills matches.
        salary_confirmed = (preferences['salary_min'] is not None and row['salary_currency']=='AED'
                            and row['salary_min'] is not None and row['salary_min']>=preferences['salary_min'])
        row['alert_relevance'] = score*10 + int(salary_confirmed)
        row['cv_score'] = None
        matches.append(row)
    days = 7 if sum(row['age_days'] <= 7 for row in matches) >= 5 else 14
    matches = [row for row in matches if row['age_days'] <= days]
    if cv_text:
        for row in matches:
            row['cv_score'] = analyse_match(cv_text, row['combined_text'][:12000])['score']
    matches.sort(key=lambda row: (-row['alert_relevance'], -(row['cv_score'] if row['cv_score'] is not None else -1),
                                 row['age_days'], row['source_url'] or '', row['role'] or '', row['company'] or ''))
    return matches[:50], days

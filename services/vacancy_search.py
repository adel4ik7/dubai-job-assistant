"""Small offline RU/EN profession vocabulary, independent of match scoring."""
import re
import unicodedata
import json


def normalize_search(value):
    text = unicodedata.normalize('NFKC', value or '').casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[\W_]+', ' ', text).split())


# Match whole queries, not individual words inside a more specific profession.
# Overlapping groups are expanded once, without recursive broadening.
PROFESSIONS = (
    ('повар', 'cook', 'chef', 'commis', 'commis chef', 'chef de partie', 'cdp', 'demi chef', 'line cook'),
    ('официант', 'waiter', 'waitress', 'server'),
    ('бариста', 'barista'),
    ('бармен', 'bartender', 'barman'),
    ('аналитик', 'analyst', 'data analyst', 'business analyst'),
    ('бухгалтер', 'accountant'),
    ('кассир', 'cashier'),
    ('администратор', 'administrator', 'receptionist', 'front desk'),
    ('уборщик', 'cleaner', 'housekeeping', 'housekeeper'),
    ('водитель', 'driver'),
    ('продавец', 'sales', 'salesperson', 'sales assistant'),
    ('менеджер', 'manager'),
    ('повар холодного цеха', 'cold kitchen', 'garde manger'),
    ('су-шеф', 'sous chef'),
    ('шеф', 'head chef', 'executive chef', 'chef'),
)
NORMALIZED_GROUPS = tuple(tuple(normalize_search(term) for term in group) for group in PROFESSIONS)


def search_terms(query):
    query = normalize_search(query)
    if not query:
        return ()
    terms = [query]
    for group in NORMALIZED_GROUPS:
        if query in group:
            terms.extend(group)
    if query in {'повар', 'cook', 'chef'}:
        terms.extend(('sous chef', 'head chef', 'executive chef'))
    return tuple(dict.fromkeys(terms))


MIN_RELEVANCE = 30


def relevance_scorer(query):
    """Compile one query for the SQLite scan; scores reflect evidence, not frequency."""
    query = normalize_search(query)
    terms = search_terms(query)
    single_terms = {t for t in terms if ' ' not in t}
    def contains(text, term):
        return bool(term) and f' {term} ' in f' {text} '

    def score(role, skills, combined, ocr, raw, company, location):
        if not terms:
            return 0
        role = normalize_search(role)
        if role == query:
            return 100
        if {role, query} <= {'cdp', 'chef de partie'}:
            return 98
        if contains(role, query):
            title_score = 94
        elif any(role == term for term in terms):
            title_score = 86
        elif any(contains(role, term) for term in terms):
            title_score = 78
        else:
            title_score = 0
        if title_score:
            if query in {'повар', 'cook', 'chef'} and any(contains(role, senior) for senior in ('head chef', 'executive chef', 'sous chef')):
                title_score -= 8
            return title_score
        try:
            parsed_skills = json.loads(skills or '[]')
            if isinstance(parsed_skills, list):
                skills = ' '.join(s for s in parsed_skills if isinstance(s, str))
        except (ValueError, TypeError):
            pass
        skills = normalize_search(skills)
        if any(contains(skills, term) for term in terms):
            return 65
        body_score = 0
        for body in (combined, ocr, raw):
            body = normalize_search(body)
            if any(contains(body, term) for term in terms):
                # A lone mention buried in a long unrelated-role post is weak evidence.
                hits = sum(1 for word in body.split() if word in single_terms)
                body_score = max(body_score, 20 if role and len(body.split()) > 80 and hits <= 1 else 45 if role else 55)
        context_score = 0
        for context in (company, location):
            context = normalize_search(context)
            # Preserve ordinary partial company/location search; synonym-only context
            # is insufficient on its own for a profession search.
            if query in context:
                context_score = 35
        return max(body_score, context_score)
    return score

"""Independent evidence groups, not keyword frequency; confidence is heuristic."""
import re
from services.vacancy_parser import ROLES, contacts, salary
from services.vacancy_locations import parse_location

INTENT = re.compile(r'\b(?:hiring|vacanc(?:y|ies)|wanted|looking for|job opening|career opportunity|'
                    r'send (?:your )?cv|apply now|walk[- ]in interview|recruitment)\b|'
                    r'ваканси\w*|требу[ею]тся|ищем|отправ(?:ить|ьте) резюме', re.I)
REQUIREMENTS = re.compile(r'\b(?:required|experience|immediate joining)\b|опыт|собеседован|резюме|работа', re.I)
SEEKER = re.compile(r'\b(?:looking for (?:a )?(?:job|work)|seeking (?:a )?(?:job|work)|my cv)\b|ищу работу|мо[её] резюме', re.I)
ADVERT = re.compile(r'\b(?:course|training|webinar|apartment|property for sale)\b|курсы|вебинар|аренда квартир', re.I)


def detect_vacancy(text):
    text = text or ''
    features = {
        'intent': bool(INTENT.search(text)),
        'role': bool(ROLES.search(text)),
        'salary': salary(text)['salary_min'] is not None,
        'contact': any(contacts(text).values()) or bool(re.search(r'https?://\S*(?:apply|career|jobs)\S*', text, re.I)),
        'location': bool(parse_location(text)),
        'requirements': bool(REQUIREMENTS.search(text)),
    }
    weights = dict(intent=30, role=20, salary=15, contact=15, location=10, requirements=10)
    score = sum(weights[k] for k, present in features.items() if present)
    if SEEKER.search(text) or (ADVERT.search(text) and not re.search(r'we are hiring|job opening|требуется', text, re.I)):
        score = min(score, 20)
    groups = sum(features.values())
    status = 'vacancy' if features['intent'] and groups >= 3 and score >= 65 else 'probably_vacancy' if groups >= 3 and score >= 45 else 'not_vacancy'
    return dict(detection_score=score, detection_status=status)

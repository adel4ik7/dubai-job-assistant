"""Conservative extraction: retain source wording and never infer missing facts."""
import re

from services.matcher import extract_requirements

EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+', re.I)
PHONE = re.compile(r'(?<!\d)(?:(?:\+|00)?971[\s().-]*(?:0[\s().-]*)?|0)(?:5[024568]|[234679])[\s().-]*\d(?:[\s().-]*\d){6}(?!\d)')
CONTACT = re.compile(r'(?<![\w@])@[a-zA-Z][\w]{3,31}\b')
URL = re.compile(r'https?://[^\s<>]+', re.I)
LOCATION = re.compile(r'\b(?:Dubai|Abu Dhabi|Sharjah|UAE|United Arab Emirates)\b|Дубай|Дубае|Абу[ -]Даби|Шарджа|Шардже|ОАЭ', re.I)
ROLES = re.compile(r'\b(?:data analyst|business analyst|software engineer|sales executive|sales manager|'
    r'accountant|analyst|developer|engineer|receptionist|waiter|waitress|chef|driver|nurse|'
    r'teacher|cashier|barista|cleaner|administrator|designer|manager|assistant)\b|'
    r'бухгалтер\w*|аналитик\w*|менеджер\w*|водител\w*|официант\w*|повар\w*|разработчик\w*|администратор\w*', re.I)
NUMBER = r'\d+(?:[,\s]\d{3})*(?:\.\d+)?\s*[kк]?'
CURRENCY = r'(?:AED|Dhs?\.?|дирхам(?:ов|а)?)'


def contacts(text):
    text = re.sub(r'(?im)^\s*(?:source|источник|forwarded from|переслано из)\s*[:–-].*$', '', text)
    email = EMAIL.search(text)
    phone = PHONE.search(text)
    telegram = CONTACT.search(EMAIL.sub('', text))
    normalized_phone = None
    if phone:
        digits = re.sub(r'\D', '', phone[0])
        digits = digits[2:] if digits.startswith('00') else digits
        digits = '971' + digits[1:] if digits.startswith('0') else digits
        normalized_phone = '+' + re.sub(r'^9710', '971', digits)
    telegram_link = re.search(r'https?://t\.me/([a-zA-Z][\w]{3,31})(?![\w/])(?:\s|$)', text)
    return dict(email=email[0].lower() if email else None, phone=normalized_phone,
                telegram_contact=telegram[0] if telegram else '@' + telegram_link[1] if telegram_link else None)


def salary(text):
    amount = rf'(?P<low>{NUMBER})(?:\s*[-–—]\s*(?P<high>{NUMBER}))?'
    patterns = (rf'\b{CURRENCY}\s*{amount}', rf'(?<![\w\d]){amount}\s*{CURRENCY}\b',
                rf'(?:salary|зарплата|з/п)\s*[:=]?\s*{amount}')
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        def number(value):
            if not value:
                return None
            value = re.sub(r'[,\s]', '', value.lower())
            return float(value.rstrip('kк')) * (1000 if value.endswith(('k', 'к')) else 1)
        low, high = number(match['low']), number(match['high'])
        if high is not None and high < low:
            return dict(salary_min=None, salary_max=None, salary_currency=None)
        return dict(salary_min=low, salary_max=high or low, salary_currency='AED' if index < 2 else None)
    return dict(salary_min=None, salary_max=None, salary_currency=None)


def parse_vacancy(text):
    result = dict.fromkeys(('role', 'company', 'location', 'experience', 'skills', 'languages', 'application_url'))
    result['raw_text'] = text
    result.update(salary(text))
    result.update(contacts(text))
    title = re.search(r'^(?:job title|position|role|должность|вакансия)\s*[:–-]\s*([^\n]+)', text, re.I | re.M)
    if not title:
        title = re.search(r'^(?:we are hiring|hiring|требуется|ищем)\s*[:–-]\s*([^\n]+)', text, re.I | re.M)
    known_role = ROLES.search(URL.sub('', EMAIL.sub('', text)))
    result['role'] = title[1].strip()[:200] if title else known_role[0] if known_role else None
    company = re.search(r'^(?:company|employer|компания|работодатель)\s*[:–-]\s*([^\n]+)', text, re.I | re.M)
    result['company'] = company[1].strip()[:200] if company else None
    locations = list(dict.fromkeys(m[0] for m in LOCATION.finditer(text)))
    result['location'] = ', '.join(locations) if locations else None
    experience = re.search(r'\b\d+(?:\s*[-–]\s*\d+)?\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:[\w ]{0,35})?experience\b|опыт[^\n.]{0,60}\d+[^\n.]{0,20}', text, re.I)
    result['experience'] = experience[0].strip() if experience else None
    requirements = extract_requirements(text)
    result['skills'] = list(dict.fromkeys(r.label for r in requirements if r.category in {'hard_skills', 'tools'})) or None
    result['languages'] = list(dict.fromkeys(r.label for r in requirements if r.category == 'languages')) or None
    russian_languages = [m[0] for m in re.finditer(r'английск\w*|арабск\w*|русск\w*', text, re.I)]
    if russian_languages:
        result['languages'] = list(dict.fromkeys((result['languages'] or []) + russian_languages))
    urls = [m[0].rstrip('.,;)') for m in URL.finditer(text)]
    # An explicit apply link is preferred; otherwise preserve a non-source web URL.
    apply_link = re.search(r'(?:apply|отклик)[^\n]{0,35}?(https?://[^\s<>]+)', text, re.I)
    result['application_url'] = apply_link[1].rstrip('.,;)') if apply_link else next((u for u in urls if not re.match(r'https?://(?:www\.)?t\.me/', u, re.I)), None)
    return result

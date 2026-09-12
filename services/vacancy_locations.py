"""Conservative offline location evidence; no location inferred from a channel name."""
import re

from services.vacancy_search import normalize_search

UAE_LOCATIONS = {
    'Dubai': ('dubai', 'дубай', 'дубае', 'дубаи'),
    'Abu Dhabi': ('abu dhabi', 'абу даби'),
    'Sharjah': ('sharjah', 'шарджа', 'шардже'),
    'Ajman': ('ajman', 'аджман', 'аджмане'),
    'Ras Al Khaimah': ('ras al khaimah', 'ras al khaima', 'рас эль хайма', 'рас аль хайма'),
    'Fujairah': ('fujairah', 'фуджейра', 'фуджейре'),
    'Umm Al Quwain': ('umm al quwain', 'умм аль кувейн', 'умм эль кувейн'),
    'UAE': ('uae', 'u a e', 'united arab emirates', 'оаэ', 'объединенные арабские эмираты'),
}
FOREIGN_LOCATIONS = {
    'India': ('india', 'индия', 'индии', 'mumbai', 'delhi', 'bangalore', 'bengaluru'),
    'Maldives': ('maldives', 'мальдивы', 'мальдивах'),
    'Saudi Arabia': ('saudi', 'saudi arabia', 'ksa', 'riyadh', 'jeddah', 'саудовская аравия', 'саудовской аравии'),
    'Qatar': ('qatar', 'doha', 'катар', 'катаре', 'доха'),
    'Oman': ('oman', 'muscat', 'оман', 'омане'),
    'Bahrain': ('bahrain', 'бахрейн', 'бахрейне'),
    'Kuwait': ('kuwait', 'кувейт', 'кувейте'),
    'Russia': ('russia', 'россия', 'россии', 'moscow', 'москва', 'москве'),
    'Turkey': ('turkey', 'turkiye', 'турция', 'турции'),
    'UK': ('united kingdom', 'uk', 'london', 'великобритания', 'лондон'),
    'USA': ('usa', 'united states', 'сша'),
}
LOCATION_FIELD = re.compile(
    r'^\s*(?:job location|work location|location|based in|локация|место работы|город|страна)\s*[:–—-]\s*(.+)$', re.I | re.M)
JOB_PLACE = re.compile(
    r'(?:hiring|job|position|vacancy|work|based|located|работа|вакансия|требуется)[^\n.!?]{0,100}?\b(?:in|at|в)\s+([^\n.!?]+)', re.I)


def recognized_locations(value):
    normalized = ' ' + normalize_search(value or '') + ' '
    return {name for name, aliases in (UAE_LOCATIONS | FOREIGN_LOCATIONS).items()
            if any(' ' + alias + ' ' in normalized for alias in aliases)}


def location_evidence(text, parsed=''):
    # A stated workplace overrides incidental country mentions elsewhere. An
    # unknown explicit workplace remains unknown, even if contacts mention Dubai.
    explicit = LOCATION_FIELD.findall(text or '')
    if explicit:
        return recognized_locations(' '.join(explicit))
    places = JOB_PLACE.findall(text or '')
    recognized = recognized_locations(' '.join(places))
    if recognized:
        return recognized
    clean = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|@[\w]+', '', text or '')
    clean = '\n'.join(line for line in clean.splitlines() if not re.search(
        r'\b(?:experience|nationality|contact|source|forwarded from)\b|опыт|гражданство|источник|контакт', line, re.I))
    found = recognized_locations(clean)
    if parsed:
        stored = recognized_locations(parsed)
        if not found and stored & recognized_locations(text):
            # Legacy parser may have mistaken experience/contact metadata for
            # the workplace. Do not preserve that inference in a strict filter.
            return set()
        return stored
    return found


def parse_location(text):
    found = location_evidence(text)
    # Country is redundant when one or more emirates/cities are known.
    if found & (UAE_LOCATIONS.keys() - {'UAE'}):
        found.discard('UAE')
    return ', '.join(name for name in UAE_LOCATIONS | FOREIGN_LOCATIONS if name in found) or None


def location_matcher(location='', uae_only=False):
    wanted = recognized_locations(location)
    def matches(parsed, combined, ocr, raw):
        found = location_evidence('\n'.join(filter(None, (raw, ocr, combined))), parsed)
        is_uae = bool(found & UAE_LOCATIONS.keys()) and not (found & FOREIGN_LOCATIONS.keys())
        if uae_only and not is_uae:
            return 0
        if wanted:
            if wanted <= UAE_LOCATIONS.keys() and not is_uae:
                return 0
            return int(bool(found & wanted) or ('UAE' in wanted and is_uae))
        if location:
            return int(normalize_search(location) in normalize_search(parsed or ''))
        return 1
    return matches

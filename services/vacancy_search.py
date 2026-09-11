"""Small offline RU/EN profession vocabulary, independent of match scoring."""
import re
import unicodedata


def normalize_search(value):
    text = unicodedata.normalize('NFKC', value or '').casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[\W_]+', ' ', text).split())


# Match whole queries, not individual words inside a more specific profession.
# Overlapping groups are expanded once, without recursive broadening.
PROFESSIONS = (
    ('повар', 'cook', 'chef', 'commis', 'chef de partie', 'cdp', 'demi chef', 'line cook'),
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
    return tuple(dict.fromkeys(terms))

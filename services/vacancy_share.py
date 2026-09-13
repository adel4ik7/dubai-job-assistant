"""Compact public vacancy sharing; no profile or CV data is accessed."""
import re
from urllib.parse import urlencode, urlsplit

from vacancy_store import salary_label


def public_url(value):
    """Only use a real supplied web URL, never manufacture a source link."""
    value = (value or '').strip()
    try:
        parsed = urlsplit(value)
        if (len(value) <= 1000 and parsed.scheme in {'http', 'https'} and
                parsed.hostname and not parsed.username and not parsed.password and
                not any(c.isspace() for c in value)):
            return value
    except ValueError:
        pass
    return ''


def share_vacancy(vacancy, tr, bot_username=None):
    """Return forwardable text and an optional standard Telegram sharing URL."""
    def field(key):
        return ' '.join(str(vacancy.get(key) or '').split())[:200]

    role, location = field('role'), field('location')
    title = role or tr('share_vacancy')
    if location:
        title += ' — ' + location
    lines = ['🔥 ' + title]
    details = []
    for icon, value in (('🏢', field('company')), ('📍', location),
                        ('💰', salary_label(vacancy))):
        if value:
            details.append(icon + ' ' + value)
    if details:
        lines.append('\n'.join(details))
    source = public_url(vacancy.get('source_url'))
    username = (bot_username or '').lstrip('@')
    bot_link = ('https://t.me/' + username + '?start=vacancy_share'
                if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username) else '')
    links = []
    if source:
        links.append((source, tr('share_source') + '\n' + source))
    if bot_link:
        links.append((bot_link, tr('share_bot') + '\n' + bot_link))
    text = '\n\n'.join(lines + [section for _, section in links])
    share_url = None
    if links:
        # Telegram inserts the selected URL itself. Avoid repeating it in text.
        url = links[0][0]
        body = '\n\n'.join(lines + [section for link, section in links if link != url])
        share_url = 'https://t.me/share/url?' + urlencode({'url': url, 'text': body})
    return text, share_url

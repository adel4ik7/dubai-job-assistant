"""Owner-scoped structured drafts. No generated facts, external APIs or email sending."""
import base64
import copy
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

BASIC = ('full_name', 'target_role', 'phone', 'email', 'current_location', 'nationality', 'visa_status')
EXPERIENCE = ('company', 'role', 'location', 'start_date', 'end_date', 'description')
EDUCATION = ('institution', 'qualification', 'field', 'dates', 'location')
SECTIONS = ('basics', 'summary', 'experience', 'education', 'skills', 'languages',
            'certifications', 'links', 'photo')
SECTION_FIELDS = {'basics': BASIC, 'summary': ('summary',), 'experience': EXPERIENCE,
                  'education': EDUCATION, 'skills': ('skills',), 'languages': ('languages',),
                  'certifications': ('certifications',), 'links': ('linkedin', 'website', 'telegram'),
                  'photo': ('photo',)}


def validate_value(field, value):
    value = value.strip()
    maximum = 3000 if field in {'description', 'summary', 'skills', 'certifications'} else 500
    if len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('cb_invalid')
    if value and field == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('cb_email_error')
    if value and field in {'start_date', 'end_date'}:
        if field == 'end_date' and value.casefold() in {'present', 'по настоящее время', 'сейчас'}:
            return 'Present'
        if not re.fullmatch(r'\d{4}(?:-(?:0[1-9]|1[0-2]))?', value):
            raise ValueError('cb_date_error')
    return value


def photo_data(raw):
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('cb_photo_error')
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width * original.height > 20_000_000:
                raise ValueError('cb_photo_error')
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((600, 600))
            output = io.BytesIO()
            image.save(output, 'JPEG', quality=85)
            return base64.b64encode(output.getvalue()).decode('ascii')
    except (OSError, Image.DecompressionBombError):
        raise ValueError('cb_photo_error') from None


def filename(name, extension):
    if extension not in {'pdf', 'docx'}:
        raise ValueError('cb_invalid')
    stem = unicodedata.normalize('NFKC', name or '')
    stem = re.sub(r'[^\w]+', '_', stem, flags=re.UNICODE).strip('_')[:90].rstrip('_')
    return f'{stem or "Resume"}_CV.{extension}'


@dataclass(frozen=True)
class CVAttachment:
    """Future Apply flow may consume this after explicit user choice; sends nothing."""
    filename: str
    mime_type: str
    content: bytes


class CVBuilder:
    def __init__(self, db, uploads_dir):
        self.db = db
        self.root = Path(uploads_dir)

    def create(self, user_id):
        with self.db._connect() as conn:
            return conn.execute('INSERT INTO cv_drafts(telegram_id) VALUES (?)', (user_id,)).lastrowid

    def get(self, user_id, draft_id):
        with self.db._connect() as conn:
            row = conn.execute('SELECT * FROM cv_drafts WHERE id=? AND telegram_id=?', (draft_id, user_id)).fetchone()
        if row is None:
            raise ValueError('cb_missing')
        result = dict(row)
        result['data'] = json.loads(result.pop('data_json'))
        return result

    def list(self, user_id, page=0):
        with self.db._connect() as conn:
            rows = conn.execute('SELECT id FROM cv_drafts WHERE telegram_id=? ORDER BY id DESC LIMIT 6 OFFSET ?',
                                (user_id, max(0, page) * 5)).fetchall()
        return [self.get(user_id, r[0]) for r in rows]

    def save(self, user_id, draft_id, data):
        self.get(user_id, draft_id)
        with self.db._connect() as conn:
            conn.execute('UPDATE cv_drafts SET data_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND telegram_id=?',
                         (json.dumps(data, ensure_ascii=False), draft_id, user_id))

    def set_section(self, user_id, draft_id, section, values, index=None):
        if section not in SECTIONS:
            raise ValueError('cb_invalid')
        data = self.get(user_id, draft_id)['data']
        if section == 'photo':
            data['photo'] = photo_data(values) if values else ''
        else:
            if set(values) - set(SECTION_FIELDS[section]):
                raise ValueError('cb_invalid')
            cleaned = {k: validate_value(k, v) for k, v in values.items()}
            if section == 'experience' and cleaned.get('end_date') not in {None, '', 'Present'} and cleaned.get('start_date'):
                start = cleaned['start_date'] + ('-01' if len(cleaned['start_date']) == 4 else '')
                end = cleaned['end_date'] + ('-12' if len(cleaned['end_date']) == 4 else '')
                if end < start:
                    raise ValueError('cb_date_error')
            if section in {'experience', 'education'}:
                entries = data.setdefault(section, [])
                if index is None:
                    if len(entries) >= 30:
                        raise ValueError('cb_limit')
                    entries.append(cleaned)
                elif 0 <= index < len(entries):
                    entries[index] = cleaned
                else:
                    raise ValueError('cb_missing')
            else:
                data.update(cleaned)
        self.save(user_id, draft_id, data)

    def remove_entry(self, user_id, draft_id, section, index):
        data = self.get(user_id, draft_id)['data']
        if section not in {'experience', 'education'} or not 0 <= index < len(data.get(section, [])):
            raise ValueError('cb_missing')
        del data[section][index]
        if data.get('_wizard', {}).get('section') == section:
            data.pop('_wizard')
        self.save(user_id, draft_id, data)

    def duplicate(self, user_id, draft_id):
        data = copy.deepcopy(self.get(user_id, draft_id)['data'])
        new_id = self.create(user_id)
        self.save(user_id, new_id, data)
        return new_id

    def attachment(self, user_id, draft_id, extension, language='en'):
        from services.cv_export import render
        data = self.get(user_id, draft_id)['data']
        if not data.get('full_name') or not data.get('target_role'):
            raise ValueError('cb_required')
        content = render(data, extension, language)
        return CVAttachment(filename(data['full_name'], extension),
                            'application/pdf' if extension == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', content)

    def activate(self, user_id, draft_id, language='en'):
        from services.cv_export import plain_text
        from services.product import UserFiles
        attachment = self.attachment(user_id, draft_id, 'docx', language)
        draft = self.get(user_id, draft_id)
        existing = self.db.get_resume(user_id, draft['resume_id']) if draft['resume_id'] else None
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f'{user_id}_{uuid4().hex}.docx'
        path.write_bytes(attachment.content)
        try:
            with self.db._connect() as conn:
                if existing:
                    rid = existing['id']
                    conn.execute('UPDATE resumes SET filename=?,file_path=?,extracted_text=? WHERE id=? AND telegram_id=?',
                                 (attachment.filename, str(path.resolve()), plain_text(draft['data'], language), rid, user_id))
                else:
                    rid = conn.execute('INSERT INTO resumes(telegram_id,filename,file_path,extracted_text) VALUES (?,?,?,?)',
                                       (user_id, attachment.filename, str(path.resolve()), plain_text(draft['data'], language))).lastrowid
                conn.execute('INSERT OR REPLACE INTO active_resumes(telegram_id,resume_id) VALUES (?,?)', (user_id, rid))
                conn.execute('UPDATE cv_drafts SET resume_id=? WHERE id=? AND telegram_id=?', (rid, draft_id, user_id))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if existing and not self.db.path_references(existing['file_path']):
            UserFiles(self.db, self.root).safe_path(user_id, existing['file_path']).unlink(missing_ok=True)
        return rid

    def delete(self, user_id, draft_id):
        from services.product import UserFiles
        row = self.get(user_id, draft_id)
        if row['resume_id']:
            UserFiles(self.db, self.root).delete_cv(user_id, row['resume_id'])
        with self.db._connect() as conn:
            conn.execute('DELETE FROM cv_drafts WHERE id=? AND telegram_id=?', (draft_id, user_id))

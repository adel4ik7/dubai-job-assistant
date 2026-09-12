"""Local preparation only. No email transport or automatic application status changes."""
import re
import json
from datetime import datetime, timezone

from locales import text
from services.product import UserFiles
from vacancy_store import VacancyStore, salary_label

EMAIL = re.compile(r'(?<![\w.+-])[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')


class ApplyFlow:
    def __init__(self, db, uploads_dir):
        self.db = db
        self.files = UserFiles(db, uploads_dir)
        self.vacancies = VacancyStore(db)

    def source(self, user_id, kind, origin_id):
        if kind == 'vacancy':
            row = self.vacancies.get(origin_id)
            if not row or row['detection_status'] not in {'vacancy', 'probably_vacancy'}:
                raise ValueError('ap_missing')
            if row['duplicate_of']:
                row = self.vacancies.get(row['duplicate_of'])
            email = row.get('email')
        elif kind == 'application':
            row = self.db.get_application(user_id, origin_id)
            if not row:
                raise ValueError('ap_missing')
            # Only literal email evidence in the saved vacancy text, never a guess.
            match = EMAIL.search(row.get('vacancy_text') or '')
            email = row.get('recipient_email') or (match.group(0) if match else None)
        else:
            raise ValueError('ap_missing')
        return row, email if email and EMAIL.fullmatch(email) else None

    def prepare(self, user_id, kind, origin_id, language):
        row, email = self.source(user_id, kind, origin_id)
        if kind == 'vacancy':
            origin_id = row['id']
        resume = self.db.active_resume(user_id)
        with self.db._connect() as conn:
            previous = conn.execute('SELECT resume_id,subject,message FROM apply_preparations WHERE telegram_id=? AND origin_kind=? AND origin_id=?',
                                    (user_id, kind, origin_id)).fetchone()
        selected = previous['resume_id'] if previous and self.db.get_resume(user_id, previous['resume_id']) else resume['id'] if resume else None
        subject, message = self.defaults(user_id, selected, row, language)
        return dict(origin_kind=kind, origin_id=origin_id, role=row.get('role') or '', company=row.get('company') or '',
                    location=row.get('location') or '', salary=salary_label(row) if kind=='vacancy' else row.get('salary') or '',
                    source_url=row.get('source_url') or '', recipient_email=email, resume_id=selected,
                    default_subject=subject, default_message=message,
                    subject=previous['subject'] if previous else subject,
                    message=previous['message'] if previous else message)

    def defaults(self, user_id, resume_id, row, language):
        profile = self.db.get_profile(user_id) or {}
        identity = {}
        if resume_id:
            with self.db._connect() as conn:
                built = conn.execute('SELECT data_json FROM cv_drafts WHERE telegram_id=? AND resume_id=?', (user_id,resume_id)).fetchone()
            if built:
                identity = json.loads(built[0])
        name = identity.get('full_name') or profile.get('full_name') or ''
        role = ' '.join((row.get('role') or text(language,'ap_job')).split())
        company = ' '.join((row.get('company') or '').split())
        subject = text(language,'pk_subject',role=role) + (' — '+ ' '.join(name.split()) if name else '')
        target = text(language,'pk_company',company=company) if company else ''
        message = text(language,'pk_message',role=role,company=target)
        signature = [name, identity.get('phone'), identity.get('email')]
        message += ''.join('\n'+value for value in signature if value)
        return subject[:200], message

    def mark_sent(self, user_id, draft):
        """User attests manual sending. Atomic, idempotent tracker update; no network."""
        self.confirm(user_id, draft)
        row, email = self.source(user_id, draft['origin_kind'], draft['origin_id'])
        vacancy_id = row['id'] if draft['origin_kind']=='vacancy' else row.get('vacancy_id')
        now = datetime.now(timezone.utc).isoformat()
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM resumes WHERE telegram_id=? AND id=?', (user_id,draft['resume_id'])).fetchone():
                raise ValueError('ap_cv_missing')
            existing = None
            if draft['origin_kind']=='application':
                existing = conn.execute('SELECT * FROM applications WHERE telegram_id=? AND id=?', (user_id,row['id'])).fetchone()
                if not existing:
                    raise ValueError('ap_missing')
            if existing is None and vacancy_id:
                existing = conn.execute('SELECT * FROM applications WHERE telegram_id=? AND vacancy_id=?', (user_id,vacancy_id)).fetchone()
            if existing is None and row.get('source_url'):
                existing = conn.execute('SELECT * FROM applications WHERE telegram_id=? AND source_url=? ORDER BY id LIMIT 1', (user_id,row['source_url'])).fetchone()
            if existing and existing['applied_at']:
                return existing['id']
            if existing:
                app_id = existing['id']
            else:
                app_id = conn.execute('''INSERT INTO applications(telegram_id,company,role,status,source,salary,vacancy_text,source_url)
                    VALUES(?,?,?,'saved',?,?,?,?)''', (user_id,row.get('company') or '',row.get('role') or '',
                    row.get('source_title') or row.get('source') or '',draft.get('salary') or '',
                    row.get('combined_text') or row.get('vacancy_text') or '',row.get('source_url') or '')).lastrowid
            conn.execute('''UPDATE applications SET vacancy_id=?,selected_cv_id=?,recipient_email=?,email_subject=?,email_body=?,
                status='applied',date_applied=?,applied_at=?,last_contact_at=?,source_url=? WHERE id=? AND telegram_id=?''',
                (vacancy_id,draft['resume_id'],email,draft['subject'],draft['message'],now[:10],now,now,
                 row.get('source_url') or '',app_id,user_id))
            return app_id

    def cv(self, user_id, resume_id):
        resume = self.db.get_resume(user_id, resume_id) if resume_id else None
        if not resume:
            raise ValueError('ap_cv_missing')
        if not self.files.safe_path(user_id, resume['file_path']).is_file():
            raise ValueError('ap_cv_missing')
        return resume

    def confirm(self, user_id, draft):
        row, email = self.source(user_id, draft['origin_kind'], draft['origin_id'])
        # Recheck owner and CV availability at the final step, including after deletion.
        self.cv(user_id, draft['resume_id'])
        if email != draft['recipient_email'] or (row.get('role') or '') != draft['role'] or (row.get('company') or '') != draft['company']:
            raise ValueError('ap_changed')
        subject = self.validate('subject', draft['subject'])
        message = self.validate('message', draft['message'])
        with self.db._connect() as conn:
            conn.execute('''INSERT INTO apply_preparations
                (telegram_id,origin_kind,origin_id,role,company,recipient_email,resume_id,subject,message)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(telegram_id,origin_kind,origin_id) DO UPDATE SET
                role=excluded.role,company=excluded.company,recipient_email=excluded.recipient_email,
                resume_id=excluded.resume_id,subject=excluded.subject,message=excluded.message,
                state='prepared',updated_at=CURRENT_TIMESTAMP''',
                (user_id, draft['origin_kind'], draft['origin_id'], draft['role'], draft['company'],
                 email, draft['resume_id'], subject, message))
            return conn.execute('SELECT id FROM apply_preparations WHERE telegram_id=? AND origin_kind=? AND origin_id=?',
                                (user_id, draft['origin_kind'], draft['origin_id'])).fetchone()[0]

    @staticmethod
    def validate(field, value):
        value = value.strip()
        if field not in {'subject', 'message'} or not value or len(value) > (200 if field == 'subject' else 3000):
            raise ValueError('ap_invalid')
        if any(ord(c) < 32 and (field == 'subject' or c not in '\n\t') for c in value):
            raise ValueError('ap_invalid')
        return value

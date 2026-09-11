"""Local preparation only. No email transport or automatic application status changes."""
import re

from locales import text
from services.product import UserFiles
from vacancy_store import VacancyStore

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
            email = row.get('email')
        elif kind == 'application':
            row = self.db.get_application(user_id, origin_id)
            if not row:
                raise ValueError('ap_missing')
            # Only literal email evidence in the saved vacancy text, never a guess.
            match = EMAIL.search(row.get('vacancy_text') or '')
            email = match.group(0) if match else None
        else:
            raise ValueError('ap_missing')
        return row, email if email and EMAIL.fullmatch(email) else None

    def prepare(self, user_id, kind, origin_id, language):
        row, email = self.source(user_id, kind, origin_id)
        resume = self.db.active_resume(user_id)
        with self.db._connect() as conn:
            previous = conn.execute('SELECT resume_id,subject,message FROM apply_preparations WHERE telegram_id=? AND origin_kind=? AND origin_id=?',
                                    (user_id, kind, origin_id)).fetchone()
        return dict(origin_kind=kind, origin_id=origin_id, role=row.get('role') or '', company=row.get('company') or '',
                    recipient_email=email, resume_id=previous['resume_id'] if previous else resume['id'] if resume else None,
                    subject=previous['subject'] if previous else text(language, 'ap_subject_default', role=row.get('role') or text(language, 'ap_job')),
                    message=previous['message'] if previous else text(language, 'ap_message_default'))

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

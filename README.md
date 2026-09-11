# Dubai Job Assistant — v0.3

## v0.4 collector (separate process)

Obtain your own API ID / hash at https://my.telegram.org under **API development
tools**, following https://core.telegram.org/api/obtaining_api_id. Add them to the
existing `.env` as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`; optionally set
`TELEGRAM_PHONE`. Never commit or share credentials. Session name defaults to
`dubai_job_collector`; session files live in the gitignored `sessions/` directory.
They grant account access and must be protected like credentials.

Install updated `requirements.txt`, then use separate terminals:

```powershell
# Terminal 1
python bot.py
# Terminal 2
python collector.py
# Explicit, bounded test history import, then exit:
python collector.py --backfill 50
```

Use `.\.venv\Scripts\python.exe` instead of `python` if needed. The first collector
login prompts privately for a missing phone, verification code and optional 2FA
password. Authentication remains local; no credentials are requested through the bot.
Stop with Ctrl+C. The collector disconnects gracefully. Run one collector per session.

`sources.json` seeds `jobs_in_dubai` once. Sources and cursors then persist in SQLite.
Source-management commands need no login: `python collector.py --list-sources`,
`--add-source CHANNEL_USERNAME`, `--disable-source ID`, `--enable-source ID`.
Only explicitly configured public broadcast channels are read; private chats/groups
are refused. It never joins channels, sends messages or applies for jobs.
On an uninitialized source the normal run establishes a current-post baseline,
without downloading history. Backfill accepts 1–500 latest posts per enabled source.
Later runs process up to 100 new posts per source per poll, sequentially; the default
poll is 300 seconds (minimum 60). FloodWait pauses for Telegram's requested duration.
Do not use the account for spam or to bypass Telegram limits.

v0.4a checkpoint: raw posts are stored as pending; OCR, detection and bot vacancy
screens are the next stages. Existing v0.3 bot functionality remains available.

A local Telegram assistant for early testers. It helps users organize CVs, compare
vacancies and track applications. **v0.3 makes no OpenAI calls and uses no paid
services**, even if an old `.env` contains an OpenAI key. AI provider code remains
for compatibility; its production menu fallback reports that AI is disabled.

## Install and run

Requires Python 3.10+ (tested on Windows with Python 3.12).

```powershell
cd path\to\dubai_job_assistant
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Only if .env does not already exist:
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe bot.py
```

Create a Telegram bot via `@BotFather` /newbot and enter its token locally as
`TELEGRAM_BOT_TOKEN` in `.env`. Never publish tokens or paste them into chat. Do not
overwrite an existing `.env`. OpenAI settings are not needed for v0.3.

On macOS/Linux use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r
requirements.txt`, and `.venv/bin/python bot.py`. The laptop must remain running
and connected for Telegram polling. Only one polling process should use the token.

## Main menu

### English / Русский

On the first `/start`, choose **English 🇬🇧** or **Русский 🇷🇺**. The choice is
stored in SQLite user settings, separately from profile fields. Change it with
`/language`, the **Language / Язык** menu button, or the language button in Profile.
Switching language cancels an unfinished form; saved data and the active CV remain.
Existing users without a choice use English and see the selector on their next
`/start`. Deleting all personal data also removes the language preference.

Menus, forms, errors, confirmations, help, statuses, dashboards and local analysis
reports support both languages through shared handlers. Technology names and
user-entered company names, roles and other free text are preserved. Interface
localization does **not** extend the matcher's English requirement vocabulary:
English CV/vacancy text is still recommended for analysis, even with Russian UI.

При первом `/start` выберите язык. Позже его можно изменить командой `/language`
или кнопкой «Язык / Language». Все основные разделы и результаты анализа доступны
по-русски. Пользовательские данные не переводятся. Для распознавания требований
анализатор по-прежнему использует небольшой англоязычный словарь.

Translations are in `locales/en.json` and `locales/ru.json`; recognized nontechnical
requirement labels are in `locales/requirement_labels_ru.json`. Add matching keys
and named placeholders to both catalogs; never run translation on arbitrary user
values. No translation API or extra dependency is used.

Use a **private chat** with the bot. The six sections are Profile, CVs, Analyse
vacancy, Applications, Dashboard and Help. `/start` or `/menu` opens the menu;
`/cancel` stops an unfinished form without saving it. Forms and the last analysis
are held in memory and expire when the bot restarts; completed records persist.

### Profile

Create a profile through a short field-by-field form. It stores full name, desired
role, desired salary (include currency and period), current location, UAE visa
status, years of experience, English level, and optional notes. Name is required;
other fields can be skipped. View the profile and use a field's Edit button to
change it or clear an optional value. Nothing is saved until the form completes.
Years accept 0–80, including one decimal place. Salary and visa status are free
text because currency, pay period and personal circumstances vary.

### CVs

Upload PDF, DOCX or TXT files up to 5 MB. Each successful upload is a separate CV
with a unique local filename and becomes active. The CV list marks the active CV
with a star; use a CV's buttons to activate it or delete it after confirmation.
Lists show five records per page. Deleting the active CV selects the newest
remaining CV; deleting the last leaves no active CV. Re-uploading the same document
does not overwrite an earlier file. Failed uploads are cleaned up when possible.
Scanned PDFs without extractable text are not supported (no OCR service is used).

### Analyse vacancy

Upload/select an active CV, then send the vacancy in one message (100–12,000
characters; Telegram clients may impose a smaller message limit). The local report
names the CV used and offers four follow-up actions:

- **Save vacancy/application**: enter company, role and tracking details; choose
  Saved if you have not applied. The original vacancy text is saved with the record.
- **Compare with another CV**: analyzes the same vacancy with another saved CV.
  It does not change the active CV. Use the newest report's buttons.
- **Most important gaps**: shows mandatory requirements not confirmed in that CV.
- **Profile gaps**: shows missing profile fields and limited comparisons for
  experience, language, location and visa. Profile facts never alter the CV score
  or become CV evidence. Desired role and salary need manual comparison.

### Applications

Add records through the Applications form. Fields: company, role, status, source,
salary if known, date applied (YYYY-MM-DD; blank if unknown/not applied), and notes.
Open a record to see its details or change status. Search by company or role;
Unicode case-insensitive substring search combines with the selected status filter.
Clear filters to see all records. Lists are paginated.

Statuses: Saved, Applied, HR screening, Interview, Test task, Final interview,
Offer, Rejected, Withdrawn. `/status ID STATUS` still works, including multiword
statuses, English/Russian labels and internal codes. The database stores only
`saved`, `applied`, `hr_screening`, `interview`, `test_task`, `final_interview`,
`offer`, `rejected`, `withdrawn` for new/updated statuses. Russian labels are:
Сохранено, Отклик отправлен, Скрининг HR, Интервью, Тестовое задание,
Финальное интервью, Оффер, Отказ, Отозвано.
Old `Company | Role | optional note` entry buttons remain supported.
Changing a record with no applied date to a submitted stage sets today's date.

### Dashboard

- Total: every application record, including Saved and legacy statuses.
- Active: Applied, HR screening, Interview, Test task, Final interview.
- Interviews: records currently in Interview, Test task or Final interview.
- Offers/rejections: records currently at Offer/Rejected.
- Interview-stage and offer rates: respective current counts divided by records
  with listed submitted statuses (excludes Saved, Withdrawn and unknown legacy
  statuses). Empty denominators produce 0%.

These are **current-state ratios**, not lifetime funnel conversion rates; there is
no transition-history inference. Offers are excluded from active applications.

## Privacy and deletion

Files are stored under `uploads/`; extracted CV text, profiles, saved vacancy text
and applications are in `data/bot.sqlite3`. Both directories and `.env` are
 gitignored. Logs omit raw CVs, secrets and exception details. Do not enable HTTP
transport debug logging. Use sample or redacted CVs for early testing.

`/delete_my_data` asks for explicit confirmation, valid for five minutes. Confirming
removes the user's profile, CV records, application records, active CV pointer,
usage counters and user row; deletes their local CV files (including failed-upload
remnants); and clears their in-memory form/analysis state. Cancel leaves data intact.
Another user's data cannot be selected or removed by changing a record ID.

Only generated files inside the configured uploads folder with the user's filename
prefix are eligible for deletion. Unsafe paths or conflicting ownership records
stop deletion for owner assistance. File-access failures keep DB records for a
retry; some files may already have been removed, and retry handles missing files.
Historical CV records sharing one file retain it until the final record is removed.
This deletes the product's local data, **not Telegram message history, manually
made backups or copies elsewhere**; it is not a forensic disk-erasure guarantee.

## Existing installations

On startup SQLite migration adds missing profile/active-CV tables and application
columns without rebuilding or deleting old tables. The newest legacy CV becomes
active; an existing active selection survives restart. Notes remain unchanged.
Migration adds nullable `users.language` and converts the nine known English status
labels into stable internal codes. Old applied dates are initialized from `created_at`.
Unknown old statuses
are still visible and may be changed to one of the new statuses. Back up local data
securely before deploying an update; owner-created backups require separate deletion.

## Local matcher

The English-language matcher uses a small explicit vocabulary, not word frequency.
It recognizes hard skills, tools, experience, education/certifications, languages,
location/visa and soft skills. Standalone filler words never become requirements.
Aliases include Power BI/PowerBI, MS/Microsoft Excel, SQL/T-SQL/PL/SQL, Python3,
and UAE/United Arab Emirates. Generic SQL does not prove a specific database tool.

Relative weights: hard skills 35, tools 20, experience 20, education 10, languages
and location together 10; soft skills contribute at most 5% after normalization.
Absent categories are excluded; optional items and wholly optional groups use
quarter weight. Soft-only/unrecognized vacancies produce overall N/A. Breakdown
percentages describe evidence coverage, not each category's contribution.

OR alternatives accept one tool; repeated constituent mentions and reversed ORs
are deduplicated. Dubai/UAE repetitions become one geographic constraint retaining
city specificity. Visa/work authorization remain separate. Explicit years stay
scoped to relevant experience; dates are not summed. Negated skills, unfinished
qualifications, unknown degree subjects and unconfirmed proficiency are handled
conservatively. Related gaps are grouped; long Telegram messages are split safely.

A gap means insufficient evidence, not proof that a capability is absent. Advice
never tells users to invent skills or experience. This is **not an official ATS
score** or hiring guarantee. Unrecognized occupations, complex phrases, non-English
vacancies and implicit facts need manual review. Extend `services/matcher.py` with
synthetic regression cases when real vacancies reveal limitations.

## Verification

```powershell
.\.venv\Scripts\python.exe -m compileall .
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
```

Automated tests use temporary SQLite/files, synthetic tokens and mocked Telegram
messages/provider transport. They cover old-schema migration, profile CRUD, multiple
CVs and selection, filters, dashboard calculations, private-chat controls, forms,
delete confirmation/cancellation, safe file deletion/retry, local analysis and
existing features. Localization tests cover catalog consistency, language persistence,
onboarding/switching, old-schema migration, localized menus/statuses/reports and
preservation of user text. No actual OpenAI requests occur. Offline Application construction
checks startup wiring; live Telegram polling still needs manual acceptance.

Early-tester smoke test: create/edit profile; upload two sample CVs; select the first;
analyze a vacancy; compare the second; inspect both gap actions; save an application;
change/filter/search its status; check dashboard; cancel then confirm CV deletion;
restart and check persistence; cancel then confirm `/delete_my_data`; check that a
second tester's records remain. Keep API features disabled.
Repeat the main flows in both languages; change language from Profile and `/language`,
restart to check persistence, and verify older application records remain searchable.

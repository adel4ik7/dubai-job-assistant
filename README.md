# Dubai Job Assistant — v0.4

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
Source-management commands work offline without Telegram login or API settings:

```powershell
python collector.py --add-source https://t.me/CHANNEL --source-title "Работа / Jobs"
python collector.py --list-sources
python collector.py --disable-source CHANNEL
python collector.py --enable-source CHANNEL
python collector.py --remove-source CHANNEL
```

Usernames, @usernames and public t.me channel links are accepted; enable/disable/remove
also accept the numeric ID from the list. Names may be Russian or English. Repeated
adds keep the same source ID; supplying a title updates its display name. Invite links
and individual post links are rejected. Normal collection picks up all enabled sources
on its next poll, without code changes or a restart.

Removal is soft: it stops collection and marks the source as removed in the admin
list, retaining posts, saved links and its cursor. Startup seeding never restores it
or overrides user titles/enabled preferences. Explicit `--add-source` restores a
removed source with its original ID and cursor. Use disable/enable for a temporary
pause. Old databases gain an additive column; existing user records are preserved.
Only explicitly configured public broadcast channels are read; private chats/groups
are refused. It never joins channels, sends messages or applies for jobs.
On an uninitialized source the normal run establishes a current-post baseline,
without downloading history. Backfill accepts 1–500 latest posts per enabled source.
Later runs process up to 100 new posts per source per poll, sequentially; the default
poll is 300 seconds (minimum 60). FloodWait pauses for Telegram's requested duration.
Do not use the account for spam or to bypass Telegram limits.

Posts are classified locally as vacancy / probably_vacancy / not_vacancy, using
independent intent, role, salary, contact, location and requirement signals. This
is heuristic confidence, not a guarantee of authenticity. Unknown fields remain
empty; an amount without a stated currency does not silently become AED. Repeated
posts retain source records with `duplicate_of`; source footers do not change hashes.

### Optional local OCR (EasyOCR, CPU, English + Russian)

Install CPU PyTorch and torchvision first on Windows, then the optional OCR extras:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-ocr.txt
python collector.py --prepare-ocr
```

Setup downloads free model weights once into gitignored `ocr_models/`. Collection
itself never downloads models or calls an OCR service. Set `VACANCY_OCR_ENABLED=true`
in `.env` after setup. Without OCR dependencies/models, text collection still works;
image-only posts are retained with their OCR status for later inspection/retry.
See https://github.com/JaidedAI/EasyOCR for upstream installation guidance.

Images alone are downloaded for OCR, up to 10 MB; preprocessing caps image pixels,
resizes, converts a temporary copy to grayscale and enhances contrast. Failures do
not stop other posts. `VACANCY_KEEP_MEDIA=false` removes temporary files on success
and failure; true retains originals after successful recognition in `collector_media/`.
Do not use OCR-derived contact or salary details without checking the original post.
The default image fixture tests exercise preprocessing and the pipeline with an
injected engine, without model downloads. After setup, run the real offline-model
fixture test with `$env:RUN_OCR_INTEGRATION='1'` and the usual unittest command.
The synthetic English vacancy image passed real CPU recognition locally; this is
a smoke test, not a guarantee of quality on complex posters or small/blurred text.

### Vacancies in the bot (RU/EN)

Open **🔎 Vacancies / 🔎 Вакансии** for Latest, Best matches, Search, Filters and
Saved. Cards omit unknown fields and link to the original public post. Inspect it
before trusting extracted details. Search covers role, company, raw text, OCR text,
combined text and location. A small offline RU/EN profession dictionary expands
whole queries: `повар` finds cook/chef/commis/CDP and `chef` also finds `повар`.
Case, punctuation, hyphen variants and extra whitespace are normalized. Specific
queries such as `повар холодного цеха` retain their own synonym group. Unknown
queries still use ordinary substring search; this is not automatic translation
or fuzzy OCR correction. The vocabulary lives in `services/vacancy_search.py`;
filters combine location substring, minimum stated salary in AED, source and last
1–365 days. Unknown currencies/salaries do not pass salary filters. Saved listings
are personal; `/delete_my_data` removes those links but retains public source posts.

Analysis uses the active CV and the existing heuristic report. Best matches runs
only on request, over the latest `VACANCY_MATCH_WINDOW` selected posts (default/max
100); it excludes unknown scores and ranks that snapshot. Nothing continuously
scores the full database. Long posts use the first 12,000 characters for matching.

**Add to applications** opens the existing review form with extracted company,
role, source, salary and original post URL. Keep or replace each known value and
fill missing required fields. Status starts as Saved and applied date is blank:
reading a vacancy is not evidence of an application. Enter the actual date/status
if you have applied. The record is inserted only when the entire form is complete;
/cancel discards the draft. No automatic external application or message is sent.

### Recovery, retention and admin

To reprocess the last 50 posts of one source, including photos previously saved
with OCR disabled:

```powershell
python collector.py --reprocess-backfill jobs_in_dubai 50
# Optional safe diagnostics:
python collector.py --reprocess-backfill jobs_in_dubai 50 --debug-pipeline
```

N must be 1–500. Only an enabled, configured public broadcast channel is accepted.
The command reads one bounded snapshot of the latest N messages, processes them
sequentially and exits. Existing source/message rows are updated in place; new posts
are inserted once. Existing content-hash deduplication groups reposts while retaining
their source links. Saved links and the ordinary collection cursor remain intact.
FloodWait is respected; one failed post does not stop later posts. Re-running the
same command does not create duplicate vacancy records. Successful old OCR is
preserved if a retry fails. Enable `VACANCY_OCR_ENABLED=true` and prepare the local
models first to recover image-only posts. Stop any other collector using the same
session before running this command. Unlike ordinary `--backfill`, this mode retries
already collected messages. It cannot be combined with `--backfill`.
Logs identify processed posts, requested OCR reruns, updated rows, duplicates,
skipped posts and failures; the final summary counts processed/skipped/failed posts.
An OCR rerun request is not proof of success: check the OCR outcome in diagnostics.

#### Diagnosing image-only posts

Windows Unicode fix: Pillow opens the image, applies EXIF orientation, converts to
RGB, and passes a NumPy array to EasyOCR. Passing `C:\Users\Адель\...` as a string
can fail inside OpenCV's filename decoding even when the file exists. The array
boundary avoids that decoding path. NumPy is part of the optional OCR installation;
ordinary bot startup does not import it. A Cyrillic-directory regression and the
real OCR pipeline test cover this boundary.

The photo-path bug in v0.4 is fixed: Telethon can change the requested temporary
stem `original` to `original.jpg` (or an image-document extension). The pipeline now
uses the actual returned filename, validates that it is inside the temporary folder,
then runs preprocessing/OCR. Previously `original.stat()` failed before OCR began.
The earlier standalone OCR test missed this downloader boundary.

Also check `VACANCY_OCR_ENABLED=true` in `.env`, then **restart the collector**.
In the diagnosed local installation OCR was disabled: 22 image records had status
`disabled`, including 18 with no caption. Such records have no OCR evidence and are
excluded from vacancy listings when classified `not_vacancy`. Enabling OCR alone
does not reprocess old message IDs; ordinary polling intentionally skips them.

To read exactly one configured, enabled public-channel post again:

```powershell
python collector.py --reprocess-message jobs_in_dubai MESSAGE_ID --debug-pipeline
```

Replace `MESSAGE_ID` with the positive number at the end of the post URL. This
updates the existing row (or inserts it if absent), preserves saved links, and does
not advance the source cursor. Failed retries preserve an existing successful OCR
result. It respects FloodWait and does not join channels or send messages.

`--debug-pipeline` is temporary for that process only. It logs source ID/message ID,
text/photo presence, download and temporary-file-existence flags, OCR called flag,
character count, a masked preview of at most 120 characters, detection result,
parsed-field presence, DB save/duplicate outcome and eligibility for the bot list.
It never logs local paths, full text, contacts, credentials or exception contents.
The preview keeps only a small vocabulary of job/technology words; unknown words
(including personal names), numbers and contacts are masked. Run without the flag
to disable these diagnostic events. Telegram/library DEBUG logging stays disabled.
Reasons distinguish `ocr_disabled`, `engine_unavailable`, `download_failed`,
`downloaded_file_missing`, `preprocess_failed`, `ocr_failed`, `ocr_empty`,
`already_collected`, `not_detected` and `duplicate`. Bot-side user filters may still
hide an otherwise eligible record; clear filters when checking a repaired post.

EasyOCR's EN/RU models are `ocr_models/craft_mlt_25k.pth` and
`ocr_models/cyrillic_g2.pth`. Use `--prepare-ocr` if they are missing. No Tesseract
executable or separate OCR command-line binary is required; CPU PyTorch and other
native dependencies are supplied by the Python wheels in the installation steps
above. This diagnosis verified the real engine/model loading on Windows and the
entire cached Telegram photo -> download -> OCR -> detection/parser -> SQLite ->
bot card path without network. OCR extracted the fixture's role, location and salary;
contact recognition is not guaranteed and missing contacts are never invented.

`python collector.py --reprocess 100` retries up to 100 stored pending/failed texts
without Telegram login. After enabling OCR, `python collector.py --retry-ocr 50`
re-reads up to 50 previously failed/disabled/empty image posts and updates their rows;
it needs the user session. Both limits are capped at 500. Missing/deleted posts are
left intact. Check failures before retrying; no aggressive automatic retry loop is used.

With `VACANCY_KEEP_MEDIA=true`, retained originals expire after
`VACANCY_MEDIA_RETENTION_DAYS` (default 7, range 1–30); cleanup runs during collection.
Only collector-generated retained filenames are eligible. Keep false for minimal
storage. Stopping the process pauses cleanup; temporary OCR files are cleaned during
normal success/failure and graceful cancellation. After a forced OS termination,
inspect orphan `collector_media/ocr_*` folders locally before removing them.

Set `ADMIN_TELEGRAM_ID` to the owner's Telegram user ID to show the private collector
statistics button and allow `/collector_stats`. Other users receive no stats.
Counts include all collected posts, collected in 24h, detected/probable vacancies,
duplicates and current OCR outcomes; resolved OCR failures stop counting as failures.
Duplicate posts retain all source rows and links. Source filters include forwarded
copies; saved records remain accessible when collection from a source is disabled.

A local Telegram assistant for early testers. It helps users organize CVs, compare
vacancies and track applications. **v0.4 makes no OpenAI calls and uses no paid
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

Use a **private chat** with the bot. The sections are Profile, CVs, Analyse
vacancy, Vacancies, Applications, Dashboard and Help. `/start` or `/menu` opens the menu;
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

### Create CV / Создать CV

The local CV Builder saves each answer in an owner-scoped SQLite draft. Fill personal
details, summary, multiple experience/education entries, skills, languages,
certifications, links and an optional photo. Skip clears a field; Keep retains it.
Dates accept YYYY or YYYY-MM, with an explicit Present option for current employment.
Save draft and exit, then Continue draft resumes the pending step after a restart.
Edit opens individual sections; experience/education support adding, editing and
confirmed removal. Draft lists are paginated. Duplicate creates an independent copy.

PDF and editable DOCX exports require a name and target role; absent fields and empty
sections are omitted. Nothing is invented. Filenames use `FirstName_LastName_CV`.
The local PDF renderer embeds Arial on Windows (DejaVu Sans on Linux, Arial on macOS)
for Cyrillic support; no Office/LibreOffice installation is needed for generation.
Install updated requirements, including ReportLab. Photos are optional, limited to
5 MB / 20 megapixels, resized locally and stored without EXIF metadata inside the
draft. No raw CV or photo content is logged.

Set as active CV creates/refreshes a generated DOCX in the existing CV system and
passes factual text to the heuristic vacancy matcher. Active CV is a snapshot:
repeat this action after editing. Existing uploaded CVs stay available. Deleting a
draft also removes its linked generated CV; `/delete_my_data` removes drafts/photos
and generated local files, while previously downloaded Telegram copies remain.

Architecture: `services/cv_builder.py` owns drafts and provides `CVAttachment`
(filename, MIME type, bytes) for a future explicit Apply/attach flow. No email is
sent. `services/cv_export.py` maps factual data into document blocks and renders them;
`templates/` holds template settings separately from Telegram/business logic.

Choose **Modern**, **Professional** or **Classic ATS** from the CV preview.
Modern uses a shaded sidebar and two columns; Professional uses a narrow text
sidebar and a wider experience column. Both support an optional square-cropped
photo without stretching. Classic ATS uses one text column, no photos, no layout
tables, no skill bars or decorative graphics. It is the default for new drafts.
PDF exports retain selectable Unicode text and use automatic A4 pagination.
DOCX exports retain editable styled text; the two-column versions use a fixed-width
borderless layout table, while Classic ATS uses ordinary paragraphs only.
PDF and Word page breaks may differ; templates are not guarantees of ATS acceptance.

**Create RU version / Create EN version** makes an independent copy of the current
draft with its own document language, template, edits and active-CV link. Headings
use that document language even if the bot menu language changes. User-entered text
is copied unchanged: translate your own content by editing sections. No translation
service or invented content is involved. Optional additional information and
references are also editable and omitted when empty.

### Uploaded CVs

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

### Apply preparation (no email sending)

Open **Apply — prepare / Откликнуться — подготовить** on a vacancy card or an
application's detail screen. The review shows role, company, extracted recipient
email (or an explicit missing-email message), selected CV, suggested subject and
editable message. Change the CV without changing your global active-CV selection.
Only literal email evidence from the vacancy is used; no employer address is guessed.

**Confirm — save only** stores an owner-scoped preparation in SQLite. It sends no
email and does not change the tracker status or applied date. Repeat confirmations
update the same preparation. Reopen Apply to review saved text. Cancel, /cancel or
leaving this screen discards unconfirmed edits; previously confirmed preparation
remains. Confirmation rechecks CV ownership/availability and changed vacancy details.
Missing email is allowed in a saved preparation, but remains explicitly flagged.
`/delete_my_data` also removes these records. No SMTP credentials, server setup or
email provider is needed. The module is a foundation for future explicit sending.

### Application tracking

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

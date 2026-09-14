# Dubai Job Assistant — v0.4

## Admin user directory

Settings → Admin stats → **👥 Users / Пользователи** is restricted to
ADMIN_TELEGRAM_ID and paginates five users at a time, latest activity first.
It shows Telegram identity, language, first recorded /start, registration and last
activity, event/search counts, CV count, application count and Job Alerts state.
CVs include drafts, counting a draft and its exported resume only once.
Only metadata/counts are selected: no CV text, messages, contacts or profile fields.
Dates and today boundaries use UTC; seven-day counters use a rolling window.
New users means registration; legacy first /start and activity can be unknown.
Telegram last names are captured on subsequent interactions, never guessed.

Trusted local operators can run `.venv\Scripts\python.exe bot.py --list-users`
without connecting to Telegram or acquiring the running bot's instance lock.
The console displays all users in a compact table, not the bot log. Keep this
identity-bearing output private. This reuses users/product_events/product_activity;
the only schema addition is nullable users.last_name. Restart the bot to load the UI.

## Share Vacancy / Поделиться вакансией

Use **📤 Share / Поделиться** on a vacancy card (including Matching jobs now).
The bot prepares a compact, forwardable RU/EN message using only public role,
company, location, salary and source metadata. Missing values are omitted. A bot
deep link is included when its initialized username is available; no CV, profile
or application message is included.

**📤 Send to friend / Переслать другу** opens the standard
[Telegram sharing window](https://core.telegram.org/widgets/share): the user chooses
the recipient and sends it manually. Without a source or bot link, the prepared
message can still be forwarded manually. Sharing preserves search pagination.

## Personal Job Alerts / Персональные уведомления

**🎯 Matching jobs now / Подходящие вакансии сейчас** shows a retrospective
selection using saved alert professions/keywords, location, UAE only and salary.
It works even with notifications OFF and does not change the subscription cutoff,
queue, quotas or delivery history. Set a profession or keyword first.

This manual selection keeps unknown salaries (including amounts whose currency
cannot be compared with AED); stated AED ranges are excluded only when the upper
end is below the requested minimum. Automatic alerts retain their stricter salary
rule. The selection uses the same synonym/relevance scorer and location rules.
Matching title evidence ranks above skills and body-only evidence; known sufficient
salary breaks equal profession-evidence ties. Optional active-CV score is secondary
to alert relevance, followed by publication date, never vacancy ID.

The query uses the publication index and a 14-day date bound, applies visibility,
location and salary filters in SQL, and evaluates at most 300 recent candidates.
Normally results cover seven days; if fewer than five relevant candidates exist,
the window expands to 14 days. Up to 50 best results are displayed one at a time,
with Next/Previous, source, compare, save and application actions. Counts refer to
this bounded selection, not the whole database. Page navigation reuses the snapshot
without repeating CV analysis; click Matching jobs now again to refresh. Editing
matching settings invalidates old page buttons. Undated, duplicate, rejected and
future posts are excluded. A missing salary is shown explicitly.

Open **🔔 Job Alerts / Уведомления** in the main menu. Set professions and optional
extra keywords/aliases (up to 12 comma/newline-separated phrases in each list),
workplace location, UAE only and optional minimum salary in AED, then enable.
Example: `Повар`, `Dubai`, `5000`, UAE only, ON. RU/EN profession synonyms work
automatically. Roles and extra aliases are alternatives; location and salary are
mandatory additional filters. Missing/non-AED salary is excluded when a minimum
is set. OCR/body-only matches must be stronger than incidental unrelated-role or
company-only mentions. Settings and notification cards follow the user's language.

Notifications are **OFF by default**. Enabling or changing settings records both a
publication-time cutoff and the current last vacancy ID. Only newly inserted posts
published after that cutoff and no more than 24 hours old qualify. No existing
database rows are replayed. Historical backfill, missing/future dates, duplicates,
disabled sources and reprocessing updates do not generate old-vacancy alerts.
Queued alerts are rechecked against current settings/source/detection before send;
they expire after 24 hours. Switching OFF cancels pending messages. One request
already in flight may still arrive after switching off or deleting data.

Run the usual two local processes, `python bot.py` and `python collector.py`.
Restart both after updating the code. No new service, API key or dependency is
needed. Collector's vacancy insertion transaction uses the existing search and
location rules to write matching `(user, vacancy)` deliveries to a SQLite outbox;
the bot runs its delivery worker automatically while polling. Nothing is sent from
the user Telegram account. If the bot is temporarily offline, fresh pending alerts
survive a restart. No email or automatic job application is sent.

Limits persist across restarts and concurrent workers: one send attempt every two
seconds globally, at least 60 seconds between attempts for each user and at most
10 attempts per user in a rolling 24 hours. A Telegram `RetryAfter` pauses all
alerts for the requested interval; only this explicit rejection is retried. Blocked
users are disabled automatically. States `pending`, `sending`, `sent`, `failed`,
`unknown`, `cancelled`, `expired` record delivery history; confirmed messages store
Telegram message IDs. Unique user/vacancy and user/content-hash keys prevent repeat
alerts for the same vacancy and cross-channel duplicates.

Telegram sendMessage has no idempotency key: a timeout/crash can leave it unclear
whether Telegram accepted a message. To avoid duplicate notifications, ambiguous
attempts and interrupted `sending` rows are **never automatically resent** (stale
`sending` becomes `unknown` after five minutes). This may lose an occasional alert;
the vacancy remains available in the normal Vacancies section. No delivery exception,
recipient ID, contact details or message contents are logged by this worker.

Each card explains the match and offers Open vacancy, Compare with CV, Save,
Add to applications and Disable notifications. These reuse the existing flows.
`/delete_my_data`, after confirmation, deletes alert preferences and delivery history
alongside the existing private data; public vacancies remain.

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
python collector.py --source-quality
python collector.py --disable-source CHANNEL
python collector.py --enable-source CHANNEL
python collector.py --remove-source CHANNEL
```

Usernames, @usernames and public t.me channel links are accepted; enable/disable/remove
also accept the numeric ID from the list. Names may be Russian or English. Repeated
adds keep the same source ID; supplying a title updates its display name. Invite links
and individual post links are rejected. Normal collection picks up all enabled sources
on its next poll, without code changes or a restart.

`--source-quality` is an offline local summary; it does not authorize Telegram or
load OCR. Each source (including disabled/removed and empty sources) reports unique
stored messages, vacancy/probably/not-vacancy/pending counts, image/OCR candidates,
OCR failures, unavailable engines, duplicates and average detection score.
`useful_rate` = (vacancy + probably_vacancy) / stored messages. Duplicates count in
their original source's totals because this measures incoming source quality.
OCR messages include images whose OCR was disabled, unavailable, oversized or
failed, as well as successfully read/empty images. Failures count `failed` only;
engine unavailability is separate. These are latest stored outcomes, not historical
attempt counts: reprocessing can change quality without inflating message totals.
Posts never downloaded/stored are absent; successful OCR retained after a failed
retry remains a success here. No message text or contact details are printed.

Removal is soft: it stops collection and marks the source as removed in the admin
list, retaining posts, saved links and its cursor. Startup seeding never restores it
or overrides user titles/enabled preferences. Explicit `--add-source` restores a
removed source with its original ID and cursor. Use disable/enable for a temporary
pause. Old databases gain an additive column; existing user records are preserved.
Only explicitly configured public broadcast channels and accessible public
megagroups/supergroups are read; private chats/groups are refused. It never joins channels, sends messages or applies for jobs.
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
queries retain partial company/location search; this is not automatic translation
or fuzzy OCR correction. The vocabulary lives in `services/vacancy_search.py`;
filters combine normalized location, minimum stated salary in AED, source and last
1–365 days. Unknown currencies/salaries do not pass salary filters. Saved listings
are personal; `/delete_my_data` removes those links but retains public source posts.

Location filters recognize RU/EN names of all seven UAE emirates/cities and
UAE/United Arab Emirates/ОАЭ. Dubai matches Дубай; UAE includes every recognized
emirate. Other location queries retain substring matching on the parsed location.
**Filters → UAE only / Только ОАЭ** toggles a strict filter that excludes unknown
and mixed-country workplaces as well as recognized foreign locations. It combines
with role search, salary, source and publication filters; Clear filters removes it.
Explicit workplace lines override incidental recruiter/contact/experience mentions.
Filtering also checks old raw/OCR text, without changing records or requiring a
backfill. Normalization is a small rule-based dictionary, not a worldwide geocoder;
ambiguous posts may require reading the original source.

With a UAE location in the user profile and no explicit location filter, latest
results put known UAE workplaces first. Search adds a small 10-point geographic
bonus to already eligible matches; role evidence still outranks body-only evidence.
The bonus does not rescue rejected noise or hide foreign vacancies. Use UAE only
to exclude them. An explicit location filter takes precedence over the profile;
saved vacancies remain available regardless of current search filters.

Search Quality V2 ranks all eligible matches before pagination: exact normalized
role (100), CDP alias (98), role phrase (94), synonym role (86/78), parsed skills
(65), text/OCR (55/45), company/location (35). Generic cook searches lower senior
head/executive/sous-chef matches slightly. An isolated body mention in a long post
with an unrelated parsed role scores 20 and falls below the minimum threshold 30.
Repeated text does not accumulate extra score. Ties use publication time, then
role/company/source URL; search does not sort by vacancy ID. This is search
relevance, separate from CV match and detection confidence.
Cards show high/medium relevance, Next, Previous and Back to search. There is no
five-result or thirty-result total cap: all qualifying records remain pageable,
one card at a time (storage requests are bounded to 100 per page). Search cannot
guarantee 30 matches when fewer eligible vacancies exist. Existing filters still
combine with the query; clear them if expected posts are hidden.

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

N must be 1–500. Only an enabled, configured public channel or megagroup is accepted.
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
other fields can be skipped. The profile overview shows data with only Edit,
Language and Main menu actions. Edit opens a compact two-column field menu.
After a valid change, a confirmation appears and the field menu reopens. Done
and Back return to the profile overview; /cancel during a field edit discards
the change and returns to the field menu. Existing validation and optional-field
clearing remain unchanged. Nothing is saved until the form completes.
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
Modern Blue uses a saturated blue identity header with an optional overlapping
portrait in PDF, a narrow summary/skills sidebar and the main experience column.
Professional uses a large portrait above the name in a narrow sidebar and restrained
grey-blue section headings in the wider experience column. Classic ATS uses a
single text column, a compact identity header, optional portrait and thin section
rules, without layout tables or skill bars. It is the default for new drafts.
Photos are cropped without stretching, slightly above center, with EXIF orientation
applied. Missing photos do not reserve a placeholder. Unknown/empty sections vanish.
PDF exports retain selectable Unicode text and use automatic A4 pagination.
DOCX exports retain editable styled text; the two-column versions use a fixed-width
borderless first-page layout table, while Classic ATS uses ordinary paragraphs
and an optional anchored photo only. Longer experience continues at full width after
the sidebar. PDF and Word use their own layout metrics: the DOCX transition is
estimated conservatively by complete items and remains editable. A very long single
item or sidebar can still span pages. No content is truncated to fit a page.
PDF and Word page breaks may differ; templates are not guarantees of ATS acceptance.

See [the visual comparison samples](examples/cv_redesign/README.md): each template
has an English PDF/DOCX with an illustrated placeholder portrait, a Russian version
without a photo and a three-page Russian stress sample. All example facts are
synthetic and are never inserted into a user's CV. Regenerate with
`.\.venv\Scripts\python.exe examples/cv_redesign/generate.py`.
PDF pages and Word-rendered DOCX pages were visually inspected; their text is
extractable and stays within A4 bounds. Word was used only for local QA because
LibreOffice is unavailable here; neither is required by the bot's export engine.

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

### Apply Pack / Пакет для отклика (manual sending only)

Open **📨 Apply / Откликнуться** on a vacancy card (including matching jobs and
notifications) or an
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

The pack includes location, known salary, source URL, selected CV, subject and
editable message. Active CV is the initial default; a saved preparation retains
its prior selection if it still exists. Choose another saved CV, download the
selected original file, or open Create CV when none is available. File formats
and filenames remain those of the saved CV; no forced PDF conversion is applied.
Rule-based RU/EN defaults mention only the actual role/company and a known name
from the selected built CV or profile. Phone/email are included only when supplied
in the selected structured CV. Unknown identity uses a neutral greeting/sign-off;
unknown company/email are never invented. Custom text is preserved on CV changes.
Copy buttons send the value in an escaped, copyable code block in the private chat;
they do not claim to write to the device clipboard.

**✅ Mark as sent / Отметить как отправленный** is the user's confirmation of a
manual application, NOT an email send. It creates or reuses their application by
vacancy ID (canonical vacancy) or an existing original source URL, recording
`selected_cv_id`, `recipient_email`, `email_subject`, `email_body`, `source_url`,
status `applied`, date and UTC `applied_at`/`last_contact_at`. `follow_up_at` starts
NULL until the user schedules a reminder from application details. A unique owner/vacancy index and atomic update prevent duplicate
records; repeated marking preserves the first applied timestamp and later tracker
statuses. Unavailable fields remain empty/NULL. Confirm/save-only remains available
and still makes no tracker status change. All records are removed by the existing
confirmed `/delete_my_data` flow. No SMTP or email API is used. Follow-up reminders notify only the user.
`/delete_my_data` also removes these records. No SMTP credentials, server setup or
email provider is needed. The module is a foundation for future explicit sending.

### Application tracking

Each application now opens a detailed card with status, original post URL, salary,
applied date/time, selected CV, literal employer email, notes, interview and follow-up.
Buttons support status changes, Timeline, interviews, follow-ups, notes, owner-only
CV download, Apply Pack, source and confirmed deletion. All screens are RU/EN.
Deleting an application deletes its events/reminders, but keeps CV files. Personal
data deletion also removes events/reminders through application cleanup triggers.

Status transitions from any existing path (menu, `/status`, Apply Pack) create
transactional `application_events`. Repeating the same status or identical schedule
does not create another event. Timeline is chronological and paginated, ten events
per page. Existing applications get one **current-status snapshot at migration**;
earlier transitions/times are not invented. The migration is additive/idempotent.

Follow-up presets are 2, 3, 5 or 7 days **from selection**, or enter a future
`YYYY-MM-DD HH:MM`. No reminder clears pending follow-ups; Reply received updates
last contact and clears the follow-up. Snooze opens the same schedule menu. Notes
replace the application's note field and are recorded in history.

Interview setup asks for date/time, format (onsite, phone, video), optional
location/link and notes. All user-entered/displayed times use **Dubai UTC+04:00**;
the database stores UTC. This also works on Windows without an external timezone
database. Scheduling an interview does not silently change the application's status.
Only future 24-hour/2-hour deadlines are queued; missed deadlines at creation are
not sent retroactively. Rescheduling cancels pending old reminders. If the bot was
offline past both deadlines, only the 2-hour reminder is delivered, while the
interview is still upcoming. No interview reminder is sent after the interview.

The existing bot worker sends reminders only to the application's owner. SQLite
unique schedule keys and atomic claims prevent duplicates, including across
restarts. Reminders share the global two-second send gate with Job Alerts; each
user's application reminders are at least 60 seconds apart. Telegram RetryAfter
pauses the shared gate; uncertain network/crash outcomes are not resent (possible
missed reminder instead of duplicate). Blocked recipients' queued application
reminders are cancelled. Offer/Rejected/Withdrawn cancel pending reminders. One
already-dispatched request can arrive after a cancellation/deletion. Nothing is
sent to employers; no SMTP or employer messaging API is implemented.

**📅 Today / Сегодня** lists today's Dubai-time interviews and follow-ups, overdue
follow-ups and active applications with no recorded status change for 14 days.
There are at most ten summaries per page, each opening its application; more are
paginated. Saved applications appear only if an interview/follow-up is scheduled.
Closed applications are excluded. Imported records start their known status age
from the migration snapshot, not an invented historical transition.

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
- Response rate: current HR screening, Interview, Test task, Final interview, Offer
  or Rejected divided by submitted records. Saved, Withdrawn and unknown statuses
  are excluded from the denominator; empty denominators produce 0%.
- Interview-stage and offer rates: respective current counts divided by records
  with listed submitted statuses (excludes Saved, Withdrawn and unknown legacy
  statuses). Empty denominators produce 0%.

These are **current-state ratios**, not lifetime funnel conversion rates; there is
no transition-history inference in these formulas. Offers are excluded from active applications.

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


## First users, onboarding and local product analytics

New users see a short RU/EN welcome on their first `/start`. Optional setup covers
language, roles, location, UAE-only, minimum AED salary, upload/create/skip CV and
Job Alerts. Each step supports skipping/back; Continue later persists the step in
SQLite. Resume from Settings. Existing accounts are not enrolled retroactively.
CV creation/upload uses the existing flow; return through Resume setup to finish
the alert choice. Alerts stay off until explicitly enabled and retain the existing
new-vacancies-only cutoff, deduplication and rate limiting.

The home screen has seven sections: Vacancies, Job Alerts, My CV, Applications,
Profile, Statistics and Settings. CV creation/analysis lives under My CV; Today
under Applications; Help, Language, Feedback and data controls under Settings.
Existing commands and saved user data remain supported.

### Metrics and definitions

`Settings -> Admin stats` or `/admin_stats` requires `ADMIN_TELEGRAM_ID` from `.env`.
Local SQLite analytics records successful actions using technical user/entity IDs;
metadata allows only an export/upload format enum. Search text, CV content,
contacts, filenames, email bodies and authentication data are not event metadata.
Activity is recorded from private user messages/button presses, never from a
background notification. No external analytics dependency is used.

Available statistics:
- Total/new users (24h/7d), active users (24h/7d), active today in UTC, and users
  active on two or more distinct UTC days. A return event is recorded once per
  subsequent active day, not for every click or notification.
- Searches, vacancy views, CV drafts created, exports delivered, applications
  created, alerts enabled/delivered and share texts prepared. A share count cannot
  prove that the user actually forwarded the message to a friend.
- Funnel coverage: started -> onboarding completed -> CV available -> vacancy
  viewed -> saved or matched -> application created. Each stage intersects the
  preceding user cohort; percentages use started users as denominator. This is
  cohort coverage, not a strict timestamp-ordered attribution funnel. Skipping
  setup completely counts as completing its optional flow. Profile completion
  means full name and desired role are present.
- Per-source processed posts, detected/probable vacancies, detection rate,
  duplicates, successful/failed OCR, views, saves and applications. Application
  attribution uses the linked vacancy/source URL while that application exists.

Product usage starts at this migration; historical events are not invented or
backfilled. Current user totals and collector source counts use existing records.
The additive migration preserves profiles, CVs, applications and collector data.

### Feedback and vacancy review

Settings -> Feedback accepts a category and a private message (1–2000 characters,
maximum five submissions per user per 24h). Feedback text is visible only to the
configured admin. The bot worker sends that admin a technical notice containing
feedback ID/category, without forwarding private feedback text. Notices are
limited to one per minute and share the existing Telegram rate-limit gate. A
Telegram FloodWait is respected; uncertain delivery outcomes are not retried
blindly. The admin must have opened the bot for Telegram to permit these notices;
feedback remains available in Admin stats even if notification delivery fails or
no admin is configured. Admin can mark feedback resolved.

Vacancy cards have Report with six reasons. A user has one report per canonical
vacancy; three independent reports flag review, without automatic deletion.
Admin can inspect reports/content, hide or restore a vacancy, inspect source
quality and disable a source. Hidden vacancies and equivalent reposts are
excluded from browsing, saved lists, fresh matching, Apply Pack creation and new
alert delivery. Already sent Telegram messages cannot be retracted by hiding.
Restart both local processes after updating so they load the moderation checks.

`/my_data` explains stored data categories. Confirmed `/delete_my_data` also
removes this user's product events, activity days, onboarding, feedback and reports
in addition to existing private records/files. It does not delete public source
posts or copies already delivered in Telegram.

Recommended acceptance check: invite 3–5 consenting testers, exercise optional
RU/EN setup -> CV -> matching -> Apply Pack, then review feedback/source quality
and seven-day activity. No payments or automatic employer messages are included.

## Collector reliability and local always-on operation

Use a dedicated PowerShell window for each local process:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_collector.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
```

Each script resolves the project directory and uses `.venv\Scripts\python.exe`.
The collector stays in the foreground until Ctrl+C; keep its window and the laptop
awake. Sleep, shutdown, Windows termination or closing the host terminal prevents
local monitoring. These scripts do not change power settings, install a server or
register an autostart task. The process-local execution-policy option does not
change the machine's policy.

Optional bounded crash recovery, **instead of** start_collector.ps1:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_collector_forever.ps1
```

This wrapper waits ten seconds after unexpected exits and stops after five failures
within ten minutes. Normal shutdown (0), another instance (3) and Ctrl+C (130 or
Windows control-C exit status) do not restart. Inspect the error and logs before
restarting a crash loop. It is not an operating-system service and cannot run while
the machine is off/asleep. The bot now has its own independent instance lock; see the Windows bot
reliability section below.

### Lifecycle and recovery

- Existing `.env` and Telethon user session are reused. Configuration, SQLite,
  authorization, OCR and enabled sources are checked; no credentials are printed.
- Telethon NewMessage events wake a **serialized history catch-up**, rather than
  doing concurrent OCR in handlers. The usual five-minute poll remains as fallback
  for missed events and public sources for which the account receives no updates.
  No automatic joining, messaging, replies or application sending is performed.
  The default loop is indefinite; explicit maintenance flags such as `--once`,
  `--backfill` and `--reprocess-backfill` intentionally finish.
- A never-checked source establishes its latest-message baseline without importing
  its old history. Use an explicit bounded backfill to import earlier posts.
- Public broadcast channels **and public megagroups/supergroups** are accepted
  only when the configured active username matches the accessible entity. Private,
  renamed, unavailable and wrong-type entities are skipped independently.
- Disconnect checks during idle periods occur at least every 15 seconds. Transient
  connection/timeout/DNS/socket failures use reconnect backoff of 2, 5, 10, 30 and
  at most 60 seconds; a successful collection cycle resets the delay. Telegram's
  own finite reconnect attempts run underneath this supervisor. FloodWait always
  waits at least the requested time (unless explicitly stopping the service).
- Processing is isolated per message. A failure cannot prevent later messages in
  the current bounded batch from being attempted. The source checkpoint never
  advances past an uncommitted message, so it is retried on the next poll; already
  stored later messages remain deduplicated. Persistent storage failures require
  operator investigation, and are not silently skipped. SQLite lock/busy failures
  receive at most three attempts with 1s/2s pauses, in addition to SQLite's 5s busy
  timeout; connections rollback and close through the existing DB context manager.
- OCR runs in a bounded local worker process using the existing EasyOCR/Pillow
  engine and Unicode-path fix. Initialization/recognition is limited to 120 seconds;
  a hung worker is terminated. Captions still go through detection when OCR fails.
  If models/native dependencies are unavailable, text-only collection continues.
  An unavailable/terminated OCR worker requires a collector restart after repair;
  use the existing `--retry-ocr` maintenance command for affected image posts.
  Failed parser processing retains available raw text and detector output.

The event-loop approach follows Telethon's supported async lifecycle; periodic
history catch-up remains authoritative for checkpoints. See the
[official Telethon lifecycle reference](https://docs.telethon.dev/en/stable/modules/client.html).

### Lock, health, logs and stopping

`runtime/collector.lock` uses a Windows kernel file lock (flock on POSIX). Another
session-consuming collector launch exits with **Collector is already running.**
without changing the first process or opening its Telethon session. Kernel locks
are released on normal exit and crashes. The small lock file is intentionally kept
in place: stale file contents do not block startup, and avoiding unlink prevents
races between concurrent launches. Read-only/source-management commands may still
run while collection is active because they do not open that session.

`runtime/collector_health.json` is replaced atomically and contains only status,
UTC startup/heartbeat/last-processed-message times, enabled/active source counts,
successful reconnect count, processed/error counts and the last exception class.
Counts are for the current process; an initial connection is not a reconnect.
`active_sources` means sources successfully validated during polling, not group
membership. A quiet channel with no new posts is healthy. Heartbeat appears on
startup and every five minutes, including during idle waits/reconnect backoff.
When inspecting a health file, verify its heartbeat freshness and process existence;
an abrupt kill cannot write a final stopped state.

Safe collector logs go both to the console and UTF-8 `logs/collector.log`, limited
to five files of 2 MiB each. No exception bodies, credentials or raw post/CV content
are written by the lifecycle logger. Existing optional pipeline diagnostics remain
explicitly opt-in and use masked short previews. Runtime, logs and session files
remain ignored by Git.

To stop gracefully from another terminal:

```powershell
.\.venv\Scripts\python.exe collector.py --stop
```

Ctrl+C uses the same drain: stop accepting new work, allow up to 30 seconds for
current processing, terminate stuck OCR if needed, disconnect (10-second cleanup
limit), write stopped state and release the kernel lock. A pending stop request
is cleared on the next fresh launch. Fatal missing configuration/session/permission
problems can still prevent startup; consult health/error types rather than running
an unbounded crash loop.

Unit tests use fake Telegram clients and no real login. Enable local OCR model
smoke tests with `RUN_OCR_INTEGRATION=1`. For a live acceptance check, leave one
collector running for at least six minutes, confirm a later heartbeat and source
checkpoints, try a second launch (exit 3), then test `--stop` and restart. Do not
turn off the entire machine's network just to test one collector when other work
or remote access depends on it.

## Run 24/7 on Windows: bot reliability

Start both processes in separate foreground watchdog windows with one command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_all.ps1
```

The launcher checks each kernel lock first; a racing second launch is also refused
by the process itself. It does not stop existing processes or change Windows power
settings. The scripts resolve their own project directory and use `.venv` Python;
no manual `cd`, credentials in scripts, server or paid service is needed. Keep the
laptop awake and the consoles open. No local process runs while Windows is asleep
or the computer is off. ExecutionPolicy applies only to the launched process.

For the bot alone choose **one** of:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_bot_forever.ps1
```

The watchdog is secondary protection: unexpected exit -> 10-second delay; five
failures within ten minutes -> `Bot repeatedly crashed. Check logs/bot.log`.
Normal exit, Ctrl+C, local duplicate-instance and Telegram conflict exits do not
restart. A stop requested during the watchdog's delay is honored on its next loop.

### What the lifecycle audit established

The installed framework at validation is python-telegram-bot 22.8. The previous
`run_polling()` call used its default `bootstrap_retries=0`, and printed "running"
**before** initialization. A timeout during bootstrap therefore propagated out of
main and returned to PowerShell. This path is reproduced by an automated test.
Steady-state PTB polling already retries Telegram/network errors indefinitely;
`Request failed` from an update error handler does not itself stop polling.
The old error handler could also fail while accessing localization/DB or sending
its error notice; PTB catches those failures, so they do not by themselves prove
why a historic process exited. Old logs omit exception types and exit markers,
and do not establish the exact historical exit cause. Two legacy bot launches
were found during replacement, making polling conflicts an additional risk.

The new implementation uses PTB's supported async initialize/start-polling/start/
stop/shutdown APIs. PTB retains its own steady-state polling retry and RetryAfter
behavior; there is no second polling retry loop layered around it. Only bootstrap
operations retry transient failures with 2/5/10/30/60-second backoff and respect
RetryAfter. Conflict/invalid token terminate rather than retrying forever. Low-level
connection/timeout/OSError failures are normalized to NetworkError for PTB. Startup
"running" is now logged after actual initialization and polling start. Pending
Telegram updates are no longer deliberately dropped at startup.

A global handler isolates user-action exceptions, uses localized generic RU/EN
messages, survives failed localization/DB/error notices and observes RetryAfter
before attempting a notice. It never replays a failed user action or retries an
ambiguous outgoing send automatically. Bot-specific SQLite connections have short
transactions, rollback/close and bounded retries of busy statements/commit (four
attempts, 1s SQLite busy timeout each, plus 0.1/0.3/0.6s delays). A BUSY_SNAPSHOT
is rolled back rather than blindly retrying the statement. Core DB schema and the
collector's connections are unchanged.

Job Alerts/follow-ups/interview reminders/feedback currently use the existing
explicit async sender, not a separately configured JobQueue. Each job has its own
exception boundary; a failed job does not prevent the others from attempting work
through their existing shared rate gate. Existing outbox/idempotency logic remains
unchanged. A configured PTB JobQueue is also stopped by Application.stop.

### Bot health, logging and stop

`runtime/bot_health.json` records starting/running/degraded/reconnecting/stopped/error,
UTC startup/heartbeat/last-update times, dispatched update count, error count,
network error count and last exception class. Dispatched updates include attempts
that later fail; this is an operational counter, not a success/conversion metric.
The same exception observed at request and polling layers is counted once.
No user IDs/content/contacts are stored in this file. A successful Telegram request
clears transient degraded state. Heartbeat is logged every five minutes, including
idle periods; validate both heartbeat freshness and process existence after crashes.

Console and UTF-8 `logs/bot.log` receive only safe runtime messages and exception
class/context, without exception bodies or unsanitized tracebacks. Rotation is
2 MiB per file with four backups. Untrusted third-party HTTP/Telegram logs are not
copied into this log. Runtime/log/session/private files remain Git-ignored.

`runtime/bot.lock` is an OS-owned lock, independent of the collector lock. A second
bot prints `Dubai Job Assistant is already running.` and exits 3. Crash/stale-file
recovery is automatic because the kernel releases ownership; the small file can
remain. A Telegram conflict from an instance on another machine writes error state,
logs the conflict separately, stops polling and exits 4 without endless retry.

Ctrl+C, SIGTERM or the following command request a graceful stop:

```powershell
.\.venv\Scripts\python.exe bot.py --stop
```

Shutdown stops polling, the explicit sender and Application/JobQueue, drains async
work with a 30-second limit per cleanup phase, closes resources and releases the
lock. OS-level forced termination or blocking native code cannot guarantee final
health writes; the watchdog does not replace normal lifecycle cleanup.

Tests use fake HTTP transports with the real PTB polling machinery, plus local
SQLite and PowerShell policy tests, without live Telegram credentials. Live
acceptance also needs user-side `/start`, search, Profile, My CV and Job Alerts
checks in Telegram; server startup and synthetic-update tests alone do not prove
that every button rendered correctly in the user's Telegram client.

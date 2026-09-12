# Dubai Job Assistant — TODO

## Application follow-up and interview tracker — 2026-09-13
- Added owner-scoped details, selected CV download, status picker, timeline, note,
  interview wizard, follow-up presets/custom date/cancel/reply, confirmed delete.
- Additive interview fields + application_events/application_reminders. DB triggers
  capture real status transitions across all existing paths and clean up private
  history/reminders on deletion. Existing rows get one honest current-status snapshot.
- Dubai UTC+04 UI dates stored in UTC. Follow-up and 24h/2h interview reminders
  are owner-only, durable and uniquely scheduled; reschedules cancel pending old
  jobs. Existing worker/global throttle reused. RetryAfter honored; ambiguous sends
  never automatically replayed. Terminal statuses stop pending reminders.
- Today paginated (10) with interviews/follow-ups/overdue/14-day unchanged statuses.
  Dashboard adds current-status response rate with only valid submitted statuses.
- RU/EN strings in catalogs; no AI, paid services or employer messages.
- Tests cover migration/history/idempotence, due times/snooze, interview deadlines,
  retries/uncertain sends, privacy/owner checks, Today, rates, RU/EN and Apply Pack.
- Verification: baseline 217 tests; final full suite 230 tests passed including OCR
  and Apply Pack. compileall, dependency and diff checks passed. No outstanding
  implementation for this milestone; employer messaging remains out of scope.

## Apply Pack — 2026-09-12
- Extended existing Apply preparation without adding email transport: vacancy data,
  explicit missing email, active/saved CV selection and download, Create CV link,
  safe RU/EN subject/message defaults using only supplied identity/contact facts.
- Copy email/subject/body uses escaped private-chat code blocks. Custom edits survive
  CV changes. Added Apply to notification and matching-job cards as well.
- Explicit Mark as sent creates/reuses the user's tracker record, canonical vacancy
  link, selected CV ID, recipient/subject/body/source URL, applied status/date and
  UTC applied_at/last_contact_at. follow_up_at nullable; no reminders or email sending.
- Additive application columns and unique owner/vacancy index. Repeated marking
  does not create duplicates or reset later tracker progress. Legacy Confirm remains
  save-only; old preparations and application flows preserved. Privacy reuses deletion.
- Tests cover identity/unknown fields, RU/EN defaults/UI, alternate CV/download,
  missing CV, copying, manual sent/idempotency/source linking and old behavior.
- Verification: baseline 210 tests; final full suite 217 tests passed including
  real local OCR. compileall, diff check and secrets/unignored-file audit passed.
- No next major stage: actual email sending stays a separate future request.

## Matching jobs now — 2026-09-12
- Added RU/EN retrospective selection inside Job Alerts using saved roles/aliases,
  location, UAE only and minimum salary. Works when alerts are OFF, without touching
  delivery history, cutoffs, quotas or automatic notification behavior.
- Existing relevance/synonym/location/CV scorers reused. Known insufficient AED
  ranges excluded; unknown/non-comparable salary retained. Exact title evidence
  outranks synonyms/skills/body; CV only breaks alert-relevance ties, then date.
- Indexed date-bound SQL selects at most 300 filtered candidates; seven days,
  expanded to 14 if fewer than five relevant matches. Top 50 cached for pagination.
- Localized count/cards/high-medium relevance/optional CV score, next/previous,
  source/save/compare/application actions and settings/back/no-results screens.
- Tests: matching preferences, synonyms, salary unknown/ranges, UAE, ranking and
  secondary CV, dates, duplicates, 300/50 bounds, RU/EN navigation/save/empty/stale.
- Verification: baseline 203 tests; final full suite 210 tests passed, including
  real local OCR. compileall, diff check and secrets/unignored-file audit passed.
- Scope complete after validation; do not start CV redesign or another major stage
  as part of this request. Existing automatic Job Alerts architecture unchanged.

## Personal Job Alerts — 2026-09-12
- Added RU/EN Job Alerts menu: professions, extra keyword/alias lists, location,
  UAE only, optional minimum AED salary, ON/OFF and current settings; /cancel.
- Defaults OFF. Enabling or editing settings establishes publication-time and
  vacancy-ID cutoffs; no existing posts are replayed. Enqueue only on initial
  vacancy insertion, never on retry/update. Unknown/future/older-than-24h dates,
  duplicates and disabled sources excluded. Role synonym/relevance and location
  rules reused; unrelated-body/company-only evidence cannot trigger alerts.
- SQLite outbox is written in the same transaction as collector vacancy insertion.
  Bot starts/stops an independent async sender with its polling lifecycle. Pending
  rows are revalidated; OFF cancels them, stale rows expire after 24 hours.
- Persistent limits: global 2 seconds, user 60 seconds and 10 attempts/rolling 24h.
  Separate attempt history counts every FloodWait retry, not just distinct posts.
  Telegram RetryAfter pauses globally; Forbidden disables the subscription.
- Unique user/vacancy and user/content keys, atomic claims and hash rechecks prevent
  duplicate notifications. Ambiguous network/crash outcomes are not retried;
  a notification can be missed in that case, rather than duplicated. Details and
  the one-in-flight limitation are documented in README.
- Cards explain matching evidence and reuse vacancy open/analyse/save/application
  actions; disable action applies to the recipient's settings. No email sending.
- Privacy deletes preferences, delivery/attempt history with existing user data.
  Monotonic delivery IDs prevent late replies after deletion modifying new rows.
- Tests include actual collector -> persisted queue, RU/EN setup/navigation/cards,
  old/backfill/duplicate exclusion, OCR matching, filters, restart safety, quotas,
  FloodWait, ambiguous errors, blocked users, lifecycle and privacy races.
- Verification: baseline 184 tests; final full suite 203 tests passed including
  real local OCR. compileall, pip check, diff and secrets/unignored-file audit passed.
- Next optional stage: visual CV template redesign in services/cv_export.py and
  templates/template_*/style.json, with synthetic PDFs for visual comparison.
  Existing CV Builder/data model/flow unchanged. No live notifications were sent
  during tests; restart bot.py and collector.py to use alerts, then opt in via menu.

## Exact continuation checkpoint — 2026-09-12
- Attached request priorities 1–3 are complete (search quality, source quality,
  UAE location filtering). Priority 6 Profile UX was already complete and its
  regressions remain green. No unfinished changes to these stages remain.
- Priority 4 Job Alerts is complete; see the current milestone above.
- Priority 5 CV visual restyling has NOT been done in this run. Existing CV Builder,
  three renderers and active-CV integration work, but visual improvement/demo PDFs
  remain. Start from `services/cv_export.py` and `templates/template_*/style.json`;
  read renderer tests and render current samples before editing only presentation.
  Preserve selectable PDF text, editable DOCX and existing wizard/data model.
- Resume commands (PowerShell, project directory):
  `.\\.venv\\Scripts\\python.exe collector.py --source-quality`
  `$env:RUN_OCR_INTEGRATION='1'; .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q`
  `.\\.venv\\Scripts\\python.exe -m compileall .`
- Previous checkpoint baseline: 184 passing tests, including real local OCR;
  compileall and dependency check passed. Commit/push each next milestone and
  keep secrets/session/database/upload/media files out of Git.

## UAE location filtering — 2026-09-12
- Shared RU/EN location normalization for all seven UAE emirates/cities and UAE;
  parser and detector use it. Country deduplicated when cities are known.
- Strict UAE-only toggle in localized vacancy filters; combines with pagination,
  salary/source/date/search. Unknown and mixed-country workplaces excluded.
- Read-time handling for legacy OCR/raw posts, prioritizing explicit workplace
  over recruiter contacts/experience. No data migration or re-collection required.
- Profile UAE location softly prioritizes UAE results when no location filter is
  set; explicit location overrides it. Saved list stays independent of filters.
- Verification: all 184 tests passed, including real local OCR; compileall passed.

## Source quality — 2026-09-12
- Added offline `python collector.py --source-quality`: per-source unique messages,
  detection outcomes, OCR candidates/failures/unavailable, duplicates, average score
  and useful rate. Includes empty, disabled and removed sources; no post content.
- Uses existing records; no schema, collector polling or user-data changes.
- Tests cover aggregate counts, empty sources, duplicates, retry updates and an
  offline CLI invocation that never loads Telegram settings or exposes post text.
- Verification: all 179 tests passed, including real local OCR; compileall passed.
- UAE location follow-up completed above; next work is in the continuation checkpoint.

## Search Quality V2 — 2026-09-12
- Audited clean main and fetched origin/main; Profile edit submenu is already done.
- Weighted search evidence: exact/alias role, synonym role, parsed skills, raw/OCR/
  combined text, company/location; whole-word profession matching, threshold 30,
  isolated long-body mention suppression and generic-query seniority adjustment.
- Sort by relevance then publication date, not vacancy ID. Query filters and
  dedup eligibility retained; rank before pagination with no overall result cap.
- Added Previous/Back to search, high/medium labels and next-page lookahead.
  Regression traverses all 35 matching cards; saves preserve current navigation.
- Verification: all 177 tests passed, including real local OCR; compileall passed.
- Read-only check on the current local database: 37 visible cook-query matches,
  ranked in about 0.03 seconds. No collection or user-data modification needed.
- Next priorities from the attached request: source-quality CLI summary, UAE-only
  location filtering, then alerts preview and CV template improvements. No APIs.

## Profile UX refactor — 2026-09-12
- Profile overview now shows data and Edit/Language/Main menu only. New Edit screen
  lists all eight fields in two columns, with Done and Back returning to Profile.
- Reuses the existing profile form and validation; successful single-field edits
  confirm the save and return to Edit. /cancel and inline Cancel discard the pending
  edit and return to Edit. Profile creation and other form destinations are unchanged.
- All new labels are in RU/EN catalogs. Added overview/edit/navigation/save/cancel/
  validation/localization regressions; no database or other product module changes.
- Verification: baseline 169 tests; final full suite 172 tests passed, including
  real local OCR. compileall and diff checks passed. Commit title:
  `Simplify Profile overview and add compact field editing menu`.
- Scope follows the latest instruction: CV template restyling, collector, matcher
  and Apply/alerts were not changed. No unfinished Profile implementation remains.

## Apply Flow foundation — 2026-09-12
- CV Builder priority completed and pushed: foundation 7c4e87b, templates 2ce8dae.
- Added Apply preparation from vacancy cards and application details: role/company,
  literal extracted email or explicit missing-email label, owner-selected CV,
  suggested subject, editable message, review/Confirm/Cancel in RU/EN.
- Confirm saves only to apply_preparations with unique owner/origin key; no email
  transport, automatic application/status/date update or notification sending.
  Rechecks source details and CV availability; stale callbacks are refused.
- Cancel/navigation discards unconfirmed edits; confirmed preparations reopen.
  Privacy deletion removes preparations. No secrets/configuration changes.
- Added service/UI regressions for no-network confirmation, idempotence, missing
  email, editable text, owner checks, deleted CV, changed source and privacy.
- Final verification passed: all 169 tests including real local OCR, compileall,
  pip check and diff check. Audit found zero tracked/unignored private-data paths
  and zero files containing configured secret values. Commit title:
  `Add local-only Apply preparation with editable message and CV selection`.
- Next milestone (not started in this session): Job Alerts preview foundation.
  Begin with new services/job_alerts.py: validated preferences and a pure bounded
  candidate matcher reusing services/vacancy_search.py. Add owner-scoped preferences
  and unique (user_id,vacancy_id) preview/delivery history in db.py, admin-only preview
  entry point and tests/test_job_alerts.py; no automatic dispatch before rate limits.
  First continuation command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`.
  Keep OpenAI, paid services, email delivery and server setup disabled.

## CV templates and language versions — 2026-09-12
- Audit: main branch, GitHub origin verified; four configured sources enabled.
  Existing offline search/matcher/OCR and private user records preserved.
- Three selectable local templates: Modern and Professional with independent
  PDF columns and editable DOCX columns; Classic ATS with a single plain-text
  column, no photo/layout tables. A4, Unicode font embedding, automatic wrapping.
- Persisted independent RU/EN CV copies; no automatic translation of user facts.
- Empty headings omitted; photo cropped without stretching; optional references
  and additional-information fields. Active CV keeps a deliberate saved snapshot.
- Visual QA: all three PDF and Word-rendered DOCX layouts inspected with Cyrillic,
  long email/LinkedIn and 1–3 pages; tightened metadata paragraph page-break rules.
- Final validation: 163 tests passed with real local OCR; compileall, pip check
  and diff check passed. Static previews use synthetic data only. No private files
  are tracked by Git. Commit: `Complete three CV templates and independent RU EN versions`.
- Next priority after stable CV Builder commit: Apply preparation in vacancy UI,
  editable subject/message, selected CV and extracted email, Confirm saves only.
  Alerts remain lower priority; do not send email or notifications automatically.

## CV Builder foundation — 2026-09-11
- Persistent owner-scoped structured drafts, resumable per-answer wizard, section
  editing, repeated experience/education, photo handling, clone and confirmed delete.
- Local PDF/DOCX attachments, sanitized filenames, empty-field omission, active CV
  snapshot integration and privacy deletion. No generated facts or external services.
- RU/EN UI, isolated template/export layer and future attachment data interface.
- Foundation verification: all 157 tests and compileall passed.
  Synthetic PDF and DOCX visually checked, including two pages and
  Cyrillic. Word used only for QA because LibreOffice is absent, not at runtime.
- Next authorized milestone: Modern/Professional/Classic ATS templates and separate
  RU/EN document versions, with long-content and extraction regression tests.

## RU/EN profession search — 2026-09-11
- Added isolated offline dictionary in services/vacancy_search.py with all requested
  profession groups, symmetric RU/EN expansion, punctuation/hyphen/space normalization
  and CDP / chef de partie aliases. Specific whole-query groups do not recursively
  expand into unrelated broader professions. Unknown queries keep substring search.
- VacancyStore.list searches role/company/raw_text/ocr_text/combined_text/location
  with bound SQL parameters before existing filters and pagination. No schema,
  dependency, UI-language or scoring changes; no APIs or paid services.
- Added eight tests covering required synonyms, each text column, company/location,
  all dictionary groups, normalization, filters, pagination and saved-user isolation.
- Verification: all 146 tests passed, including real local OCR; full compileall,
  dependency and diff checks passed. Implementation complete; commit title:
  `Add offline RU EN profession synonyms to vacancy search` (target origin/main).
- Next operational check: after restarting the bot, search for повар and chef in
  either UI language and use Next with existing filters. No live user data changed.

## Multi-source management completed — 2026-09-11
- Added username/@username/public t.me link normalization, case-insensitive unique
  sources, RU/EN display titles, and CLI add/list/enable/disable/remove operations.
  Numeric IDs remain supported for enable/disable/remove. Commands work offline.
- Safe removal marks a source disabled/removed, retaining vacancies, saved links,
  source IDs and collection cursors. Explicit add restores it; startup seed does not.
  Existing jobs_in_dubai and custom titles/settings are preserved. New enabled
  sources are picked up on the next normal polling cycle without code changes.
- Additive removed_at migration preserves old source rows and user data.
- Verification: 138 tests passed, including real local OCR, Unicode path regression,
  source migration, offline CLI, title/link normalization, retained saved vacancies,
  and dynamic enabled-source collection. Full compileall, pip check and diff check pass.
- Earlier milestone commits: 0cac47d (manual Unicode OCR fix), 80419fa (reprocess logs).
- Commit for this milestone: `Add safe offline multi-source management`.
- No unfinished implementation remains. Next operational step: optionally add real
  sources using the documented CLI and run --reprocess-backfill jobs_in_dubai 50
  after stopping any collector sharing its session. No live Telegram replay or
  modification of user records was performed during these tests. No AI/paid APIs.

## Reprocess-backfill log completion — 2026-09-11
- Existing bounded mode from 62ac542 retained; no duplicate implementation added.
- Added explicit updated/duplicate/skipped outcomes, photo OCR rerun requests and
  processed/skipped/failed summary; logs omit message content and contacts.
- Regression verifies logs alongside existing bounds, dedup, stable IDs, checkpoint,
  FloodWait, per-message failure and cancellation tests.
- Verification: 130 tests including real OCR and compileall passed.
- Next after commit: multi-source link normalization and safe source removal.

## Windows Unicode OCR fix — 2026-09-11
- Preserved owner's manual Pillow -> EXIF transpose -> RGB -> NumPy -> readtext fix.
  No string filename crosses the EasyOCR/OpenCV boundary. NumPy import is lazy so
  bot startup remains compatible without the optional OCR installation.
- Added Cyrillic Windows-like directory regression checking RGB array/dtype and
  closed file handle; real OCR integration now also uses a Cyrillic media directory.
- Owner reported live validation on jobs_in_dubai/33267 (382 OCR chars, score 60,
  probably_vacancy, vacancy 52). This live result was not independently replayed.
- Verification passed: 129 tests including real local OCR and compileall.
- Next: commit this fix separately, then improve existing reprocess-backfill
  logs and multi-source CLI management. No manual code changes discarded.

## Bounded reprocess-backfill — 2026-09-11
- [x] `collector.py --reprocess-backfill SOURCE N` reprocesses a single latest-post
  snapshot of 1–500 messages from one configured enabled public broadcast channel.
- [x] Uses existing `process_message(force=True)` and store hash dedup/update logic;
  retries photo OCR, preserves record IDs/saved links and does not touch the source
  checkpoint. Repeated runs remain idempotent. No new matching or product features.
- [x] Sequential processing, per-message error isolation, FloodWait handling and
  cancellation preserve existing safety behavior; debug-pipeline remains opt-in.
- [x] Regression tests: repeated batch + disabled OCR records + repost links,
  bounded source selection, failed posts/FloodWait and cancellation.
- Verification passed: 128 tests including real local OCR, compileall, dependency
  and diff checks. No live Telegram replay or changes to user records during development.
- Next exact action: stop any collector sharing the session, then run
  `.\.venv\Scripts\python.exe collector.py --reprocess-backfill jobs_in_dubai 50 --debug-pipeline`.
  Inspect repaired cards with bot filters cleared. OCR must be enabled and models ready.
- Commit title: `Add bounded vacancy backfill reprocessing using existing deduplication`.

## Photo-vacancy diagnosis and fix — 2026-09-11
- [x] Read-only local diagnosis: OCR flag was false; aggregate DB counts showed
  22 supported images with `ocr_status=disabled`, including 18 without captions.
  No raw posts, contacts, credentials or session contents were printed.
- [x] Found separate download boundary bug in `services/vacancy_pipeline.py`:
  Telethon appends `.jpg`/document extension to `original`; ignored return value
  caused FileNotFoundError before OCR. Use/validate the actual returned path now.
- [x] Real EasyOCR EN/RU models load successfully on Windows. The extended offline
  fixture uses Telethon's actual cached-photo downloader, real CPU OCR, detector,
  parser, SQLite insertion and bot card rendering. Role/location/salary are checked;
  OCR can misread email punctuation, so absent contacts are not inferred.
- [x] `--debug-pipeline`: opt-in stage flags, counts, redacted <=120-character preview,
  parser field-presence flags, save/duplicate/UI eligibility and skip/failure reason.
  No raw paths/exception strings or arbitrary OCR words/names/contacts are logged.
- [x] `--reprocess-message SOURCE ID`: one configured enabled public post; preserves
  record ID and source cursor, handles FloodWait and preserves previous successful
  OCR if retry fails. Ordinary polling remains idempotent. Existing --retry-ocr now
  uses the same safe save/retry path and emits diagnostics when requested.
- [x] Enabled only the non-secret local `.env` flag VACANCY_OCR_ENABLED=true after
  verifying models; all other bytes preserved. `.env` remains ignored and unstaged.
- [x] 124 tests passed with RUN_OCR_INTEGRATION=1, including six new diagnostic/
  replay/privacy tests and the upgraded full photo-pipeline integration fixture.
- Final checks passed: full suite, compileall, pip check and diff check. Staged-file
  audit excludes .env, models, sessions and all user data before commit.
- Next exact step: restart the owner's collector so it reads the enabled flag.
  Stop any other process using the same Telethon session before running
  `.\.venv\Scripts\python.exe collector.py --reprocess-message jobs_in_dubai MESSAGE_ID --debug-pipeline`.
  Replace MESSAGE_ID with the affected post's ID; or use `--retry-ocr 50` for the
  previously disabled batch. Clear bot vacancy filters to verify the repaired card.
  No live Telegram replay was run during development; existing posts were not rewritten.
  No new product features or paid/AI API services were introduced.
- Commit title: `Fix photo download path and add safe vacancy pipeline diagnostics`.

## v0.4 — Telegram Vacancy Collector
- [x] v0.4a: separate Telethon process, local source configuration/SQLite tables,
  safe first-run baseline, bounded backfill, source controls and checkpointing.
- [x] v0.4b: local OCR, preprocessing, vacancy detection, conservative field parser.
- [x] v0.4c: localized Vacancies UI, bounded CV ranking, saved and application conversion.
- [x] v0.4d: content deduplication, admin stats, recovery/hardening and final verification.
- v0.4b now processes new posts with `VacancyPipeline`; bot screens are next.
  Existing user/profile/CV/application tables are preserved. No Telegram login was
  performed. Source seed is `sources.json`; SQLite becomes authoritative after seeding.
- v0.4a verification: 93 local tests, compileall and dependency check. Telethon installed
  locally; tests use a fake transport and never require Telegram credentials.
- v0.4b: 103 tests passed, including detector negatives, salary/contact/location/OR,
  hashing, duplicate links, image preprocessing and injected OCR success/failure.
  EasyOCR is optional in requirements-ocr.txt; `--prepare-ocr` downloads local models.
  Actual EasyOCR weights/accuracy and Telegram login are not exercised by unit tests.
- v0.4c: 109 tests passed, including RU/EN cards, bounded lazy ranking, saved isolation,
  privacy cleanup, combined filters and review/edit/cancel application conversion.
  `vacancy_ui.py` shares ProductUI reports/forms. `db.py` adds application source_url
  and deletes private saved links on erasure; public vacancies remain.
- v0.4d: canonical hash dedup keeps each source/message row, including during retry;
  source filters find reposts. Admin-only counts use current processing outcomes.
  Retained images expire after configured 1–30 days; false removes all temporary
  images. `--reprocess N` retries stored texts offline; `--retry-ocr N` fetches only
  bounded failed/disabled image posts from configured enabled public channels.
- Failure/cancellation tests verify checkpoints only advance after persistence,
  FloodWait sleeps as instructed, malformed posts do not block the rest, unsafe
  sources are refused, and logs omit exception contents. Existing v0.3/RU-EN tests
  still run unchanged except intentional additive menu/schema expectations.
- Local dependencies: Telethon 1.45.0, Pillow 12.3.0, EasyOCR 1.7.2, CPU torch
  2.14.0 and torchvision 0.29.0 installed successfully. `--prepare-ocr` downloaded
  EN/RU models into ignored `ocr_models/`. Real English fixture recognition passed
  with network blocked in the test. No Telegram authorization or live polling ran.
- Final verification passed: 118 tests with `RUN_OCR_INTEGRATION=1` (otherwise one
  optional model test skips), compileall, pip check and git diff --check. Staged-file
  audit before commit excludes models, session files, .env, data and uploads.
- Exact next step after final commit: owner fills collector keys in existing .env,
  sets `VACANCY_OCR_ENABLED=true` if wanted, runs `collector.py --backfill 50` locally
  and completes hidden phone/code/2FA prompts. Start bot separately; test latest,
  filters, best matches, saved -> application review, RU/EN and admin with two users.
  Do not add OpenAI, payments, auto-apply, unsolicited messages or Mini App.
- Implementation is complete; only owner-authorized live Telegram acceptance remains.
  v0.4 commits: ca2a3b8 (a), 20bcfa1 (b), f2159e0 (c); final d commit title:
  `Harden v0.4 collector recovery privacy and local OCR verification`.

## Current checkpoint — RU/EN localization (2026-09-11)

- [x] Persistent `users.language` settings (`en`/`ru`), English fallback for old users.
- [x] First `/start` language selector; later `/language`, main menu and Profile buttons.
- [x] Shared handlers with external EN/RU catalogs for all menus, forms, validation,
  confirmations, errors, help, onboarding, status labels and disabled AI fallback.
- [x] Localized analysis reports, reasons, recommendations, profile/important gaps;
  unchanged score logic, technology names and arbitrary user-entered text.
- [x] Stable internal application statuses and idempotent migration of nine old labels;
  filters, dashboard and old `/status` inputs remain compatible. Unknown old values remain.
- [x] Automated catalog, persistence, migration, language-change and shared UI tests.
- [x] README updated with language flows, migration and English-recognizer limitation.
- [ ] Live Telegram acceptance in RU and EN with two testers (requires owner's setup).

### Exact state / continuation
- Runtime localization: `locales/__init__.py`, `en.json`, `ru.json`,
  `requirement_labels_ru.json`. `bot.make_main_menu` and `ProductUI` resolve the
  stored language on each request; no duplicated language-specific handlers.
- `db.py` adds nullable language without overwriting existing users. Unselected users
  receive English fallback and choose on `/start`. `statuses.py` normalizes stable
  codes and EN/RU inputs; migration rewrites only recognized old English status labels.
- Changing language cancels pending forms/confirmations but preserves saved data and
  analysis context. Privacy deletion removes language together with the user row.
- `services/matcher.py` accepts optional `language='en'`; report rendering can change
  language independently of matching. English defaults preserve existing callers.
  The requirement recognizer remains English; RU interface does not imply Russian CV
  recognition. Free-text profile location/visa still needs conservative manual review.
- Verification: 87 automated tests passed (75 existing + 12 localization tests).
  Tests use temporary SQLite/files and mocked Telegram/provider transport. No live
  polling, OpenAI requests, paid services or real user data changes during development.
- Final checks passed: full unittest suite, `python -m compileall .`, `pip check`,
  git diff whitespace check. Use `.\.venv\Scripts\python.exe` for Python in this
  environment. Offline startup wiring is covered by the real Application construction
  tests; live Telegram polling remains a manual acceptance step.
- Next action: run the bot with the locally configured Telegram token; test first
  language selection, Profile -> Language, `/language`, CV upload/selection, report,
  save/status/filter/search/dashboard, then cancel/confirm deletion in each language.
  Restart and check persisted language and legacy applications. Keep AI disabled.
- No incomplete implementation remains. Commit title:
  `Add RU EN localization and stable application statuses`.

Earlier checkpoints below describe historical states; this checkpoint supersedes
their English-only UI and legacy-status storage descriptions.

## v0.3 — product usability and user profile (2026-09-11 checkpoint)
- [x] Profile create/view/edit: all eight fields, validation and cancellable forms.
- [x] Multiple CV list/pagination, persistent active CV, individual confirmed deletion.
- [x] Applications v2: all fields, nine statuses, detail view and status buttons.
- [x] Status filters and Unicode case-insensitive company/role search with pagination.
- [x] Dashboard counts and explicitly labelled current-state conversion ratios.
- [x] Analysis follow-ups: save vacancy, compare another CV, important/profile gaps.
- [x] `/delete_my_data`: expiring confirmation, local files + all user records + session state.
- [x] Six-section private-chat menu; legacy status/entry and matcher compatibility.
- [x] Production AI is disabled in v0.3 even with an existing API key. No paid services.
- [x] Automated migration, CRUD, selection, filters, dashboard, deletion and UI tests.
- [ ] Manual live Telegram acceptance with two test users and sample CVs.

### Implemented state and continuation
- `db.py`: additive, idempotent SQLite migration; `profiles` and `active_resumes`
  tables; application source/salary/date_applied/vacancy_text columns. No existing
  user data is rewritten except backfilling legacy applied dates from created_at.
  Existing custom statuses remain visible. First migration selects newest legacy CV;
  later starts preserve selection. SQLite connections close explicitly and enable
  secure_delete (not a guarantee for backups or forensic disk erasure).
- `services/product.py`: field validation, separate self-reported profile gap checks,
  and `UserFiles` deletion. It preflights path ownership/boundaries, preserves other
  users' files and shared-file references, cleans failed-upload remnants, and retains
  records if filesystem cleanup fails so the operation can be retried.
- `product_ui.py`: profile/application forms, lists and pagination, status/search,
  dashboard, analysis follow-ups and five-minute deletion confirmations. Stale form
  and analysis buttons are rejected. Unfinished forms/analysis live only in memory.
- `bot.py`: six-section menu, active-CV analysis, private-chat routing, unique upload
  filenames, 5 MB upload limit and failed-upload cleanup. Existing matcher unchanged.
  Legacy `Company | Role | notes`, `/status`, and disabled AI-menu fallback retained.
- `tests/test_product.py` and `tests/test_product_ui.py`: new persistence/security/UX
  checks; existing bot report test updated for the requested post-analysis buttons.
- README replaced with v0.3 setup, user flows, migration, privacy, dashboard formulas
  and a two-user manual acceptance checklist. Previous checkpoints below are historical.

### Verification / next exact step
- Final commands: `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`,
  `.\.venv\Scripts\python.exe -m compileall .`, `.\.venv\Scripts\python.exe -m pip check`.
- Test suite: 75 tests (49 previous + 26 new), all local with mock network transports.
  Real Telegram Application construction and handler flow are tested without polling.
- Final verification on 2026-09-11: all 75 tests passed; compileall, pip check and
  git diff --check passed. PTB's existing mixed-conversation warning and asyncio
  slow-callback notices during SQLite test setup are non-failing diagnostics.
- No live Telegram polling or actual API requests were started. No real user data was
  deleted during development; destructive tests operate in temporary test directories.
- Manual next action: run `.\.venv\Scripts\python.exe bot.py` with the owner's locally
  configured Telegram token; follow README's early-tester smoke test with two users.
  Back up existing data securely before deploying the migration. Owner-created backups
  and Telegram message history are outside `/delete_my_data` and need separate handling.
- If issues appear, continue in `product_ui.py:buttons` / `form_received` for UX,
  `db.py:_init` for migration, or `services/product.py:UserFiles` for deletion. Add
  synthetic regression tests before fixing. Known limits: profile comparison is
  conservative and does not infer salary fit or specialist experience; dashboard is
  a current-status snapshot, not historical funnel analytics. Forms reset on restart.
- No unfinished implementation remains; only live acceptance is pending. Do not start
  payment, API integration or Mini App. Commit title: `Add v0.3 profiles CV management and application workflows`.

## Current priority — offline heuristic analysis (2026-09-10)
- [x] Real-vacancy fixes: OR/constituent deduplication, Dubai/UAE consolidation,
  5% maximum soft-skills contribution, grouped reports and separate category breakdown.
- [x] Replace frequency keyword matching with classified requirements.
- [x] Seven categories, synonym normalization and standalone stopword exclusion.
- [x] Weighted scoring, mandatory/optional distinction and simple alternatives.
- [x] Explicit experience/proficiency/degree/location checks and truthful recommendations.
- [x] Full Telegram report with safe splitting; existing features retained.
- [x] Automated regressions and dependency/startup sanity checks.
- [ ] Owner's manual Telegram acceptance of local analysis with a sample CV.

Latest instruction overrides the older AI acceptance plan below: do NOT call OpenAI
or introduce paid services. Existing AI code/menu fallback remains intact; AI tests
use mocks only. Earlier checkpoint is historical, not authorization to resume API use.

## Milestone 0 — foundation
- [x] Telegram bot skeleton
- [x] Local SQLite database
- [x] CV upload
- [x] PDF/DOCX/TXT parsing
- [x] Vacancy keyword analysis
- [x] Applications tracker
- [x] Status updates

## Milestone 1 — AI layer
- [x] Create provider interface in `services/ai.py`
- [x] Add AI CV review
- [x] Add vacancy-specific CV bullet rewriting
- [x] Add cover-letter generation
- [x] Add interview-question generator
- [x] Add usage limits per user
- [x] Add safe prompt length limits and error handling
- [ ] Manual live acceptance with owner-provided Telegram/OpenAI credentials (see checkpoint)

## Milestone 2 — productization
- [x] User onboarding/profile
- [x] Desired role / salary / location / visa status
- [x] Better application tracker
- [ ] Analytics
- [ ] Admin panel
- [x] Privacy / delete-my-data command
- [x] Logging without leaking CV text or secrets

## Milestone 3 — monetization
- [ ] Free tier
- [ ] Pro tier
- [ ] Telegram Stars payment flow
- [ ] Subscription state
- [ ] Referral system

## Milestone 4 — Mini App
- [ ] Web UI
- [ ] CV dashboard
- [ ] Application kanban
- [ ] Mobile-first design

## Resume-after-limit rule
Before doing new work:
1. Read this file.
2. Run tests or at least `python -m compileall .`.
3. Inspect `git status`.
4. Continue from the first unchecked task.
5. Update this file after each completed task.

## Checkpoint — 2026-09-10, Milestone 1 implementation complete

### Completed
- Read all project source and required instruction files before editing. Initial compile passed.
- No Git repository or `.env` existed at session start. No user secrets were read or created.
- Created `.venv` using the available bundled Python 3.12; installed all requirements.
  `python` and `py` were not in this session's PATH: use `.\.venv\Scripts\python.exe`.
- `services/ai.py`: async provider protocol, OpenAI Responses HTTP provider, service with
  four actions, fact-only prompting, untrusted-source separation, input/output limits,
  45-second total timeout, safe errors, no automatic retries, `store=false`.
- `db.py`: additive `ai_usage` table, atomic per-user/day attempt reservation. Failed
  dispatched requests count; invalid inputs do not. Existing data/schema preserved.
  Connections now close explicitly for reliable Windows file handling.
- `bot.py`: AI menu with data-sharing notice; vacancy confirmation flow; safe plain-text
  output splitting; `/start`, `/menu`, `/cancel` clear pending AI work. Local heuristic
  matching and tracker remain independent of AI. Import no longer starts configuration
  or DB writes; `build_application()` supports offline startup checks.
- Suppressed transport logs and raw exception details that could expose CV text/secrets.
- `config.py`, `.env.example`, `requirements.txt`, `README.md` updated for optional AI.
- Added 14 non-network tests covering actions, provider responses/errors, invalid inputs,
  timeout errors, atomic/persistent/user-isolated quota, output splitting, startup without
  AI, actual conversation reset routing, error-log privacy, parser/matcher and tracker.

### Verification and exact limits
- `.\.venv\Scripts\python.exe -m compileall .` passed.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` — 14 passed.
- `.\.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.\.venv\Scripts\python.exe bot.py` reaches configuration and exits with a clear
  missing-token message. Live Telegram polling is NOT verified: `.env` is absent.
- Offline tests successfully construct the Telegram Application without an OpenAI key
  using a synthetic token, temporary DB and mocked messages. No real API calls were made.
- PTB emits its informational `per_message=False` warning for mixed callback/text
  conversations. This configuration is intentional; `/menu` routing is regression-tested.
- AI factuality is instructed, not guaranteed. Live draft quality and account/model access
  still require acceptance testing. No payment or Milestone 2 features were started.
- No partially implemented feature remains; pending work is live acceptance.

### Next exact action
1. Owner creates `.env` from `.env.example` only if absent, adds the BotFather token
   locally, and optionally adds an OpenAI key. Never paste keys into task messages/logs.
2. Run `.\.venv\Scripts\python.exe bot.py`. Exercise `/start`, sample CV upload,
   Analyse vacancy, add application and `/status ID Interview` without an OpenAI key.
3. Configure AI locally and restart. Use sample/non-sensitive CV text to test CV review,
   bullet improvements, cover letter and interview questions. Check fact/suggestion
   separation, cancellation and the daily limit. Disable AI again and verify fallback.
4. If live checks fail, continue in `services/ai.py:OpenAIProvider.generate` for provider
   issues, or `bot.py:buttons`, `ai_vacancy_received`, `run_ai` for Telegram flow issues.
   Add a non-network regression test for any discovered bug and rerun all three checks.
5. Once manual acceptance passes, mark its checkbox above. Next development milestone:
   Milestone 2 onboarding/profile. Keep payment features deferred until AI is stable.

### Local environment / Git
- Python used to create this environment:
  `C:\Users\Адель\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
  On another laptop install Python 3.10+ and recreate `.venv` using README instructions.
- Installed: python-telegram-bot 22.8, python-dotenv 1.2.3, pypdf 6.18.0,
  python-docx 1.2.0, httpx 0.28.1. Dependency compatibility check passed.
- Git repository initialized on `codex/ai-layer`; existing author identity retained.
  Initial milestone commit title: `Implement optional AI assistant milestone`.
  Locate it using `git log -1 --oneline`; do not stage `.env`, data or uploads.
  Sandbox Git commands require the per-command option
  `-c safe.directory=C:/Users/Адель/Desktop/dubai_job_assistant` due to the sandbox
  account differing from the folder owner. No global Git configuration was changed.

## Checkpoint — offline heuristic analyzer, 2026-09-10

### Complete
- Replaced `services/matcher.py` frequency ranking with a small explicit English
  requirement vocabulary, aliases, contextual rules and evidence-based matching.
- Categories: hard skills; tools; experience; education/certifications; languages;
  Dubai/UAE/location/visa; soft skills. Stopwords alone never create requirements.
- Weights 35/20/20/10/10/5, with languages/location sharing the 10% group. Empty groups
  excluded. Optional items use quarter weight within groups; wholly optional groups
  also receive quarter group weight. Repetitions cannot raise importance.
- Report includes full strong/important/optional lists, explanations for gaps,
  actionable truthful recommendations, coverage limits and non-official ATS disclaimer.
- Years remain scoped and explicit; no invented totals from employment dates. Basic
  skill/language mentions do not establish requested advanced proficiency. Degree
  subjects, negations, in-progress credentials, optional higher experience thresholds,
  simple alternatives and location/visa distinctions have regression coverage.
- `bot.py:vacancy_received` splits full reports using the existing UTF-16-safe helper.
  Upload/parser, tracker/status and AI fallback code remain available. No dependencies
  added, no schema migration, no secrets read/changed, no API calls or paid services used.
- `README.md` documents scoring math and realistic limitations.

### Verification
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`: 40 tests passed
  (26 new tests plus the original 14). AI transport/provider calls are mocked.
- `.\.venv\Scripts\python.exe -m pip check`: no broken requirements.
- `.\.venv\Scripts\python.exe -m compileall .`: passed after the final code changes.
- Offline startup constructs the real Telegram Application with synthetic credentials
  and no OpenAI key. Handler regression checks cover local analysis, long reports,
  tracker/status, cancellation and disabled AI menu. No live polling started.
- The existing PTB informational warning about mixed callback/text conversations
  (`per_message=False`) remains expected; actual command reset routing is tested.

### Exact continuation
1. Run the three verification commands above; inspect `git status`.
2. With the owner's locally configured Telegram token, manually test upload -> Analyse
   vacancy -> weighted report, then add/update an application and verify AI-disabled
   fallback. Use sample text. Keep OpenAI disabled; never request keys in chat.
3. If a real vacancy exposes a false match, add an anonymized/synthetic regression to
   `tests/test_matcher.py`, then adjust `services/matcher.py:extract_requirements` or
   `evidence_status`. Vocabulary is in `CATALOG`; aliases in `normalize`.
4. Known limits: English vocabulary coverage, complex negation/alternatives, implicit
   proficiency and experience dates remain conservative/manual. No unfinished feature
   is left. Do not claim the score measures all requirements or actual competence.
5. Commit for this checkpoint: `Improve offline vacancy analysis with weighted requirements`.
   Find its hash with `git log -1 --oneline`. Use the scoped safe.directory option from
   the previous checkpoint when running Git under the sandbox account.

## Latest checkpoint — matcher deduplication and score calibration

- Completed in `services/matcher.py`: canonical OR chains (including reverse order,
  slash alternatives and OR inside AND lists), constituent deduplication before
  scoring, and one Dubai/UAE geographic requirement retaining the city specificity.
  Visa, authorization and experience constraints are not folded into geography.
- Explicit OR conditions remain sufficient despite repeated bare constituent mentions;
  distinct scopes/levels are preserved. Partially overlapping OR groups are not
  combined into a single broad alternative.
- Soft skills now contribute at most 5% after normalization, including sparse
  vacancies; missing all soft skills cannot drop an otherwise complete score below
  95. Soft-only vacancies receive overall N/A. Other weight ratios remain unchanged.
- Added separate `breakdown` for all seven categories, with None/N/A when absent.
  Existing `category_scores`, detailed requirements, matched/missing and suggestions
  remain available. `effective_weights` exposes the normalized weights for testing.
- Report groups related gaps/matches by category, shows four labels plus a remainder
  count, and gives one recommendation per category. Full details remain in returned
  analyzer data. Existing Telegram splitting and other bot features are unchanged.
- Updated README and regression tests: 49 tests pass, including nine dedicated
  OR/dedup/location/soft-weight/breakdown/grouping tests. Updated the bot report test
  to verify compact output and Telegram size safety instead of requiring verbosity.
- No OpenAI requests, paid services, dependencies, database or configuration changes.
- Verification: all 49 tests, final compileall, pip check and diff check passed.
  Startup is covered by offline Application construction and handler
  tests; no live polling or external messages were started.
- Next action: manually recheck the real vacancy in Telegram with sample CV data.
  If another extraction issue appears, add a synthetic case in `tests/test_matcher.py`
  and update `extract_requirements`, `deduplicate_requirements` or `evidence_status`.
  Keep external AI disabled. No partially implemented work remains.
- Commit title: `Deduplicate vacancy requirements and cap soft skill impact`.

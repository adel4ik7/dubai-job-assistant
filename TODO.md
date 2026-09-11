# Dubai Job Assistant — TODO

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

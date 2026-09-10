# Dubai Job Assistant — TODO

## Current priority — offline heuristic analysis (2026-09-10)
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
- [ ] User onboarding/profile
- [ ] Desired role / salary / location / visa status
- [ ] Better application tracker
- [ ] Analytics
- [ ] Admin panel
- [ ] Privacy / delete-my-data command
- [ ] Logging without leaking CV text or secrets

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

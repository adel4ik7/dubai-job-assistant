# Dubai Job Assistant — TODO

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

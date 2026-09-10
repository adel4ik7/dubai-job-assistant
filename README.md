# Dubai Job Assistant — AI Telegram assistant

Telegram bot for:
- uploading a CV (PDF/DOCX/TXT);
- extracting CV text;
- pasting a vacancy;
- classifying vacancy requirements and calculating a weighted local match score;
- highlighting CV evidence, important gaps and optional gaps;
- saving job applications in SQLite;
- updating application status.
- optional AI CV review, vacancy-specific bullet suggestions, cover-letter drafts and interview questions.

## 1. Create the Telegram bot

Open `@BotFather` in Telegram:

1. `/newbot`
2. Choose a display name.
3. Choose a username ending in `bot`.
4. Copy the token.

Do not publish the token.

## 2. Install

### Windows PowerShell

```powershell
cd path\to\dubai_job_assistant
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Paste the BotFather token into `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
```

Then:

```powershell
python bot.py
```

### macOS / Linux

```bash
cd /path/to/dubai_job_assistant
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## 3. Test

In Telegram:
1. `/start`
2. Upload a PDF/DOCX/TXT CV.
3. Tap `Analyse vacancy`.
4. Paste a full job description.
5. Save an application.
6. Change its status with `/status ID Interview`.

## Important

The match score is heuristic, not an official ATS score or guarantee. Local matching,
CV uploads and the tracker work without an OpenAI key. AI features require an API
account with model access and may incur API charges.

## Optional AI setup

Current development mode is offline heuristic analysis: no OpenAI calls or paid
services are needed for **Analyse vacancy**. The existing optional AI menu is retained.

Add these settings to your existing `.env` (do not overwrite your Telegram token):

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
AI_DAILY_LIMIT=5
```

Fill the key locally. Leave it blank to disable AI; `AI_DAILY_LIMIT=0` also disables AI.
Choose a Responses-compatible model available to your account. Restart after changes.
No OpenAI SDK is required; the provider uses the project's HTTPX dependency.

Open **AI assistant**, read the data-sharing notice, then choose an action.
**Send CV for review** dispatches the saved CV. Other actions ask for a vacancy;
sending the vacancy confirms sending both texts. `/cancel`, `/menu` and `/start`
clear the pending AI action. Drafts distinguish CV facts, suggestions and information
needing confirmation. The model is instructed never to invent candidate credentials;
this is not a factual guarantee, so users must verify every draft.

Limits: CV text 100–16,000 characters; vacancy 100–12,000 characters, in one Telegram
message (the Telegram client may impose a smaller message limit). Oversized inputs
are rejected, never silently truncated. The shared daily allowance across all four
actions resets at 00:00 UTC. Dispatched attempts, including timeouts and failed API
requests, count toward the allowance to bound repeated request costs. Invalid inputs
and disabled AI do not count. Counters survive restarts in SQLite. No automatic retries.
The overall AI wait is bounded to 45 seconds; output is limited and split for Telegram.

## Privacy

Uploaded files and extracted CV text are stored on the owner's laptop in `uploads/`
and `data/`. They remain until the owner removes them; automated deletion is a future
milestone. AI actions send extracted text to OpenAI, never the original file, filename,
Telegram ID or username as separate fields. Contact details inside the CV text are
still included: remove sensitive details before uploading if needed. Drafts are sent
back through Telegram and are not saved in the local database.

The provider uses the [Responses API](https://developers.openai.com/api/docs/guides/text)
with `store=false`. This does not guarantee zero retention; provider abuse-monitoring
retention may still apply. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).
HTTP transport logs and exception details are suppressed to avoid leaking tokens or CV
text. Keep credentials only in the gitignored `.env`; do not enable HTTP debug logging.

## Offline verification

Requires Python 3.10 or later; this milestone was tested with Python 3.12 on Windows.

```powershell
.\.venv\Scripts\python.exe -m compileall .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe bot.py
```

Tests need no credentials or network. They build the Telegram application with a
synthetic token, exercise handlers with fake messages, mock the AI transport, and use
temporary SQLite databases. Actual polling requires your BotFather token in `.env`.
Importing `bot` no longer loads secrets, creates a database or starts polling.

Manual acceptance with your configured bot: upload a sample CV; run local matching;
save/update an application; exercise each AI action; cancel a pending vacancy; reach
the daily limit. Restart with `OPENAI_API_KEY` blank and confirm local features work.

## Local vacancy analysis

`services/matcher.py` uses a curated English-language vocabulary and deterministic
rules, not keyword frequency. It recognizes seven categories: hard skills, tools,
experience, education/certifications, languages, location/visa and soft skills.
Standalone filler words (good, looking, preferred, advantage, basic, skills,
knowledge) cannot become scored requirements. Repetition does not increase weight.

Aliases include Power BI/PowerBI, MS/Microsoft Excel/Excel, SQL/T-SQL/PL/SQL/Structured
Query Language, Python/Python3/Python programming, and UAE/United Arab Emirates.
PostgreSQL, MySQL and SQL Server can support a generic SQL requirement, while generic
SQL does not establish experience with a particular database. Dubai can support a
UAE location requirement; UAE alone does not establish Dubai residency.

The group weights are hard skills **35%**, tools **20%**, experience **20%**, education
**10%**, languages and location together **10%**, and soft skills **5%**. Within each
group mandatory or unspecified requirements weigh 1, optional requirements weigh
0.25. The group score is matched weight / total requirement weight. Empty groups are
excluded and remaining group weights are normalized. A wholly optional group also
receives only a quarter of its usual group weight. For example, with only Python
and Excel required, an Excel-only CV scores 20 / (35 + 20), or 36%.

Preferred/optional headings and local phrases such as 'an advantage' are recognized.
Mandatory occurrences override optional duplicates; a higher preferred experience
threshold stays optional. Simple two-item alternatives such as 'Python or SQL' count
as one requirement. Years must be explicitly stated in the CV and relevant to the
required specialization; dates are not summed and unrelated experience is not added.
Known degree subjects are checked. Negated skills and qualifications in progress do
not count as established evidence. Requested advanced/fluent proficiency needs an
explicit level statement. Past Dubai experience and relocation interest do not prove
current residence; employer-sponsored visa benefits do not become candidate visa
requirements, and location alone does not prove work authorization.

Reports show overall score, strong matches, important gaps, optional gaps and
concrete recommendations. Every gap means **insufficient CV evidence**, not proof of
a missing capability. Recommendations only highlight existing evidence or suggest
verification/development; they never tell users to add unsupported qualifications.
Long reports are split into Telegram-safe messages without dropping requirements.

This measures recognized CV evidence, not hiring probability or an official ATS
score. A small dictionary cannot cover every occupation, degree, language level,
negation or complex alternative. Bare mentions do not prove competence. Unknown
requirements need manual review; when none are recognized the score is **N/A**.
The initial rules target English-language Dubai vacancies; other languages require
manual review. Add vocabulary in `CATALOG`, normalization in `normalize`, and
regressions in `tests/test_matcher.py` when extending coverage.

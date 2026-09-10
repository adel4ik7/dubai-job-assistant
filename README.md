# Dubai Job Assistant — AI Telegram assistant

Telegram bot for:
- uploading a CV (PDF/DOCX/TXT);
- extracting CV text;
- pasting a vacancy;
- calculating a simple keyword match score;
- highlighting matching and missing keywords;
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

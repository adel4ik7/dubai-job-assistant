# Dubai Job Assistant

When I moved to Dubai, looking for work meant going through Telegram groups, checking vacancies and trying to keep track of useful posts. I started this project to bring that work into one place.

Dubai Job Assistant is a Telegram tool for finding vacancies, organising a CV and keeping track of applications. I am building it with AI tools while learning Python and how the different parts of an application fit together.

## What it does

- Collects posts from configured public Telegram sources and filters them for vacancies.
- Helps search and filter jobs, with duplicate handling.
- Lets users save vacancies and track applications.
- Includes CV creation, upload and vacancy comparison tools.
- Offers personal job alerts and an English or Russian interface.

It is a working project that I am still improving. Vacancy detection and CV matching use local rules, so results need a human check. A match score is only a guide, not an official ATS score.

## How it works

There are two main Python processes:

- **Collector** reads configured public Telegram sources and stores the results in SQLite.
- **Bot** provides the menus, searches, CV tools and application tracker.

The current setup runs locally. The computer needs to stay awake and connected for collection and notifications to continue. It does not apply for jobs or message employers automatically.

Optional local OCR can read text from vacancy images. The current v0.4 bot does not make OpenAI API calls.

## Run it locally

You need Python 3.10 or newer. The project has been tested on Windows with Python 3.12.

From the project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you do not already have a `.env` file, copy `.env.example` to `.env`. Keep an existing configuration rather than overwriting it.

Create a bot through Telegram's **@BotFather** and put its token in `TELEGRAM_BOT_TOKEN` in your local `.env`.

For the collector, get your own API credentials from [my.telegram.org](https://my.telegram.org) and set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. The first collector login asks for your phone, verification code and, if enabled, Telegram 2FA password locally.

Then start these in separate terminals:

```powershell
# Bot
.\.venv\Scripts\python.exe bot.py
```

```powershell
# Collector
.\.venv\Scripts\python.exe collector.py
```

Open a private chat with your bot and send `/start`. Run only one instance of each process. Stop them with Ctrl+C.

Tokens, Telegram session files and CVs should stay private and out of Git.

## What I am learning through this

This project gives me practical problems to work on: storing data, filtering messy posts, handling duplicates and keeping a long-running process working. I am still developing my Python skills, and AI assistance is part of the development process.

## More detail

The [technical notes](TECHNICAL_NOTES.md) contain source configuration, OCR setup, alert behaviour, data controls, troubleshooting and test commands.

Users can inspect the data explanation with `/my_data` and request deletion through `/delete_my_data`. Job alerts are off until the user enables them.

# AGENTS.md

You are developing Dubai Job Assistant, a Telegram product for job seekers.

## Development rules
- Work autonomously in small milestones.
- Preserve existing working features.
- Never hardcode Telegram tokens or API keys.
- Never print secrets to logs.
- Store secrets only in `.env`, which is gitignored.
- Prefer simple, maintainable Python over clever abstractions.
- Keep the project runnable on a normal Windows laptop.
- Use SQLite until scale justifies another database.
- After meaningful changes:
  1. run `python -m compileall .`;
  2. run tests if present;
  3. update `TODO.md`;
  4. summarize what changed.
- If a task is unfinished because of limits/errors, record exact next steps in `TODO.md`.
- Do not delete user files or rewrite unrelated files without need.
- Treat CV content as sensitive data.
- Do not log raw CV content.
- Do not claim heuristic job-match scores are official ATS scores.

## Product direction
The product should evolve from:
Telegram MVP -> AI job assistant -> paid Telegram service -> Mini App.

The primary user outcome is:
"Help me apply faster and improve the quality of each application."

# Master prompt for Codex

Continue development of the local project **Dubai Job Assistant**.

First inspect the entire repository, especially `README.md`, `AGENTS.md`, `TODO.md`, `.gitignore`, and the current Git status. Do not assume the project is empty.

Your job is to take the project to the next stable milestone while preserving all working functionality.

## Working protocol

1. Read `TODO.md` and start from the first incomplete high-priority item.
2. Before modifying code, run a basic sanity check such as `python -m compileall .` and inspect obvious startup errors.
3. Make small coherent changes.
4. Never hardcode, expose, print, commit, or overwrite Telegram tokens or API keys.
5. Use `.env` for secrets.
6. Treat uploaded CVs as sensitive. Never include raw CV text in logs.
7. Prefer simple maintainable Python.
8. Keep Windows compatibility.
9. After every milestone:
   - run sanity checks/tests;
   - update `TODO.md`;
   - if Git is available, create a clear commit;
   - write the next exact action in `TODO.md`.
10. If your session or usage limit is about to end, stop starting new features and leave the repository in a runnable state. Update `TODO.md` with:
    - what is complete;
    - what is partially complete;
    - exact file/function to continue from;
    - the command needed to test it.

## Next target

Implement **Milestone 1 — AI layer** behind a clean provider/service interface.

Requirements:
- Bot must still start even if `OPENAI_API_KEY` is absent.
- Existing heuristic vacancy analysis must remain available as fallback.
- Add AI actions for:
  - CV review;
  - vacancy-specific CV improvement suggestions;
  - cover-letter draft;
  - interview questions.
- Do not invent experience, skills, qualifications or achievements for the candidate.
- Clearly separate candidate facts from suggestions.
- Add reasonable input-size limits and graceful errors.
- Add minimal automated tests for non-network logic.
- Do not add payment features until Milestone 1 is stable.

When finished, leave the repository runnable and update `TODO.md`.

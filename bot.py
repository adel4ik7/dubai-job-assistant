import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import Settings, load_settings
from db import Database
from services.ai import AIError, AIService, OpenAIProvider, TASKS, message_chunks
from services.matcher import analyse_match, format_analysis
from services.resume_parser import ResumeParseError, extract_text

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)
# HTTP request URLs can contain Telegram tokens. Never log transport details.
for logger_name in ("httpx", "httpcore", "telegram", "pypdf"):
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

settings: Settings
db: Database
ai: AIService

WAIT_VACANCY = 1
WAIT_APPLICATION = 2
WAIT_AI_VACANCY = 3

MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📄 Upload CV", callback_data="upload_cv")],
    [InlineKeyboardButton("🎯 Analyse vacancy", callback_data="analyse_vacancy")],
    [InlineKeyboardButton("✨ AI assistant", callback_data="ai_menu")],
    [InlineKeyboardButton("➕ Add application", callback_data="add_application")],
    [InlineKeyboardButton("📋 My applications", callback_data="my_applications")],
    [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
])


def user_id(update: Update) -> int:
    return update.effective_user.id


async def ensure_user(update: Update) -> None:
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("ai_action", None)
    await ensure_user(update)
    text = (
        "🇦🇪 *Dubai Job Assistant*\n\n"
        "Upload your CV, compare it with a vacancy and track your applications.\n\n"
        "MVP runs locally on the owner's computer. Your CV is stored locally there."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("ai_action", None)
    await ensure_user(update)
    await update.message.reply_text("Choose an action:", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    await update.message.reply_text(
        "Commands:\n"
        "/start — open the bot\n"
        "/menu — main menu\n"
        "/cancel — cancel the current action\n\n"
        "CV formats: PDF, DOCX, TXT.\n"
        "Analyse vacancy is a local heuristic, not an official ATS score.\n"
        "AI assistant: CV review, bullet improvements, cover letter and interview questions.\n"
        "AI actions send CV text and any vacancy to OpenAI after your confirmation."
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await ensure_user(update)

    if query.data == "ai_menu":
        context.user_data.pop("ai_action", None)
        if not ai.available:
            await query.message.reply_text("AI is disabled. Analyse vacancy and the tracker remain available.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        await query.message.reply_text(
            "AI actions send your saved CV text and any vacancy to OpenAI. "
            "Remove sensitive details from your CV before uploading if needed. "
            f"Limit: {ai.daily_limit} attempts per day (UTC), including failed requests. "
            "Drafts can contain errors; verify all claims. Choose an action:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(label, callback_data="ai_" + action)]
                for action, label in (("review", "Send CV for review"),
                                      ("improve", "Improve CV bullets"),
                                      ("letter", "Draft cover letter"),
                                      ("interview", "Interview questions"))
            ]),
        )
        return ConversationHandler.END

    if query.data.startswith("ai_"):
        action = query.data.removeprefix("ai_")
        context.user_data.pop("ai_action", None)
        if action not in TASKS or not ai.available:
            await query.message.reply_text("AI is unavailable. Use Analyse vacancy.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        if not db.latest_resume(user_id(update)):
            await query.message.reply_text("Upload a CV first.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        if action == "review":
            return await run_ai(update, context, action)
        context.user_data["ai_action"] = action
        await query.message.reply_text(
            "Paste a vacancy (100–12,000 characters) in one message. "
            "Sending it confirms that your CV text and this vacancy will be sent to OpenAI. "
            "Use /cancel to stop."
        )
        return WAIT_AI_VACANCY

    context.user_data.pop("ai_action", None)

    if query.data == "upload_cv":
        await query.message.reply_text("Send me your CV as PDF, DOCX or TXT.")
        return ConversationHandler.END

    if query.data == "analyse_vacancy":
        resume = db.latest_resume(user_id(update))
        if not resume:
            await query.message.reply_text("Upload a CV first.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        await query.message.reply_text("Paste the full vacancy description in one message.")
        return WAIT_VACANCY

    if query.data == "add_application":
        await query.message.reply_text(
            "Send one line in this format:\n\nCompany | Role | optional note\n\n"
            "Example:\nAcme Hospitality | Operations Assistant | Applied via LinkedIn"
        )
        return WAIT_APPLICATION

    if query.data == "my_applications":
        rows = db.list_applications(user_id(update))
        if not rows:
            await query.message.reply_text("No applications saved yet.", reply_markup=MAIN_MENU)
            return ConversationHandler.END

        lines = ["📋 *Applications*"]
        for row in rows:
            lines.append(
                f"\n#{row['id']} — *{row['company']}*\n"
                f"{row['role']} · {row['status']}"
                + (f"\n_{row['notes']}_" if row["notes"] else "")
            )
        lines.append("\n\nChange status: `/status ID STATUS`\nExample: `/status 3 Interview`")
        await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    if query.data == "help":
        await query.message.reply_text(
            "1. Upload CV\n"
            "2. Paste a vacancy\n"
            "3. Get a match report\n"
            "4. Save applications and update their status\n\n"
            "AI assistant can review your CV and draft application materials. "
            "AI actions send text to OpenAI; verify the resulting drafts."
        )
        return ConversationHandler.END

    return ConversationHandler.END


async def receive_cv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    doc = update.message.document
    if not doc:
        return

    original_name = doc.file_name or "resume"
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        await update.message.reply_text("Supported formats: PDF, DOCX, TXT.")
        return

    tg_file = await doc.get_file()
    safe_name = f"{user_id(update)}_{doc.file_unique_id}{suffix}"
    path = settings.uploads_dir / safe_name
    await tg_file.download_to_drive(custom_path=path)

    try:
        text = extract_text(path)
    except ResumeParseError as exc:
        await update.message.reply_text(f"Could not read CV: {exc}")
        return
    except Exception:
        log.error("CV parse failed; details omitted for privacy")
        await update.message.reply_text("Could not parse this file.")
        return

    db.add_resume(user_id(update), original_name, str(path), text)
    await update.message.reply_text(
        f"✅ CV saved: {original_name}\nExtracted {len(text):,} characters.",
        reply_markup=MAIN_MENU,
    )


async def vacancy_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await ensure_user(update)
    vacancy = (update.message.text or "").strip()
    if len(vacancy) < 100:
        await update.message.reply_text("The vacancy text is too short. Paste the full description.")
        return WAIT_VACANCY

    resume = db.latest_resume(user_id(update))
    if not resume:
        await update.message.reply_text("CV not found. Upload it again.")
        return ConversationHandler.END

    result = analyse_match(resume["extracted_text"], vacancy)
    await update.message.reply_text(format_analysis(result), reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def application_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await ensure_user(update)
    raw = (update.message.text or "").strip()
    parts = [p.strip() for p in raw.split("|", maxsplit=2)]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "Use: Company | Role | optional note\nExample: Acme | Analyst | LinkedIn"
        )
        return WAIT_APPLICATION

    company, role = parts[0], parts[1]
    notes = parts[2] if len(parts) == 3 else ""
    app_id = db.add_application(user_id(update), company, role, notes)

    await update.message.reply_text(
        f"✅ Application #{app_id} saved.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Use: /status ID STATUS\nExample: /status 3 Interview")
        return

    app_id = int(context.args[0])
    status = " ".join(context.args[1:]).strip()[:40]
    ok = db.update_application_status(user_id(update), app_id, status)
    await update.message.reply_text("✅ Status updated." if ok else "Application not found.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("ai_action", None)
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Request failed; details omitted for privacy")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong. Open /menu and try again.")


async def run_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, vacancy: str = "") -> int:
    resume = db.latest_resume(user_id(update))
    message = update.effective_message
    if not resume:
        await message.reply_text("Upload a CV first.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    await message.reply_text("Preparing your AI draft…")
    try:
        result = await ai.run(user_id(update), action, resume["extracted_text"], vacancy)
    except AIError as exc:
        await message.reply_text(str(exc), reply_markup=MAIN_MENU)
    else:
        chunks = message_chunks(result)
        for index, chunk in enumerate(chunks):
            await message.reply_text(chunk, reply_markup=MAIN_MENU if index == len(chunks) - 1 else None)
    return ConversationHandler.END


async def ai_vacancy_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    vacancy = (update.message.text or "").strip()
    if not 100 <= len(vacancy) <= 12000:
        await update.message.reply_text("Paste 100–12,000 characters, or /cancel.")
        return WAIT_AI_VACANCY
    action = context.user_data.pop("ai_action", None)
    if action not in TASKS:
        await update.message.reply_text("Open AI assistant and choose an action again.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    return await run_ai(update, context, action, vacancy)


def build_application(config: Settings | None = None) -> Application:
    global settings, db, ai
    settings = config or load_settings()
    db = Database(settings.database_path)
    provider = OpenAIProvider(settings.openai_api_key, settings.openai_model) if settings.openai_api_key else None
    ai = AIService(provider, db, settings.ai_daily_limit)
    app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(False).build()

    conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(buttons), CommandHandler("start", start),
                      CommandHandler("menu", menu), CommandHandler("cancel", cancel)],
        states={
            WAIT_AI_VACANCY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_vacancy_received)
            ],
            WAIT_VACANCY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vacancy_received)
            ],
            WAIT_APPLICATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, application_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.Document.ALL, receive_cv))
    app.add_handler(conversation)
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    try:
        app = build_application()
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1) from None
    print("Dubai Job Assistant is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

import logging
from pathlib import Path
from uuid import uuid4

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
from db import Database, STATUSES
from product_ui import ProductUI, WAIT_FORM, WAIT_SEARCH
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
product: ProductUI

WAIT_VACANCY = 1
WAIT_APPLICATION = 2
WAIT_AI_VACANCY = 3

MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("👤 Profile", callback_data="p:profile"), InlineKeyboardButton("📄 CVs", callback_data="p:cvs:0")],
    [InlineKeyboardButton("🎯 Analyse vacancy", callback_data="analyse_vacancy")],
    [InlineKeyboardButton("📋 Applications", callback_data="p:apps:0"), InlineKeyboardButton("📊 Dashboard", callback_data="p:dashboard")],
    [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
])


def user_id(update: Update) -> int:
    return update.effective_user.id


async def ensure_user(update: Update) -> None:
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_pending(context)
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
    reset_pending(context)
    context.user_data.pop("ai_action", None)
    await ensure_user(update)
    await update.message.reply_text("Choose an action:", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update)
    await update.effective_message.reply_text(
        "Commands:\n"
        "/start — open the bot\n"
        "/menu — main menu\n"
        "/cancel — cancel the current action\n\n"
        "CV formats: PDF, DOCX, TXT.\n"
        "Analyse vacancy is a local heuristic, not an official ATS score.\n"
        "Profile — preferences and self-reported facts.\n"
        "CVs — upload, choose active, or delete.\n"
        "Applications — add, search, filter and change status.\n"
        "Dashboard — current application statistics.\n"
        "/status ID STATUS — change application status\n"
        "/delete_my_data — delete your local data after confirmation.\n"
        "Use a private chat. Files and text are stored on the owner's laptop. AI is disabled in v0.3.",
        reply_markup=MAIN_MENU,
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if getattr(getattr(update, "effective_chat", None), "type", "private") != "private":
        await query.message.reply_text("Please use a private chat with the bot.")
        return ConversationHandler.END
    await ensure_user(update)
    handled = await product.buttons(update, context)
    if handled is not None:
        return handled
    reset_pending(context)

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
        if not db.active_resume(user_id(update)):
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
        resume = db.active_resume(user_id(update))
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
        await help_command(update, context)
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
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("Please upload a CV smaller than 5 MB.")
        return

    tg_file = await doc.get_file()
    safe_name = f"{user_id(update)}_{uuid4().hex}{suffix}"
    path = settings.uploads_dir / safe_name
    try:
        await tg_file.download_to_drive(custom_path=path)
        text = extract_text(path)
    except ResumeParseError as exc:
        cleanup_failed_upload(path)
        await update.message.reply_text(f"Could not read CV: {exc}")
        return
    except Exception:
        cleanup_failed_upload(path)
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
    if len(vacancy) > 12000:
        await update.message.reply_text("Use at most 12,000 characters, or /cancel.")
        return WAIT_VACANCY

    resume = db.active_resume(user_id(update))
    if not resume:
        await update.message.reply_text("CV not found. Upload it again.")
        return ConversationHandler.END

    await product.report(update, context, vacancy, resume)
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
    if status not in STATUSES:
        await update.message.reply_text("Choose a status: " + ", ".join(STATUSES))
        return
    ok = db.update_application_status(user_id(update), app_id, status)
    await update.message.reply_text("✅ Status updated." if ok else "Application not found.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_pending(context)
    context.user_data.pop("ai_action", None)
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Request failed; details omitted for privacy")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong. Open /menu and try again.")


async def run_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, vacancy: str = "") -> int:
    resume = db.active_resume(user_id(update))
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


def reset_pending(context) -> None:
    for key in ("form", "delete_confirmation", "cv_delete", "ai_action"):
        context.user_data.pop(key, None)


def cleanup_failed_upload(path: Path) -> None:
    # Only called with a freshly generated path inside settings.uploads_dir.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.error("Failed upload cleanup incomplete; delete-my-data can retry")


async def cv_received(update, context):
    reset_pending(context)
    await receive_cv(update, context)
    return ConversationHandler.END


def build_application(config: Settings | None = None) -> Application:
    global settings, db, ai, product
    settings = config or load_settings()
    db = Database(settings.database_path)
    # v0.3 is strictly local, even if an old .env still contains an API key.
    ai = AIService(None, db, settings.ai_daily_limit)
    product = ProductUI(db, settings.uploads_dir, MAIN_MENU)
    app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(False).build()

    conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(buttons), CommandHandler("start", start, filters=filters.ChatType.PRIVATE),
                      CommandHandler("menu", menu, filters=filters.ChatType.PRIVATE), CommandHandler("cancel", cancel, filters=filters.ChatType.PRIVATE),
                      CommandHandler("delete_my_data", product.delete_my_data, filters=filters.ChatType.PRIVATE),
                      MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, cv_received)],
        states={
            WAIT_FORM: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, product.form_received)],
            WAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, product.search_received)],
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

    app.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("status", status_command, filters=filters.ChatType.PRIVATE))
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

from locales import text as locale_text, status_label
from statuses import normalize_status
import logging
from pathlib import Path
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from config import Settings, load_settings
from db import Database, STATUSES
from product_ui import ProductUI, WAIT_FORM, WAIT_SEARCH
from services.ai import AIError, AIService, OpenAIProvider, TASKS, message_chunks
from services.matcher import analyse_match, format_analysis
from services.resume_parser import ResumeParseError, extract_text
logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s: %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)
for logger_name in ('httpx', 'httpcore', 'telegram', 'pypdf'):
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)
settings: Settings
db: Database
ai: AIService
product: ProductUI
WAIT_VACANCY = 1
WAIT_APPLICATION = 2
WAIT_AI_VACANCY = 3
def make_main_menu(language='en'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(locale_text(language, 'menu_profile'), callback_data='p:profile'), InlineKeyboardButton(locale_text(language, 'menu_cvs'), callback_data='p:cvs:0')],
        [InlineKeyboardButton(locale_text(language, 'menu_analyse'), callback_data='analyse_vacancy')],
        [InlineKeyboardButton(locale_text(language, 'menu_applications'), callback_data='p:apps:0'), InlineKeyboardButton(locale_text(language, 'menu_dashboard'), callback_data='p:dashboard')],
        [InlineKeyboardButton(locale_text(language, 'menu_help'), callback_data='help'), InlineKeyboardButton(locale_text(language, 'language_button'), callback_data='p:language')]])

MAIN_MENU = make_main_menu()

def user_id(update: Update) -> int:
    return update.effective_user.id

async def ensure_user(update: Update) -> None:
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    reset_pending(context)
    context.user_data.pop('ai_action', None)
    await ensure_user(update)
    if not db.language_selected(user_id(update)):
        return await product.language_menu(update, context)
    text = tr('dubai_job_assistant_upload_your_cv_compare_it_with_a_vacancy')
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=product.menu(update))
    return ConversationHandler.END

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    reset_pending(context)
    context.user_data.pop('ai_action', None)
    await ensure_user(update)
    await update.message.reply_text(tr('choose_an_action'), reply_markup=product.menu(update))
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tr = product.translator(update)
    await ensure_user(update)
    await update.effective_message.reply_text(tr('commands_start_open_the_bot_menu_main_menu_cancel_cancel_the'), reply_markup=product.menu(update))

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    query = update.callback_query
    await query.answer()
    if getattr(getattr(update, 'effective_chat', None), 'type', 'private') != 'private':
        await query.message.reply_text(tr('please_use_a_private_chat_with_the_bot'))
        return ConversationHandler.END
    await ensure_user(update)
    handled = await product.buttons(update, context)
    if handled is not None:
        return handled
    reset_pending(context)
    if query.data == 'ai_menu':
        context.user_data.pop('ai_action', None)
        if not ai.available:
            await query.message.reply_text(tr('ai_is_disabled_analyse_vacancy_and_the_tracker_remain_availa'), reply_markup=product.menu(update))
            return ConversationHandler.END
        await query.message.reply_text(tr('ai_actions_send_your_saved_cv_text_and_any_vacancy_to_openai', v0=ai.daily_limit), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data='ai_' + action)] for action, label in (('review', tr('send_cv_for_review')), ('improve', tr('improve_cv_bullets')), ('letter', tr('draft_cover_letter')), ('interview', tr('interview_questions')))]))
        return ConversationHandler.END
    if query.data.startswith('ai_'):
        action = query.data.removeprefix('ai_')
        context.user_data.pop('ai_action', None)
        if action not in TASKS or not ai.available:
            await query.message.reply_text(tr('ai_is_unavailable_use_analyse_vacancy'), reply_markup=product.menu(update))
            return ConversationHandler.END
        if not db.active_resume(user_id(update)):
            await query.message.reply_text(tr('upload_a_cv_first'), reply_markup=product.menu(update))
            return ConversationHandler.END
        if action == 'review':
            return await run_ai(update, context, action)
        context.user_data['ai_action'] = action
        await query.message.reply_text(tr('paste_a_vacancy_100_12_000_characters_in_one_message_sending'))
        return WAIT_AI_VACANCY
    context.user_data.pop('ai_action', None)
    if query.data == 'upload_cv':
        await query.message.reply_text(tr('send_me_your_cv_as_pdf_docx_or_txt'))
        return ConversationHandler.END
    if query.data == 'analyse_vacancy':
        resume = db.active_resume(user_id(update))
        if not resume:
            await query.message.reply_text(tr('upload_a_cv_first'), reply_markup=product.menu(update))
            return ConversationHandler.END
        await query.message.reply_text(tr('paste_the_full_vacancy_description_in_one_message'))
        return WAIT_VACANCY
    if query.data == 'add_application':
        await query.message.reply_text(tr('send_one_line_in_this_format_company_role_optional_note_exam'))
        return WAIT_APPLICATION
    if query.data == 'help':
        await help_command(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

async def receive_cv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tr = product.translator(update)
    await ensure_user(update)
    doc = update.message.document
    if not doc:
        return
    original_name = doc.file_name or 'resume'
    suffix = Path(original_name).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.txt'}:
        await update.message.reply_text(tr('supported_formats_pdf_docx_txt'))
        return
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text(tr('please_upload_a_cv_smaller_than_5_mb'))
        return
    tg_file = await doc.get_file()
    safe_name = f'{user_id(update)}_{uuid4().hex}{suffix}'
    path = settings.uploads_dir / safe_name
    try:
        await tg_file.download_to_drive(custom_path=path)
        text = extract_text(path)
    except ResumeParseError as exc:
        cleanup_failed_upload(path)
        await update.message.reply_text(tr('error_parse'))
        return
    except Exception:
        cleanup_failed_upload(path)
        log.error('CV parse failed; details omitted for privacy')
        await update.message.reply_text(tr('could_not_parse_this_file'))
        return
    db.add_resume(user_id(update), original_name, str(path), text)
    await update.message.reply_text(tr('cv_saved_v0_extracted_v1_characters', v0=original_name, v1=len(text)), reply_markup=product.menu(update))

async def vacancy_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    await ensure_user(update)
    vacancy = (update.message.text or '').strip()
    if len(vacancy) < 100:
        await update.message.reply_text(tr('the_vacancy_text_is_too_short_paste_the_full_description'))
        return WAIT_VACANCY
    if len(vacancy) > 12000:
        await update.message.reply_text(tr('use_at_most_12_000_characters_or_cancel'))
        return WAIT_VACANCY
    resume = db.active_resume(user_id(update))
    if not resume:
        await update.message.reply_text(tr('cv_not_found_upload_it_again'))
        return ConversationHandler.END
    await product.report(update, context, vacancy, resume)
    return ConversationHandler.END

async def application_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    await ensure_user(update)
    raw = (update.message.text or '').strip()
    parts = [p.strip() for p in raw.split('|', maxsplit=2)]
    if len(parts) < 2 or not parts[0] or (not parts[1]):
        await update.message.reply_text(tr('use_company_role_optional_note_example_acme_analyst_linkedin'))
        return WAIT_APPLICATION
    company, role = (parts[0], parts[1])
    notes = parts[2] if len(parts) == 3 else ''
    app_id = db.add_application(user_id(update), company, role, notes)
    await update.message.reply_text(tr('application_v0_saved', v0=app_id), reply_markup=product.menu(update))
    return ConversationHandler.END

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tr = product.translator(update)
    await ensure_user(update)
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(tr('use_status_id_status_example_status_3_interview'))
        return
    app_id = int(context.args[0])
    status = normalize_status(' '.join(context.args[1:]).strip()[:40])
    if status not in STATUSES:
        await update.message.reply_text(tr('choose_a_status') + ', '.join(status_label(db.get_language(user_id(update)), s) for s in STATUSES))
        return
    ok = db.update_application_status(user_id(update), app_id, status)
    await update.message.reply_text(tr('status_updated') if ok else tr('application_not_found'))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    reset_pending(context)
    context.user_data.pop('ai_action', None)
    await update.message.reply_text(tr('cancelled'), reply_markup=product.menu(update))
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    tr = product.translator(update)
    log.error('Request failed; details omitted for privacy')
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(tr('something_went_wrong_open_menu_and_try_again'))

async def run_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, vacancy: str='') -> int:
    tr = product.translator(update)
    resume = db.active_resume(user_id(update))
    message = update.effective_message
    if not resume:
        await message.reply_text(tr('upload_a_cv_first'), reply_markup=product.menu(update))
        return ConversationHandler.END
    await message.reply_text(tr('preparing_your_ai_draft'))
    try:
        result = await ai.run(user_id(update), action, resume['extracted_text'], vacancy)
    except AIError as exc:
        await message.reply_text(tr('error_ai'), reply_markup=product.menu(update))
    else:
        chunks = message_chunks(result)
        for index, chunk in enumerate(chunks):
            await message.reply_text(chunk, reply_markup=product.menu(update) if index == len(chunks) - 1 else None)
    return ConversationHandler.END

async def ai_vacancy_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tr = product.translator(update)
    vacancy = (update.message.text or '').strip()
    if not 100 <= len(vacancy) <= 12000:
        await update.message.reply_text(tr('paste_100_12_000_characters_or_cancel'))
        return WAIT_AI_VACANCY
    action = context.user_data.pop('ai_action', None)
    if action not in TASKS:
        await update.message.reply_text(tr('open_ai_assistant_and_choose_an_action_again'), reply_markup=product.menu(update))
        return ConversationHandler.END
    return await run_ai(update, context, action, vacancy)

def reset_pending(context) -> None:
    for key in ('form', 'delete_confirmation', 'cv_delete', 'ai_action'):
        context.user_data.pop(key, None)

def cleanup_failed_upload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.error('Failed upload cleanup incomplete; delete-my-data can retry')

async def cv_received(update, context):
    reset_pending(context)
    await receive_cv(update, context)
    return ConversationHandler.END

def build_application(config: Settings | None=None) -> Application:
    global settings, db, ai, product
    settings = config or load_settings()
    db = Database(settings.database_path)
    ai = AIService(None, db, settings.ai_daily_limit)
    product = ProductUI(db, settings.uploads_dir, make_main_menu)
    app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(False).build()
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(buttons),
            CommandHandler('start', start, filters=filters.ChatType.PRIVATE),
            CommandHandler('menu', menu, filters=filters.ChatType.PRIVATE),
            CommandHandler('cancel', cancel, filters=filters.ChatType.PRIVATE),
            CommandHandler('language', product.language_menu, filters=filters.ChatType.PRIVATE),
            CommandHandler('delete_my_data', product.delete_my_data, filters=filters.ChatType.PRIVATE),
            MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, cv_received),
        ],
        states={
            WAIT_FORM: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, product.form_received)],
            WAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, product.search_received)],
            WAIT_AI_VACANCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ai_vacancy_received)],
            WAIT_VACANCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacancy_received)],
            WAIT_APPLICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, application_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler('help', help_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler('status', status_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(conversation)
    app.add_error_handler(error_handler)
    return app

def main() -> None:
    try:
        app = build_application()
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1) from None
    print(locale_text('en', 'operator_running'))
    app.run_polling(drop_pending_updates=True)
if __name__ == '__main__':
    main()

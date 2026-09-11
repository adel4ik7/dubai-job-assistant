"""v0.3 private-chat product screens. No network services beyond Telegram replies."""
from locales import translator, field_labels, status_label, value_label
import secrets
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler
from db import PROFILE_FIELDS, STATUSES
from services.ai import message_chunks
from services.matcher import analyse_match, format_analysis
from services.product import APP_LABELS, ENGLISH_LEVELS, PROFILE_LABELS, PrivacyError, UserFiles, profile_gaps, validate_field
WAIT_FORM, WAIT_SEARCH = (10, 11)
END = ConversationHandler.END

def keyboard(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])

class ProductUI:

    def __init__(self, db, uploads_dir, main_menu):
        self.db, self.menu_factory = (db, main_menu)
        self.files = UserFiles(db, uploads_dir)


    def language(self, update):
        user = getattr(update, 'effective_user', None)
        return self.db.get_language(user.id) if user else 'en'

    def translator(self, update):
        return translator(self.language(update))

    def menu(self, update):
        return self.menu_factory(self.language(update))

    async def language_menu(self, update, context):
        tr = self.translator(update)
        for key in ('form', 'cv_delete', 'delete_confirmation', 'ai_action', 'vacancy_input', 'builder_form', 'builder_delete'):
            context.user_data.pop(key, None)
        await self.reply(update, tr('language_prompt'), keyboard([
            [(tr('language_en'), 'p:lang:en')], [(tr('language_ru'), 'p:lang:ru')]]))
        return END

    async def reply(self, update, text, markup=None):
        chunks = message_chunks(text)
        for index, chunk in enumerate(chunks):
            await update.effective_message.reply_text(chunk, reply_markup=markup if index == len(chunks) - 1 else None)

    async def profile(self, update):
        tr = self.translator(update)
        profile = self.db.get_profile(update.effective_user.id)
        text = tr('profile') + ('\n'.join((f"{label}: {value_label(self.language(update), key, profile[key]) or tr('not_specified')}" for key, label in field_labels(self.language(update), PROFILE_FIELDS).items())) if profile else tr('create_your_profile_to_check_application_preferences'))
        rows = [[(tr('edit') + label, 'p:edit:' + key)] for key, label in field_labels(self.language(update), PROFILE_FIELDS).items()] if profile else [[(tr('create_profile'), 'p:create')]]
        rows.append([(tr('language_button'), 'p:language')])
        rows.append([(tr('main_menu'), 'p:home')])
        await self.reply(update, text, keyboard(rows))

    async def prompt(self, update, context):
        tr = self.translator(update)
        form = context.user_data['form']
        field = form['fields'][form['index']]
        labels = field_labels(self.language(update), PROFILE_FIELDS if form['kind'] == 'profile' else APP_LABELS)
        rows = []
        choices = STATUSES if field == 'status' else ENGLISH_LEVELS if field == 'english_level' else ()
        if choices:
            rows = [[(value_label(self.language(update), field, choice), f"p:choice:{form['nonce']}:{index}")] for index, choice in enumerate(choices)]
        if field in form['values'] and form['values'][field]:
            rows.insert(0, [(tr('v_keep_value', value=value_label(self.language(update), field, form['values'][field])[:70]), f"p:keep:{form['nonce']}")])
        if field not in {'company', 'role', 'full_name', 'status'}:
            rows.append([(tr('skip_clear'), f"p:skip:{form['nonce']}")])
        rows.append([(tr('cancel'), 'p:home')])
        await self.reply(update, tr('v0_v1_v2_send_a_value_or_use_the_buttons_cancel_stops_withou', v0=form['index'] + 1, v1=len(form['fields']), v2=labels[field]), keyboard(rows))

    async def begin_form(self, update, context, kind, fields, defaults=None):
        context.user_data['form'] = {'kind': kind, 'fields': list(fields), 'index': 0, 'values': defaults or {}, 'nonce': secrets.token_hex(4)}
        await self.prompt(update, context)
        return WAIT_FORM

    async def form_received(self, update, context, value=None):
        tr = self.translator(update)
        form = context.user_data.get('form')
        if not form:
            await self.reply(update, tr('this_form_expired_open_menu'), self.menu(update))
            return END
        field = form['fields'][form['index']]
        try:
            value = validate_field(field, update.message.text if value is None else value, language=self.language(update))
        except ValueError as exc:
            await self.reply(update, str(exc))
            return WAIT_FORM
        form['values'][field] = value
        form['index'] += 1
        form['nonce'] = secrets.token_hex(4)
        if form['index'] < len(form['fields']):
            await self.prompt(update, context)
            return WAIT_FORM
        user_id = update.effective_user.id
        if form['kind'] == 'profile':
            self.db.save_profile(user_id, **form['values'])
            context.user_data.pop('form', None)
            await self.profile(update)
        else:
            app_id = self.db.add_application(user_id, **form['values'])
            if form['values'].get('vacancy_text') and context.user_data.get('analysis'):
                context.user_data['analysis']['saved'] = True
            context.user_data.pop('form', None)
            await self.reply(update, tr('application_v0_saved_2', v0=app_id), self.menu(update))
        return END

    async def cvs(self, update, context, page=0, compare=False):
        tr = self.translator(update)
        rows = self.db.list_resumes(update.effective_user.id, limit=6, offset=page * 5)
        buttons = []
        for row in rows[:5]:
            prefix = tr('compare') if compare else '★ ' if row['active'] else ''
            callback = f"p:{('compare' if compare else 'cv')}:{row['id']}"
            if compare:
                if not context.user_data.get('analysis'):
                    await self.reply(update, tr('analyse_a_vacancy_first'), self.menu(update))
                    return
                callback += ':' + context.user_data['analysis']['token']
            buttons.append([(f"{prefix}#{row['id']} {row['filename'][:45]}", callback)])
        if page:
            buttons.append([(tr('previous'), f"p:{('comparepage' if compare else 'cvs')}:{page - 1}")])
        if len(rows) > 5:
            buttons.append([(tr('next'), f"p:{('comparepage' if compare else 'cvs')}:{page + 1}")])
        if not compare:
            buttons.append([(tr('upload_cv'), 'upload_cv')])
        buttons.append([(tr('main_menu'), 'p:home')])
        await self.reply(update, tr('choose_a_cv_to_compare') if compare else tr('cvs_marks_active_new_uploads_become_active') + (tr('no_cvs_yet') if not rows else tr('select_a_cv_to_activate_or_delete_it')), keyboard(buttons))

    async def applications(self, update, context, page=0):
        tr = self.translator(update)
        selection = context.user_data.get('app_filter', {})
        rows = self.db.list_applications(update.effective_user.id, 6, offset=page * 5, **selection)
        filters = [(tr('field_status'), status_label(self.language(update), selection['status']))] if selection.get('status') else []
        if selection.get('search'):
            filters.append((tr('search'), selection['search']))
        text = tr('applications') + (tr('filter_v0', v0='; '.join(f'{label}: {value}' for label, value in filters)) if filters else '')
        buttons = []
        for row in rows[:5]:
            text += f"\n#{row['id']} {row['company']} — {row['role']} · {status_label(self.language(update), row['status'])}"
            buttons.append([(tr('open_v0', v0=row['id']), f"p:app:{row['id']}")])
        if not rows:
            text += tr('no_applications_found')
        if page:
            buttons.append([(tr('previous'), f'p:apps:{page - 1}')])
        if len(rows) > 5:
            buttons.append([(tr('next'), f'p:apps:{page + 1}')])
        buttons += [[(tr('add'), 'p:add'), (tr('filter'), 'p:filter'), (tr('search'), 'p:search')], [(tr('clear_filters'), 'p:clear'), (tr('main_menu'), 'p:home')]]
        await self.reply(update, text, keyboard(buttons))

    async def search_received(self, update, context):
        tr = self.translator(update)
        value = (update.message.text or '').strip()
        if not 1 <= len(value) <= 100:
            await self.reply(update, tr('enter_1_100_characters_or_cancel'))
            return WAIT_SEARCH
        context.user_data.setdefault('app_filter', {})['search'] = value
        await self.applications(update, context)
        return END

    async def report(self, update, context, vacancy, resume):
        tr = self.translator(update)
        token = secrets.token_hex(4)
        context.user_data['analysis'] = {'vacancy': vacancy, 'resume_id': resume['id'], 'token': token}
        markup = keyboard([[(tr('save_vacancy_application'), f'p:save:{token}')], [(tr('compare_with_another_cv'), f'p:other:{token}')], [(tr('most_important_gaps'), f'p:gaps:{token}'), (tr('profile_gaps'), f'p:profilegaps:{token}')], [(tr('main_menu'), 'p:home')]])
        await self.reply(update, tr('cv_v0_v1', v0=resume['id'], v1=resume['filename']) + format_analysis(analyse_match(resume['extracted_text'], vacancy, language=self.language(update)), language=self.language(update)), markup)

    async def delete_my_data(self, update, context):
        tr = self.translator(update)
        context.user_data.pop('form', None)
        context.user_data.pop('cv_delete', None)
        context.user_data.pop('ai_action', None)
        token = secrets.token_hex(8)
        context.user_data['delete_confirmation'] = (token, time.monotonic())
        await self.reply(update, tr('delete_all_your_locally_stored_profile_cvs_files_application'), keyboard([[(tr('yes_delete_my_data'), 'p:erase:' + token)], [(tr('cancel'), 'p:home')]]))
        return END

    async def buttons(self, update, context):
        tr = self.translator(update)
        data = update.callback_query.data
        if data == 'my_applications':
            data = 'p:apps:0'
        if not data.startswith('p:'):
            return None
        parts = data.split(':')
        action = parts[1]
        user_id = update.effective_user.id
        if action != 'erase':
            context.user_data.pop('delete_confirmation', None)
        if action not in {'deletecv', 'erasecv'}:
            context.user_data.pop('cv_delete', None)
        if action not in {'skip', 'choice', 'keep'}:
            context.user_data.pop('form', None)
        try:
            if action == 'language':
                return await self.language_menu(update, context)
            elif action == 'lang':
                self.db.set_language(user_id, parts[2])
                tr = self.translator(update)
                await self.reply(update, tr('language_saved'), self.menu(update))
            elif action == 'home':
                await self.reply(update, tr('choose_a_section'), self.menu(update))
            elif action == 'profile':
                await self.profile(update)
            elif action == 'create':
                return await self.begin_form(update, context, 'profile', PROFILE_FIELDS)
            elif action == 'edit' and parts[2] in PROFILE_FIELDS:
                return await self.begin_form(update, context, 'profile', [parts[2]])
            elif action in {'skip', 'choice', 'keep'}:
                form = context.user_data.get('form')
                if not form or parts[2] != form['nonce']:
                    await self.reply(update, tr('this_button_expired_use_the_latest_form'))
                    return WAIT_FORM if form else END
                field = form['fields'][form['index']]
                choices = STATUSES if field == 'status' else ENGLISH_LEVELS if field == 'english_level' else ()
                value = form['values'][field] if action == 'keep' else '-' if action == 'skip' else choices[int(parts[3])]
                return await self.form_received(update, context, value)
            elif action in {'cvs', 'comparepage'}:
                await self.cvs(update, context, max(0, int(parts[2])), action == 'comparepage')
            elif action == 'cv':
                resume = self.db.get_resume(user_id, int(parts[2]))
                if not resume:
                    await self.reply(update, tr('cv_not_found'), self.menu(update))
                else:
                    await self.reply(update, tr('cv_v0_v1_2', v0=resume['id'], v1=resume['filename']), keyboard([[(tr('set_active'), f"p:active:{resume['id']}")], [(tr('delete_cv'), f"p:deletecv:{resume['id']}")], [(tr('back_to_cvs'), 'p:cvs:0')]]))
            elif action == 'active':
                ok = self.db.select_resume(user_id, int(parts[2]))
                await self.reply(update, tr('active_cv_selected') if ok else tr('cv_not_found'))
                await self.cvs(update, context)
            elif action == 'deletecv':
                resume = self.db.get_resume(user_id, int(parts[2]))
                if resume:
                    token = secrets.token_hex(8)
                    context.user_data['cv_delete'] = (token, resume['id'], time.monotonic())
                    await self.reply(update, tr('delete_cv_v0_and_its_local_file', v0=resume['id']), keyboard([[(tr('confirm_deletion'), 'p:erasecv:' + token)], [(tr('cancel'), 'p:cvs:0')]]))
            elif action == 'erasecv':
                pending = context.user_data.pop('cv_delete', None)
                if pending and pending[0] == parts[2] and (time.monotonic() - pending[2] < 300):
                    self.files.delete_cv(user_id, pending[1])
                    context.user_data.pop('analysis', None)
                    await self.cvs(update, context)
                else:
                    await self.reply(update, tr('confirmation_expired_open_cvs_again'))
            elif action == 'add':
                return await self.begin_form(update, context, 'application', APP_LABELS)
            elif action == 'apps':
                await self.applications(update, context, max(0, int(parts[2])))
            elif action == 'clear':
                context.user_data.pop('app_filter', None)
                await self.applications(update, context)
            elif action == 'filter':
                await self.reply(update, tr('filter_by_status'), keyboard([[(status_label(self.language(update), s), f'p:filterstatus:{i}')] for i, s in enumerate(STATUSES)]))
            elif action == 'filterstatus':
                context.user_data.setdefault('app_filter', {})['status'] = STATUSES[int(parts[2])]
                await self.applications(update, context)
            elif action == 'search':
                await self.reply(update, tr('enter_part_of_company_or_role_search_combines_with_the_selec'))
                return WAIT_SEARCH
            elif action == 'app':
                app = self.db.get_application(user_id, int(parts[2]))
                if app:
                    text = tr('application_v0', v0=app['id']) + '\n'.join((f"{label}: {value_label(self.language(update), key, app[key]) or tr('not_specified')}" for key, label in field_labels(self.language(update), APP_LABELS).items()))
                    if app.get('source_url'):
                        text += '\n' + tr('field_source_url') + ': ' + app['source_url']
                    await self.reply(update, text, keyboard([[(tr('change_status'), f"p:status:{app['id']}")], [(tr('back'), 'p:apps:0')]]))
                else:
                    await self.reply(update, tr('application_not_found'))
            elif action == 'status':
                if self.db.get_application(user_id, int(parts[2])):
                    await self.reply(update, tr('choose_status'), keyboard([[(status_label(self.language(update), s), f'p:setstatus:{parts[2]}:{i}')] for i, s in enumerate(STATUSES)]))
            elif action == 'setstatus':
                ok = self.db.update_application_status(user_id, int(parts[2]), STATUSES[int(parts[3])])
                await self.reply(update, tr('status_updated_2') if ok else tr('application_not_found'), self.menu(update))
            elif action == 'dashboard':
                d = self.db.dashboard(user_id)
                await self.reply(update, tr('dashboard') + '\n'.join((f'{label}: {d[key]}' for key, label in (('total', tr('total_applications')), ('active', tr('active_applications')), ('interviews', tr('interviews_tests')), ('offers', tr('offers')), ('rejections', tr('rejections'))))) + tr('current_interview_stage_rate_v0_offer_rate_v1_rates_use_curr', v0=d['interview_rate'], v1=d['offer_rate']), self.menu(update))
            elif action in {'save', 'other', 'gaps', 'profilegaps', 'compare'}:
                analysis = context.user_data.get('analysis')
                if not analysis or (action != 'compare' and parts[2] != analysis['token']):
                    await self.reply(update, tr('analysis_expired_analyse_the_vacancy_again'), self.menu(update))
                    return END
                if action == 'compare' and (len(parts) < 4 or parts[3] != analysis['token']):
                    await self.reply(update, tr('comparison_expired_use_the_latest_analysis'), self.menu(update))
                    return END
                if action == 'save':
                    if analysis.get('saved'):
                        await self.reply(update, tr('this_vacancy_has_already_been_saved'))
                        return END
                    return await self.begin_form(update, context, 'application', APP_LABELS, {'vacancy_text': analysis['vacancy']})
                if action == 'other':
                    await self.cvs(update, context, compare=True)
                elif action == 'compare':
                    resume = self.db.get_resume(user_id, int(parts[2]))
                    if resume:
                        await self.report(update, context, analysis['vacancy'], resume)
                elif action == 'profilegaps':
                    await self.reply(update, profile_gaps(self.db.get_profile(user_id), analysis['vacancy'], language=self.language(update)), self.menu(update))
                else:
                    resume = self.db.get_resume(user_id, analysis['resume_id'])
                    if not resume:
                        await self.reply(update, tr('cv_no_longer_exists_analyse_again'))
                    else:
                        gaps = analyse_match(resume['extracted_text'], analysis['vacancy'], language=self.language(update))['important_gaps']
                        await self.reply(update, tr('important_gaps_not_confirmed_in_cv') + ('\n'.join(('• ' + r['label'] + ': ' + r['reason'] for r in gaps)) or tr('none_identified')), self.menu(update))
            elif action == 'erase':
                pending = context.user_data.pop('delete_confirmation', None)
                if not pending or parts[2] != pending[0] or time.monotonic() - pending[1] >= 300:
                    await self.reply(update, tr('confirmation_expired_use_delete_my_data_again'))
                else:
                    markup = self.menu(update)
                    self.files.delete_all(user_id)
                    context.user_data.clear()
                    await self.reply(update, tr('your_local_data_and_cv_files_have_been_deleted_telegram_hist'), markup)
            return END
        except PrivacyError as exc:
            await self.reply(update, tr(str(exc)), self.menu(update))
            return END
        except (ValueError, IndexError, KeyError):
            await self.reply(update, tr('this_action_is_no_longer_valid_open_menu_and_try_again'), self.menu(update))
            return END

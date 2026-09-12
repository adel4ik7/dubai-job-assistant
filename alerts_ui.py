"""RU/EN alert settings and notifications; existing vacancy actions are reused."""
import json
import asyncio
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler
from locales import translator
from services.job_alerts import JobAlerts, matching_reasons
from vacancy_store import VacancyStore, salary_label
from services.matching_jobs import matching_jobs

WAIT_ALERTS = 16


def keyboard(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


def notification(language, preferences, vacancy):
    tr = translator(language)
    lines = [tr('al_new')]
    for field in ('role', 'company', 'location'):
        if vacancy[field]:
            lines.append(vacancy[field][:200])
    if salary_label(vacancy):
        lines.append(salary_label(vacancy))
    lines += ['', tr('al_why')]
    for reason in matching_reasons(preferences, vacancy):
        lines.append('• ' + tr(reason, location=preferences['location'], salary=preferences['salary_min']))
    vid = vacancy['id']
    return '\n'.join(lines), keyboard([
        [(tr('al_open'), f'v:open:{vid}')],
        [(tr('ap_apply'), f'ap:vacancy:{vid}')],
        [(tr('v_analyse'), f'v:analyse:{vid}')],
        [(tr('v_save'), f'v:save:{vid}'), (tr('v_convert'), f'v:convert:{vid}')],
        [(tr('al_disable'), 'al:off')]])


class AlertsUI:
    def __init__(self, product):
        self.product = product
        self.store = JobAlerts(product.db)
        self.vacancies = VacancyStore(product.db)

    async def menu(self, update, context):
        context.user_data.pop('alert_field', None)
        tr = self.product.translator(update)
        pref = self.store.preferences(update.effective_user.id)
        lines = [tr('al_menu'), tr('al_on' if pref['enabled'] else 'al_off')]
        for field in ('roles', 'keywords', 'location', 'salary_min', 'uae_only'):
            value = pref[field]
            if field in {'roles', 'keywords'}:
                value = ', '.join(json.loads(value))
            elif field == 'uae_only':
                value = tr('al_yes' if value else 'al_no')
            elif field == 'salary_min' and value is not None:
                value = f'{value:,} AED'
            lines.append(tr('al_' + field) + ': ' + (str(value) if value is not None and value != '' else tr('al_unset')))
        lines += ['', tr('al_future_only')]
        rows = [[(tr('al_disable' if pref['enabled'] else 'al_enable'), 'al:off' if pref['enabled'] else 'al:on')],
                [(tr('mj_button'), 'al:matches')],
                [(tr('al_roles'), 'al:edit:roles'), (tr('al_keywords'), 'al:edit:keywords')],
                [(tr('al_location'), 'al:edit:location'), (tr('al_salary_min'), 'al:edit:salary_min')],
                [(tr('al_uae_only'), 'al:uae')], [(tr('main_menu'), 'p:home')]]
        await self.product.reply(update, '\n'.join(lines), keyboard(rows))
        return ConversationHandler.END

    async def buttons(self, update, context):
        tr = self.product.translator(update)
        user = update.effective_user.id
        action = update.callback_query.data.split(':')[1:]
        try:
            if action[0] == 'matches':
                pref = self.store.preferences(user)
                cv = self.product.db.active_resume(user)
                rows, days = await asyncio.to_thread(matching_jobs, self.product.db, pref, cv['extracted_text'] if cv else None)
                context.user_data['matching_jobs'] = {'rows': rows, 'days': days, 'token': secrets.token_hex(4)}
                return await self.match_card(update, context, 0)
            if action[0] in {'page', 'save'}:
                selection = context.user_data.get('matching_jobs')
                if not selection or selection['token'] != action[1]:
                    await self.product.reply(update, tr('v_stale'))
                    return ConversationHandler.END
                if action[0] == 'save':
                    index = int(action[2])
                    if not 0 <= index < len(selection['rows']):
                        raise ValueError('invalid_page')
                    if self.vacancies.save(user, selection['rows'][index]['id']):
                        await self.product.reply(update, tr('mj_saved'))
                return await self.match_card(update, context, int(action[2]))
            if action[0] in {'on', 'off'}:
                self.store.update(user, 'enabled', action[0] == 'on')
            elif action[0] == 'uae':
                self.store.update(user, 'uae_only', not self.store.preferences(user)['uae_only'])
                context.user_data.pop('matching_jobs', None)
            elif action[0] == 'edit' and action[1] in {'roles', 'keywords', 'location', 'salary_min'}:
                context.user_data['alert_field'] = action[1]
                await self.product.reply(update, tr('al_prompt_' + action[1]), keyboard([[(tr('cancel'), 'al:home')]]))
                return WAIT_ALERTS
        except (ValueError, IndexError):
            await self.product.reply(update, tr('al_invalid'))
        return await self.menu(update, context)

    async def match_card(self, update, context, index):
        tr = self.product.translator(update)
        selection = context.user_data['matching_jobs']
        rows = selection['rows']
        if not rows:
            await self.product.reply(update, tr('mj_empty'), keyboard([
                [(tr('mj_edit'), 'al:home')], [(tr('mj_all'), 'v:home')], [(tr('back'), 'al:home')]]))
            return ConversationHandler.END
        if not 0 <= index < len(rows):
            raise ValueError('invalid_page')
        vacancy = rows[index]
        lines = [tr('mj_heading', count=len(rows)), tr('mj_window', days=selection['days'])]
        for field in ('role', 'company', 'location'):
            if vacancy[field]:
                lines.append(tr('v_'+field)+': '+vacancy[field][:200])
        if salary_label(vacancy):
            lines.append(tr('v_salary')+': '+salary_label(vacancy))
        else:
            lines.append(tr('mj_salary_unknown'))
        lines += [tr('v_published')+': '+vacancy['published_at'][:10], tr('v_source')+': '+vacancy['source_title'],
                  tr('mj_high' if vacancy['alert_relevance'] >= 750 else 'mj_medium')]
        if vacancy['cv_score'] is not None:
            lines.append(tr('mj_cv', score=vacancy['cv_score']))
        vid = vacancy['id']
        buttons = []
        if vacancy['source_url']:
            buttons.append([InlineKeyboardButton(tr('mj_source'), url=vacancy['source_url'])])
        buttons += [[InlineKeyboardButton(tr('mj_analyse'), callback_data=f'v:analyse:{vid}')],
                    [InlineKeyboardButton(tr('ap_apply'), callback_data=f'ap:vacancy:{vid}')],
                    [InlineKeyboardButton(tr('mj_save'), callback_data=f'al:save:{selection["token"]}:{index}'),
                     InlineKeyboardButton(tr('mj_apply'), callback_data=f'v:convert:{vid}')]]
        controls = []
        for target, key in ((index-1, 'mj_previous'), (index+1, 'mj_next')):
            if 0 <= target < len(rows):
                controls.append(InlineKeyboardButton(tr(key), callback_data=f'al:page:{selection["token"]}:{target}'))
        if controls:
            buttons.append(controls)
        buttons += [[InlineKeyboardButton(tr('mj_settings'), callback_data='al:home')],
                    [InlineKeyboardButton(tr('back'), callback_data='al:home')]]
        await self.product.reply(update, '\n'.join(lines), InlineKeyboardMarkup(buttons))
        return ConversationHandler.END

    async def receive(self, update, context):
        tr = self.product.translator(update)
        field = context.user_data.get('alert_field')
        try:
            value = update.message.text or ''
            if not field or not 1 <= len(value) <= 1000:
                raise ValueError
            self.store.update(update.effective_user.id, field, value)
        except (TypeError, ValueError):
            await self.product.reply(update, tr('al_invalid'))
            return WAIT_ALERTS
        await self.product.reply(update, tr('al_saved'))
        context.user_data.pop('matching_jobs', None)
        return await self.menu(update, context)

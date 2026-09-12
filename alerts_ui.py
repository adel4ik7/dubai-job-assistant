"""RU/EN alert settings and notifications; existing vacancy actions are reused."""
import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler
from locales import translator
from services.job_alerts import JobAlerts, matching_reasons
from vacancy_store import salary_label

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
        [(tr('v_analyse'), f'v:analyse:{vid}')],
        [(tr('v_save'), f'v:save:{vid}'), (tr('v_convert'), f'v:convert:{vid}')],
        [(tr('al_disable'), 'al:off')]])


class AlertsUI:
    def __init__(self, product):
        self.product = product
        self.store = JobAlerts(product.db)

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
            if action[0] in {'on', 'off'}:
                self.store.update(user, 'enabled', action[0] == 'on')
            elif action[0] == 'uae':
                self.store.update(user, 'uae_only', not self.store.preferences(user)['uae_only'])
            elif action[0] == 'edit' and action[1] in {'roles', 'keywords', 'location', 'salary_min'}:
                context.user_data['alert_field'] = action[1]
                await self.product.reply(update, tr('al_prompt_' + action[1]), keyboard([[(tr('cancel'), 'al:home')]]))
                return WAIT_ALERTS
        except (ValueError, IndexError):
            await self.product.reply(update, tr('al_invalid'))
        return await self.menu(update, context)

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
        return await self.menu(update, context)

"""Shared RU/EN vacancy screens; reuse existing CV analysis and application forms."""
import asyncio
import secrets
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

from services.matcher import analyse_match
from services.product import APP_LABELS
from vacancy_store import VacancyStore, salary_label, application_defaults

WAIT_VACANCY_INPUT = 12
END = ConversationHandler.END


def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


class VacancyUI:
    def __init__(self, product, window=100, admin_id=None):
        self.product, self.db = product, product.db
        self.store = VacancyStore(self.db)
        self.window = min(100, max(1, window))
        self.admin_id = admin_id

    async def menu(self, update, context):
        tr = self.product.translator(update)
        context.user_data.pop('vacancy_input', None)
        rows = [[(tr('v_latest'), 'v:latest'), (tr('v_best'), 'v:best')],
                [(tr('v_search'), 'v:search'), (tr('v_filters'), 'v:filters')],
                [(tr('v_saved'), 'v:saved')], [(tr('main_menu'), 'p:home')]]
        if self.admin_id and update.effective_user.id == self.admin_id:
            rows.insert(-1, [(tr('v_admin'), 'v:admin')])
        await self.product.reply(update, tr('v_menu'), markup(rows))

    async def listing(self, update, context, mode='latest', offset=0):
        tr = self.product.translator(update)
        user = update.effective_user.id
        selection = context.user_data.get('vacancy_filters', {})
        if mode == 'best':
            resume = self.db.active_resume(user)
            if not resume:
                await self.product.reply(update, tr('upload_a_cv_first'))
                return
            candidates = self.store.list(limit=self.window, **selection)
            def rank():
                ranked = [(v, analyse_match(resume['extracted_text'], v['combined_text'][:12000])['score']) for v in candidates]
                return sorted((item for item in ranked if item[1] is not None), key=lambda item: item[1], reverse=True)
            ranked = await asyncio.to_thread(rank)
            context.user_data['vacancy_ranked'] = [(v['id'], score) for v, score in ranked]
            await self.product.reply(update, tr('v_rank_window', count=len(candidates)))
        if mode == 'best' or mode == 'ranked':
            mode = 'ranked'
            ranked = context.user_data.get('vacancy_ranked', [])
            row = self.store.get(ranked[offset][0]) if offset < len(ranked) else None
            score = ranked[offset][1] if row else None
            has_next = offset + 1 < len(ranked)
        else:
            rows = self.store.list(limit=2, offset=offset, saved_user=user if mode == 'saved' else None,
                                   **({} if mode == 'saved' else selection))
            row, score = (rows[0] if rows else None), None
            has_next = len(rows) > 1
        if not row:
            await self.product.reply(update, tr('v_empty'), markup([[(tr('v_back_search'), 'v:search')], [(tr('v_menu'), 'v:home')]]))
            return
        nonce = secrets.token_hex(4)
        context.user_data['vacancy_page'] = (nonce, mode, offset)
        context.user_data['vacancy_navigation'] = {'id': row['id'], 'has_next': has_next, 'relevance': row.get('search_relevance')}
        await self.card(update, context, row, nonce, score)

    async def card(self, update, context, vacancy, nonce=None, score=None):
        tr = self.product.translator(update)
        lines = [tr('v_card', id=vacancy['id'])]
        navigation = context.user_data.get('vacancy_navigation', {})
        page = context.user_data.get('vacancy_page')
        if navigation.get('id') == vacancy['id'] and page:
            nonce = nonce or page[0]
            relevance = navigation.get('relevance')
            if relevance is not None:
                lines.append(tr('v_search_high' if relevance >= 75 else 'v_search_medium'))
        for field in ('role', 'company', 'location'):
            if vacancy[field]:
                lines.append(tr('v_' + field) + ': ' + vacancy[field])
        if salary_label(vacancy):
            lines.append(tr('v_salary') + ': ' + salary_label(vacancy))
        if vacancy['published_at']:
            lines.append(tr('v_published') + ': ' + vacancy['published_at'][:10])
        lines.append(tr('v_source') + ': ' + vacancy['source_title'])
        if score is not None:
            lines.append(tr('v_match_score', score=score))
        if vacancy['detection_status'] == 'probably_vacancy':
            lines.append(tr('v_probably'))
        if vacancy['combined_text']:
            lines.append('\n' + vacancy['combined_text'][:900])
        saved = self.store.is_saved(update.effective_user.id, vacancy['id'])
        rows = [[InlineKeyboardButton(tr('v_analyse'), callback_data=f"v:analyse:{vacancy['id']}")],
                [InlineKeyboardButton(tr('v_remove' if saved else 'v_save'), callback_data=f"v:{'remove' if saved else 'save'}:{vacancy['id']}")],
                [InlineKeyboardButton(tr('v_convert'), callback_data=f"v:convert:{vacancy['id']}")],
                [InlineKeyboardButton(tr('ap_apply'), callback_data=f"ap:vacancy:{vacancy['id']}")]]
        if vacancy['source_url']:
            rows.insert(0, [InlineKeyboardButton(tr('v_open'), url=vacancy['source_url'])])
        if nonce:
            controls = []
            if page and page[2] > 0:
                controls.append(InlineKeyboardButton(tr('previous'), callback_data='v:previous:' + nonce))
            if navigation.get('has_next', True):
                controls.append(InlineKeyboardButton(tr('v_next'), callback_data='v:next:' + nonce))
            if controls:
                rows.append(controls)
        if context.user_data.get('vacancy_filters', {}).get('search'):
            rows.append([InlineKeyboardButton(tr('v_back_search'), callback_data='v:search')])
        rows.append([InlineKeyboardButton(tr('v_menu'), callback_data='v:home')])
        await self.product.reply(update, '\n'.join(lines), InlineKeyboardMarkup(rows))

    async def input_received(self, update, context):
        tr = self.product.translator(update)
        field = context.user_data.get('vacancy_input')
        if field not in {'search', 'location', 'salary_min', 'days'}:
            await self.product.reply(update, tr('this_form_expired_open_menu'))
            return END
        value = (update.message.text or '').strip()
        try:
            if not 1 <= len(value) <= 100:
                raise ValueError
            if value != '-' and field in {'salary_min', 'days'}:
                value = int(value)
                if not 0 <= value <= (365 if field == 'days' else 1000000) or (field == 'days' and value == 0):
                    raise ValueError
        except ValueError:
            await self.product.reply(update, tr('v_invalid_filter'))
            return WAIT_VACANCY_INPUT
        selection = context.user_data.setdefault('vacancy_filters', {})
        if value == '-':
            selection.pop(field, None)
        else:
            selection[field] = value
        context.user_data.pop('vacancy_input', None)
        await self.listing(update, context)
        return END

    async def buttons(self, update, context):
        data = update.callback_query.data
        if not data.startswith('v:'):
            return None
        tr = self.product.translator(update)
        parts = data.split(':')
        action = parts[1]
        try:
            if action == 'home':
                await self.menu(update, context)
            elif action == 'admin':
                await self.admin_stats(update, context)
            elif action in {'latest', 'best', 'saved'}:
                await self.listing(update, context, action)
            elif action in {'next', 'previous'}:
                page = context.user_data.get('vacancy_page')
                if not page or page[0] != parts[2]:
                    await self.product.reply(update, tr('v_stale'))
                else:
                    await self.listing(update, context, page[1], max(0, page[2] + (1 if action == 'next' else -1)))
            elif action == 'filters':
                rows = [[(tr('v_' + field), 'v:input:' + field)] for field in ('location', 'salary_min', 'days')]
                rows += [[(tr('v_source'), 'v:sources')], [(tr('clear_filters'), 'v:clear')], [(tr('v_menu'), 'v:home')]]
                selection = context.user_data.get('vacancy_filters', {})
                summary = '\n'.join(tr('v_' + key) + ': ' + str(value) for key, value in selection.items())
                await self.product.reply(update, tr('v_filters') + '\n' + summary, markup(rows))
            elif action == 'clear':
                context.user_data.pop('vacancy_filters', None)
                await self.listing(update, context)
            elif action == 'search' or action == 'input':
                field = 'search' if action == 'search' else parts[2]
                if field not in {'search', 'location', 'salary_min', 'days'}:
                    raise ValueError
                context.user_data['vacancy_input'] = field
                await self.product.reply(update, tr('v_prompt_' + field))
                return WAIT_VACANCY_INPUT
            elif action == 'sources':
                rows = [[(s['title'], f"v:source:{s['id']}")] for s in self.store.sources(True)]
                rows.append([(tr('v_menu'), 'v:home')])
                await self.product.reply(update, tr('v_source'), markup(rows))
            elif action == 'source':
                context.user_data.setdefault('vacancy_filters', {})['source_id'] = int(parts[2])
                await self.listing(update, context)
            elif action in {'analyse', 'save', 'remove', 'convert'}:
                vacancy = self.store.get(int(parts[2]))
                if not vacancy or vacancy['detection_status'] not in {'vacancy', 'probably_vacancy'}:
                    raise ValueError
                if action == 'analyse':
                    cv = self.db.active_resume(update.effective_user.id)
                    if cv:
                        await self.product.report(update, context, vacancy['combined_text'][:12000], cv)
                    else:
                        await self.product.reply(update, tr('upload_a_cv_first'))
                elif action == 'convert':
                    await self.product.reply(update, tr('v_review_draft'))
                    return await self.product.begin_form(update, context, 'application',
                        [*APP_LABELS, 'source_url'], application_defaults(vacancy))
                else:
                    if action == 'save':
                        self.store.save(update.effective_user.id, vacancy['id'])
                    else:
                        self.store.unsave(update.effective_user.id, vacancy['id'])
                    await self.card(update, context, vacancy)
            return END
        except (ValueError, KeyError, IndexError):
            await self.product.reply(update, tr('v_stale'))
            return END

    async def admin_stats(self, update, context):
        if not self.admin_id or update.effective_user.id != self.admin_id:
            return END
        tr = self.product.translator(update)
        stats = self.store.stats()
        await self.product.reply(update, tr('v_admin') + '\n' + '\n'.join(
            tr('v_stat_' + key) + ': ' + str(value or 0) for key, value in stats.items()))
        return END

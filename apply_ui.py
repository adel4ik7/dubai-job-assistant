"""Reviewable, cancellable application preparation. Confirm only saves locally."""
import secrets
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram.ext import ConversationHandler
from product_ui import keyboard
from services.apply_flow import ApplyFlow
from services.growth import Growth

WAIT_APPLY = 41
END = ConversationHandler.END


class ApplyUI:
    def __init__(self, product):
        self.product = product
        self.flow = ApplyFlow(product.db, product.files.root)

    async def show(self, update, context):
        tr = self.product.translator(update)
        draft = context.user_data['apply_draft']
        token = secrets.token_hex(4)
        context.user_data['apply_token'] = token
        context.user_data.pop('apply_field', None)
        resume = self.product.db.get_resume(update.effective_user.id, draft['resume_id']) if draft['resume_id'] else None
        lines = [tr('ap_title'), tr('ap_no_send'),
                 tr('v_role') + ': ' + (draft['role'] or tr('not_specified')),
                 tr('v_company') + ': ' + (draft['company'] or tr('not_specified')),
                 tr('ap_recipient') + ': ' + (draft['recipient_email'] or tr('ap_no_email')),
                 tr('ap_cv') + ': ' + (resume['filename'] if resume else tr('ap_cv_missing')),
                 tr('ap_subject') + ': ' + draft['subject'], tr('ap_message') + ':\n' + draft['message']]
        for field,key in (('location','v_location'),('salary','v_salary'),('source_url','field_source_url')):
            if draft.get(field):
                lines.insert(4, tr(key)+': '+draft[field])
        rows = [
            [(tr('ap_choose_cv'), f'ap:cvs:{token}:0')],
            [(tr('ap_edit_subject'), f'ap:edit:{token}:subject'), (tr('ap_edit_message'), f'ap:edit:{token}:message')],
            [(tr('pk_copy_email'), f'ap:copy:{token}:recipient_email'), (tr('pk_copy_subject'), f'ap:copy:{token}:subject')],
            [(tr('pk_copy_message'), f'ap:copy:{token}:message')],
            [(tr('pk_sent'), f'ap:sent:{token}')],
            [(tr('ap_confirm'), f'ap:confirm:{token}'), (tr('cancel'), f'ap:cancel:{token}')]]
        if not resume:
            rows.insert(1,[(tr('cb_menu'),'cb:home')])
        else:
            rows.insert(1,[(tr('pk_download'),f'ap:download:{token}')])
        markup = keyboard(rows)
        if draft.get('source_url'):
            markup = InlineKeyboardMarkup([*markup.inline_keyboard,[InlineKeyboardButton(tr('v_open'),url=draft['source_url'])]])
        # Keep long editable messages within Telegram's per-message limit.
        summary = '\n\n'.join(lines)
        if len(summary)>3900:
            await self.product.reply(update, summary[:3500])
            await self.product.reply(update, summary[3500:], markup)
        else:
            await self.product.reply(update, summary, markup)
        return END

    async def buttons(self, update, context):
        tr = self.product.translator(update)
        parts = update.callback_query.data.split(':')
        action = parts[1]
        uid = update.effective_user.id
        try:
            if action in {'vacancy', 'application'}:
                context.user_data['apply_draft'] = self.flow.prepare(uid, action, int(parts[2]), self.product.language(update))
                Growth(self.product.db).track(uid,'apply_pack_opened',action,int(parts[2]))
                return await self.show(update, context)
            if not context.user_data.get('apply_draft') or parts[2] != context.user_data.get('apply_token'):
                raise ValueError('ap_missing')
            if action == 'cancel':
                for key in ('apply_draft', 'apply_token', 'apply_field'):
                    context.user_data.pop(key, None)
                await self.product.reply(update, tr('ap_cancelled'), self.product.menu(update))
                return END
            if action == 'confirm':
                self.flow.confirm(uid, context.user_data['apply_draft'])
                for key in ('apply_draft', 'apply_token', 'apply_field'):
                    context.user_data.pop(key, None)
                await self.product.reply(update, tr('ap_prepared'), self.product.menu(update))
                return END
            if action == 'copy' and parts[3] in {'recipient_email','subject','message'}:
                value = context.user_data['apply_draft'][parts[3]]
                if value:
                    await update.effective_message.reply_text('<pre>'+escape(value)+'</pre>',parse_mode='HTML')
                else:
                    await self.product.reply(update,tr('ap_no_email'))
                return END
            if action == 'download':
                resume = self.flow.cv(uid,context.user_data['apply_draft']['resume_id'])
                with self.flow.files.safe_path(uid,resume['file_path']).open('rb') as document:
                    await update.effective_message.reply_document(document=document,filename=resume['filename'])
                return END
            if action == 'sent':
                app_id = self.flow.mark_sent(uid, context.user_data['apply_draft'])
                for key in ('apply_draft','apply_token','apply_field'):
                    context.user_data.pop(key,None)
                await self.product.reply(update,tr('pk_sent_done',id=app_id),self.product.menu(update))
                return END
            if action == 'edit' and parts[3] in {'subject', 'message'}:
                context.user_data['apply_field'] = parts[3]
                context.user_data.pop('apply_token', None)
                await self.product.reply(update, tr('ap_enter_' + parts[3]))
                return WAIT_APPLY
            if action == 'cvs':
                page = max(0, int(parts[3]))
                rows = self.product.db.list_resumes(uid, limit=6, offset=page*5)
                token = context.user_data['apply_token']
                buttons = [[(r['filename'][:60], f"ap:cv:{token}:{r['id']}")] for r in rows[:5]]
                if page:
                    buttons.append([(tr('previous'), f'ap:cvs:{token}:{page-1}')])
                if len(rows) > 5:
                    buttons.append([(tr('next'), f'ap:cvs:{token}:{page+1}')])
                buttons.append([(tr('ap_review'), f'ap:review:{token}')])
                if not rows:
                    buttons.append([(tr('cb_menu'),'cb:home')])
                await self.product.reply(update, tr('ap_choose_cv') if rows else tr('ap_cv_missing'), keyboard(buttons))
                return END
            if action == 'cv':
                resume = self.flow.cv(uid, int(parts[3]))
                draft = context.user_data['apply_draft']
                draft['resume_id'] = resume['id']
                subject,message = self.flow.defaults(uid,resume['id'],draft,self.product.language(update))
                for field,value in (('subject',subject),('message',message)):
                    if draft[field] == draft.get('default_'+field):
                        draft[field] = value
                    draft['default_'+field] = value
                return await self.show(update, context)
            if action == 'review':
                return await self.show(update, context)
            raise ValueError('ap_missing')
        except (ValueError, IndexError) as exc:
            key = str(exc) if str(exc).startswith('ap_') else 'ap_missing'
            await self.product.reply(update, tr(key))
            return END

    async def receive(self, update, context):
        tr = self.product.translator(update)
        try:
            field = context.user_data.get('apply_field')
            if field not in {'subject', 'message'} or 'apply_draft' not in context.user_data:
                raise ValueError('ap_missing')
            context.user_data['apply_draft'][field] = self.flow.validate(field, update.message.text or '')
            return await self.show(update, context)
        except ValueError as exc:
            await self.product.reply(update, tr(str(exc)))
            return WAIT_APPLY

"""One RU/EN Telegram flow backed by persistent CV drafts."""
import asyncio
import io
import secrets

from telegram.ext import ConversationHandler

from locales import translator
from product_ui import keyboard
from services.cv_builder import CVBuilder, SECTIONS, SECTION_FIELDS, validate_value
from services.growth import Growth
from services.cv_export import plain_text
from services.cv_export import TEMPLATE_IDS, TEMPLATES, template_style

WAIT_CV_BUILDER = 40
END = ConversationHandler.END


class CVBuilderUI:
    def __init__(self, product):
        self.product = product
        self.builder = CVBuilder(product.db, product.files.root)

    def tr(self, update):
        return translator(self.product.language(update))

    async def home(self, update, context, page=0):
        tr = self.tr(update)
        context.user_data.pop('builder_form', None)
        rows = self.builder.list(update.effective_user.id, page)
        buttons = [[(tr('cb_new'), 'cb:new')]]
        for row in rows[:5]:
            buttons.append([(f"#{row['id']} [{row['data'].get('_language', 'en').upper()}] {row['data'].get('full_name') or tr('cb_draft')}", f"cb:view:{row['id']}")])
        if page:
            buttons.append([(tr('previous'), f'cb:home:{page-1}')])
        if len(rows) > 5:
            buttons.append([(tr('next'), f'cb:home:{page+1}')])
        buttons.append([(tr('main_menu'), 'p:home')])
        await self.product.reply(update, tr('cb_intro'), keyboard(buttons))
        return END

    async def view(self, update, context, draft_id):
        tr = self.tr(update)
        data = self.builder.get(update.effective_user.id, draft_id)['data']
        context.user_data.pop('builder_form', None)
        context.user_data.pop('builder_delete', None)
        buttons = [[(tr('cb_continue'), f'cb:resume:{draft_id}')]] if data.get('_wizard') or data.get('_next_section') else []
        buttons += [[(tr('cb_pdf'), f'cb:pdf:{draft_id}'), (tr('cb_docx'), f'cb:docx:{draft_id}')],
                    [(tr('cb_templates'), f'cb:templates:{draft_id}')],
                    [(tr('cb_copy_ru'), f'cb:version:{draft_id}:ru'), (tr('cb_copy_en'), f'cb:version:{draft_id}:en')],
                    [(tr('cb_active'), f'cb:active:{draft_id}')],
                    [(tr('cb_edit'), f'cb:sections:{draft_id}'), (tr('cb_duplicate'), f'cb:duplicate:{draft_id}')],
                    [(tr('cb_delete'), f'cb:delete:{draft_id}')], [(tr('cb_menu'), 'cb:home')]]
        language = data.get('_language', self.product.language(update))
        preview = plain_text(data, language) or tr('cb_empty')
        preview = tr('cb_document_options', document_language=language.upper(), template=template_style(data.get('_template', 'template_3'))['name']) + '\n\n' + preview
        if data.get('photo'):
            preview += '\n' + tr('cb_photo_saved')
        await self.product.reply(update, tr('cb_preview', id=draft_id) + '\n\n' + preview + '\n\n' + tr('cb_snapshot'), keyboard(buttons))
        return END

    async def sections(self, update, draft_id):
        tr = self.tr(update)
        await self.product.reply(update, tr('cb_choose_section'), keyboard(
            [[(tr('cb_' + section), f'cb:section:{draft_id}:{section}')] for section in SECTIONS] +
            [[(tr('cb_view'), f'cb:view:{draft_id}')]]))
        return END

    async def entries(self, update, draft_id, section, flow=False):
        tr = self.tr(update)
        data = self.builder.get(update.effective_user.id, draft_id)['data']
        buttons = []
        for index, entry in enumerate(data.get(section, [])):
            title = entry.get('company') or entry.get('institution') or str(index+1)
            buttons.append([(tr('cb_edit') + ': ' + title[:45], f'cb:entry:{draft_id}:{section}:{index}'),
                            (tr('cb_delete'), f'cb:drop:{draft_id}:{section}:{index}')])
        buttons += [[(tr('cb_add_entry'), f'cb:add:{draft_id}:{section}:{int(flow)}')],
                    [(tr('cb_next_section'), f'cb:next:{draft_id}:{section}')],
                    [(tr('cb_save'), f'cb:view:{draft_id}')]]
        await self.product.reply(update, tr('cb_' + section), keyboard(buttons))
        return END

    async def begin(self, update, context, draft_id, section, index=None, flow=False):
        data = self.builder.get(update.effective_user.id, draft_id)['data']
        if section in {'experience', 'education'}:
            entries = data.get(section, [])
            if index is not None and not 0 <= index < len(entries):
                raise ValueError('cb_missing')
            if index is None:
                self.builder.set_section(update.effective_user.id, draft_id, section, {})
                data = self.builder.get(update.effective_user.id, draft_id)['data']
                entries = data[section]
                index = len(entries) - 1
            values = dict(entries[index])
        else:
            values = {k: data.get(k, '') for k in SECTION_FIELDS[section] if k != 'photo'}
        data['_wizard'] = dict(section=section, index=index, position=0, values=values,
                               flow=flow, nonce=secrets.token_hex(4))
        self.builder.save(update.effective_user.id, draft_id, data)
        context.user_data['builder_form'] = draft_id
        return await self.prompt(update, context, draft_id)

    async def prompt(self, update, context, draft_id):
        tr = self.tr(update)
        data = self.builder.get(update.effective_user.id, draft_id)['data']
        wizard = data.get('_wizard')
        if not wizard:
            return await self.view(update, context, draft_id)
        field = SECTION_FIELDS[wizard['section']][wizard['position']]
        token = wizard['nonce']
        buttons = [[(tr('cb_skip'), f'cb:skip:{draft_id}:{token}')]]
        if wizard['values'].get(field):
            buttons.insert(0, [(tr('cb_keep'), f'cb:keep:{draft_id}:{token}')])
        if field == 'end_date':
            buttons.insert(0, [(tr('cb_present'), f'cb:present:{draft_id}:{token}')])
        buttons.append([(tr('cb_save'), f'cb:view:{draft_id}')])
        hint = tr('cb_photo_hint') if field == 'photo' else tr('cb_date_hint') if field in {'start_date', 'end_date'} else tr('cb_text_hint')
        current = wizard['values'].get(field, '')
        await self.product.reply(update, tr('cb_prompt', section=tr('cb_' + wizard['section']), field=tr('cb_' + field)) +
                                 '\n' + hint + ('\n\n' + current if current else ''), keyboard(buttons))
        return WAIT_CV_BUILDER

    async def next_section(self, update, context, draft_id, section):
        index = SECTIONS.index(section) + 1
        if index == len(SECTIONS):
            return await self.view(update, context, draft_id)
        section = SECTIONS[index]
        if section in {'experience', 'education'}:
            # Persist the next step even if the bot restarts before an entry is added.
            data = self.builder.get(update.effective_user.id, draft_id)['data']
            data['_next_section'] = section
            self.builder.save(update.effective_user.id, draft_id, data)
            return await self.entries(update, draft_id, section, True)
        return await self.begin(update, context, draft_id, section, flow=True)

    async def receive(self, update, context, supplied=None):
        tr = self.tr(update)
        draft_id = context.user_data.get('builder_form')
        try:
            if not draft_id:
                raise ValueError('cb_missing')
            data = self.builder.get(update.effective_user.id, draft_id)['data']
            wizard = data.get('_wizard')
            if not wizard:
                raise ValueError('cb_missing')
            field = SECTION_FIELDS[wizard['section']][wizard['position']]
            if field == 'photo':
                value = supplied
                if supplied is None:
                    photos = update.effective_message.photo
                    if not photos or (photos[-1].file_size or 0) > 5 * 1024 * 1024:
                        raise ValueError('cb_photo_error')
                    remote = await photos[-1].get_file()
                    value = bytes(await remote.download_as_bytearray())
                self.builder.set_section(update.effective_user.id, draft_id, 'photo', value or b'')
                data = self.builder.get(update.effective_user.id, draft_id)['data']
            else:
                value = supplied if supplied is not None else update.effective_message.text
                if value is None:
                    raise ValueError('cb_invalid')
                wizard['values'][field] = validate_value(field, value)
                # Persist every answer in the actual section, not only wizard state.
                self.builder.set_section(update.effective_user.id, draft_id, wizard['section'], wizard['values'], wizard['index'])
                data = self.builder.get(update.effective_user.id, draft_id)['data']
            if wizard['position'] + 1 < len(SECTION_FIELDS[wizard['section']]):
                wizard['position'] += 1
                wizard['nonce'] = secrets.token_hex(4)
                data['_wizard'] = wizard
                self.builder.save(update.effective_user.id, draft_id, data)
                return await self.prompt(update, context, draft_id)
            data = self.builder.get(update.effective_user.id, draft_id)['data']
            data.pop('_wizard', None)
            data.pop('_next_section', None)
            self.builder.save(update.effective_user.id, draft_id, data)
            context.user_data.pop('builder_form', None)
            if wizard['section'] in {'experience', 'education'}:
                if wizard['flow']:
                    data['_next_section'] = wizard['section']
                    self.builder.save(update.effective_user.id, draft_id, data)
                return await self.entries(update, draft_id, wizard['section'], wizard['flow'])
            if wizard['flow']:
                return await self.next_section(update, context, draft_id, wizard['section'])
            return await self.view(update, context, draft_id)
        except ValueError as exc:
            await self.product.reply(update, tr(str(exc)))
            return WAIT_CV_BUILDER

    async def buttons(self, update, context):
        tr = self.tr(update)
        parts = update.callback_query.data.split(':')
        action = parts[1]
        uid = update.effective_user.id
        try:
            if action == 'home':
                return await self.home(update, context, int(parts[2]) if len(parts) > 2 else 0)
            if action == 'new':
                return await self.begin(update, context, self.builder.create(uid), 'basics', flow=True)
            draft_id = int(parts[2])
            data = self.builder.get(uid, draft_id)['data']
            if action == 'view':
                return await self.view(update, context, draft_id)
            if action == 'sections':
                return await self.sections(update, draft_id)
            if action == 'resume':
                if data.get('_wizard'):
                    context.user_data['builder_form'] = draft_id
                    return await self.prompt(update, context, draft_id)
                if data.get('_next_section'):
                    return await self.entries(update, draft_id, data['_next_section'], True)
                return await self.sections(update, draft_id)
            if action in {'skip', 'keep', 'present'}:
                wizard = data.get('_wizard')
                if not wizard or parts[3] != wizard['nonce'] or context.user_data.get('builder_form') != draft_id:
                    raise ValueError('cb_missing')
                field = SECTION_FIELDS[wizard['section']][wizard['position']]
                value = wizard['values'].get(field, '') if action == 'keep' else 'Present' if action == 'present' else ''
                return await self.receive(update, context, value)
            if action in {'section', 'entry', 'add', 'next'}:
                section = parts[3]
                if section not in SECTIONS:
                    raise ValueError('cb_invalid')
                if action == 'next':
                    return await self.next_section(update, context, draft_id, section)
                if action == 'section' and section in {'experience', 'education'}:
                    return await self.entries(update, draft_id, section)
                return await self.begin(update, context, draft_id, section,
                                        int(parts[4]) if action == 'entry' else None,
                                        action == 'add' and parts[4] == '1')
            if action in {'delete', 'drop'}:
                token = secrets.token_hex(4)
                context.user_data['builder_delete'] = (draft_id, parts[3:] if action == 'drop' else [], token)
                await self.product.reply(update, tr('cb_confirm_delete'), keyboard([
                    [(tr('cb_delete'), f'cb:confirm:{draft_id}:{token}')], [(tr('cancel'), f'cb:view:{draft_id}')]]))
                return END
            if action == 'confirm':
                pending = context.user_data.pop('builder_delete', None)
                if not pending or pending[0] != draft_id or pending[2] != parts[3]:
                    raise ValueError('cb_missing')
                if pending[1]:
                    section, index = pending[1]
                    self.builder.remove_entry(uid, draft_id, section, int(index))
                    return await self.entries(update, draft_id, section)
                self.builder.delete(uid, draft_id)
                return await self.home(update, context)
            if action == 'duplicate':
                return await self.view(update, context, self.builder.duplicate(uid, draft_id))
            if action == 'version':
                new_id = self.builder.duplicate(uid, draft_id, parts[3])
                await self.product.reply(update, tr('cb_translation_notice'))
                return await self.view(update, context, new_id)
            if action == 'templates':
                for template in TEMPLATE_IDS:
                    preview = TEMPLATES / template / 'preview.png'
                    if preview.is_file():
                        with preview.open('rb') as image:
                            await update.effective_message.reply_photo(photo=image, caption=template_style(template)['name'])
                await self.product.reply(update, tr('cb_template_hint'), keyboard(
                    [[(template_style(t)['name'], f'cb:template:{draft_id}:{t}')] for t in TEMPLATE_IDS]))
                return END
            if action == 'template':
                self.builder.set_template(uid, draft_id, parts[3])
                return await self.view(update, context, draft_id)
            if action in {'pdf', 'docx'}:
                attachment = await asyncio.to_thread(self.builder.attachment, uid, draft_id, action, self.product.language(update))
                await update.effective_message.reply_document(document=io.BytesIO(attachment.content), filename=attachment.filename)
                Growth(self.product.db).track(uid,'cv_generated','cv',draft_id,{'format':action})
                return await self.view(update, context, draft_id)
            if action == 'active':
                await asyncio.to_thread(self.builder.activate, uid, draft_id, self.product.language(update))
                await self.product.reply(update, tr('cb_activated'))
                return await self.view(update, context, draft_id)
            raise ValueError('cb_invalid')
        except (ValueError, IndexError) as exc:
            key = str(exc) if str(exc).startswith('cb_') else 'cb_invalid'
            await self.product.reply(update, tr(key))
            return END

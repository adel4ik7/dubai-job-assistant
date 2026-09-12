"""Shared application details, timeline and Dubai-time scheduling screens."""
import secrets
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler
from locales import status_label
from statuses import STATUSES
from product_ui import keyboard
from services.application_tracker import ApplicationTracker, parse_local, display_date
from services.product import PrivacyError

WAIT_TRACKER = 42
END = ConversationHandler.END


class TrackerUI:
    def __init__(self,product):
        self.product = product
        self.store = ApplicationTracker(product.db)

    def app(self,update,app_id):
        row = self.product.db.get_application(update.effective_user.id,app_id)
        if not row:
            raise ValueError('application_not_found')
        return row

    async def details(self,update,context,app_id):
        context.user_data.pop('tracker_delete',None)
        context.user_data.pop('tracker_action',None)
        tr = self.product.translator(update)
        app = self.product.db.get_application(update.effective_user.id,app_id)
        if not app:
            await self.product.reply(update,tr('application_not_found'));return END
        lines = [tr('application_v0',v0=app_id),tr('field_role')+': '+app['role'],tr('field_company')+': '+app['company'],
                 tr('field_status')+': '+status_label(self.product.language(update),app['status'])]
        for field,key in (('salary','v_salary'),('recipient_email','ap_recipient'),('notes','field_notes'),('source_url','field_source_url')):
            if app.get(field):
                lines.append(tr(key)+': '+app[field])
        for field,key in (('applied_at','at_applied'),('interview_at','at_interview_time'),('follow_up_at','at_follow_time')):
            value = display_date(app[field]) if app.get(field) else (app.get('date_applied') if field=='applied_at' else '')
            if value:
                lines.append(tr(key)+': '+value)
        if app.get('interview_at'):
            lines += [tr('at_'+app['interview_format']),app.get('interview_location') or '',app.get('interview_notes') or '']
        cv = self.product.db.get_resume(update.effective_user.id,app['selected_cv_id']) if app.get('selected_cv_id') else None
        lines += [tr('ap_cv')+': '+(cv['filename'] if cv else tr('not_specified')),tr('at_timezone')]
        rows = [[(tr('at_status'),f'at:status:{app_id}'),(tr('at_timeline'),f'at:history:{app_id}:0')],
                [(tr('at_interview'),f'at:interview:{app_id}')],[(tr('at_follow'),f'at:follow:{app_id}')],
                [(tr('at_note'),f'at:note:{app_id}'),(tr('at_cv'),f'at:cv:{app_id}')],
                [(tr('ap_apply'),f'ap:application:{app_id}')],[(tr('at_delete'),f'at:delete:{app_id}')],[(tr('back'),'p:apps:0')]]
        markup = keyboard(rows)
        if app.get('source_url'):
            markup = InlineKeyboardMarkup([*markup.inline_keyboard,[InlineKeyboardButton(tr('at_source'),url=app['source_url'])]])
        await self.product.reply(update,'\n'.join(lines)[:3900],markup)
        return END

    async def buttons(self,update,context):
        tr = self.product.translator(update)
        user = update.effective_user.id
        parts = update.callback_query.data.split(':')
        action = parts[1]
        try:
            if action=='today':
                offset = max(0,int(parts[2]))
                rows = self.store.today(user,offset=offset)
                buttons = []
                lines = [tr('at_today'),tr('at_timezone')]
                for row in rows[:10]:
                    tags = ', '.join(tr('at_'+key) for key in ('interview_today','follow_today','overdue','stale') if row[key])
                    lines.append(f"{row['role']} — {row['company']}\n{tags}")
                    buttons.append([(f"{row['role'][:30]} — {row['company'][:30]}",f"at:open:{row['id']}")])
                if not rows:
                    lines.append(tr('at_nothing'))
                if offset:
                    buttons.append([(tr('previous'),f'at:today:{max(0,offset-10)}')])
                if len(rows)>10:
                    buttons.append([(tr('next'),f'at:today:{offset+10}')])
                buttons.append([(tr('main_menu'),'p:home')])
                await self.product.reply(update,'\n\n'.join(lines)[:3900],keyboard(buttons))
                return END
            app_id = int(parts[2]); app = self.app(update,app_id)
            if action=='status':
                await self.product.reply(update,tr('choose_status'),keyboard([[(status_label(self.product.language(update),s),f'at:set:{app_id}:{s}')] for s in STATUSES]+[[(tr('back'),f'at:open:{app_id}')]]))
                return END
            if action=='set':
                self.product.db.update_application_status(user,app_id,parts[3])
            elif action=='follow':
                token = secrets.token_hex(4);context.user_data['tracker_action']=(app_id,token)
                rows = [[(tr('at_days',days=d),f'at:days:{app_id}:{d}:{token}') for d in (2,3)],
                        [(tr('at_days',days=d),f'at:days:{app_id}:{d}:{token}') for d in (5,7)],
                        [(tr('at_custom'),f'at:custom:{app_id}')],[(tr('at_no_reminder'),f'at:days:{app_id}:0:{token}')],[(tr('back'),f'at:open:{app_id}')]]
                await self.product.reply(update,tr('at_follow'),keyboard(rows));return END
            elif action=='days':
                if context.user_data.pop('tracker_action',None)!=(app_id,parts[4]) or int(parts[3]) not in (0,2,3,5,7):
                    raise ValueError('at_expired')
                self.store.follow_up(user,app_id,time.time()+int(parts[3])*86400 if int(parts[3]) else None)
            elif action in {'custom','note','interview'}:
                context.user_data['tracker_form']={'app_id':app_id,'kind':action,'step':0,'values':{}}
                await self.prompt(update,context);return WAIT_TRACKER
            elif action=='reply':
                self.store.replied(user,app_id)
            elif action=='history':
                offset=max(0,int(parts[3])); rows=self.store.timeline(user,app_id,offset)
                lines=[tr('at_timeline')]
                for row in rows[:10]:
                    description=status_label(self.product.language(update),row['new_status']) if row['new_status'] else tr('at_event_'+row['event_type'])
                    if row['event_type']=='snapshot':
                        description=tr('at_snapshot')+': '+description
                    lines.append(display_date(row['created_at'])+' — '+description+('\n'+row['note'][:160] if row['note'] else ''))
                buttons=[[(tr('back'),f'at:open:{app_id}')]]
                if offset: buttons.insert(0,[(tr('previous'),f'at:history:{app_id}:{max(0,offset-10)}')])
                if len(rows)>10: buttons.insert(0,[(tr('next'),f'at:history:{app_id}:{offset+10}')])
                await self.product.reply(update,'\n'.join(lines),keyboard(buttons));return END
            elif action=='cv':
                resume=self.product.db.get_resume(user,app['selected_cv_id']) if app.get('selected_cv_id') else None
                if not resume: raise ValueError('ap_cv_missing')
                path=self.product.files.safe_path(user,resume['file_path'])
                if not path.is_file(): raise ValueError('ap_cv_missing')
                with path.open('rb') as file:
                    await update.effective_message.reply_document(document=file,filename=resume['filename'])
                return END
            elif action=='delete':
                token=secrets.token_hex(4);context.user_data['tracker_delete']=(app_id,token,time.monotonic())
                await self.product.reply(update,tr('at_delete_confirm'),keyboard([[(tr('at_delete'),f'at:erase:{app_id}:{token}')],[(tr('cancel'),f'at:open:{app_id}')]]));return END
            elif action=='erase':
                pending=context.user_data.pop('tracker_delete',None)
                if not pending or pending[:2]!=(app_id,parts[3]) or time.monotonic()-pending[2]>300: raise ValueError('at_expired')
                self.store.delete(user,app_id)
                await self.product.applications(update,context);return END
            return await self.details(update,context,app_id)
        except PrivacyError as exc:
            await self.product.reply(update,tr(str(exc)));return END
        except OSError:
            await self.product.reply(update,tr('ap_cv_missing'));return END
        except (ValueError,IndexError,KeyError) as exc:
            key=str(exc) if str(exc).startswith(('at_','ap_')) or str(exc)=='application_not_found' else 'at_invalid'
            await self.product.reply(update,tr(key));return END

    async def prompt(self,update,context):
        form=context.user_data['tracker_form']
        key='at_prompt_'+( ('date','format','location','notes')[form['step']] if form['kind']=='interview' else 'notes' if form['kind']=='note' else 'date')
        await self.product.reply(update,self.product.translator(update)(key),keyboard([[(self.product.translator(update)('cancel'),f"at:open:{form['app_id']}")]]))

    async def receive(self,update,context):
        tr=self.product.translator(update)
        form=context.user_data.get('tracker_form')
        if not form:
            await self.product.reply(update,tr('at_expired'));return END
        try:
            value=(update.message.text or '').strip(); uid=update.effective_user.id; app_id=form['app_id']
            if form['kind']=='note': self.store.note(uid,app_id,'' if value=='-' else value)
            elif form['kind']=='custom': self.store.follow_up(uid,app_id,parse_local(value))
            else:
                field=('when','format','location','notes')[form['step']]
                if field=='when': value=parse_local(value)
                elif field=='format':
                    variants={tr('at_'+code).casefold():code for code in ('onsite','phone','video')}
                    value=variants.get(value.casefold(),value.casefold())
                    if value not in ('onsite','phone','video'): raise ValueError('at_invalid_interview')
                else:
                    value='' if value=='-' else value
                    if len(value)>(500 if field=='location' else 1000): raise ValueError('at_invalid')
                form['values'][field]=value;form['step']+=1
                if form['step']<4:
                    await self.prompt(update,context);return WAIT_TRACKER
                self.store.interview(uid,app_id,**form['values'])
            context.user_data.pop('tracker_form',None)
            return await self.details(update,context,app_id)
        except ValueError:
            # Keep final interview step editable after a time/validation failure.
            if form['kind']=='interview': form['step']=min(form['step'],3)
            await self.product.reply(update,tr('at_invalid'));return WAIT_TRACKER

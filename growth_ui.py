"""First-user setup, private feedback and admin screens; reuses existing product flows."""
from telegram.ext import ConversationHandler
from product_ui import keyboard
from locales import text
from services.growth import Growth, FEEDBACK, REASONS
from services.job_alerts import JobAlerts
from vacancy_store import VacancyStore

WAIT_GROWTH = 50
END = ConversationHandler.END


class GrowthUI:
    def __init__(self, product, admin_id, builder):
        self.product, self.db, self.builder = product, product.db, builder
        self.store = Growth(self.db, admin_id)
        self.alerts = JobAlerts(self.db)
        self.vacancies = VacancyStore(self.db)

    async def welcome(self, update, context):
        language = 'ru' if str(getattr(update.effective_user,'language_code','')).startswith('ru') else self.product.language(update)
        tr=lambda key: text(language,key)
        await self.product.reply(update,tr('g_welcome'),keyboard([
            [(tr('g_begin'),'g:resume')],[(tr('g_skip_all'),'g:finish')],[(tr('g_later'),'g:later')]]))
        return END

    async def setup(self, update, context):
        tr=self.product.translator(update); user=update.effective_user.id
        row=self.store.onboarding(user)
        if not row or row['state']=='completed':
            return await self.settings(update,context)
        step=max(0,row['step']); self.store.step(user,step)
        context.user_data.pop('growth_input',None)
        rows=[]
        if step==0:
            rows=[[(tr('language_en'),'g:value:0:en'),(tr('language_ru'),'g:value:0:ru')]]
        elif step in (3,6):
            rows=[[(tr('g_yes'),f'g:value:{step}:1'),(tr('g_no'),f'g:value:{step}:0')]]
        elif step==5:
            rows=[[(tr('g_upload'),'g:cv:upload'),(tr('cb_menu'),'g:cv:create')]]
        else:
            context.user_data['growth_input']=('onboarding',step)
        rows += [[(tr('g_skip'),f'g:skip:{step}'),(tr('back'),f'g:back:{step}')],[(tr('g_later'),'g:later')]]
        await self.product.reply(update,tr('g_step',step=step+1)+'\n'+tr('g_step_'+str(step)),keyboard(rows))
        return WAIT_GROWTH if step in (1,2,4) else END

    async def settings(self, update, context):
        tr=self.product.translator(update)
        rows=[[(tr('language_button'),'p:language')],[(tr('g_feedback'),'g:feedback')],
              [(tr('g_my_data'),'g:data')],[(tr('g_delete'),'g:delete')],
              [(tr('menu_help'),'help')]]
        row=self.store.onboarding(update.effective_user.id)
        if row and row['state']!='completed':
            rows.insert(0,[(tr('g_resume'),'g:resume')])
        if self.store.admin_id and update.effective_user.id==self.store.admin_id:
            rows.append([(tr('g_admin'),'g:admin')])
        rows.append([(tr('main_menu'),'p:home')])
        await self.product.reply(update,tr('g_settings'),keyboard(rows)); return END

    async def data(self, update, context):
        await self.product.reply(update,self.product.translator(update)('g_data_text'))
        return END

    async def receive(self, update, context):
        tr=self.product.translator(update); user=update.effective_user.id
        form=context.user_data.get('growth_input')
        if not form:
            await self.product.reply(update,tr('v_stale')); return END
        value=(update.message.text or '').strip()
        try:
            if form[0]=='feedback':
                self.store.feedback(user,form[1],value,form[2])
                context.user_data.pop('growth_input',None)
                await self.product.reply(update,tr('g_feedback_saved'),self.product.menu(update)); return END
            row=self.store.onboarding(user)
            if not row or row['state']!='active' or row['step']!=form[1]:
                raise ValueError
            step=form[1]
            field={1:'roles',2:'location',4:'salary_min'}[step]
            self.alerts.update(user,field,value)
            if step in (1,2):
                self.db.save_profile(user,**{('desired_role' if step==1 else 'current_location'):('' if value=='-' else value)})
            self.store.step(user,step+1)
            return await self.setup(update,context)
        except (ValueError,KeyError):
            await self.product.reply(update,tr('g_invalid')); return WAIT_GROWTH

    async def admin_stats(self, update, context):
        tr=self.product.translator(update)
        try:
            s=self.store.stats(update.effective_user.id)
        except PermissionError:
            await self.product.reply(update,tr('g_denied')); return END
        lines=[tr('g_admin')]
        for key in ('total_users','new_24h','new_7d','active_24h','active_7d','active_today','returning'):
            lines.append(tr('g_stat_'+key)+': '+str(s[key]))
        lines.append('\n'+tr('g_usage'))
        for key in ('vacancy_searched','vacancy_viewed','cv_created','cv_generated','application_created','job_alert_enabled','job_alert_delivered','vacancy_shared'):
            lines.append(tr('g_event_'+key)+': '+str(s['usage'].get(key,0)))
        lines.append('\n'+tr('g_funnel'))
        for i,(count,rate) in enumerate(zip(s['funnel'],s['conversion'])):
            lines.append(tr('g_funnel_'+str(i))+f': {count} ({rate}%)')
        rows=[[(tr('g_feedback'),'g:feedbacks:0'),(tr('g_reports'),'g:reports:0')],
              [(tr('g_sources'),'g:sources:0')],[(tr('g_settings'),'g:settings')]]
        await self.product.reply(update,'\n'.join(lines),keyboard(rows)); return END

    async def buttons(self, update, context):
        tr=self.product.translator(update); user=update.effective_user.id
        parts=update.callback_query.data.split(':'); action=parts[1]
        context.user_data.pop('growth_input',None)
        try:
            if action=='settings': return await self.settings(update,context)
            if action=='data': return await self.data(update,context)
            if action=='delete': return await self.product.delete_my_data(update,context)
            if action=='cvs':
                await self.product.reply(update,tr('g_my_cv'),keyboard([
                    [(tr('g_upload'),'upload_cv'),(tr('cb_menu'),'cb:home')],
                    [(tr('menu_cvs'),'p:cvs:0')],[(tr('menu_analyse'),'analyse_vacancy')],[(tr('main_menu'),'p:home')]])); return END
            if action=='applications':
                await self.product.reply(update,tr('g_applications'),keyboard([
                    [(tr('menu_applications'),'p:apps:0'),(tr('at_today'),'at:today:0')],[(tr('main_menu'),'p:home')]])); return END
            if action in {'resume','finish','later','skip','back','value','cv'}:
                row=self.store.onboarding(user)
                if not row or row['state']=='completed': raise ValueError
                if action=='resume': return await self.setup(update,context)
                if action=='finish':
                    self.store.step(user,7,'completed')
                    await self.product.reply(update,tr('g_finished'),self.product.menu(update)); return END
                if action=='later':
                    self.store.step(user,row['step'],'paused')
                    await self.product.reply(update,tr('g_paused'),self.product.menu(update)); return END
                if action=='cv':
                    if row['step']!=5: raise ValueError
                    self.store.step(user,6,'paused')
                    await self.product.reply(update,tr('g_cv_continue'),keyboard([[(tr('g_resume'),'g:resume')]]))
                    if parts[2]=='create': return await self.builder.home(update,context)
                    await self.product.reply(update,tr('send_me_your_cv_as_pdf_docx_or_txt')); return END
                step=int(parts[2])
                if row['step']!=step or row['state']!='active': raise ValueError
                if action=='value':
                    value=parts[3]
                    if step==0: self.db.set_language(user,value)
                    elif step in (3,6) and value in {'0','1'}:
                        self.alerts.update(user,'uae_only' if step==3 else 'enabled',int(value))
                    else: raise ValueError
                if action=='back': self.store.step(user,max(0,step-1))
                elif step==6:
                    self.store.step(user,7,'completed')
                    await self.product.reply(update,tr('g_finished'),self.product.menu(update)); return END
                else: self.store.step(user,step+1)
                return await self.setup(update,context)
            if action=='feedback':
                await self.product.reply(update,tr('g_feedback'),keyboard([[(tr('g_fb_'+c),'g:category:'+c)] for c in FEEDBACK]+[[(tr('back'),'g:settings')]])); return END
            if action=='category':
                if parts[2] not in FEEDBACK: raise ValueError
                context.user_data['growth_input']=('feedback',parts[2],None)
                await self.product.reply(update,tr('g_feedback_prompt'),keyboard([[(tr('cancel'),'g:settings')]])); return WAIT_GROWTH
            if action=='report':
                vid=int(parts[2])
                if not self.vacancies.get_visible(vid): raise ValueError
                await self.product.reply(update,tr('g_report'),keyboard([[(tr('g_reason_'+r),f'g:reason:{vid}:{r}')] for r in REASONS]+[[(tr('cancel'),f'v:open:{vid}')]])); return END
            if action=='reason':
                self.store.report(user,int(parts[2]),parts[3])
                await self.product.reply(update,tr('g_report_saved')); return END
            self.store.admin(user)
            if action=='admin': return await self.admin_stats(update,context)
            if action=='feedbacks':
                offset=max(0,int(parts[2])); rows=self.store.feedback_rows(user,offset)
                for row in rows:
                    await self.product.reply(update,f"#{row['id']} · "+tr('g_fb_'+row['category'])+' · '+tr('g_feedback_'+row['status'])+'\n'+row['message'],
                        keyboard([[(tr('g_resolve'),f"g:resolve:{row['id']}")]]))
                await self.product.reply(update,tr('g_feedback') if rows else tr('g_empty'),keyboard([
                    [(tr('previous'),f'g:feedbacks:{max(0,offset-5)}'),(tr('v_next'),f'g:feedbacks:{offset+5}')],[(tr('back'),'g:admin')]]))
            elif action=='resolve':
                with self.db._connect() as conn: conn.execute("UPDATE feedback SET status='resolved' WHERE id=?",(int(parts[2]),))
                await self.product.reply(update,tr('g_done'))
            elif action=='reports':
                offset=max(0,int(parts[2])); rows=self.store.reports(user,offset)
                for row in rows:
                    message=f"#{row['id']} · {row['role'] or ''}\n"+tr('g_report_count',count=row['reports'])
                    if row['reports']>=3: message+='\n'+tr('g_review_required')
                    message+='\n'+tr('g_hidden' if row['hidden'] else 'g_visible')
                    await self.product.reply(update,message,keyboard([
                        [(tr('g_hide'),f"g:hide:{row['id']}:1"),(tr('g_restore'),f"g:hide:{row['id']}:0")],
                        [(tr('g_view'),f"g:review:{row['id']}")]]))
                await self.product.reply(update,tr('g_reports') if rows else tr('g_empty'),keyboard([
                    [(tr('previous'),f'g:reports:{max(0,offset-5)}'),(tr('v_next'),f'g:reports:{offset+5}')],[(tr('back'),'g:admin')]]))
            elif action=='review':
                row=self.vacancies.get(int(parts[2]))
                if not row: raise ValueError
                with self.db._connect() as conn:
                    reasons=list(conn.execute('SELECT reason,COUNT(*) n FROM vacancy_reports WHERE vacancy_id=? GROUP BY reason',(row['id'],)))
                summary='\n'.join(tr('g_reason_'+r[0])+': '+str(r[1]) for r in reasons)
                await self.product.reply(update,summary+'\n\n'+row['combined_text'][:2800],keyboard([
                    [(tr('g_hide'),f"g:hide:{row['id']}:1"),(tr('g_restore'),f"g:hide:{row['id']}:0")],[(tr('back'),'g:reports:0')]]))
            elif action=='hide':
                self.store.hide(user,int(parts[2]),parts[3]=='1')
                await self.product.reply(update,tr('g_done'))
            elif action=='sources':
                offset=max(0,int(parts[2])); usage=self.store.source_usage(user)
                rows=self.vacancies.source_quality()[offset:offset+5]
                for row in rows:
                    await self.product.reply(update,tr('g_source_summary',title=row['title'],
                        posts=row['processed'],detected=row['vacancies']+row['probably_vacancy'],
                        rate=round(100*row['useful_rate'],1),duplicates=row['duplicates'],
                        success=row['ocr_success'],failed=row['ocr_failures'],
                        views=usage.get((row['id'],'vacancy_viewed'),0),saves=usage.get((row['id'],'vacancy_saved'),0),
                        applications=usage.get((row['id'],'application_created'),0)),
                        keyboard([[(tr('g_disable_source'),f"g:disable:{row['id']}")]]))
                await self.product.reply(update,tr('g_sources'),keyboard([
                    [(tr('previous'),f'g:sources:{max(0,offset-5)}'),(tr('v_next'),f'g:sources:{offset+5}')],[(tr('back'),'g:admin')]]))
            elif action=='disable':
                self.vacancies.enable_source(int(parts[2]),False)
                await self.product.reply(update,tr('g_done'))
            return END
        except PermissionError:
            await self.product.reply(update,tr('g_denied')); return END
        except (ValueError,KeyError,IndexError):
            await self.product.reply(update,tr('g_invalid')); return END

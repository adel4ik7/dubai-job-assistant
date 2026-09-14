import asyncio,json,logging,os,tempfile,time,unittest,subprocess,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram import Update
from telegram.ext import Application,CommandHandler
from telegram.request import BaseRequest,HTTPXRequest
from telegram.error import NetworkError,TimedOut,RetryAfter,Conflict
import bot
from services.bot_runtime import BotHealth,bootstrap,serve,ResilientRequest,InstanceLock,AlreadyRunning,configure_logging
from services.bot_db import BotDatabase,retry_busy
from services.alert_sender import start_sender,stop_sender


class FakeRequest(BaseRequest):
    def __init__(self,failures=()):self.failures=list(failures);self.polls=0;self.poll_times=[]
    @property
    def read_timeout(self):return 10
    async def initialize(self):pass
    async def shutdown(self):pass
    async def do_request(self,url,method,**kwargs):
        if url.endswith('/getMe'):result={'id':123,'is_bot':True,'first_name':'Test','username':'test_bot'}
        elif url.endswith('/getUpdates'):
            self.polls+=1;self.poll_times.append(time.monotonic())
            if self.failures:raise self.failures.pop(0)
            await asyncio.sleep(.01);result=[]
        else:result=True
        return 200,json.dumps({'ok':True,'result':result}).encode()


class BotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.h=BotHealth(self.root/'health.json')
    def application(self,request=None):
        request=request or FakeRequest()
        return Application.builder().token('123:synthetic').request(FakeRequest()).get_updates_request(request).build()
    async def test_real_framework_polling_stays_alive_and_shutdown(self):
        app=self.application();stop=asyncio.Event()
        task=asyncio.create_task(serve(app,self.h,self.root/'stop',stop=stop,heartbeat_seconds=.1))
        await asyncio.sleep(.55)
        self.assertFalse(task.done());self.assertTrue(app.running);self.assertEqual(self.h.data['status'],'running')
        self.assertIsNotNone(self.h.data['last_heartbeat_at'])
        stop.set();self.assertEqual(await asyncio.wait_for(task,2),0)
        self.assertFalse(app.running);self.assertFalse(app.updater.running);self.assertEqual(self.h.data['status'],'stopped')
    async def test_real_framework_network_error_retries_polling(self):
        req=FakeRequest([NetworkError('private')]);app=self.application(req);stop=asyncio.Event()
        task=asyncio.create_task(serve(app,self.h,self.root/'stop',stop=stop))
        await asyncio.sleep(1.8)
        self.assertFalse(task.done());self.assertGreater(req.polls,1)
        self.assertGreater(self.h.data['network_error_count'],0)
        stop.set();await task
    async def test_real_framework_timeout_retryafter(self):
        req=FakeRequest([TimedOut(),RetryAfter(1)]);app=self.application(req);stop=asyncio.Event()
        task=asyncio.create_task(serve(app,self.h,self.root/'stop',stop=stop))
        await asyncio.sleep(2)
        self.assertFalse(task.done());self.assertGreaterEqual(req.polls,3)
        self.assertGreaterEqual(req.poll_times[2]-req.poll_times[1],1)
        stop.set();await task
    async def test_conflict_terminates_without_infinite_retry(self):
        app=self.application(FakeRequest([Conflict('secret')]))
        code=await asyncio.wait_for(serve(app,self.h,self.root/'stop'),2)
        self.assertEqual(code,4);self.assertEqual(self.h.data['status'],'error')
        self.assertEqual(self.h.data['last_error_type'],'Conflict')
    async def test_original_default_bootstrap_exits_on_first_timeout(self):
        app=self.application()
        with patch.object(Application,'initialize',new=AsyncMock(side_effect=TimedOut())) as initialize:
            with self.assertRaises(TimedOut):await app._bootstrap_initialize(max_retries=0)
        self.assertEqual(initialize.await_count,1)

    async def test_error_notice_obeys_retryafter(self):
        message=SimpleNamespace(reply_text=AsyncMock())
        update=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(type='private'),effective_user=SimpleNamespace(language_code='en'))
        context=SimpleNamespace(error=RetryAfter(4),application=SimpleNamespace(bot_data={}))
        with patch.object(bot,'product',SimpleNamespace(language=lambda u:'en'),create=True),patch('bot.wait_stop',new=AsyncMock(return_value=False)) as wait:
            await bot.error_handler(update,context)
        self.assertEqual(wait.call_args.args[1],4);message.reply_text.assert_awaited_once()

    async def test_bootstrap_backoff_and_stop(self):
        stop=asyncio.Event();delays=[]
        async def wait(event,seconds):
            delays.append(seconds)
            if len(delays)==7:stop.set()
        await bootstrap(AsyncMock(side_effect=NetworkError('secret')),stop,self.h,wait)
        self.assertEqual(delays,[2,5,10,30,60,60,60])
    async def test_bootstrap_retryafter_and_recovery(self):
        stop=asyncio.Event();wait=AsyncMock()
        action=AsyncMock(side_effect=[RetryAfter(9),TimedOut(),None])
        self.assertTrue(await bootstrap(action,stop,self.h,wait))
        self.assertEqual([c.args[1] for c in wait.call_args_list],[9,2])
    async def test_stop_while_bootstrap_never_connects(self):
        app=self.application();stop=asyncio.Event()
        with patch.object(Application,'initialize',new=AsyncMock(side_effect=NetworkError('secret'))):
            task=asyncio.create_task(serve(app,self.h,self.root/'stop',stop=stop))
            await asyncio.sleep(.03);stop.set()
            self.assertEqual(await asyncio.wait_for(task,2),0)
    async def test_request_records_framework_timeout_without_duplicate_count(self):
        request=ResilientRequest(health=self.h);error=TimedOut()
        try:
            with patch.object(BaseRequest,'post',new=AsyncMock(side_effect=error)):
                with self.assertRaises(TimedOut):await request.post(url='synthetic')
            self.h.error(error,'polling')
            self.assertEqual(self.h.data['network_error_count'],1)
        finally:await request.shutdown()

    async def test_http_transport_normalizes_oserror_without_details(self):
        request=ResilientRequest()
        try:
            with patch.object(HTTPXRequest,'do_request',new=AsyncMock(side_effect=OSError('secret token'))):
                with self.assertRaises(NetworkError) as error:await request.do_request(url='synthetic',method='POST')
            self.assertNotIn('secret',str(error.exception))
        finally:await request.shutdown()
    async def test_global_handler_survives_failed_notice_and_localization(self):
        update=SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock(side_effect=NetworkError('secret'))),effective_chat=SimpleNamespace(type='private'),effective_user=SimpleNamespace(language_code='ru'))
        context=SimpleNamespace(error=ValueError('private profile'),application=SimpleNamespace(bot_data={'runtime_health':self.h}))
        with patch.object(bot,'product',SimpleNamespace(language=lambda update: (_ for _ in ()).throw(sqlite3.OperationalError('private DB'))),create=True):
            await bot.error_handler(update,context)
        self.assertIn('Не удалось',update.effective_message.reply_text.call_args.args[0]);self.assertEqual(self.h.data['error_count'],2)
    async def test_framework_isolates_one_update_from_next(self):
        app=self.application();called=[]
        async def action(update,context):
            called.append(update.update_id)
            if update.update_id==1:raise ValueError('private text')
        app.add_handler(CommandHandler('start',action));app.add_error_handler(bot.error_handler)
        await app.initialize()
        try:
            for n in (1,2):
                update=Update.de_json({'update_id':n,'message':{'message_id':n,'date':0,'chat':{'id':1,'type':'private'},'from':{'id':1,'is_bot':False,'first_name':'Synthetic'},'text':'/start','entities':[{'type':'bot_command','offset':0,'length':6}]}},app.bot)
                with patch('telegram.Message.reply_text',new_callable=AsyncMock):await app.process_update(update)
            self.assertEqual(called,[1,2])
        finally:await app.shutdown()
    async def test_failed_background_job_does_not_block_others(self):
        app=SimpleNamespace(bot=None,bot_data={'job_alerts':SimpleNamespace(db=None),'growth':object(),'runtime_health':self.h})
        with patch('services.alert_sender.deliver_feedback',new=AsyncMock(side_effect=ValueError('secret'))),patch('services.alert_sender.deliver_reminder',new=AsyncMock(return_value=False)) as reminder,patch('services.alert_sender.deliver_one',new=AsyncMock(return_value=False)) as alert:
            await start_sender(app);await asyncio.sleep(.03);await stop_sender(app)
            reminder.assert_awaited();alert.assert_awaited()
    def test_bot_lock_stale_and_separate_from_collector(self):
        path=self.root/'bot.lock';path.write_text('stale')
        with InstanceLock(path),InstanceLock(self.root/'collector.lock'):
            with self.assertRaises(AlreadyRunning):
                with InstanceLock(path):pass
        with InstanceLock(path):pass
    def test_health_contains_no_content_and_atomic_update(self):
        self.h.update_seen();self.h.error(TimedOut(),'handler')
        data=json.loads(self.h.path.read_text());self.assertEqual(data['processed_updates'],1)
        self.assertEqual(data['network_error_count'],1);self.assertFalse(self.h.path.with_suffix('.tmp').exists())
    def test_db_retry_bounded_and_rollback(self):
        action=AsyncMock() # actual retry function remains synchronous
        delays=[];calls=[]
        def failed():calls.append(1);raise sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.OperationalError):retry_busy(failed,delays.append)
        self.assertEqual(delays,[.1,.3,.6]);self.assertEqual(len(calls),4)
        db=BotDatabase(self.root/'db.sqlite')
        with self.assertRaises(ValueError):
            with db._connect() as c:c.execute("INSERT INTO users(telegram_id) VALUES(1)");raise ValueError()
        db.upsert_user(1,None,None)
        with db._connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
    def test_safe_logs_exclude_exception_body_and_third_party(self):
        root=logging.getLogger();previous=root.handlers[:]
        try:
            configure_logging(self.root/'bot.log')
            self.h.error(ValueError('token private CV'),'handler')
            logging.getLogger('httpx').critical('secret URL')
            data=(self.root/'bot.log').read_text();self.assertIn('ValueError',data)
            self.assertNotIn('private CV',data);self.assertNotIn('secret URL',data)
        finally:
            for handler in root.handlers:handler.close()
            root.handlers=previous
    @unittest.skipUnless(os.name=='nt','Windows watchdog')
    def test_watchdog_conflict_normal_stop_and_crash_cap(self):
        cmd=". ./scripts/bot_watchdog_policy.ps1; $n=Get-Date; foreach($code in @(0,3,4,130,1)) {Get-BotRestartDecision $code @($n) $n}; Get-BotRestartDecision 1 @($n,$n,$n,$n,$n) $n"
        r=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-Command',cmd],capture_output=True,text=True)
        self.assertEqual(r.returncode,0);self.assertEqual(r.stdout.split(),['stop','stop','stop','stop','retry','error'])

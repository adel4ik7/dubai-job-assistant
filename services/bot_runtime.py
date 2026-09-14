"""Supported PTB async lifecycle. PTB still owns steady-state polling retries."""
import asyncio
from datetime import datetime,timezone,timedelta
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import time

from telegram.error import NetworkError,TimedOut,RetryAfter,Conflict,InvalidToken
from telegram.request import HTTPXRequest
from services.collector_runtime import InstanceLock,AlreadyRunning

log=logging.getLogger('bot.runtime')
BACKOFF=(2,5,10,30,60)


def now(): return datetime.now(timezone.utc).isoformat()


def transient(exc):
    return isinstance(exc,(NetworkError,RetryAfter,ConnectionError,TimeoutError,OSError))


class BotHealth:
    def __init__(self,path):
        self.path=Path(path)
        self.data=dict(status='starting',started_at=now(),last_heartbeat_at=None,last_update_at=None,
            processed_updates=0,error_count=0,network_error_count=0,last_error_type=None)
        self.boot=time.monotonic();self.running=False
        self.save()
    def save(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.data,indent=2)+'\n',encoding='utf-8')
            os.replace(temp,self.path)
        except OSError:
            log.warning('Health write failed; error_type=OSError')
    def set(self,**values):
        if not set(values)<=self.data.keys():raise ValueError('Unknown health field')
        self.data.update(values);self.save()
    def error(self,exc,where):
        if getattr(self,'_last_error',None) is exc:return
        self._last_error=exc
        network=transient(exc)
        self.set(error_count=self.data['error_count']+1,
            network_error_count=self.data['network_error_count']+int(network),last_error_type=type(exc).__name__,
            status='error' if isinstance(exc,(Conflict,InvalidToken)) else 'degraded' if self.running else 'reconnecting')
        log.warning('Request failed; context=%s; error_type=%s',where,type(exc).__name__)
    def update_seen(self):
        self.set(last_update_at=now(),processed_updates=self.data['processed_updates']+1)
    def success(self):
        self._last_error=None
        if self.running and self.data['status'] not in ('running','error'):
            self.set(status='running')
    def heartbeat(self):
        self.set(last_heartbeat_at=now())
        log.info('Bot alive; uptime=%sm; status=%s',int((time.monotonic()-self.boot)/60),self.data['status'])


class SafeFilter(logging.Filter):
    def filter(self,record):
        if record.name!='bot.runtime':return False
        record.exc_info=None;record.exc_text=None;record.stack_info=None
        return True


def configure_logging(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    root=logging.getLogger();root.setLevel(logging.INFO)
    for handler in root.handlers[:]:handler.close();root.removeHandler(handler)
    for handler in (logging.StreamHandler(),RotatingFileHandler(path,maxBytes=2*1024*1024,backupCount=4,encoding='utf-8')):
        handler.addFilter(SafeFilter())
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        root.addHandler(handler)


class ResilientRequest(HTTPXRequest):
    """Normalize low-level failures for PTB; never retry ambiguous outbound sends."""
    def __init__(self,*args,health=None,**kwargs):
        self.health=health
        super().__init__(*args,**kwargs)
    async def post(self,*args,**kwargs):
        try:
            return await super().post(*args,**kwargs)
        except (NetworkError,RetryAfter,Conflict,InvalidToken) as exc:
            if self.health:
                self.health.error(exc,'telegram_request')
                if isinstance(exc,(Conflict,InvalidToken)):
                    callback=getattr(self.health,'fatal_callback',None)
                    if callback:callback(exc)
            raise

    async def do_request(self,*args,**kwargs):
        try:
            response=await super().do_request(*args,**kwargs)
        except (ConnectionError,TimeoutError,OSError) as exc:
            raise NetworkError('Temporary transport failure') from None
        if self.health and response[0]==200:self.health.success()
        return response


async def wait_stop(stop,delay):
    try:
        await asyncio.wait_for(stop.wait(),timeout=delay)
        return True
    except asyncio.TimeoutError:return False


async def bootstrap(action,stop,health,wait=wait_stop):
    """Only startup operations need this: public initialize() has no retry option."""
    attempt=0
    while not stop.is_set():
        try:
            await action()
            return True
        except (Conflict,InvalidToken):raise
        except Exception as exc:
            if not transient(exc):raise
            health.error(exc,'bootstrap')
            if isinstance(exc,RetryAfter):
                delay=exc.retry_after.total_seconds() if isinstance(exc.retry_after,timedelta) else exc.retry_after
            else:
                delay=BACKOFF[min(attempt,len(BACKOFF)-1)];attempt+=1
            log.warning('Bootstrap retry; delay_seconds=%s',delay)
            await wait(stop,delay)
    return False


async def serve(app,health,stop_path,*,heartbeat_seconds=300,drain_seconds=30,stop=None):
    stop=stop or asyncio.Event()
    app.bot_data['runtime_health']=health
    app.bot_data['runtime_stop']=stop
    fatal=[];loop=asyncio.get_running_loop();old_signals={}
    old_loop_handler=loop.get_exception_handler()
    def loop_error(loop,context):
        health.error(context.get('exception') or RuntimeError(),'async_task')
    loop.set_exception_handler(loop_error)
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:old_signals[sig]=signal.signal(sig,lambda *_:loop.call_soon_threadsafe(stop.set))
        except ValueError:pass
    def polling_error(exc):
        # PTB requires this callback to never raise.
        health.error(exc,'polling')
        if isinstance(exc,(Conflict,InvalidToken)):
            log.error('Telegram polling conflict; stop the other bot instance.' if isinstance(exc,Conflict) else 'Invalid bot token; check configuration.')
            fatal.append(exc);stop.set()
    health.fatal_callback=polling_error
    async def monitor():
        heartbeat_at=0
        while not stop.is_set():
            if time.monotonic()>=heartbeat_at:
                health.heartbeat();heartbeat_at=time.monotonic()+heartbeat_seconds
            try:
                if stop_path.exists():stop_path.unlink(missing_ok=True);stop.set()
            except OSError as exc:health.error(exc,'stop_request')
            await wait_stop(stop,.5)
    async def startup():
        if not await bootstrap(app.initialize,stop,health):return
        # bootstrap_retries=0 here: retry only transient bootstrap failures above,
        # terminating conflicts instead of PTB's retry-all-TelegramError policy.
        if not await bootstrap(lambda:app.updater.start_polling(timeout=10,poll_interval=.2,
            bootstrap_retries=0,drop_pending_updates=False,error_callback=polling_error),stop,health):return
        await app.start()
        if app.post_init:await app.post_init(app)
        health.running=True;health.set(status='running')
        log.info('Dubai Job Assistant is running...')
        await stop.wait()
    async def bounded(action,where):
        try:await asyncio.wait_for(action(),timeout=drain_seconds)
        except Exception as exc:health.error(exc,where)
    monitor_task=asyncio.create_task(monitor())
    work=asyncio.create_task(startup());stopper=asyncio.create_task(stop.wait())
    try:
        done,_=await asyncio.wait((work,stopper),return_when=asyncio.FIRST_COMPLETED)
        if work in done:await work
        elif not health.running:
            work.cancel();await asyncio.gather(work,return_exceptions=True)
        else:await work
    except Exception as exc:
        health.error(exc,'lifecycle');fatal.append(exc)
    finally:
        stop.set();log.info('Bot stopping; draining current operations.')
        if app.updater.running:await bounded(app.updater.stop,'polling_shutdown')
        # Stop explicit sender before app.stop; app.stop also stops a configured JobQueue.
        if app.post_stop:await bounded(lambda:app.post_stop(app),'jobs_shutdown')
        if app.running:await bounded(app.stop,'application_shutdown')
        await bounded(app.shutdown,'resources_shutdown')
        monitor_task.cancel();stopper.cancel()
        await asyncio.gather(monitor_task,stopper,return_exceptions=True)
        for sig,handler in old_signals.items():signal.signal(sig,handler)
        loop.set_exception_handler(old_loop_handler)
        health.running=False
        health.set(status='error' if fatal else 'stopped')
        log.info('Bot stopped; outcome=%s','error' if fatal else 'normal')
    return 4 if fatal and isinstance(fatal[0],Conflict) else 1 if fatal else 0

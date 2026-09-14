"""Collector-only lifecycle primitives. State/logs contain technical data only."""
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import time
from datetime import datetime, timezone

log = logging.getLogger('collector')
BACKOFF = (2, 5, 10, 30, 60)


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """Kernel-owned lock, automatically released on crash; never unlink its inode."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file = None
            raise AlreadyRunning('Collector is already running.') from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close(); self.file = None


def utc():
    return datetime.now(timezone.utc).isoformat()


class Health:
    def __init__(self, path):
        self.path = Path(path)
        self.data = dict(status='starting', started_at=utc(), last_heartbeat_at=None,
            last_message_at=None, enabled_sources=0, active_sources=0, reconnect_count=0,
            processed_count=0, error_count=0, last_error_type=None)
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.data, indent=2)+'\n', encoding='utf-8')
            os.replace(temp, self.path)
        except OSError:
            log.warning('Health write failed; error_type=OSError')

    def update(self, **values):
        if not set(values) <= self.data.keys():
            raise ValueError('Unknown health field')
        self.data.update(values); self.save()

    def error(self, exc):
        # Store class name only, never str(exc), traceback or request args.
        self.update(error_count=self.data['error_count']+1, last_error_type=type(exc).__name__)

    def processed(self):
        self.update(processed_count=self.data['processed_count']+1,last_message_at=utc())

    def heartbeat(self):
        self.update(last_heartbeat_at=utc())
        log.info('Collector alive; sources=%s; active_sources=%s; last_message_at=%s; reconnects=%s',
            self.data['enabled_sources'], self.data['active_sources'],
            self.data['last_message_at'] or 'none', self.data['reconnect_count'])


def configure_logging(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('collector'); logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in logger.handlers[:]:
        old.close(); logger.removeHandler(old)
    # Only the collector namespace is persisted. Third-party RPC logs are excluded.
    for handler in (logging.StreamHandler(), RotatingFileHandler(root/'collector.log',
                    maxBytes=2*1024*1024, backupCount=4, encoding='utf-8')):
        handler.setFormatter(formatter); logger.addHandler(handler)
    logging.getLogger('telethon').setLevel(logging.CRITICAL)


async def wait_stop(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def supervise(operation, connect, is_connected, stop, health, *, poll_seconds=300,
                    wake=None, wait=wait_stop):
    """Retry finite operations, not recursive event loops. Successful cycles reset backoff."""
    from telethon.errors import FloodWaitError
    attempt = 0
    force_connect = False
    connected_once = False
    while not stop.is_set():
        try:
            if force_connect or not is_connected():
                health.update(status='reconnecting' if connected_once else 'starting')
                log.info('Telegram disconnected; reconnecting.' if connected_once else 'Connecting to Telegram.')
                await connect()
                force_connect = False
                if connected_once:
                    health.update(reconnect_count=health.data['reconnect_count']+1)
                    log.info('Telegram reconnected.')
            connected_once = True
            await operation()
            health.update(status='running')
            attempt = 0
            if wake is None:
                await wait(stop, poll_seconds)
            else:
                # Event-driven catch-up with a one-second debounce, plus periodic fallback.
                await wait(stop, 1)
                if wake.is_set():
                    wake.clear()
                    continue
                sleeper = asyncio.create_task(wait(stop, min(poll_seconds, 15)))
                event = asyncio.create_task(wake.wait())
                try:
                    # Check disconnect every <=15s without querying Telegram unnecessarily.
                    deadline = time.monotonic()+poll_seconds
                    while not stop.is_set():
                        done, _ = await asyncio.wait((sleeper,event),return_when=asyncio.FIRST_COMPLETED)
                        if event in done or not is_connected() or time.monotonic() >= deadline:
                            break
                        sleeper = asyncio.create_task(wait(stop,min(15,max(0,deadline-time.monotonic()))))
                finally:
                    for task in (sleeper,event):
                        task.cancel()
                    await asyncio.gather(sleeper,event,return_exceptions=True)
                wake.clear()
        except FloodWaitError as exc:
            health.error(exc)
            log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
            await wait(stop, exc.seconds)
        except Exception as exc:
            health.error(exc)
            network = isinstance(exc,(ConnectionError,TimeoutError,OSError))
            force_connect = force_connect or network
            health.update(status='reconnecting' if network else 'error')
            delay=BACKOFF[min(attempt,len(BACKOFF)-1)]; attempt+=1
            log.warning('Collector cycle failed; error_type=%s; retry_seconds=%s',type(exc).__name__,delay)
            await wait(stop,delay)


async def heartbeat_loop(health, stop, interval=300):
    health.heartbeat()
    while not await wait_stop(stop,interval):
        health.heartbeat()


async def run_controlled(operation, health, stop_path, close_ocr=lambda: None, grace=30):
    """Ctrl+C and --stop use the same bounded drain, without cancelling active OCR first."""
    stop=asyncio.Event(); loop=asyncio.get_running_loop()
    old={}
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:
            old[sig]=signal.signal(sig,lambda *_: loop.call_soon_threadsafe(stop.set))
        except ValueError:
            pass
    async def requested():
        while not stop.is_set():
            if stop_path.exists():
                stop_path.unlink(missing_ok=True); stop.set(); return
            await wait_stop(stop,0.5)
    worker=asyncio.create_task(operation(stop))
    watcher=asyncio.create_task(requested())
    beat=asyncio.create_task(heartbeat_loop(health,stop))
    stopper=asyncio.create_task(stop.wait())
    try:
        done,_=await asyncio.wait((worker,stopper),return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
        else:
            log.info('Collector stopping; draining current work.')
            done,_=await asyncio.wait((worker,),timeout=grace)
            if not done:
                close_ocr()
                worker.cancel()
                await asyncio.gather(worker,return_exceptions=True)
    finally:
        stop.set()
        for task in (watcher,beat,stopper): task.cancel()
        await asyncio.gather(watcher,beat,stopper,return_exceptions=True)
        if not worker.done():
            close_ocr(); worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
        for sig,handler in old.items(): signal.signal(sig,handler)

"""Read-only public-channel collector. Run separately from bot.py."""
import argparse
import asyncio
import getpass
import logging
import re
import sqlite3
from datetime import datetime, timezone

from telethon import TelegramClient, errors, types, events, functions

from collector_config import load_collector_settings
from config import BASE_DIR
from db import Database
from vacancy_store import VacancyStore, normalize_source
from services.vacancy_pipeline import VacancyPipeline, analyze_text, prune_media
from services.collector_diagnostics import Diagnostics

from services.collector_runtime import (AlreadyRunning, InstanceLock, Health, configure_logging,
    supervise, run_controlled)

log = logging.getLogger('collector')


def public_source(entity, username):
    names = [getattr(entity, 'username', None)] + [u.username for u in (getattr(entity, 'usernames', None) or []) if u.active]
    return (isinstance(entity, types.Channel) and bool(entity.broadcast or entity.megagroup)
            and username.casefold() in [n.casefold() for n in names if n])


def bounded_backfill(value):
    try:
        number = int(value)
        if 1 <= number <= 500:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Backfill must be between 1 and 500.')


class Collector:
    def __init__(self, client, store, processor=None, sleep=asyncio.sleep, diagnostics=None, health=None):
        self.client, self.store, self.processor, self.sleep = client, store, processor, sleep
        self.diagnostics = diagnostics or Diagnostics()
        self.health = health
        self.active_sources = set()
        self.entity_ids = set()
        self.stop = None

    async def process_message(self, source, message, force=False):
        message_id = getattr(message, 'id', None)
        if not isinstance(message_id, int) or message_id <= 0:
            log.warning('Malformed message ignored; source_id=%s', source['id'])
            return
        previous = self.store.message_record(source['id'], message_id)
        if previous and not force:
            log.info('Duplicate skipped; source_id=%s message_id=%s', source['id'], message_id)
            self.diagnostics.event(source['id'], message_id, 'skip', reason='already_collected',
                vacancy_saved=False, duplicate=True, has_text=bool(getattr(message, 'message', '') or ''),
                has_photo=bool(getattr(message, 'photo', None)), media_downloaded=False,
                temp_path_exists=False, ocr_called=False)
            return
        raw = getattr(message, 'message', '') or ''
        raw = raw if isinstance(raw, str) else ''
        date = getattr(message, 'date', None)
        values = dict(raw_text=raw, combined_text=raw,
            published_at=date.astimezone(timezone.utc).isoformat() if isinstance(date, datetime) else None,
            source_url=f"https://t.me/{source['telegram_username']}/{message_id}")
        if self.processor:
            try:
                values.update(await self.processor(self.client, source, message))
                if self.health and values.get('ocr_status')=='failed':
                    self.health.update(error_count=self.health.data['error_count']+1,last_error_type='OCRFailure')
            except errors.FloodWaitError:
                raise
            except Exception as exc:
                if self.health: self.health.error(exc)
                self.diagnostics.event(source['id'], message_id, 'pipeline', reason='pipeline_failed', vacancy_saved=False)
                if force:
                    raise  # A failed retry must not replace a previously successful result.
                try:
                    values.update(analyze_text(raw))
                except Exception:
                    from services.vacancy_detector import detect_vacancy
                    values.update(detect_vacancy(raw))
                values['processing_error'] = 'pipeline_failed'
                log.warning('Message processing failed; source_id=%s message_id=%s', source['id'], message_id)
        try:
            if previous:
                vacancy_id, inserted = previous['id'], False
                # Preserve recognized image content on a failed/disabled retry.
                if previous['ocr_text'] and values.get('ocr_status') in {'failed', 'disabled', 'unavailable', 'oversized', 'empty'}:
                    log.warning('Skipped update: previous OCR preserved; source_id=%s message_id=%s', source['id'], message_id)
                    self.diagnostics.event(source['id'], message_id, 'save', reason='retry_failed_previous_preserved', vacancy_saved=False)
                    return previous
                self.store.update_pipeline(vacancy_id, values)
                log.info('Vacancy updated; source_id=%s message_id=%s vacancy_id=%s', source['id'], message_id, vacancy_id)
            else:
                vacancy_id, inserted = self.store.insert(source['id'], message_id, **values)
        except Exception:
            self.diagnostics.event(source['id'], message_id, 'save', reason='db_write_failed', vacancy_saved=False)
            raise
        row = self.store.get(vacancy_id)
        eligible = row['detection_status'] in {'vacancy', 'probably_vacancy'} and row['duplicate_of'] is None and bool(source['enabled'])
        self.diagnostics.event(source['id'], message_id, 'save_and_ui', vacancy_saved=True,
            duplicate=bool(row['duplicate_of']), ui_eligible=eligible,
            detection_score=row['detection_score'], detection_status=row['detection_status'],
            reason='duplicate' if row['duplicate_of'] else 'not_detected' if row['detection_status'] not in {'vacancy', 'probably_vacancy'} else 'source_disabled' if not source['enabled'] else 'visible_without_user_filters')
        if self.health: self.health.processed()
        log.info('Message processed; source_id=%s message_id=%s', source['id'], message_id)
        if row['duplicate_of']:
            log.info('Duplicate skipped from listings; vacancy_id=%s', vacancy_id)
        elif row['detection_status'] in {'vacancy', 'probably_vacancy'}:
            log.info('Vacancy detected; vacancy_id=%s', vacancy_id)
        return row

    async def collect_source(self, source, backfill=None):
        log.info('Source started; source_id=%s', source['id'])
        entity = await self.client.get_entity(source['telegram_username'])
        if not public_source(entity, source['telegram_username']):
            self.active_sources.discard(source['id'])
            log.warning('Source unavailable; source_id=%s', source['id'])
            return
        self.active_sources.add(source['id'])
        self.entity_ids.add(entity.id)
        if backfill:
            messages = await self.client.get_messages(entity, limit=backfill, wait_time=1)
            valid = [m for m in messages if isinstance(getattr(m, 'id', None), int) and m.id > 0]
            if len(valid) != len(messages):
                log.warning('Malformed messages skipped; source_id=%s', source['id'])
            messages = sorted(valid, key=lambda m: m.id)
        elif source['last_checked_at'] is None:
            # Establish a baseline; never fetch the channel's history implicitly.
            latest = await self.client.get_messages(entity, limit=1)
            self.store.checkpoint(source['id'], latest[0].id if latest else 0)
            return
        else:
            messages = self.client.iter_messages(entity, min_id=source['last_message_id'], reverse=True, limit=100, wait_time=1)
        highest = source['last_message_id']
        cursor_blocked = False
        async def handle(message):
            nonlocal highest, cursor_blocked
            if not isinstance(getattr(message, 'id', None), int) or message.id <= 0:
                log.warning('Malformed message skipped; source_id=%s', source['id'])
                return
            for attempt in range(3):
                try:
                    await self.process_message(source, message)
                    if not cursor_blocked:
                        highest = max(highest, message.id)
                        self.store.checkpoint(source['id'], highest)
                    return
                except errors.FloodWaitError:
                    raise
                except Exception as exc:
                    if self.health: self.health.error(exc)
                    if isinstance(exc, sqlite3.OperationalError) and ('locked' in str(exc).lower() or 'busy' in str(exc).lower()) and attempt < 2:
                        log.warning('DB busy; source_id=%s message_id=%s; retry=%s',source['id'],message.id,attempt+1)
                        await self.sleep((1,2)[attempt])
                        continue
                    # Continue this batch but never checkpoint past an uncommitted message.
                    cursor_blocked = True
                    log.warning('Message failed; source_id=%s message_id=%s; error_type=%s',source['id'],message.id,type(exc).__name__)
                    return
        if isinstance(messages, list):
            for message in messages:
                if self.stop and self.stop.is_set(): break
                await handle(message)
        else:
            async for message in messages:
                if self.stop and self.stop.is_set(): break
                await handle(message)
        self.store.checkpoint(source['id'], highest)
        log.info('Source checked; source_id=%s; last_message_id=%s',source['id'],highest)

    async def run_once(self, backfill=None):
        sources = self.store.sources(enabled_only=True)
        self.active_sources.intersection_update(s['id'] for s in sources)
        if self.health: self.health.update(enabled_sources=len(sources))
        for source in sources:
            if self.stop and self.stop.is_set(): break
            while True:
                try:
                    # Reload the checkpoint after partial progress and FloodWait.
                    fresh = next(s for s in self.store.sources() if s['id'] == source['id'])
                    if fresh['enabled']:
                        await self.collect_source(fresh, backfill)
                    break
                except errors.FloodWaitError as exc:
                    log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
                    if self.stop:
                        from services.collector_runtime import wait_stop
                        if await wait_stop(self.stop,exc.seconds): return
                    else:
                        await self.sleep(exc.seconds)
                except (ConnectionError, TimeoutError, OSError):
                    raise
                except Exception as exc:
                    self.active_sources.discard(source['id'])
                    if self.health: self.health.error(exc)
                    log.warning('Source unavailable; source_id=%s; error_type=%s', source['id'],type(exc).__name__)
                    break
            if self.health: self.health.update(active_sources=len(self.active_sources))
            await self.sleep(1)


async def authenticate(client, phone):
    await client.connect()
    if not await client.is_user_authorized():
        phone = phone or getpass.getpass('Telegram phone (hidden): ')
        await client.send_code_request(phone)
        try:
            await client.sign_in(phone, getpass.getpass('Telegram verification code (hidden): '))
        except errors.SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass('Telegram 2FA password (hidden): '))
    # A bot login/session is never valid for this separate user-account reader.
    if (await client.get_me()).bot:
        raise ValueError('Use a user-account session for the collector.')


async def run(args, health=None):
    diagnostics = Diagnostics(args.debug_pipeline)
    if args.prepare_ocr:
        from services.ocr import EasyOCREngine
        await asyncio.to_thread(EasyOCREngine, BASE_DIR / 'ocr_models', True)
        log.info('Local OCR models are ready.')
        return
    data_dir = BASE_DIR / 'data'
    data_dir.mkdir(exist_ok=True)
    for attempt in range(3):
        try:
            store = VacancyStore(Database(data_dir / 'bot.sqlite3'))
            log.info('Database self-check passed.')
            break
        except sqlite3.OperationalError as exc:
            if health: health.error(exc)
            if attempt==2 or not any(word in str(exc).lower() for word in ('locked','busy')):
                raise
            log.warning('Database startup busy; retry=%s',attempt+1)
            await asyncio.sleep(attempt+1)
    store.seed_sources(BASE_DIR / 'sources.json')
    if args.reprocess:
        for row in store.retry_candidates(args.reprocess):
            try:
                store.update_pipeline(row['id'], analyze_text(row['raw_text'], row['ocr_text']))
            except Exception:
                log.warning('Reprocessing failed; vacancy_id=%s', row['id'])
        return
    if args.add_source:
        source_id = store.add_source(args.add_source, title=args.source_title)
        log.info('Source added or already present; source_id=%s', source_id)
        return
    if args.remove_source:
        source_id = store.remove_source(args.remove_source)
        log.info('Source removed from collection; existing posts retained; source_id=%s', source_id)
        return
    if args.disable_source or args.enable_source:
        store.enable_source(args.disable_source or args.enable_source, bool(args.enable_source))
        return
    if args.list_sources:
        for source in store.sources(include_removed=True):
            print(source['id'], source['telegram_username'], source['title'],
                  'removed' if source['removed_at'] else 'enabled' if source['enabled'] else 'disabled')
        return
    if args.source_quality:
        print('SOURCE QUALITY (latest stored outcome per unique source/message)')
        for row in store.source_quality():
            state = 'removed' if row['removed_at'] else 'enabled' if row['enabled'] else 'disabled'
            print(f"{row['telegram_username']} | {row['title']} | {state}")
            print('  ' + ' '.join(f'{key}={row[key]}' for key in (
                'processed', 'vacancies', 'probably_vacancy', 'not_vacancy', 'pending',
                'ocr_messages', 'ocr_failures', 'ocr_unavailable', 'duplicates')))
            print(f"  avg_detection_score={row['avg_detection_score']:.1f} "
                  f"useful_rate={row['useful_rate']:.1%} (vacancy + probably_vacancy)")
        return
    settings = load_collector_settings()
    log.info('OCR configured: %s', 'enabled' if settings.ocr_enabled else 'disabled (image-only posts cannot be detected; set VACANCY_OCR_ENABLED=true)')
    settings.session_path.parent.mkdir(exist_ok=True)
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash,
                            receive_updates=True, flood_sleep_threshold=0, timeout=15,
                            connection_retries=3, request_retries=3, retry_delay=2, auto_reconnect=True)
    engine = None
    collector = None
    disconnect_task = None
    wake = asyncio.Event()
    async def connect():
        nonlocal disconnect_task
        if disconnect_task is not None:
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task,return_exceptions=True)
        if client.is_connected():
            await client.disconnect()
        await authenticate(client, settings.phone)
        await client(functions.updates.GetStateRequest())
        log.info('Telegram session self-check passed; updates enabled.')
        async def watch_disconnect():
            try:
                await client.disconnected
            except Exception as exc:
                if health: health.error(exc)
                log.warning('Telegram disconnected; error_type=%s',type(exc).__name__)
            finally:
                wake.set()
        disconnect_task = asyncio.create_task(watch_disconnect())
    async def execute(stop):
        nonlocal engine, collector
        # A bounded native worker also makes Ctrl+C during OCR drainable.
        from services.collector_ocr import ManagedOCREngine
        if settings.ocr_enabled:
            try:
                engine = ManagedOCREngine(BASE_DIR / 'ocr_models',wait_ready=False)
                await asyncio.to_thread(engine.ready)
                log.info('OCR engine ready; local EN/RU models loaded on CPU.')
            except Exception as exc:
                if engine is not None: engine.close()
                engine = None
                if health: health.error(exc)
                log.warning('OCR unavailable; continuing in text-only mode; error_type=%s',type(exc).__name__)
        collector = Collector(client, store, VacancyPipeline(settings, engine, diagnostics), diagnostics=diagnostics, health=health)
        collector.stop = stop
        if args.reprocess_backfill or args.reprocess_message or args.retry_ocr:
            await connect()
        if args.reprocess_backfill:
            await reprocess_backfill(collector, *args.reprocess_backfill)
            return
        if args.reprocess_message:
            await reprocess_message(collector, *args.reprocess_message)
            return
        if args.retry_ocr:
            if not settings.ocr_enabled or engine is None:
                log.warning('Enable OCR and prepare its local models before --retry-ocr.')
                return
            await retry_ocr(collector, args.retry_ocr)
            return
        async def on_message(event):
            # Only a wake-up signal: serialized history catch-up owns checkpoints/OCR.
            channel_id = getattr(getattr(event.message,'peer_id',None),'channel_id',None)
            if not stop.is_set() and channel_id in collector.entity_ids:
                wake.set()
        client.add_event_handler(on_message, events.NewMessage())
        async def cycle():
            try:
                prune_media(settings.media_dir, settings.media_retention_days)
            except Exception as exc:
                if health: health.error(exc)
                log.warning('Media cleanup failed; error_type=%s',type(exc).__name__)
            await collector.run_once(args.backfill)
            log.info('Collector listening for new messages; enabled_sources=%s; active_sources=%s',
                health.data['enabled_sources'],health.data['active_sources'])
            if args.once or args.backfill:
                stop.set()
        try:
            await supervise(cycle, connect, client.is_connected, stop, health,
                            poll_seconds=settings.poll_seconds,wake=wake)
        finally:
            client.remove_event_handler(on_message)
    def close_ocr():
        if engine is not None:
            engine.close()
    try:
        await run_controlled(execute, health, BASE_DIR/'runtime'/'collector.stop',close_ocr)
    finally:
        close_ocr()
        if disconnect_task is not None:
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task,return_exceptions=True)
        try:
            await asyncio.wait_for(client.disconnect(),timeout=10)
        except Exception as exc:
            if health: health.error(exc)
            log.warning('Disconnect cleanup failed; error_type=%s',type(exc).__name__)



async def retry_ocr(collector, limit):
    sources = {s['id']: s for s in collector.store.sources(True)}
    for row in collector.store.retry_candidates(limit, ocr_only=True):
        while True:
            try:
                source = sources[row['source_id']]
                entity = await collector.client.get_entity(source['telegram_username'])
                if not public_source(entity, source['telegram_username']):
                    break
                message = await collector.client.get_messages(entity, ids=row['source_message_id'])
                if message:
                    await collector.process_message(source, message, force=True)
                break
            except errors.FloodWaitError as exc:
                await collector.sleep(exc.seconds)
            except Exception:
                log.warning('OCR retry failed; vacancy_id=%s', row['id'])
                break
        await collector.sleep(1)


async def reprocess_backfill(collector, username, limit):
    """One bounded snapshot; reuse force-update/hash dedup without moving cursors."""
    limit = bounded_backfill(limit)
    source = next((s for s in collector.store.sources(True)
                   if s['telegram_username'].casefold() == username.removeprefix('@').casefold()), None)
    if not source:
        log.warning('Backfill retry refused: source must be configured and enabled.')
        return
    while True:
        try:
            entity = await collector.client.get_entity(source['telegram_username'])
            names = [getattr(entity, 'username', None)] + [u.username for u in (getattr(entity, 'usernames', None) or []) if u.active]
            if not public_source(entity, source['telegram_username']):
                log.warning('Backfill retry refused: source is not an accessible public channel/group; source_id=%s', source['id'])
                return
            messages = await collector.client.get_messages(entity, limit=limit, wait_time=1)
            break
        except errors.FloodWaitError as exc:
            log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
            await collector.sleep(exc.seconds)
    seen = set()
    processed, failed, skipped = 0, 0, 0
    for message in reversed(messages[:limit]):
        message_id = getattr(message, 'id', None)
        if not isinstance(message_id, int) or message_id <= 0 or isinstance(message, types.MessageEmpty) or message_id in seen:
            skipped += 1
            log.info('Skipped malformed, missing or repeated message; source_id=%s', source['id'])
            continue
        seen.add(message_id)
        while True:
            try:
                if getattr(message, 'photo', None) or getattr(getattr(message, 'document', None), 'mime_type', '').startswith('image/'):
                    log.info('OCR rerun requested; source_id=%s message_id=%s (requires enabled OCR and local models)', source['id'], message_id)
                await collector.process_message(source, message, force=True)
                processed += 1
                break
            except errors.FloodWaitError as exc:
                log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
                await collector.sleep(exc.seconds)
            except Exception:
                failed += 1
                log.warning('Backfill message retry failed; source_id=%s message_id=%s', source['id'], message_id)
                break
        await collector.sleep(1)
    log.info('Backfill retry finished; source_id=%s processed=%s skipped=%s failed=%s', source['id'], processed, skipped, failed)


async def reprocess_message(collector, username, message_id):
    try:
        message_id = int(message_id)
        if message_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('Message ID must be a positive integer') from None
    source = next((s for s in collector.store.sources(True)
                   if s['telegram_username'].casefold() == username.removeprefix('@').casefold()), None)
    if not source:
        log.warning('Single-message retry refused: source must be configured and enabled.')
        return
    while True:
        try:
            entity = await collector.client.get_entity(source['telegram_username'])
            names = [getattr(entity, 'username', None)] + [u.username for u in (getattr(entity, 'usernames', None) or []) if u.active]
            if not public_source(entity, source['telegram_username']):
                collector.diagnostics.event(source['id'], message_id, 'skip', reason='not_public_channel', vacancy_saved=False)
                return
            message = await collector.client.get_messages(entity, ids=message_id)
            if message is None or isinstance(message, types.MessageEmpty):
                collector.diagnostics.event(source['id'], message_id, 'skip', reason='message_missing', vacancy_saved=False)
                return
            # Deliberately leave the collection high-water mark unchanged.
            return await collector.process_message(source, message, force=True)
        except errors.FloodWaitError as exc:
            log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
            await collector.sleep(exc.seconds)


def main():
    parser = argparse.ArgumentParser(description='Read configured public Telegram job channels into local SQLite.')
    parser.add_argument('--backfill', type=bounded_backfill, help='Fetch latest N posts (1-500), then exit.')
    parser.add_argument('--once', action='store_true', help='One polling cycle, then exit.')
    parser.add_argument('--debug-pipeline', action='store_true', help='Temporary safe stage diagnostics; masked OCR preview up to 120 characters.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--prepare-ocr', action='store_true', help='Download free EN/RU OCR models once; no Telegram login.')
    group.add_argument('--reprocess', type=bounded_backfill, metavar='N', help='Reprocess up to N pending/failed stored texts offline.')
    group.add_argument('--retry-ocr', type=bounded_backfill, metavar='N', help='Retry up to N failed/disabled images from Telegram.')
    group.add_argument('--reprocess-message', nargs=2, metavar=('SOURCE', 'MESSAGE_ID'), help='Read and reprocess exactly one configured public-channel post, without advancing its cursor.')
    group.add_argument('--reprocess-backfill', nargs=2, metavar=('SOURCE', 'N'), help='Reprocess latest 1-500 posts of one configured source, updating existing rows without advancing its cursor.')
    group.add_argument('--add-source', metavar='USERNAME_OR_LINK')
    parser.add_argument('--source-title', help='Optional RU/EN display name with --add-source.')
    group.add_argument('--list-sources', action='store_true')
    group.add_argument('--source-quality', action='store_true', help='Show per-source quality from local stored posts; no Telegram login.')
    group.add_argument('--disable-source', metavar='ID_OR_USERNAME_OR_LINK')
    group.add_argument('--enable-source', metavar='ID_OR_USERNAME_OR_LINK')
    group.add_argument('--remove-source', metavar='ID_OR_USERNAME_OR_LINK', help='Stop collection without deleting vacancies or saved links; explicit add restores it.')
    group.add_argument('--stop', action='store_true', help='Request graceful stop of the running collector.')
    args = parser.parse_args()
    if args.source_title is not None and not args.add_source:
        parser.error('--source-title requires --add-source.')
    if args.add_source:
        try:
            args.add_source = normalize_source(args.add_source)
        except ValueError as exc:
            parser.error(str(exc))
    if args.reprocess_backfill:
        username, number = args.reprocess_backfill
        if args.backfill is not None:
            parser.error('Use either --backfill or --reprocess-backfill, not both.')
        if not re.fullmatch(r'@?[a-zA-Z][a-zA-Z0-9_]{3,31}', username):
            parser.error('--reprocess-backfill requires a public source username.')
        try:
            args.reprocess_backfill = (username, bounded_backfill(number))
        except argparse.ArgumentTypeError:
            parser.error('--reprocess-backfill N must be between 1 and 500.')
    if args.reprocess_message:
        username, number = args.reprocess_message
        if not re.fullmatch(r'@?[a-zA-Z][a-zA-Z0-9_]{3,31}', username) or not number.isascii() or not number.isdigit() or not 0 < int(number) <= 2147483647:
            parser.error('--reprocess-message requires a public username and positive message ID.')
    runtime = BASE_DIR/'runtime'
    if args.stop:
        runtime.mkdir(exist_ok=True)
        (runtime/'collector.stop').write_text('stop',encoding='utf-8')
        print('Collector stop requested.')
        return
    # Read-only/config management commands do not open the Telethon session.
    local = any((args.list_sources,args.source_quality,args.add_source,args.disable_source,
                 args.enable_source,args.remove_source,args.prepare_ocr,args.reprocess))
    if local:
        logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
        try:
            asyncio.run(run(args))
        except KeyboardInterrupt:
            return
        except Exception as exc:
            log.error('Collector command failed; error_type=%s',type(exc).__name__)
            raise SystemExit(1) from None
        return
    health = None
    try:
        with InstanceLock(runtime/'collector.lock'):
            configure_logging(BASE_DIR/'logs')
            (runtime/'collector.stop').unlink(missing_ok=True)
            health = Health(runtime/'collector_health.json')
            try:
                asyncio.run(run(args,health))
            except KeyboardInterrupt:
                pass
            except Exception as exc:
                health.error(exc); health.update(status='error')
                log.error('Collector failed; error_type=%s; check configuration/session/permissions.',type(exc).__name__)
                raise SystemExit(1) from None
            health.update(status='stopped')
            log.info('Collector stopped.')
    except AlreadyRunning:
        print('Collector is already running.')
        raise SystemExit(3) from None


if __name__ == '__main__':
    main()

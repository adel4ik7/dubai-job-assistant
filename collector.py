"""Read-only public-channel collector. Run separately from bot.py."""
import argparse
import asyncio
import getpass
import logging
import re
from datetime import datetime, timezone

from telethon import TelegramClient, errors, types

from collector_config import load_collector_settings
from config import BASE_DIR
from db import Database
from vacancy_store import VacancyStore, normalize_source
from services.vacancy_pipeline import VacancyPipeline, analyze_text, prune_media
from services.collector_diagnostics import Diagnostics

log = logging.getLogger('collector')


def bounded_backfill(value):
    try:
        number = int(value)
        if 1 <= number <= 500:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Backfill must be between 1 and 500.')


class Collector:
    def __init__(self, client, store, processor=None, sleep=asyncio.sleep, diagnostics=None):
        self.client, self.store, self.processor, self.sleep = client, store, processor, sleep
        self.diagnostics = diagnostics or Diagnostics()

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
            except errors.FloodWaitError:
                raise
            except Exception:
                self.diagnostics.event(source['id'], message_id, 'pipeline', reason='pipeline_failed', vacancy_saved=False)
                if force:
                    raise  # A failed retry must not replace a previously successful result.
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
        log.info('Message processed; source_id=%s message_id=%s', source['id'], message_id)
        if row['duplicate_of']:
            log.info('Duplicate skipped from listings; vacancy_id=%s', vacancy_id)
        elif row['detection_status'] in {'vacancy', 'probably_vacancy'}:
            log.info('Vacancy detected; vacancy_id=%s', vacancy_id)
        return row

    async def collect_source(self, source, backfill=None):
        log.info('Source started; source_id=%s', source['id'])
        entity = await self.client.get_entity(source['telegram_username'])
        public_names = [getattr(entity, 'username', None)] + [u.username for u in (getattr(entity, 'usernames', None) or []) if u.active]
        if not isinstance(entity, types.Channel) or not entity.broadcast or source['telegram_username'].lower() not in [n.lower() for n in public_names if n]:
            log.warning('Source is not an allowed public broadcast channel; source_id=%s', source['id'])
            return
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
        async def handle(message):
            nonlocal highest
            if not isinstance(getattr(message, 'id', None), int) or message.id <= 0:
                log.warning('Malformed message skipped; source_id=%s', source['id'])
                return
            await self.process_message(source, message)
            highest = max(highest, getattr(message, 'id', 0) or 0)
            self.store.checkpoint(source['id'], highest)
        if isinstance(messages, list):
            for message in messages:
                await handle(message)
        else:
            async for message in messages:
                await handle(message)
        self.store.checkpoint(source['id'], highest)

    async def run_once(self, backfill=None):
        for source in self.store.sources(enabled_only=True):
            while True:
                try:
                    # Reload the checkpoint after partial progress and FloodWait.
                    fresh = next(s for s in self.store.sources() if s['id'] == source['id'])
                    if fresh['enabled']:
                        await self.collect_source(fresh, backfill)
                    break
                except errors.FloodWaitError as exc:
                    log.warning('Telegram requested a wait; seconds=%s', exc.seconds)
                    await self.sleep(exc.seconds)
                except Exception:
                    # DB/network errors leave the cursor at the last committed message.
                    log.warning('Source failed; retry on next poll; source_id=%s', source['id'])
                    break
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


async def run(args):
    diagnostics = Diagnostics(args.debug_pipeline)
    if args.prepare_ocr:
        from services.ocr import EasyOCREngine
        await asyncio.to_thread(EasyOCREngine, BASE_DIR / 'ocr_models', True)
        log.info('Local OCR models are ready.')
        return
    data_dir = BASE_DIR / 'data'
    data_dir.mkdir(exist_ok=True)
    store = VacancyStore(Database(data_dir / 'bot.sqlite3'))
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
                            receive_updates=False, flood_sleep_threshold=0)
    try:
        while True:
            try:
                await authenticate(client, settings.phone)
                break
            except errors.FloodWaitError as exc:
                log.warning('Telegram requested an authentication wait; seconds=%s', exc.seconds)
                await asyncio.sleep(exc.seconds)
        from services.ocr import EasyOCREngine
        engine = None
        if settings.ocr_enabled:
            try:
                engine = await asyncio.to_thread(EasyOCREngine, BASE_DIR / 'ocr_models')
                log.info('OCR engine ready; local EN/RU models loaded on CPU.')
            except Exception as exc:
                reason = 'dependency_missing' if isinstance(exc, ImportError) else 'model_missing' if isinstance(exc, FileNotFoundError) else 'native_library_or_model_access' if isinstance(exc, OSError) else 'engine_initialization_failed'
                log.warning('OCR unavailable; reason=%s. Check requirements-ocr.txt and --prepare-ocr.', reason)
        collector = Collector(client, store, VacancyPipeline(settings, engine, diagnostics), diagnostics=diagnostics)
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
        while True:
            prune_media(settings.media_dir, settings.media_retention_days)
            await collector.run_once(args.backfill)
            if args.once or args.backfill:
                break
            await asyncio.sleep(settings.poll_seconds)
    finally:
        await client.disconnect()


async def retry_ocr(collector, limit):
    sources = {s['id']: s for s in collector.store.sources(True)}
    for row in collector.store.retry_candidates(limit, ocr_only=True):
        while True:
            try:
                source = sources[row['source_id']]
                entity = await collector.client.get_entity(source['telegram_username'])
                if not isinstance(entity, types.Channel) or not entity.broadcast or not entity.username:
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
            if not isinstance(entity, types.Channel) or not entity.broadcast or source['telegram_username'].casefold() not in [n.casefold() for n in names if n]:
                log.warning('Backfill retry refused: source is not a public broadcast channel; source_id=%s', source['id'])
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
            if not isinstance(entity, types.Channel) or not entity.broadcast or source['telegram_username'].casefold() not in [n.casefold() for n in names if n]:
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
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('telethon').setLevel(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info('Collector stopped.')
    except Exception:
        # Never render exception messages: RPC errors can contain auth/user data.
        log.error('Collector could not start. Check .env, local permissions and Telegram authorization.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()

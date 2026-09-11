"""Read-only public-channel collector. Run separately from bot.py."""
import argparse
import asyncio
import getpass
import logging

from telethon import TelegramClient, errors, types

from collector_config import load_collector_settings
from config import BASE_DIR
from db import Database
from vacancy_store import VacancyStore

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
    def __init__(self, client, store, processor=None, sleep=asyncio.sleep):
        self.client, self.store, self.processor, self.sleep = client, store, processor, sleep

    async def process_message(self, source, message):
        message_id = getattr(message, 'id', None)
        if not isinstance(message_id, int) or message_id <= 0:
            log.warning('Malformed message ignored; source_id=%s', source['id'])
            return
        if self.store.has_message(source['id'], message_id):
            log.info('Duplicate skipped; source_id=%s message_id=%s', source['id'], message_id)
            return
        raw = getattr(message, 'message', '') or ''
        date = getattr(message, 'date', None)
        values = dict(raw_text=raw, combined_text=raw,
            published_at=date.isoformat() if date else None,
            source_url=f"https://t.me/{source['telegram_username']}/{message_id}")
        if self.processor:
            try:
                values.update(await self.processor(self.client, source, message))
            except errors.FloodWaitError:
                raise
            except Exception:
                values['processing_error'] = 'pipeline_failed'
                log.warning('Message processing failed; source_id=%s message_id=%s', source['id'], message_id)
        vacancy_id, inserted = self.store.insert(source['id'], message_id, **values)
        row = self.store.get(vacancy_id)
        log.info('Message processed; source_id=%s message_id=%s', source['id'], message_id)
        if inserted and row['duplicate_of']:
            log.info('Duplicate skipped from listings; vacancy_id=%s', vacancy_id)
        elif row['detection_status'] in {'vacancy', 'probably_vacancy'}:
            log.info('Vacancy detected; vacancy_id=%s', vacancy_id)

    async def collect_source(self, source, backfill=None):
        log.info('Source started; source_id=%s', source['id'])
        entity = await self.client.get_entity(source['telegram_username'])
        public_names = [getattr(entity, 'username', None)] + [u.username for u in (getattr(entity, 'usernames', None) or []) if u.active]
        if not isinstance(entity, types.Channel) or not entity.broadcast or source['telegram_username'].lower() not in [n.lower() for n in public_names if n]:
            log.warning('Source is not an allowed public broadcast channel; source_id=%s', source['id'])
            return
        if backfill:
            messages = await self.client.get_messages(entity, limit=backfill, wait_time=1)
            messages = sorted(messages, key=lambda m: m.id)
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
    data_dir = BASE_DIR / 'data'
    data_dir.mkdir(exist_ok=True)
    store = VacancyStore(Database(data_dir / 'bot.sqlite3'))
    store.seed_sources(BASE_DIR / 'sources.json')
    if args.add_source:
        store.add_source(args.add_source)
        return
    if args.disable_source or args.enable_source:
        store.enable_source(args.disable_source or args.enable_source, bool(args.enable_source))
        return
    if args.list_sources:
        for source in store.sources():
            print(source['id'], source['telegram_username'], 'enabled' if source['enabled'] else 'disabled')
        return
    settings = load_collector_settings()
    settings.session_path.parent.mkdir(exist_ok=True)
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash,
                            receive_updates=False, flood_sleep_threshold=0)
    try:
        await authenticate(client, settings.phone)
        collector = Collector(client, store)
        while True:
            await collector.run_once(args.backfill)
            if args.once or args.backfill:
                break
            await asyncio.sleep(settings.poll_seconds)
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description='Read configured public Telegram job channels into local SQLite.')
    parser.add_argument('--backfill', type=bounded_backfill, help='Fetch latest N posts (1-500), then exit.')
    parser.add_argument('--once', action='store_true', help='One polling cycle, then exit.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--add-source', metavar='USERNAME')
    group.add_argument('--list-sources', action='store_true')
    group.add_argument('--disable-source', type=int, metavar='ID')
    group.add_argument('--enable-source', type=int, metavar='ID')
    args = parser.parse_args()
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

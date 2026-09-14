import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from datetime import datetime, timezone

from telethon import types, errors
from collector import Collector, public_source
from db import Database
from vacancy_store import VacancyStore
from services.collector_runtime import (InstanceLock, AlreadyRunning, Health, supervise,
    heartbeat_loop, run_controlled, configure_logging)
from services.vacancy_pipeline import analyze_text


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.health=Health(self.root/'health.json')

    async def test_lifecycle_remains_alive_and_clean_stop(self):
        stop=asyncio.Event(); operation=AsyncMock(); connected=lambda: True
        task=asyncio.create_task(supervise(operation,AsyncMock(),connected,stop,self.health,poll_seconds=.01))
        await asyncio.sleep(.04)
        self.assertFalse(task.done());self.assertGreaterEqual(operation.await_count,2)
        stop.set();await asyncio.wait_for(task,1)

    async def test_disconnect_reconnect_and_backoff_reset(self):
        stop=asyncio.Event(); delays=[]; connected=False
        async def connect():
            nonlocal connected
            connected=True
        operation=AsyncMock(side_effect=[ConnectionError('private secret'),None,TimeoutError('secret'),None])
        async def wait(event,seconds):
            delays.append(seconds)
            if len(delays)==4: stop.set()
        await supervise(operation,connect,lambda:connected,stop,self.health,poll_seconds=300,wait=wait)
        self.assertEqual(delays,[2,300,2,300]);self.assertEqual(self.health.data['reconnect_count'],2)
        self.assertNotIn('secret',self.health.path.read_text())

    async def test_backoff_capped_and_flood_wait_respected(self):
        stop=asyncio.Event(); delays=[]
        async def wait(event,seconds):
            delays.append(seconds)
            if len(delays)==8:stop.set()
        op=AsyncMock(side_effect=[errors.FloodWaitError(None,capture=67)]+[OSError('DNS private')]*7)
        await supervise(op,AsyncMock(),lambda:True,stop,self.health,wait=wait)
        self.assertEqual(delays,[67,2,5,10,30,60,60,60])

    async def test_new_message_wakeup_triggers_early_history_sync(self):
        stop=asyncio.Event();wake=asyncio.Event();operation=AsyncMock()
        task=asyncio.create_task(supervise(operation,AsyncMock(),lambda:True,stop,self.health,poll_seconds=300,wake=wake))
        await asyncio.sleep(.02);wake.set()
        await asyncio.sleep(1.05)
        self.assertGreaterEqual(operation.await_count,2)
        stop.set();await asyncio.wait_for(task,1)

    async def test_heartbeat_runs_even_without_new_messages(self):
        stop=asyncio.Event()
        with self.assertLogs('collector') as logs:
            task=asyncio.create_task(heartbeat_loop(self.health,stop,interval=.01))
            await asyncio.sleep(.035);stop.set();await task
        self.assertGreaterEqual(sum('Collector alive' in l for l in logs.output),2)
        data=json.loads(self.health.path.read_text())
        self.assertIsNotNone(data['last_heartbeat_at']);self.assertIsNone(data['last_message_at'])

    async def test_explicit_stop_drains_work_and_no_orphan_task(self):
        stop_path=self.root/'stop'; finished=asyncio.Event()
        async def operation(stop):
            await stop.wait();await asyncio.sleep(.01);finished.set()
        task=asyncio.create_task(run_controlled(operation,self.health,stop_path,grace=.1))
        await asyncio.sleep(.01);stop_path.touch()
        await asyncio.wait_for(task,2)
        self.assertTrue(finished.is_set());self.assertFalse(stop_path.exists())

    async def test_shutdown_cancels_hung_work_after_grace(self):
        stop_path=self.root/'stop'; closed=[]
        async def operation(stop):await asyncio.Event().wait()
        task=asyncio.create_task(run_controlled(operation,self.health,stop_path,lambda:closed.append(True),grace=.01))
        await asyncio.sleep(.01);stop_path.touch();await asyncio.wait_for(task,2)
        self.assertTrue(closed)

    def test_lock_rejects_second_and_recovers_stale_file(self):
        path=self.root/'collector.lock';path.write_text('stale process text')
        with InstanceLock(path):
            with self.assertRaises(AlreadyRunning):
                with InstanceLock(path):pass
        with InstanceLock(path):pass

    def test_crash_releases_kernel_lock(self):
        path=self.root/'crash.lock'
        script="from services.collector_runtime import InstanceLock; import os; lock=InstanceLock(__import__('pathlib').Path(__import__('sys').argv[1])); lock.__enter__(); os._exit(9)"
        result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True)
        self.assertEqual(result.returncode,9)
        with InstanceLock(path):pass

    def test_health_atomic_and_no_private_error_details(self):
        self.health.processed();self.health.error(ValueError('phone email secret contents'))
        data=json.loads(self.health.path.read_text());self.assertEqual(data['processed_count'],1)
        self.assertEqual(data['last_error_type'],'ValueError');self.assertNotIn('secret',self.health.path.read_text())
        self.assertFalse(self.health.path.with_suffix('.tmp').exists())

    def test_rotating_log_has_no_third_party_messages(self):
        logger=logging.getLogger('collector'); old=logger.handlers[:];propagate=logger.propagate
        try:
            configure_logging(self.root/'logs')
            logger.info('Collector alive; sources=7')
            logging.getLogger('telethon').warning('private authentication secret')
            data=(self.root/'logs'/'collector.log').read_text(encoding='utf-8')
            self.assertIn('Collector alive',data);self.assertNotIn('secret',data)
            rotating=logger.handlers[-1];self.assertEqual(rotating.backupCount,4)
        finally:
            for handler in logger.handlers:handler.close()
            logger.handlers=old;logger.propagate=propagate

    @unittest.skipUnless(os.name=='nt','PowerShell wrapper on Windows')
    def test_watchdog_policy_stops_clean_exit_and_bounds_crashes(self):
        command=". ./scripts/watchdog_policy.ps1; $now=Get-Date; Get-CollectorRestartDecision 0 @($now) $now; Get-CollectorRestartDecision 3 @($now) $now; Get-CollectorRestartDecision 130 @($now) $now; Get-CollectorRestartDecision 1 @($now) $now; Get-CollectorRestartDecision 1 @($now,$now,$now,$now,$now) $now; Get-CollectorRestartDecision 1 @($now.AddMinutes(-11)) $now"
        result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-Command',command],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.split(),['stop','stop','stop','retry','error','retry'])


class CollectionReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.db=Database(root/'db.sqlite');self.store=VacancyStore(self.db)
        self.sid=self.store.add_source('test_jobs');self.health=Health(root/'health.json')
        self.entity=types.Channel(id=1,title='Test',photo=types.ChatPhotoEmpty(),date=datetime.now(timezone.utc),broadcast=True,username='test_jobs')
        self.client=SimpleNamespace(get_entity=AsyncMock(return_value=self.entity),get_messages=AsyncMock())
        self.collector=Collector(self.client,self.store,sleep=AsyncMock(),health=self.health)

    def message(self,n):return SimpleNamespace(id=n,message='Hiring Cook in Dubai. Salary 5000 AED. Apply now',date=datetime.now(timezone.utc))

    def test_public_types_and_inaccessible_sources(self):
        self.assertTrue(public_source(self.entity,'test_jobs'))
        self.entity.broadcast=False;self.entity.megagroup=True
        self.assertTrue(public_source(self.entity,'test_jobs'))
        self.entity.username=None
        self.assertFalse(public_source(self.entity,'test_jobs'))
        self.assertFalse(public_source(types.ChannelForbidden(id=1,access_hash=2,title='Private'),'test_jobs'))

    async def test_one_failed_message_does_not_block_remaining_batch(self):
        self.client.get_messages.return_value=[self.message(3),self.message(2),self.message(1)]
        original=self.store.insert
        def insert(source,mid,**values):
            if mid==2:raise sqlite3.OperationalError('broken private data')
            return original(source,mid,**values)
        with patch.object(self.store,'insert',side_effect=insert):await self.collector.run_once(backfill=3)
        self.assertTrue(self.store.has_message(self.sid,3))
        self.assertEqual(self.store.sources()[0]['last_message_id'],1)
        self.assertGreaterEqual(self.health.data['error_count'],1)

    async def test_locked_db_retried_bounded_without_duplicate(self):
        self.client.get_messages.return_value=[self.message(1)]
        original=self.store.insert;attempts=[]
        def insert(*a,**kw):
            attempts.append(1)
            if len(attempts)<3:raise sqlite3.OperationalError('database is locked')
            return original(*a,**kw)
        with patch.object(self.store,'insert',side_effect=insert):await self.collector.run_once(backfill=1)
        self.assertEqual(len(attempts),3);self.assertTrue(self.store.has_message(self.sid,1))
        self.collector.sleep.assert_any_await(2)

    def test_failed_db_transaction_rolls_back_and_next_write_succeeds(self):
        with self.assertRaises(sqlite3.OperationalError):
            with self.db._connect() as conn:
                conn.execute("UPDATE vacancy_sources SET title='rollback' WHERE id=?",(self.sid,))
                raise sqlite3.OperationalError('local synthetic failure')
        self.assertNotEqual(self.store.sources()[0]['title'],'rollback')
        self.store.checkpoint(self.sid,3)
        self.assertEqual(self.store.sources()[0]['last_message_id'],3)

    async def test_parser_failure_keeps_caption_and_detection(self):
        self.collector.processor=AsyncMock(side_effect=ValueError('private text'))
        self.client.get_messages.return_value=[self.message(1)]
        with patch('collector.analyze_text',side_effect=ValueError('private parser error')):
            await self.collector.run_once(backfill=1)
        row=self.store.message_record(self.sid,1)
        self.assertEqual(row['raw_text'],self.message(1).message)
        self.assertIn(row['detection_status'],('vacancy','probably_vacancy'))

    async def test_source_failure_continues_other_sources(self):
        other=self.store.add_source('other_jobs')
        self.client.get_entity.side_effect=[ValueError('private request'),self.entity]
        self.entity.username='other_jobs';self.client.get_messages.return_value=[self.message(1)]
        await self.collector.run_once(backfill=1)
        self.assertTrue(self.store.has_message(other,1));self.assertEqual(self.health.data['active_sources'],1)

    async def test_network_failure_reaches_reconnect_supervisor(self):
        self.client.get_entity.side_effect=ConnectionError('DNS private')
        with self.assertRaises(ConnectionError):await self.collector.run_once()

    async def test_ocr_failure_continues_caption_pipeline(self):
        from services.vacancy_pipeline import VacancyPipeline
        settings=SimpleNamespace(ocr_enabled=True,media_dir=Path(self.temp.name)/'media',keep_media=False)
        self.client.download_media=AsyncMock(side_effect=OSError('private image error'))
        message=self.message(1);message.photo=True
        pipeline=VacancyPipeline(settings,object())
        row=await pipeline(self.client,{'id':self.sid},message)
        self.assertEqual(row['ocr_status'],'failed');self.assertIn(row['detection_status'],('vacancy','probably_vacancy'))


@unittest.skipUnless(os.getenv('RUN_OCR_INTEGRATION')=='1','Enable local OCR integration explicitly')
class ManagedOCRIntegrationTests(unittest.TestCase):
    def test_real_worker_unicode_image_and_shutdown(self):
        from services.collector_ocr import ManagedOCREngine
        from PIL import Image, ImageDraw, ImageFont
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'Фото_Адель.png'
            image=Image.new('RGB',(1000,320),'white')
            ImageDraw.Draw(image).multiline_text((30,20),'HIRING COOK\nDubai UAE\nSalary 5000 AED',fill='black',font=ImageFont.load_default(size=42),spacing=24)
            image.save(path)
            worker=ManagedOCREngine(Path(__file__).resolve().parents[1]/'ocr_models')
            try:
                result=worker.read(path)
                self.assertIn('Dubai',result);self.assertIn('5000',result)
                with self.assertRaises(RuntimeError):worker.read(Path(folder)/'missing.png')
                self.assertTrue(worker.process.is_alive())
            finally:
                worker.close()
            self.assertFalse(worker.process.is_alive())

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import redirect_stdout
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from db import Database
from config import Settings
from services.growth import Growth, stamp
from services.admin_users import directory, render, print_users
from locales import text


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=Database(self.root/'test.db')
        self.now=datetime(2026,9,15,12,tzinfo=timezone.utc)
        for uid in range(1,8): self.db.upsert_user(uid,'user'+str(uid),'First','Last')
        with self.db._connect() as c:
            c.execute('UPDATE users SET created_at=?',(stamp(self.now-timedelta(days=10)),))
            c.execute('UPDATE users SET created_at=? WHERE telegram_id=1',(stamp(self.now),))

    def test_counts_order_and_no_sensitive_data(self):
        g=Growth(self.db)
        g.touch(2,self.now-timedelta(days=1));g.touch(1,self.now)
        g.track(1,'user_started',now=self.now,key='once')
        g.track(1,'vacancy_searched',now=self.now)
        rid=self.db.add_resume(1,'private.txt','private-path','PRIVATE CV')
        self.db.add_application(1,'PRIVATE COMPANY','PRIVATE ROLE')
        with self.db._connect() as c:
            c.execute('INSERT INTO cv_drafts(telegram_id,resume_id) VALUES(1,?)',(rid,))
            c.execute('INSERT INTO cv_drafts(telegram_id) VALUES(1)')
        counts,rows=directory(self.db,now=self.now)
        self.assertEqual(counts,dict(total=7,new_today=1,new_7d=1,active_today=1,active_7d=2))
        self.assertEqual([r['telegram_id'] for r in rows[:2]],[1,2])
        self.assertEqual(rows[0]['cvs'],2);self.assertEqual(rows[0]['applications'],1)
        self.assertEqual(rows[0]['searches'],1);self.assertEqual(rows[0]['first_start'],stamp(self.now))
        self.assertEqual(rows[1]['language'],'en');self.assertIsNone(rows[1]['first_start'])
        for lang in ('ru','en'):
            output=render(counts,rows,lambda k,**kw:text(lang,k,**kw))
            self.assertIn(text(lang,'au_title'),output);self.assertNotIn('PRIVATE',output)
        self.assertEqual(len(directory(self.db,5,now=self.now)[1]),2)

    def test_cli_no_network_and_sanitized(self):
        self.db.upsert_user(1,'name\x1b\n','First','Last')
        output=io.StringIO()
        with patch('sys.argv',['bot.py','--list-users']),patch.object(bot,'Database',return_value=self.db),patch.object(bot,'build_application') as build,redirect_stdout(output):
            bot.main()
        build.assert_not_called();self.assertIn('last_activity',output.getvalue())
        self.assertNotIn('\x1b',output.getvalue())

    def test_legacy_migration(self):
        path=self.root/'old.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at TEXT)')
            c.execute("INSERT INTO users VALUES(44,'old','Old','2020-01-01 00:00:00')")
        c.close()
        db=Database(path); row=directory(db)[1][0]
        self.assertEqual(row['registered_at'],'2020-01-01 00:00:00');self.assertIsNone(row['last_name'])
        db.upsert_user(44,'old','Old','Surname')
        self.assertEqual(directory(db)[1][0]['last_name'],'Surname')

    def test_delete_removes_directory_entry(self):
        self.db.delete_user_records(1)
        self.assertEqual(directory(self.db)[0]['total'],6)


class DirectoryUITests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_only_and_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bot.build_application(Settings('123:'+'x'*30,99,None,root/'test.db',root))
            for uid in range(1,8):bot.db.upsert_user(uid,'user','First')
            message=SimpleNamespace(reply_text=AsyncMock())
            update=SimpleNamespace(effective_user=SimpleNamespace(id=1),effective_message=message,
                callback_query=SimpleNamespace(data='g:users:0'))
            context=SimpleNamespace(user_data={})
            await bot.growth_ui.buttons(update,context)
            self.assertIn(text('en','g_denied'),message.reply_text.call_args.args[0])
            update.effective_user.id=99
            bot.db.upsert_user(99,'admin','Admin')
            for lang in ('en','ru'):
                bot.db.set_language(99,lang)
                await bot.growth_ui.buttons(update,context)
                self.assertIn(text(lang,'au_title'),message.reply_text.call_args.args[0])
                keys=message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
                self.assertIn('g:users:5',[b.callback_data for r in keys for b in r])
            update.callback_query.data='g:users:5'
            await bot.growth_ui.buttons(update,context)
            keys=message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertIn('g:users:0',[b.callback_data for r in keys for b in r])

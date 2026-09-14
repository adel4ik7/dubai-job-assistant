"""Bot-only SQLite busy retries. Never replay an entire user action/transaction."""
from contextlib import contextmanager
import sqlite3
import time
from db import Database


def retry_busy(action, sleep=time.sleep):
    for attempt in range(4):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            # BUSY_SNAPSHOT needs a transaction rollback, not statement retry.
            code=getattr(exc,'sqlite_errorcode',None)
            busy=code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or (code is None and 'locked' in str(exc).lower())
            if not busy or attempt==3: raise
            sleep((.1,.3,.6)[attempt])


class BusyConnection(sqlite3.Connection):
    def execute(self,*args,**kwargs):
        return retry_busy(lambda:super(BusyConnection,self).execute(*args,**kwargs))
    def commit(self):
        return retry_busy(lambda:super(BusyConnection,self).commit())


class BotDatabase(Database):
    @contextmanager
    def _connect(self):
        conn=sqlite3.connect(self.path,timeout=1,factory=BusyConnection)
        try:
            conn.row_factory=sqlite3.Row
            conn.create_function('casefold',1,lambda value:(value or '').casefold(),deterministic=True)
            conn.execute('PRAGMA secure_delete=ON')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

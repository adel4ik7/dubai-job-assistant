"""Bounded local OCR process: a native OCR hang cannot trap collector shutdown."""
import multiprocessing
from pathlib import Path


def _worker(pipe, model_dir):
    # Native libraries must never emit recognized content or exception details.
    import os
    with open(os.devnull, 'w') as sink:
        import contextlib
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                from services.ocr import EasyOCREngine
                engine = EasyOCREngine(Path(model_dir))
                pipe.send(('ready', ''))
                while True:
                    path = pipe.recv()
                    if path is None:
                        break
                    try:
                        pipe.send(('ok', engine.read(Path(path))))
                    except Exception:
                        pipe.send(('error', ''))
            except (EOFError, BrokenPipeError):
                pass
            except Exception:
                try:
                    pipe.send(('error', ''))
                except (EOFError, BrokenPipeError, OSError):
                    pass
            finally:
                pipe.close()


class ManagedOCREngine:
    def __init__(self, model_dir, timeout=120, wait_ready=True):
        self.timeout = timeout
        ctx = multiprocessing.get_context('spawn')
        self.pipe, child = ctx.Pipe()
        self.process = ctx.Process(target=_worker, args=(child, str(model_dir)), daemon=True)
        self.process.start()
        child.close()
        if wait_ready:
            self.ready()

    def ready(self):
        try:
            if not self.pipe.poll(self.timeout) or self.pipe.recv()[0] != 'ready':
                raise RuntimeError('OCR worker unavailable')
        except BaseException:
            self.close()
            raise

    def read(self, path):
        if not self.process.is_alive():
            raise RuntimeError('OCR worker unavailable')
        self.pipe.send(str(path))
        if not self.pipe.poll(self.timeout):
            self.close()
            raise TimeoutError('OCR worker timeout')
        status, value = self.pipe.recv()
        if status != 'ok':
            raise RuntimeError('OCR failed')
        return value

    def close(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=2)
        self.pipe.close()

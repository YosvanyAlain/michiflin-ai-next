from __future__ import annotations

import threading

from .errors import AppError
from .job_runner import OfflineRunner


class WorkerPool:
    """Explicitly started and joined under the runtime lock; never detach live worker ownership."""

    def __init__(self, runner: OfflineRunner, stop: threading.Event) -> None:
        if not isinstance(runner, OfflineRunner) or not isinstance(stop, threading.Event):
            raise ValueError("Invalid trusted worker pool")
        self.runner, self.stop = runner, stop
        self.failed = threading.Event()
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> bool:
        with self._lock:
            if self._started:
                raise AppError("job_workers_already_started")
            self._started = True
            if self.stop.is_set():
                return False
            try:
                for number in range(self.runner.queue.limits.workers_global):
                    thread = threading.Thread(target=self._run, name=f"michiflin-worker-{number + 1}", daemon=False)
                    thread.start()
                    self.threads.append(thread)
            except BaseException:
                self.stop.set()
                for thread in self.threads:
                    thread.join()
                raise
            return True

    def _run(self) -> None:
        try:
            while not self.stop.is_set():
                if not self.runner.run_once(self.stop):
                    self.stop.wait(0.2)
        except BaseException:
            self.failed.set()
            self.stop.set()

    def healthy(self) -> bool:
        return bool(self.threads) and not self.stop.is_set() and not self.failed.is_set() and all(t.is_alive() for t in self.threads)

    def check(self) -> None:
        if self.failed.is_set():
            raise AppError("job_workers_failed")

    def close(self) -> None:
        self.stop.set()
        # Do not release runtime.lock while a parent thread can still access the vault/database.
        for thread in self.threads:
            thread.join()
        self.check()

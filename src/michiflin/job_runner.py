from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace

from . import sandbox
from .errors import AppError
from .jobs import JobQueue, Kind


@dataclass(frozen=True)
class OfflineHandler:
    kind: Kind
    script: str
    policy: sandbox.Policy = sandbox.Policy()

    def __post_init__(self):
        if (not isinstance(self.kind, Kind) or not isinstance(self.script, str)
                or not 1 <= len(self.script.encode()) <= 32000 or not isinstance(self.policy, sandbox.Policy)):
            raise ValueError("Invalid reviewed offline handler")


class OfflineRunner:
    """Reviewed, non-billable, offline handlers only; never sends mail or publishes files.

    Pausing discards this isolated attempt. Resume recomputes within the remaining original
    execution budget, rather than pretending to suspend/restore an OS process or its tempfs.
    The hosting supervisor must hold runtime.lock and own all runner threads until shutdown.
    """

    def __init__(self, queue: JobQueue, handlers: Mapping[str, OfflineHandler]) -> None:
        self.queue, self.handlers = queue, dict(handlers)
        self._validate()
        # Probe once during trusted supervisor setup, not as an unadmitted per-job process.
        if self.handlers and not sandbox.available():
            raise AppError("sandbox_unavailable")

    def _validate(self) -> None:
        if (set(self.handlers) != set(self.queue.kinds)
                or any(not isinstance(handler, OfflineHandler) or handler.kind != self.queue.kinds[name]
                       for name, handler in self.handlers.items())):
            raise AppError("job_handler_mismatch")

    def run_once(self, stop: threading.Event | None = None) -> bool:
        self._validate()
        if stop is not None and stop.is_set():
            return False
        claim = self.queue.claim()
        if claim is None:
            return False
        handler = self.handlers[claim.kind]
        remaining = claim.deadline - self.queue.clock()
        policy = replace(handler.policy, wall_seconds=min(handler.policy.wall_seconds, max(1, math.ceil(remaining))))
        local_stop = threading.Event()
        last_heartbeat = float("-inf")
        control = "running"

        def tick():
            nonlocal last_heartbeat, control
            if stop is not None and stop.is_set():
                local_stop.set()
            now = time.monotonic()
            if now - last_heartbeat >= 0.2:
                control = self.queue.heartbeat(claim, current=0, total=1)["state"]
                last_heartbeat = now
            if control in {"pausing", "cancelling"}:
                local_stop.set()

        try:
            try:
                result = sandbox.run(handler.script, claim.payload, policy=policy, stop=local_stop, tick=tick)
            except AppError as error:
                if error.code == "sandbox_cancelled" and control in {"pausing", "cancelling"}:
                    self.queue.acknowledge(claim, "pause" if control == "pausing" else "cancel")
                elif error.code != "sandbox_cancelled":
                    if not error.code.startswith("sandbox_"):
                        raise
                    self.queue.finish(claim, error=error.code)
                # Hosting shutdown leaves this job uncertain, never user-cancelled or replayable.
            else:
                self.queue.finish(claim, result=result)
        finally:
            # sandbox.run always reaps before returning, including callback failures and cancellation.
            self.queue.worker_stopped(claim)
        return True

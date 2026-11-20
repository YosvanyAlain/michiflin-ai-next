from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace

from . import sandbox
from .artifacts import ArtifactStore
from .errors import AppError
from .jobs import JobQueue, Kind


@dataclass(frozen=True)
class OfflineHandler:
    kind: Kind
    script: str
    policy: sandbox.Policy = sandbox.Policy()
    artifact_field: str | None = None

    def __post_init__(self):
        if (not isinstance(self.kind, Kind) or not isinstance(self.script, str)
                or not 1 <= len(self.script.encode()) <= 32000 or not isinstance(self.policy, sandbox.Policy)
                or (self.artifact_field is not None and (not isinstance(self.artifact_field, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.artifact_field)))):
            raise ValueError("Invalid reviewed offline handler")


class OfflineRunner:
    """Reviewed, non-billable, offline handlers only; never sends mail or publishes files.

    Pausing discards this isolated attempt. Resume recomputes within the remaining original
    execution budget, rather than pretending to suspend/restore an OS process or its tempfs.
    The hosting supervisor must hold runtime.lock and own all runner threads until shutdown.
    """

    def __init__(self, queue: JobQueue, handlers: Mapping[str, OfflineHandler], *, artifacts: ArtifactStore | None = None) -> None:
        self.queue, self.handlers, self.artifacts = queue, dict(handlers), artifacts
        self._validate()
        # Probe once during trusted supervisor setup, not as an unadmitted per-job process.
        if self.handlers and not sandbox.available():
            raise AppError("sandbox_unavailable")

    def _validate(self) -> None:
        if (set(self.handlers) != set(self.queue.kinds)
                or any(not isinstance(handler, OfflineHandler) or handler.kind != self.queue.kinds[name]
                       for name, handler in self.handlers.items())):
            raise AppError("job_handler_mismatch")
        if (any(handler.artifact_field is not None for handler in self.handlers.values())
                and (not isinstance(self.artifacts, ArtifactStore) or self.artifacts.ledger is not self.queue.ledger)):
            raise AppError("job_artifact_store_mismatch")

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
                with ExitStack() as resources:
                    tick()
                    if local_stop.is_set():
                        raise AppError("sandbox_cancelled")
                    inputs = {}
                    if handler.artifact_field is not None:
                        try:
                            binding = self.queue.artifact_input(claim, handler.artifact_field)
                            stream = resources.enter_context(self.artifacts.open(*binding))
                        except (AppError, OSError):
                            raise AppError("job_input_unavailable") from None
                        inputs["input_file"] = stream
                    result = sandbox.run(handler.script, claim.payload, policy=policy, stop=local_stop, tick=tick, **inputs)
            except AppError as error:
                if error.code == "sandbox_cancelled" and control in {"pausing", "cancelling"}:
                    self.queue.acknowledge(claim, "pause" if control == "pausing" else "cancel")
                elif error.code != "sandbox_cancelled":
                    if not error.code.startswith("sandbox_") and error.code != "job_input_unavailable":
                        raise
                    self.queue.finish_offline(claim, error=error.code)
                # Hosting shutdown leaves this job uncertain, never user-cancelled or replayable.
            else:
                self.queue.finish_offline(claim, result=result)
        finally:
            # sandbox.run always reaps before returning, including callback failures and cancellation.
            self.queue.worker_stopped(claim)
        return True

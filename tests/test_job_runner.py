import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from michiflin import sandbox
from michiflin.errors import AppError
from michiflin.job_runner import OfflineHandler, OfflineRunner
from michiflin.jobs import JobQueue, Kind
from michiflin.storage import Ledger


@pytest.fixture
def queue(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.bind(1, "owner", 10)
    return JobQueue(ledger, kinds={"synthetic.v1": Kind(lambda _value: None)})


@pytest.fixture
def isolated():
    if not sandbox.available():
        if os.environ.get("MICHIFLIN_REQUIRE_SANDBOX") == "1":
            pytest.fail("Required namespace/seccomp sandbox is unavailable")
        pytest.skip("Isolated execution is unavailable and stays disabled")


def setup_job(queue, script):
    job = queue.enqueue("owner", 10, 1, "synthetic-request-0001", "synthetic.v1", {"input": 3})
    runner = OfflineRunner(queue, {"synthetic.v1": OfflineHandler(queue.kinds["synthetic.v1"], script)})
    return job, runner


def status(queue, job):
    return queue.get("owner", 10, 1, job["id"])


def signal_started(monkeypatch):
    entered = threading.Event()
    original = sandbox.run

    def wrapped(*args, **kwargs):
        tick = kwargs.get("tick")
        if tick:
            def notify():
                tick()
                entered.set()
            kwargs["tick"] = notify
        return original(*args, **kwargs)

    monkeypatch.setattr(sandbox, "run", wrapped)
    return entered


def test_real_queue_to_isolated_worker_to_durable_result(isolated, queue):
    job, runner = setup_job(queue, 'import json,sys; x=json.load(sys.stdin); print(json.dumps({"output":x["input"]*2}))')
    assert runner.run_once()
    value = status(queue, job)
    assert value["state"] == "completed" and value["worker_stopped"]
    assert value["result"] == {"output": 6}
    assert not runner.run_once()


def test_real_failure_is_not_a_success_card_or_a_retry(isolated, queue):
    job, runner = setup_job(queue, 'raise RuntimeError("synthetic-private-error")')
    assert runner.run_once()
    value = status(queue, job)
    assert value["state"] == "failed" and value["worker_stopped"]
    assert value["error"] == "sandbox_execution_failed"
    assert not runner.run_once()


@pytest.mark.parametrize("action,state", [("cancel", "cancelled"), ("pause", "paused")])
def test_real_cancel_and_pause_reap_before_releasing_slot(isolated, queue, monkeypatch, action, state):
    job, runner = setup_job(queue, 'import time; time.sleep(30)')
    entered = signal_started(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once)
        assert entered.wait(3)
        assert not status(queue, job)["worker_stopped"]
        queue.control("owner", 10, 1, job["id"], action)
        assert future.result(timeout=4)
    assert status(queue, job)["state"] == state
    assert status(queue, job)["worker_stopped"]
    if action == "pause":
        queue.control("owner", 10, 1, job["id"], "resume")
        assert status(queue, job)["state"] == "queued"


def test_host_shutdown_is_uncertain_not_implicitly_cancelled(isolated, queue, monkeypatch):
    job, runner = setup_job(queue, 'import time; time.sleep(30)')
    entered = signal_started(monkeypatch)
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once, stop)
        assert entered.wait(3)
        stop.set()
        assert future.result(timeout=4)
    assert status(queue, job)["state"] == "uncertain"
    assert status(queue, job)["worker_stopped"]
    assert not runner.run_once()


def test_startup_probe_unavailable_leaves_jobs_untouched(queue, monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: False)
    with pytest.raises(AppError, match="sandbox_unavailable"):
        setup_job(queue, 'print("{}")')
    assert queue.list("owner", 10, 1)["jobs"][0]["state"] == "queued"


def test_handler_registry_mismatch_cannot_claim(queue):
    with pytest.raises(AppError, match="job_handler_mismatch"):
        OfflineRunner(queue, {})


def test_stopped_supervisor_does_not_claim_jobs(isolated, queue):
    job, runner = setup_job(queue, 'print("{}")')
    stop = threading.Event()
    stop.set()
    assert not runner.run_once(stop)
    assert status(queue, job)["state"] == "queued"

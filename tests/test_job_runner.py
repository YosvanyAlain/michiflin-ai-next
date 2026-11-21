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


def setup_artifact_job(queue, *, body=b'synthetic private input', script=None):
    from dataclasses import replace

    from michiflin.artifacts import ArtifactStore
    from michiflin.file_jobs import CHECKSUM_HANDLER, CHECKSUM_KIND

    store = ArtifactStore(queue.ledger)
    artifact = store.put('owner', 10, 1, 'synthetic-source-0001', 'data', body)
    handler = CHECKSUM_HANDLER if script is None else replace(CHECKSUM_HANDLER, script=script)
    queue.kinds = {CHECKSUM_KIND: handler.kind}
    job = queue.enqueue('owner', 10, 1, 'synthetic-request-0001', CHECKSUM_KIND, {'artifact': artifact['id']})
    runner = OfflineRunner(queue, {CHECKSUM_KIND: handler}, artifacts=store)
    return artifact, job, runner, store


def test_real_artifact_checksum_is_durable_without_exposing_paths(isolated, queue):
    artifact, job, runner, store = setup_artifact_job(queue, body=b'private' * 100000)
    assert runner.run_once()
    assert status(queue, job)['result'] == {key: artifact[key] for key in ('bytes', 'sha256')}
    assert status(queue, job)['worker_stopped']
    with queue.ledger.connection() as db:
        assert db.execute('SELECT readers FROM artifacts').fetchone()[0] == 0
    assert store.delete('owner', 10, 1, artifact['id'])['deleted']


@pytest.mark.parametrize('action,state', [('pause', 'paused'), ('cancel', 'cancelled'), ('shutdown', 'uncertain')])
def test_artifact_pin_remains_until_actual_worker_reaped(isolated, queue, monkeypatch, action, state):
    artifact, job, runner, store = setup_artifact_job(queue, script='import time; time.sleep(30)')
    entered = signal_started(monkeypatch)
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once, stop)
        assert entered.wait(3)
        with pytest.raises(AppError, match='artifact_busy'):
            store.delete('owner', 10, 1, artifact['id'])
        if action == 'shutdown':
            stop.set()
        else:
            queue.control('owner', 10, 1, job['id'], action)
        assert future.result(timeout=4)
    assert status(queue, job)['state'] == state and status(queue, job)['worker_stopped']
    assert store.delete('owner', 10, 1, artifact['id'])['deleted']


@pytest.mark.parametrize('failure', ['deleted', 'corrupted', 'other_instance', 'other_owner'])
def test_unavailable_or_foreign_input_never_reaches_worker(isolated, queue, monkeypatch, failure):
    artifact, job, runner, store = setup_artifact_job(queue)
    if failure == 'deleted':
        store.delete('owner', 10, 1, artifact['id'])
    elif failure == 'corrupted':
        (store.directory / (artifact['id'] + '.blob')).write_bytes(b'corrupt')
    else:
        queue.ledger.bind(2, 'owner' if failure == 'other_instance' else 'other', 10)
        with queue.ledger.transaction() as db:
            db.execute('UPDATE artifacts SET instance=2 WHERE id=?', (artifact['id'],))
    monkeypatch.setattr(sandbox, 'run', lambda *a, **k: pytest.fail('Untrusted input must not reach a worker'))
    assert runner.run_once()
    assert status(queue, job)['state'] == 'failed' and status(queue, job)['error'] == 'job_input_unavailable'
    assert status(queue, job)['worker_stopped']
    with queue.ledger.connection() as db:
        assert db.execute('SELECT readers FROM artifacts').fetchone()[0] == 0


def test_artifact_runner_requires_same_ledger_and_reviewed_input_field(queue):
    from dataclasses import replace

    from michiflin.file_jobs import CHECKSUM_HANDLER, CHECKSUM_KIND
    queue.kinds = {CHECKSUM_KIND: CHECKSUM_HANDLER.kind}
    with pytest.raises(AppError, match='job_artifact_store_mismatch'):
        OfflineRunner(queue, {CHECKSUM_KIND: CHECKSUM_HANDLER})
    for value in ('/host/path', 1, ''):
        with pytest.raises(ValueError):
            replace(CHECKSUM_HANDLER, artifact_field=value)


def test_checksum_admission_does_not_accept_host_paths_or_other_arguments(queue):
    from michiflin.file_jobs import CHECKSUM_HANDLER, CHECKSUM_KIND
    queue.kinds = {CHECKSUM_KIND: CHECKSUM_HANDLER.kind}
    for payload in ({'artifact': '/host/path'}, {'artifact': 'a' * 32, 'owner': 'other'}, {}):
        with pytest.raises(AppError):
            queue.enqueue('owner', 10, 1, 'synthetic-request-0001', CHECKSUM_KIND, payload)
    assert queue.list('owner', 10, 1)['jobs'] == []


@pytest.mark.parametrize('action,expected', [('pause', 'paused'), ('cancel', 'cancelled'), ('revoke', 'cancelled')])
def test_offline_output_is_discarded_when_control_wins_publication_race(isolated, queue, monkeypatch, action, expected):
    job, runner = setup_job(queue, 'print("{}")')
    original = sandbox.run

    def raced(*a, **kwargs):
        result = original(*a, **kwargs)
        if action == 'revoke':
            queue.ledger.revoke(1)
        else:
            queue.control('owner', 10, 1, job['id'], action)
        return result

    monkeypatch.setattr(sandbox, 'run', raced)
    assert runner.run_once()
    with queue.ledger.connection() as db:
        row = db.execute('SELECT * FROM jobs').fetchone()
        assert row['state'] == expected and row['slot'] == 0 and row['result'] is None
    if action == 'pause':
        queue.control('owner', 10, 1, job['id'], 'resume')
        monkeypatch.setattr(sandbox, 'run', original)
        assert runner.run_once()
        result = status(queue, job)
        assert result['state'] == 'completed' and result['current'] == result['total'] == 1


def test_deleted_paused_artifact_is_not_replaced_or_silently_resumed(isolated, queue, monkeypatch):
    artifact, job, runner, store = setup_artifact_job(queue, script='import time; time.sleep(30)')
    entered = signal_started(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once)
        assert entered.wait(3)
        queue.control('owner', 10, 1, job['id'], 'pause')
        assert future.result(timeout=4)
    assert store.delete('owner', 10, 1, artifact['id'])['deleted']
    queue.control('owner', 10, 1, job['id'], 'resume')
    assert runner.run_once()
    assert status(queue, job)['state'] == 'failed'
    assert status(queue, job)['error'] == 'job_input_unavailable'


def test_worker_callback_failure_reaps_before_releasing_artifact_pin(isolated, queue, monkeypatch):
    artifact, job, runner, store = setup_artifact_job(queue, script='import time; time.sleep(30)')
    original = sandbox.run

    def injected(*args, **kwargs):
        def fail():
            with pytest.raises(AppError, match='artifact_busy'):
                store.delete('owner', 10, 1, artifact['id'])
            raise RuntimeError('synthetic parent callback failure')
        kwargs['tick'] = fail
        return original(*args, **kwargs)

    monkeypatch.setattr(sandbox, 'run', injected)
    with pytest.raises(RuntimeError, match='synthetic parent callback failure'):
        runner.run_once()
    assert status(queue, job)['state'] == 'uncertain' and status(queue, job)['worker_stopped']
    assert store.delete('owner', 10, 1, artifact['id'])['deleted']


def test_real_isolated_binary_result_can_be_verified_and_published_without_a_host_path(isolated, queue):
    import tempfile

    from michiflin.artifacts import ArtifactStore

    store = ArtifactStore(queue.ledger)
    body = b'synthetic private source' * 10000
    original = store.put('owner', 10, 1, 'synthetic-file-input', 'source.bin', body)
    script = '''import sys,json,hashlib
h=hashlib.sha256(); size=0
with open('/input.bin','rb') as source:
    for block in iter(lambda:source.read(65536),b''):
        data=block.upper(); sys.stdout.buffer.write(data); h.update(data); size+=len(data)
print(json.dumps({'bytes':size,'sha256':h.hexdigest()}),file=sys.stderr)
'''
    with store.open('owner', 10, 1, original['id']) as source, tempfile.TemporaryFile() as output:
        result = sandbox.run(script, {}, input_file=source, output_file=output)
        published = store.import_output('owner', 10, 1, 'synthetic-worker-output', 'result.bin', output, result)
    assert published['state'] == 'ready' and published['id'] != original['id']
    with store.open('owner', 10, 1, published['id']) as output:
        assert output.read() == body.upper()
    with store.open('owner', 10, 1, original['id']) as source:
        assert source.read() == body

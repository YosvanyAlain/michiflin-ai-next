import os
import threading
from dataclasses import replace

import pytest

from michiflin import sandbox
from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.identity import Principal
from michiflin.service import Service


@pytest.fixture
def service(tmp_path):
    value = Service(Settings(state_dir=tmp_path / 'state'))
    member = Principal('member@example.invalid', 'A' * 40, 20, True, True)
    value.register_instance(member, 42)
    return value, member


@pytest.fixture
def isolated():
    if not sandbox.available():
        if os.environ.get('MICHIFLIN_REQUIRE_SANDBOX') == '1':
            pytest.fail('Required namespace/seccomp sandbox is unavailable')
        pytest.skip('Offline worker pool remains disabled without isolation')


def request(service, operation, args=None, number=1):
    return {'id': f'synthetic-request-{number:04d}', 'op': operation, 'args': args or {},
            'session': service.session, 'locale': 'en'}


def test_service_construction_and_hello_never_probe_or_start_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, 'available', lambda: pytest.fail('Screen/service construction must not probe'))
    value = Service(Settings(state_dir=tmp_path / 'state'))
    member = Principal('member@example.invalid', 'A' * 40, 20, True, True)
    value.register_instance(member, 42)
    response = value.handle(member, 42, request(value, 'hello'))
    assert response['ok'] and 'files.checksum' not in response['data']['operations']
    assert value.workers is None and value.jobs.kinds == {}


def test_unavailable_isolation_does_not_expose_submission_or_fallback(service, monkeypatch):
    value, member = service
    monkeypatch.setattr(sandbox, 'available', lambda: False)
    assert value.start_workers(threading.Event()) is None
    assert value.jobs.kinds == {} and 'files.checksum' not in value.operations
    assert not value.handle(member, 42, request(value, 'files.checksum', {'id': 'a' * 32}))['ok']


def test_startup_stop_does_not_probe_or_claim(service, monkeypatch):
    value, _ = service
    monkeypatch.setattr(sandbox, 'available', lambda: pytest.fail('Must not probe'))
    stop = threading.Event()
    stop.set()
    assert value.start_workers(stop) is None


def test_real_protocol_checksum_runs_in_bounded_owned_pool_and_persists(isolated, service, monkeypatch):
    value, member = service
    done = threading.Event()
    original = value.jobs.worker_stopped

    def reaped(claim):
        original(claim)
        done.set()

    monkeypatch.setattr(value.jobs, 'worker_stopped', reaped)
    artifact = value.artifacts.put(member.storage_key, 20, 42, 'synthetic-file-input', 'data.bin', b'synthetic' * 100000)
    pool = value.start_workers(threading.Event())
    try:
        assert pool.healthy() and len(pool.threads) == value.jobs.limits.workers_global
        assert all(not thread.daemon for thread in pool.threads)
        hello = value.handle(member, 42, request(value, 'hello'))
        assert 'files.checksum' in hello['data']['operations']
        payload = request(value, 'files.checksum', {'id': artifact['id']}, 2)
        submitted = value.handle(member, 42, payload)
        assert submitted['ok']
        assert done.wait(5)
        result = value.handle(member, 42, request(value, 'jobs.status', {'id': submitted['data']['id']}, 3))
        assert result['ok'] and result['data']['state'] == 'completed' and result['data']['worker_stopped']
        assert result['data']['result'] == {key: artifact[key] for key in ('bytes', 'sha256')}
        assert result['data']['current'] == result['data']['total'] == 1
        assert value.handle(member, 42, payload) == submitted
        assert len(value.jobs.list(member.storage_key, 20, 42)['jobs']) == 1
        with pytest.raises(AppError, match='job_workers_already_started'):
            value.start_workers(threading.Event())
    finally:
        pool.close()
    assert not pool.healthy() and all(not thread.is_alive() for thread in pool.threads)
    response = value.handle(member, 42, request(value, 'files.checksum', {'id': artifact['id']}, 4))
    assert response['error'] == 'job_workers_unavailable'
    restarted = Service(value.settings)
    assert restarted.jobs.get(member.storage_key, 20, 42, submitted['data']['id'])['state'] == 'completed'
    assert restarted.workers is None


def test_submission_rejects_paths_foreign_instances_and_extra_fields(isolated, service):
    value, member = service
    artifact = value.artifacts.put(member.storage_key, 20, 42, 'synthetic-file-input', 'data.bin', b'private')
    value.register_instance(member, 43)
    pool = value.start_workers(threading.Event())
    try:
        for index, args in enumerate(({'id': '/host/path'}, {'id': artifact['id'], 'owner': 'other'}, {}), start=1):
            assert not value.handle(member, 42, request(value, 'files.checksum', args, index))['ok']
        assert value.handle(member, 43, request(value, 'files.checksum', {'id': artifact['id']}, 4))['error'] == 'artifact_not_found'
        for index, principal in enumerate((replace(member, encrypted=False), replace(member, one_to_one=False),
                                            replace(member, fingerprint='B' * 40)), start=5):
            assert not value.handle(principal, 42, request(value, 'files.checksum', {'id': artifact['id']}, index))['ok']
        assert value.jobs.list(member.storage_key, 20, 42)['jobs'] == []
    finally:
        pool.close()


def test_worker_failure_stops_pool_without_logging_private_error_and_joins(isolated, service, monkeypatch, caplog):
    from michiflin.job_runner import OfflineRunner

    value, _ = service
    def broken(*_):
        raise RuntimeError('synthetic-private-input-path-and-credentials')
    monkeypatch.setattr(OfflineRunner, 'run_once', broken)
    pool = value.start_workers(threading.Event())
    assert pool.stop.wait(3)
    with pytest.raises(AppError, match='job_workers_failed'):
        pool.close()
    assert all(not thread.is_alive() for thread in pool.threads)
    assert 'synthetic-private' not in caplog.text


def test_pool_global_and_per_identity_limits_apply_to_real_workers(isolated, service, monkeypatch):
    import michiflin.service as module
    from michiflin.file_jobs import CHECKSUM_HANDLER

    value, member = service
    other = replace(member, address='other@example.invalid', fingerprint='B' * 40, chat_id=21)
    value.register_instance(other, 43)
    artifact_a = value.artifacts.put(member.storage_key, 20, 42, 'synthetic-source-first', 'a', b'owner a')
    artifact_b = value.artifacts.put(other.storage_key, 21, 43, 'synthetic-source-other', 'b', b'owner b')
    monkeypatch.setattr(module, 'CHECKSUM_HANDLER', replace(CHECKSUM_HANDLER, script='import time; time.sleep(.4)\n' + CHECKSUM_HANDLER.script))
    original_run, original_stopped = sandbox.run, value.jobs.worker_stopped
    lock, done = threading.Lock(), threading.Event()
    owners = {artifact_a['id']: 'a', artifact_b['id']: 'b'}
    active, peaks, finished = {'a': 0, 'b': 0}, {'a': 0, 'b': 0, 'global': 0}, []

    def measured(script, payload, **kwargs):
        tick = kwargs.get('tick')
        if tick is None:
            return original_run(script, payload, **kwargs)
        owner, observed = owners[payload['artifact']], False
        def measured_tick():
            nonlocal observed
            tick()
            if not observed:
                with lock:
                    active[owner] += 1
                    peaks[owner] = max(peaks[owner], active[owner])
                    peaks['global'] = max(peaks['global'], sum(active.values()))
                observed = True
        kwargs['tick'] = measured_tick
        try:
            return original_run(script, payload, **kwargs)
        finally:
            if observed:
                with lock:
                    active[owner] -= 1

    def reaped(claim):
        original_stopped(claim)
        with lock:
            finished.append(claim.identifier)
            if len(finished) == 3:
                done.set()

    monkeypatch.setattr(sandbox, 'run', measured)
    monkeypatch.setattr(value.jobs, 'worker_stopped', reaped)
    pool = value.start_workers(threading.Event())
    try:
        for principal, instance, artifact, number in ((member, 42, artifact_a, 1), (member, 42, artifact_a, 2), (other, 43, artifact_b, 1)):
            assert value.handle(principal, instance, request(value, 'files.checksum', {'id': artifact['id']}, number))['ok']
        assert done.wait(6)
        assert peaks == {'a': 1, 'b': 1, 'global': 2}
        assert active == {'a': 0, 'b': 0}
    finally:
        pool.close()


def test_owned_pool_shutdown_reaps_active_attempt_and_leaves_uncertain_without_replay(isolated, service, monkeypatch):
    import michiflin.service as module
    from michiflin.file_jobs import CHECKSUM_HANDLER

    value, member = service
    artifact = value.artifacts.put(member.storage_key, 20, 42, 'synthetic-file-input', 'data.bin', b'input')
    monkeypatch.setattr(module, 'CHECKSUM_HANDLER', replace(CHECKSUM_HANDLER, script='import time; time.sleep(30)'))
    entered = threading.Event()
    original_run = sandbox.run

    def observe(*args, **kwargs):
        tick = kwargs.get('tick')
        if tick:
            def started():
                tick()
                entered.set()
            kwargs['tick'] = started
        return original_run(*args, **kwargs)

    monkeypatch.setattr(sandbox, 'run', observe)
    pool = value.start_workers(threading.Event())
    try:
        response = value.handle(member, 42, request(value, 'files.checksum', {'id': artifact['id']}))
        assert response['ok'] and entered.wait(5)
    finally:
        pool.close()
    result = value.jobs.get(member.storage_key, 20, 42, response['data']['id'])
    assert result['state'] == 'uncertain' and result['worker_stopped']
    assert value.artifacts.delete(member.storage_key, 20, 42, artifact['id'])['deleted']
    assert all(not thread.is_alive() for thread in pool.threads)

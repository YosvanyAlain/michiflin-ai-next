from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from michiflin.errors import AppError
from michiflin.jobs import JobQueue, Kind, Limits
from michiflin.storage import Ledger


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


def validate(payload):
    if set(payload) != {"text"} or not isinstance(payload["text"], str):
        raise AppError("invalid_synthetic_payload")


@pytest.fixture
def queue(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.bind(1, "owner-a", 10)
    ledger.bind(2, "owner-b", 20)
    return JobQueue(ledger, kinds={"synthetic.v1": Kind(validate)}, clock=Clock())


def enqueue(queue, owner="owner-a", instance=1, chat=10, number=1, **kwargs):
    return queue.enqueue(owner, chat, instance, f"synthetic-request-{number:04d}", "synthetic.v1", {"text": "sample"}, **kwargs)


def get(queue, job):
    return queue.get("owner-a", 10, 1, job["id"])


def test_durable_schema_reopen_and_idempotent_submission(queue):
    job = enqueue(queue, reserved_bytes=10)
    reopened = JobQueue(Ledger(queue.ledger.path.parent), kinds=queue.kinds, clock=queue.clock)
    assert enqueue(reopened, reserved_bytes=10) == job
    assert get(reopened, job)["state"] == "queued"
    with pytest.raises(AppError, match="request_identity_conflict"):
        enqueue(reopened, reserved_bytes=11)


def test_changed_payload_does_not_replay(queue):
    enqueue(queue)
    with pytest.raises(AppError, match="request_identity_conflict"):
        queue.enqueue("owner-a", 10, 1, "synthetic-request-0001", "synthetic.v1", {"text": "different"})


def test_parallel_submission_is_exactly_once(queue):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: enqueue(queue), range(16)))
    assert len({value["id"] for value in values}) == 1
    assert len(queue.list("owner-a", 10, 1)["jobs"]) == 1


def test_owner_fairness_and_fifo(queue):
    queue.limits = replace(queue.limits, workers_global=1)
    first = enqueue(queue)
    queue.clock.value += 1
    second = enqueue(queue, number=2)
    third = enqueue(queue, "owner-b", 2, 20)
    claimed = queue.claim()
    assert claimed.identifier == first["id"]
    queue.finish(claimed, result={"ok": True})
    assert queue.claim() is None  # Finished Python code is not proof the OS worker exited.
    queue.worker_stopped(claimed)
    next_claim = queue.claim()
    assert next_claim.identifier == third["id"]
    queue.finish(next_claim, result={})
    queue.worker_stopped(next_claim)
    assert queue.claim().identifier == second["id"]


def test_parallel_claim_respects_owner_and_global_slots(queue):
    enqueue(queue)
    enqueue(queue, number=2)
    enqueue(queue, "owner-b", 2, 20)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: queue.claim(), range(16)))
    assert len([claim for claim in claims if claim]) == 2


@pytest.mark.parametrize("binding", [("owner-b", 10, 1), ("owner-a", 20, 1), ("owner-a", 10, 2)])
def test_identity_chat_and_instance_binding(queue, binding):
    job = enqueue(queue)
    with pytest.raises(AppError, match="instance_not_authorized"):
        queue.get(*binding, job["id"])
    with pytest.raises(AppError, match="instance_not_authorized"):
        queue.control(*binding, job["id"], "cancel")


def test_separate_authorized_instance_cannot_see_or_control_job(queue):
    job = enqueue(queue)
    queue.ledger.bind(3, "owner-a", 10)
    assert queue.list("owner-a", 10, 3) == {"jobs": []}
    with pytest.raises(AppError, match="job_not_found"):
        queue.get("owner-a", 10, 3, job["id"])


def test_revoked_work_cannot_be_claimed_or_read(queue):
    job = enqueue(queue)
    with queue.ledger.transaction() as db:
        db.execute("UPDATE instances SET revoked=1 WHERE id=1")
    assert queue.claim() is None
    with pytest.raises(AppError, match="instance_not_authorized"):
        get(queue, job)
    with queue.ledger.connection() as db:
        assert db.execute("SELECT state FROM jobs").fetchone()[0] == "cancelled"


def test_running_revoked_work_gets_stop_request(queue):
    enqueue(queue)
    claim = queue.claim()
    with queue.ledger.transaction() as db:
        db.execute("UPDATE instances SET revoked=1 WHERE id=1")
    assert queue.heartbeat(claim, current=1, total=10)["state"] == "cancelling"


def test_pause_checkpoints_resume_and_runtime_budget(queue):
    job = enqueue(queue)
    claim = queue.claim()
    queue.clock.value += 5
    queue.heartbeat(claim, current=2, total=10, checkpoint={"part": 2})
    assert queue.control("owner-a", 10, 1, job["id"], "pause")["state"] == "pausing"
    queue.acknowledge(claim, "pause")
    with pytest.raises(AppError, match="job_not_resumable"):
        queue.control("owner-a", 10, 1, job["id"], "resume")
    queue.worker_stopped(claim)
    queue.control("owner-a", 10, 1, job["id"], "resume")
    resumed = queue.claim()
    assert resumed.checkpoint == {"part": 2}
    assert resumed.deadline == queue.clock.value + 55
    with pytest.raises(AppError, match="job_lease_invalid"):
        queue.finish(claim, result={})


def test_pause_and_cancel_queued_never_starts(queue):
    job = enqueue(queue)
    assert queue.control("owner-a", 10, 1, job["id"], "pause")["state"] == "paused"
    assert queue.claim() is None
    assert queue.control("owner-a", 10, 1, job["id"], "cancel")["state"] == "cancelled"
    with pytest.raises(AppError, match="job_not_resumable"):
        queue.control("owner-a", 10, 1, job["id"], "resume")


def test_running_cancellation_is_cooperative_not_fake_completion(queue):
    job = enqueue(queue)
    claim = queue.claim()
    assert queue.control("owner-a", 10, 1, job["id"], "cancel")["state"] == "cancelling"
    assert queue.heartbeat(claim, current=0, total=1)["state"] == "cancelling"
    queue.acknowledge(claim, "cancel")
    assert not get(queue, job)["worker_stopped"]
    queue.worker_stopped(claim)
    assert get(queue, job)["state"] == "cancelled"
    assert get(queue, job)["worker_stopped"]


def test_completion_wins_race_with_cancellation_without_lying(queue):
    job = enqueue(queue)
    claim = queue.claim()
    queue.control("owner-a", 10, 1, job["id"], "cancel")
    queue.finish(claim, result={"receipt": "synthetic-confirmed"})
    assert get(queue, job)["state"] == "completed"
    assert get(queue, job)["result"]["receipt"] == "synthetic-confirmed"


def test_lease_expiry_never_requeues_or_releases_resources(queue):
    job = enqueue(queue, reserved_bytes=20)
    claim = queue.claim()
    queue.clock.value += 31
    queue.recover()
    assert get(queue, job)["state"] == "uncertain"
    assert not get(queue, job)["worker_stopped"]
    assert queue.claim() is None
    assert enqueue(queue, reserved_bytes=20)["id"] == job["id"]
    with pytest.raises(AppError, match="job_lease_expired"):
        queue.finish(claim, result={})
    with pytest.raises(AppError, match="job_not_resumable"):
        queue.control("owner-a", 10, 1, job["id"], "resume")
    queue.worker_stopped(claim)
    assert get(queue, job)["state"] == "uncertain"
    assert get(queue, job)["worker_stopped"]


def test_startup_marks_even_live_lease_uncertain_and_preserves_queued_work(queue):
    job = enqueue(queue)
    second = enqueue(queue, number=2)
    queue.claim()
    reopened = JobQueue(Ledger(queue.ledger.path.parent), kinds=queue.kinds, clock=queue.clock)
    reopened.recover(startup=True)
    assert get(reopened, job)["state"] == "uncertain"
    assert get(reopened, second)["state"] == "queued"
    assert reopened.claim() is None


def test_expiry_and_delay(queue):
    job = enqueue(queue, delay=5, ttl=10)
    assert queue.claim() is None
    queue.clock.value += 11
    assert queue.claim() is None
    assert get(queue, job)["state"] == "expired"


def test_delayed_work_runs_only_in_window(queue):
    job = enqueue(queue, delay=5, ttl=10)
    assert queue.claim() is None
    queue.clock.value += 5
    assert queue.claim().identifier == job["id"]


def test_failure_result_and_lease_tokens_are_private(queue):
    job = enqueue(queue)
    claim = queue.claim()
    assert claim.token not in repr(claim)
    assert "sample" not in repr(claim)
    queue.finish(claim, error="synthetic_failure")
    assert get(queue, job)["error"] == "synthetic_failure"
    for private in ("payload", "checkpoint", "lease_hash", "token", "owner", "request_id"):
        assert private not in get(queue, job)
    with pytest.raises(AppError, match="job_lease_invalid"):
        queue.worker_stopped(replace(claim, token="0" * 64))


def test_monotonic_progress_and_bounded_checkpoint(queue):
    enqueue(queue)
    claim = queue.claim()
    queue.heartbeat(claim, current=2, total=10)
    for current, total in [(1, 10), (3, 11), (11, 10), (True, 10), (0, 10**13)]:
        with pytest.raises(AppError, match="invalid_job_progress"):
            queue.heartbeat(claim, current=current, total=total)
    with pytest.raises(AppError, match="job_data_limit"):
        queue.heartbeat(claim, current=3, total=10, checkpoint={"x": "x" * 4001})


def test_pending_and_record_limits_do_not_evict_replay_guards(queue):
    queue.limits = replace(queue.limits, pending_owner=1, records_owner=2)
    first = enqueue(queue)
    with pytest.raises(AppError, match="job_capacity_limit"):
        enqueue(queue, number=2)
    queue.control("owner-a", 10, 1, first["id"], "cancel")
    second = enqueue(queue, number=2)
    queue.control("owner-a", 10, 1, second["id"], "cancel")
    with pytest.raises(AppError, match="job_history_limit"):
        enqueue(queue, number=3)
    assert enqueue(queue)["id"] == first["id"]


def test_storage_budget_retained_until_verified_cleanup(queue):
    queue.limits = replace(queue.limits, bytes_owner=20, bytes_global=30)
    job = enqueue(queue, reserved_bytes=20)
    claim = queue.claim()
    queue.finish(claim, result={})
    with pytest.raises(AppError, match="job_cleanup_not_safe"):
        queue.release_storage(job["id"])
    queue.worker_stopped(claim)
    with pytest.raises(AppError, match="job_storage_limit"):
        enqueue(queue, number=2, reserved_bytes=1)
    queue.release_storage(job["id"])
    assert enqueue(queue, number=2, reserved_bytes=20)["state"] == "queued"


@pytest.mark.parametrize("payload", [None, [], {"text": float("nan")}, {"text": "x" * 16001}, {"wrong": 1}])
def test_invalid_and_oversized_payloads_refused(queue, payload):
    with pytest.raises(AppError):
        queue.enqueue("owner-a", 10, 1, "synthetic-request-0001", "synthetic.v1", payload)


@pytest.mark.parametrize("kwargs", [{"ttl": True}, {"ttl": 0}, {"ttl": 86401}, {"delay": -1},
                                    {"delay": 3600}, {"reserved_bytes": -1}, {"reserved_bytes": True}])
def test_invalid_budgets_refused(queue, kwargs):
    with pytest.raises(AppError, match="invalid_job_budget"):
        enqueue(queue, **kwargs)


def test_missing_handler_never_executes_queued_work(queue):
    enqueue(queue)
    queue.kinds = {}
    assert queue.claim() is None
    with pytest.raises(AppError, match="job_kind_unavailable"):
        enqueue(queue, number=2)


def test_invalid_control_identifier_and_result_are_refused(queue):
    job = enqueue(queue)
    with pytest.raises(AppError, match="invalid_job"):
        queue.get("owner-a", 10, 1, [])
    with pytest.raises(AppError, match="invalid_job_control"):
        queue.control("owner-a", 10, 1, job["id"], [])
    claim = queue.claim()
    for kwargs in ({}, {"error": "secret/address"}, {"result": {}, "error": "failure"}, {"result": []}):
        with pytest.raises(AppError):
            queue.finish(claim, **kwargs)


@pytest.mark.parametrize("kwargs", [{"workers_global": 17}, {"workers_owner": 3}, {"lease_seconds": 0},
                                    {"bytes_owner": True}, {"records_global": 100000}])
def test_trusted_configuration_is_still_bounded(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


def test_identical_timestamp_fifo_uses_insertion_order(queue):
    first = enqueue(queue)
    enqueue(queue, number=2)
    assert queue.claim().identifier == first["id"]


def test_heartbeat_without_checkpoint_preserves_last_checkpoint(queue):
    enqueue(queue)
    claim = queue.claim()
    queue.heartbeat(claim, current=1, total=2, checkpoint={"part": 1})
    queue.heartbeat(claim, current=2, total=2)
    with queue.ledger.connection() as db:
        assert db.execute("SELECT checkpoint FROM jobs").fetchone()[0] == '{"part":1}'


def test_ledger_runtime_recovery_marks_jobs_uncertain_in_same_checkpoint(queue):
    job = enqueue(queue)
    queue.claim()
    queue.ledger.recover_interrupted()
    assert get(queue, job)["state"] == "uncertain"
    assert not get(queue, job)["worker_stopped"]


def test_operator_reconciliation_is_audited_and_never_requeues(queue):
    job = enqueue(queue, reserved_bytes=10)
    claim = queue.claim()
    queue.recover(startup=True)
    queue.reconcile(job["id"], evidence_sha256="a" * 64, outcome="completed", result={"receipt": "confirmed"})
    assert get(queue, job)["state"] == "completed"
    assert get(queue, job)["worker_stopped"]
    assert get(queue, job)["result"] == {"receipt": "confirmed"}
    with pytest.raises(AppError, match="job_lease_invalid"):
        queue.finish(claim, result={"late": True})
    with pytest.raises(AppError, match="job_not_uncertain"):
        queue.reconcile(job["id"], evidence_sha256="b" * 64, outcome="cancelled")
    with queue.ledger.connection() as db:
        assert db.execute("SELECT evidence_sha256 FROM job_resolutions").fetchone()[0] == "a" * 64
        assert db.execute("SELECT reserved_bytes FROM jobs").fetchone()[0] == 10
    queue.release_storage(job["id"])


@pytest.mark.parametrize("kwargs", [{"outcome": "queued", "evidence_sha256": "a" * 64},
                                    {"outcome": "completed", "evidence_sha256": "a" * 64},
                                    {"outcome": "cancelled", "evidence_sha256": "secret evidence"}])
def test_reconciliation_rejects_retry_and_unstructured_evidence(queue, kwargs):
    job = enqueue(queue)
    with pytest.raises(AppError, match="invalid_job_resolution"):
        queue.reconcile(job["id"], **kwargs)


def test_worker_input_binding_uses_persisted_payload_and_fenced_identity(queue):
    enqueue(queue)
    claim = queue.claim()
    claim.payload['text'] = 'cannot replace durable worker input'
    assert queue.artifact_input(claim, 'text') == ('owner-a', 10, 1, 'sample')
    with pytest.raises(AppError, match='job_lease_invalid'):
        queue.artifact_input(replace(claim, token='0' * 64), 'text')
    queue.clock.value += 31
    with pytest.raises(AppError, match='job_lease_expired'):
        queue.artifact_input(claim, 'text')


def test_revocation_blocks_new_worker_input_binding(queue):
    enqueue(queue)
    claim = queue.claim()
    queue.ledger.revoke(1)
    with pytest.raises(AppError, match='instance_not_authorized'):
        queue.artifact_input(claim, 'text')


@pytest.mark.parametrize('state', ['paused', 'uncertain', 'running'])
def test_pending_work_cannot_be_hidden_by_newer_terminal_history(queue, state):
    job = enqueue(queue)
    with queue.ledger.transaction() as db:
        db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job['id']))
        source = dict(db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
        for number in range(55):
            row = dict(source, id=f'{number + 100:032x}', request_id=f'synthetic-finished-{number:04d}',
                       state='completed', result='{}', created=source['created'] + number + 1)
            db.execute('INSERT INTO jobs (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    listing = queue.list('owner-a', 10, 1)['jobs']
    assert len(listing) == 50
    assert listing[0]['id'] == job['id']


@pytest.mark.parametrize('minimum', [True, -1, 256 * 1024 * 1024 + 1])
def test_reviewed_kind_minimum_storage_has_hard_trusted_bounds(minimum):
    with pytest.raises(ValueError):
        Kind(validate, minimum_bytes=minimum)


def test_output_kind_cannot_be_admitted_or_claimed_with_a_smaller_reservation(queue):
    queue.kinds['synthetic.v1'] = Kind(validate, minimum_bytes=1024)
    with pytest.raises(AppError, match='invalid_job_budget'):
        enqueue(queue, reserved_bytes=1023)
    job = enqueue(queue, reserved_bytes=1024)
    with queue.ledger.transaction() as db:
        db.execute('UPDATE jobs SET reserved_bytes=1023 WHERE id=?', (job['id'],))
    with pytest.raises(AppError, match='job_kind_budget_mismatch'):
        queue.claim()
    assert get(queue, job)['state'] == 'queued' and get(queue, job)['worker_stopped']


@pytest.mark.parametrize('action,state', [('pause', 'paused'), ('cancel', 'cancelled'), ('revoke', 'cancelled')])
def test_prepublication_control_or_revocation_wins_without_a_publication_intent(queue, action, state):
    job = enqueue(queue, reserved_bytes=100)
    claim = queue.claim()
    if action == 'revoke':
        queue.ledger.revoke(1)
    else:
        queue.control('owner-a', 10, 1, job['id'], action)
    assert not queue.begin_publication(claim)
    with queue.ledger.connection() as db:
        row = db.execute('SELECT * FROM jobs').fetchone()
        assert row['state'] == state and not row['publication_started'] and row['slot']
    queue.worker_stopped(claim)
    with queue.ledger.connection() as db:
        assert db.execute('SELECT slot FROM jobs').fetchone()[0] == 0


def test_publication_intent_fences_late_controls_and_cannot_be_replayed_or_discarded(queue):
    job = enqueue(queue, reserved_bytes=100)
    claim = queue.claim()
    assert queue.begin_publication(claim)
    assert get(queue, job)['publishing'] and not get(queue, job)['worker_stopped']
    for action in ('pause', 'cancel', 'resume'):
        with pytest.raises(AppError, match='job_publication_started'):
            queue.control('owner-a', 10, 1, job['id'], action)
    with pytest.raises(AppError, match='job_publication_started'):
        queue.begin_publication(claim)
    with pytest.raises(AppError, match='job_publication_started'):
        queue.finish_offline(claim, result={'must_not_discard_publication': True})
    queue.finish(claim, result={'artifact': 'synthetic-confirmed-private-output'})
    assert get(queue, job)['state'] == 'completed' and not get(queue, job)['worker_stopped']
    queue.worker_stopped(claim)
    assert get(queue, job)['worker_stopped']
    assert get(queue, job)['current'] == get(queue, job)['total'] == 1
    queue.release_storage(job['id'])


def test_interrupted_publication_is_uncertain_and_keeps_slot_budget_and_replay_fence(queue):
    job = enqueue(queue, reserved_bytes=100)
    claim = queue.claim()
    assert queue.begin_publication(claim)
    queue.ledger.recover_interrupted()
    value = get(queue, job)
    assert value['state'] == 'uncertain' and value['publishing'] and not value['worker_stopped']
    assert enqueue(queue, reserved_bytes=100)['id'] == job['id']
    with pytest.raises(AppError, match='job_publication_started'):
        queue.control('owner-a', 10, 1, job['id'], 'cancel')
    with pytest.raises(AppError, match='job_cleanup_not_safe'):
        queue.release_storage(job['id'])
    with queue.ledger.connection() as db:
        assert db.execute('SELECT reserved_bytes FROM jobs').fetchone()[0] == 100
    assert queue.claim() is None
    queue.worker_stopped(claim)
    assert get(queue, job)['state'] == 'uncertain' and get(queue, job)['worker_stopped']


def test_forged_or_expired_claim_cannot_begin_private_publication(queue):
    enqueue(queue)
    claim = queue.claim()
    with pytest.raises(AppError, match='job_lease_invalid'):
        queue.begin_publication(replace(claim, token='0' * 64))
    queue.clock.value += 31
    with pytest.raises(AppError, match='job_lease_expired'):
        queue.begin_publication(claim)


def test_revocation_after_intent_cannot_turn_published_data_into_false_cancellation(queue):
    enqueue(queue)
    claim = queue.claim()
    assert queue.begin_publication(claim)
    queue.ledger.revoke(1)
    assert queue.heartbeat(claim, current=0, total=1)['state'] == 'cancelling'
    with pytest.raises(AppError, match='job_publication_started'):
        queue.acknowledge(claim, 'cancel')
    queue.finish(claim, result={'artifact': 'synthetic-already-confirmed-output'})
    queue.worker_stopped(claim)
    with queue.ledger.connection() as db:
        row = db.execute('SELECT * FROM jobs').fetchone()
        assert row['state'] == 'completed' and row['result'] and not row['slot']


def test_additive_publication_schema_upgrade_preserves_existing_jobs_and_handles_parallel_reopen(queue):
    job = enqueue(queue)
    with queue.ledger.transaction() as db:
        db.execute('ALTER TABLE jobs DROP COLUMN publication_started')
    def reopen(_):
        return JobQueue(queue.ledger, kinds=queue.kinds, clock=queue.clock)
    with ThreadPoolExecutor(max_workers=4) as pool:
        reopened = list(pool.map(reopen, range(4)))
    for value in reopened:
        assert not get(value, job)['publishing']
        assert get(value, job)['state'] == 'queued'


def test_atomic_publication_and_cancel_race_has_only_honest_serialized_outcomes(queue):
    import threading
    for number in range(1, 21):
        job = enqueue(queue, number=number)
        claim = queue.claim()
        barrier = threading.Barrier(2)
        def publish(barrier=barrier, claim=claim):
            barrier.wait(timeout=3)
            return queue.begin_publication(claim)
        def cancel(barrier=barrier, job=job):
            barrier.wait(timeout=3)
            try:
                return queue.control('owner-a', 10, 1, job['id'], 'cancel')['state']
            except AppError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            publication = pool.submit(publish)
            cancellation = pool.submit(cancel)
            entered, control = publication.result(timeout=5), cancellation.result(timeout=5)
        if entered:
            assert control == 'job_publication_started'
            queue.finish(claim, result={'confirmed': True})
        else:
            assert control == 'cancelling'
            assert get(queue, job)['state'] == 'cancelled'
        queue.worker_stopped(claim)

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from michiflin.config import Settings
from michiflin.identity import Principal
from michiflin.service import Operation, Service


@pytest.fixture
def member():
    return Principal("reader@example.invalid", "A" * 40, 20, True, True)


@pytest.fixture
def service(tmp_path, member):
    value = Service(Settings(state_dir=tmp_path))
    value.register_instance(member, 42)
    return value


def request(service, op="utilities.calculate", args=None, identifier="synthetic-request-0001"):
    return {"id": identifier, "op": op, "args": args or {"expression": "17 * 23"},
            "session": service.session, "locale": "en"}


def test_public_access_does_not_require_a_temporary_owner_grant(service, member):
    response = service.handle(member, 42, request(service))
    assert response["ok"] and response["data"]["value"] == 391


def test_hello_does_not_disclose_administrators_or_private_links(service, member):
    response = service.handle(member, 42, request(service, "hello"))
    assert response["data"]["role"] == "member"
    assert "admin.health" not in response["data"]["operations"]
    assert member.address not in str(response)
    assert "dccontact:" not in str(response)


@pytest.mark.parametrize("change", [
    {"address": "other@example.invalid"}, {"fingerprint": "B" * 40}, {"chat_id": 21},
    {"encrypted": False}, {"one_to_one": False},
])
def test_bindings_and_encryption_cannot_be_bypassed(service, member, change):
    assert not service.handle(replace(member, **change), 42, request(service))["ok"]


def test_email_membership_alone_never_grants_administration(service, member):
    admin_service = Service(replace(service.settings, admin_keys={member.address: member.fingerprint}))
    value = request(admin_service, "admin.health")
    assert admin_service.handle(member, 42, value)["error"] == "administrator_required"
    assert admin_service.handle(replace(member, verified=True), 42, value)["ok"]
    impostor = replace(member, verified=True, fingerprint="B" * 40)
    assert not admin_service.handle(impostor, 42, value)["ok"]


def test_instance_binding_cannot_be_transferred(service, member):
    with pytest.raises(Exception, match="instance_binding_conflict"):
        service.register_instance(replace(member, address="other@example.invalid"), 42)


def test_repeat_request_is_cached_and_a_changed_body_is_rejected(service, member):
    calls = []
    service.operations["synthetic.test"] = Operation(lambda *_: calls.append(1) or {"count": len(calls)})
    value = request(service, "synthetic.test")
    first = service.handle(member, 42, value)
    assert first == service.handle(member, 42, value)
    assert calls == [1]
    changed = copy.deepcopy(value)
    changed["args"] = {"changed": True}
    assert service.handle(member, 42, changed)["error"] == "request_identity_conflict"


def test_restart_never_reexecutes_a_running_operation(service, member):
    value = request(service)
    service.ledger.reserve(42, value["id"], member.storage_key, value["op"], value["args"], 40, 3)
    restarted = Service(service.settings)
    restarted.ledger.recover_interrupted()
    value["session"] = restarted.session
    assert restarted.handle(member, 42, value)["error"] == "previous_outcome_uncertain"


def test_old_session_does_not_execute_work(service, member):
    value = request(service)
    value["session"] = "old-session"
    assert service.handle(member, 42, value)["error"] == "session_changed"


@pytest.mark.parametrize("identifier", ["short", "x" * 81, "<script>unsafe</script>", 123, None])
def test_invalid_request_identifiers_are_refused(service, member, identifier):
    assert service.handle(member, 42, request(service, identifier=identifier))["error"] == "invalid_request_id"


def test_daily_limit_applies_to_public_users(service, member):
    limited = Service(replace(service.settings, daily_requests=1))
    assert limited.handle(member, 42, request(limited))["ok"]
    value = request(limited, identifier="synthetic-request-0002")
    assert limited.handle(member, 42, value)["error"] == "daily_limit"


def test_concurrent_retries_do_not_duplicate_work(service, member):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(*_):
        calls.append(1)
        entered.set()
        assert release.wait(4)
        return {"count": len(calls)}

    service.operations["synthetic.test"] = Operation(execute)
    value = request(service, "synthetic.test")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.handle, member, 42, value)
        try:
            assert entered.wait(4)
            assert service.handle(member, 42, value)["error"] == "request_pending"
        finally:
            release.set()
        assert first.result()["ok"]
    assert calls == [1]


def test_notes_are_isolated_by_sender_and_key(service, member):
    added = service.handle(member, 42, request(service, "notes.add", {"text": "A private note"}))
    identifier = added["data"]["id"]
    stranger = replace(member, address="stranger@example.invalid")
    service.register_instance(stranger, 43)
    value = request(service, "notes.delete", {"id": identifier}, identifier="delete-request-0001")
    assert service.handle(stranger, 43, value)["data"]["deleted"] is False
    assert service.ledger.notes(member.storage_key)[0]["text"] == "A private note"


def test_model_keys_are_not_exposed_to_the_app(tmp_path, member):
    settings = Settings(state_dir=tmp_path, online_providers=({"id": "test", "name": "Test", "url": "https://example.invalid/v1/chat/completions", "model": "example", "key": "synthetic-secret-do-not-return"},))
    service = Service(settings)
    service.register_instance(member, 42)
    response = service.handle(member, 42, request(service, "hello"))
    assert "synthetic-secret" not in str(response)
    assert "synthetic-secret" not in repr(settings)
    assert response["data"]["models"] == [{"id": "test", "name": "Test", "local": False}]


@pytest.mark.parametrize("session", ["🐈", [], {}, None, 123])
def test_malformed_session_is_a_safe_error(service, member, session):
    value = request(service)
    value["session"] = session
    assert service.handle(member, 42, value)["error"] == "session_changed"


@pytest.mark.parametrize("result", [{"text": "🐈" * 90000}, {"value": float("nan")}, {"value": object()}])
def test_unserializable_or_large_result_is_settled_not_left_running(service, member, result):
    service.operations["synthetic.result"] = Operation(lambda *_: result)
    value = request(service, "synthetic.result")
    first = service.handle(member, 42, value)
    assert first["error"] in {"result_too_large", "result_invalid"}
    assert service.handle(member, 42, value) == first


def test_service_wide_budget_cannot_be_bypassed_by_rotating_sender_keys(service, member):
    limited = Service(replace(service.settings, global_daily_requests=1))
    assert limited.handle(member, 42, request(limited))["ok"]
    another = replace(member, address="another@example.invalid", fingerprint="B" * 40)
    limited.register_instance(another, 43)
    result = limited.handle(another, 43, request(limited, identifier="synthetic-other-1234"))
    assert result["error"] == "service_daily_limit"


def test_note_mutation_and_cached_response_commit_together(service, member, monkeypatch):
    monkeypatch.setattr(service.ledger, "settle", lambda *_: pytest.fail("Non-atomic checkpoint used"))
    value = request(service, "notes.add", {"text": "Atomic private note"})
    first = service.handle(member, 42, value)
    assert first["ok"]
    restarted = Service(service.settings)
    restarted.ledger.recover_interrupted()
    value["session"] = restarted.session
    assert restarted.handle(member, 42, value) == first
    assert len(restarted.ledger.notes(member.storage_key)) == 1
    deletion = request(restarted, "notes.delete", {"id": first["data"]["id"]}, identifier="atomic-delete-0001")
    removed = restarted.handle(member, 42, deletion)
    assert removed["data"]["deleted"] is True
    assert restarted.handle(member, 42, deletion) == removed
    assert not restarted.ledger.notes(member.storage_key)


@pytest.mark.parametrize("operation", ["notes.add", "notes.delete"])
def test_checkpoint_failure_rolls_back_note_mutation(service, member, operation):
    import sqlite3

    note = service.ledger.add_note(member.storage_key, "Retain on failed delete")
    args = {"text": "Do not partially commit"} if operation == "notes.add" else {"id": note}
    identifier = "atomic-failure-0001"
    service.ledger.reserve(42, identifier, member.storage_key, operation, args, 40, 3)
    with service.ledger.connection() as db:
        db.execute("CREATE TRIGGER synthetic_checkpoint_failure BEFORE UPDATE OF response ON requests "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic checkpoint interruption'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.ledger.complete_note(42, identifier, member.storage_key, operation, args)
    assert [row["id"] for row in service.ledger.notes(member.storage_key)] == [note]
    with service.ledger.connection() as db:
        assert db.execute("SELECT state FROM requests WHERE id=?", (identifier,)).fetchone()[0] == "running"
        db.execute("DROP TRIGGER synthetic_checkpoint_failure")
    response = service.ledger.complete_note(42, identifier, member.storage_key, operation, args)
    assert response["ok"]


def test_atomic_note_cannot_mutate_a_different_reserved_request(service, member):
    from michiflin.errors import AppError

    args = {"text": "Original note"}
    service.ledger.reserve(42, "atomic-bound-0001", member.storage_key, "notes.add", args, 40, 3)
    with pytest.raises(AppError, match="request_checkpoint_failed"):
        service.ledger.complete_note(42, "atomic-bound-0001", member.storage_key, "notes.add", {"text": "Changed note"})
    assert not service.ledger.notes(member.storage_key)

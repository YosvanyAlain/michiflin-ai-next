import hashlib
import os
import threading
from dataclasses import replace

import pytest
from test_miniapp import browser

from michiflin import sandbox
from michiflin.file_jobs import CHECKSUM_HANDLER


@pytest.fixture
def isolated():
    if not sandbox.available():
        if os.environ.get('MICHIFLIN_REQUIRE_SANDBOX') == '1':
            pytest.fail('Required namespace/seccomp sandbox is unavailable')
        pytest.skip('Real worker/browser integration remains disabled without isolation')


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('locale', ['en', 'es'])
def test_real_bilingual_file_to_queue_to_isolated_checksum_result(isolated, tmp_path, engine, locale):
    done = threading.Event()
    content = b'Synthetic isolated checksum input.\x00\xff'

    def observe(service, _principal):
        original = service.jobs.worker_stopped
        def stopped(claim):
            original(claim)
            done.set()
        service.jobs.worker_stopped = stopped

    with browser(tmp_path, locale, engine, workers=True, configure_service=observe) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('input[type=file]').set_input_files({'name': 'private.bin', 'mimeType': 'application/octet-stream', 'buffer': content})
        page.locator('[data-required-operation="files.put"]').click()
        page.locator('.server-file-row').wait_for()
        page.locator('[data-required-operation="files.checksum"]').click()
        page.locator('.server-file-output code').wait_for()
        assert done.wait(5)
        page.evaluate("location.hash='server_jobs'")
        page.locator('.server-jobs').wait_for()
        assert not page.evaluate("window.__requests.some(r=>r.op.startsWith('jobs.'))")
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=completed]').wait_for()
        page.locator('[data-required-operation="jobs.status"]').click()
        page.get_by_text(hashlib.sha256(content).hexdigest(), exact=True).wait_for()
        assert page.locator('progress').get_attribute('value') == '1'
        assert not page.evaluate("window.__requests.some(r=>r.op==='jobs.control')")
        for width in (320, 390, 768, 1440):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert content.decode(errors='ignore') not in page.evaluate('JSON.stringify(localStorage)')
        page.reload()
        page.locator('.server-jobs').wait_for()
        assert page.locator('.job-row').count() == 0
        assert not page.evaluate("window.__requests.some(r=>r.op.startsWith('jobs.'))")
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=completed]').wait_for()
        assert errors == []


@pytest.mark.parametrize('engine,locale', [('chromium', 'en'), ('webkit', 'es')])
def test_real_pause_resume_and_confirmed_cancel_do_not_claim_instant_worker_stop(isolated, tmp_path, engine, locale, monkeypatch):
    import michiflin.service as module

    handler = replace(CHECKSUM_HANDLER, script='import time; time.sleep(30)',
                      policy=replace(sandbox.Policy(), wall_seconds=20))
    monkeypatch.setattr(module, 'CHECKSUM_HANDLER', handler)
    started, reaped = threading.Event(), threading.Event()
    original_run = sandbox.run

    def observe_worker(*a, **kwargs):
        original_tick = kwargs.get('tick')
        if original_tick:
            def tick():
                original_tick()
                started.set()
            kwargs['tick'] = tick
        return original_run(*a, **kwargs)
    monkeypatch.setattr(sandbox, 'run', observe_worker)

    def observe(service, _principal):
        original = service.jobs.worker_stopped
        def stopped(claim):
            original(claim)
            reaped.set()
        service.jobs.worker_stopped = stopped

    labels = {'en': ('Pause', 'Resume', 'Stop task'), 'es': ('Pausar', 'Reanudar', 'Detener tarea')}[locale]
    with browser(tmp_path, locale, engine, workers=True, configure_service=observe) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('input[type=file]').set_input_files({'name': 'data.bin', 'mimeType': 'application/octet-stream', 'buffer': b'synthetic'})
        page.locator('[data-required-operation="files.put"]').click()
        page.locator('.server-file-row').wait_for()
        page.locator('[data-required-operation="files.checksum"]').click()
        page.locator('.server-file-output code').wait_for()
        assert started.wait(5)
        page.evaluate("location.hash='server_jobs'")
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=running]').wait_for()
        page.get_by_role('button', name=labels[0], exact=True).click()
        page.locator('.job-row[data-job-state=pausing]').wait_for()
        assert reaped.wait(5)
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=paused]').wait_for()
        started.clear()
        reaped.clear()
        page.get_by_role('button', name=labels[1], exact=True).click()
        assert started.wait(5)
        page.get_by_role('button', name=labels[2], exact=True).click()
        page.locator('.job-cancel-dialog .secondary').click()
        assert not page.evaluate("window.__requests.some(r=>r.op==='jobs.control'&&r.args.action==='cancel')")
        page.get_by_role('button', name=labels[2], exact=True).click()
        page.locator('.job-cancel-dialog .primary').click()
        assert reaped.wait(5)
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=cancelled]').wait_for()
        assert not page.evaluate("window.__requests.some(r=>r.op==='files.delete')")
        assert errors == []


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_queue_is_manual_and_disabled_without_connection(tmp_path, engine):
    with browser(tmp_path, engine=engine, connected=False) as (page, errors):
        page.evaluate("location.hash='server_jobs'")
        page.locator('.server-jobs').wait_for()
        assert page.locator('[data-required-operation="jobs.list"]').is_disabled()
        assert page.evaluate('Michiflin.transport.tasks.length') == 0
        assert errors == []


def test_uncertain_job_has_no_retry_or_control_and_foreign_kinds_are_text_only(tmp_path):
    def prepare(service, principal):
        from michiflin.jobs import Kind
        service.jobs.kinds = {'synthetic.v1': Kind(lambda _: None)}
        job = service.jobs.enqueue(principal.storage_key, 20, 42, 'synthetic-request-job', 'synthetic.v1', {})
        with service.ledger.transaction() as db:
            db.execute("UPDATE jobs SET state='uncertain',kind=?,slot=1 WHERE id=?", ('<img src=x onerror=alert(1)>', job['id']))

    with browser(tmp_path, configure_service=prepare) as (page, errors):
        page.evaluate("location.hash='server_jobs'")
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row[data-job-state=uncertain]').wait_for()
        assert page.locator('.job-row img').count() == 0
        assert page.locator('.job-row h3').inner_text().startswith('<img')
        assert page.locator('[data-required-operation="jobs.control"]').count() == 0
        assert not page.evaluate("window.__requests.some(r=>r.op==='jobs.control')")
        assert errors == []


@pytest.mark.parametrize('engine,locale', [('chromium', 'en'), ('webkit', 'es')])
def test_private_publication_intent_has_no_false_pause_cancel_or_retry_controls(tmp_path, engine, locale):
    def prepare(service, principal):
        from michiflin.jobs import Kind
        service.jobs.kinds = {'synthetic.v1': Kind(lambda _: None)}
        service.jobs.enqueue(principal.storage_key, 20, 42, 'synthetic-publication-job', 'synthetic.v1', {})
        assert service.jobs.begin_publication(service.jobs.claim())
    with browser(tmp_path, locale, engine, configure_service=prepare) as (page, errors):
        page.evaluate("location.hash='server_jobs'")
        page.locator('[data-required-operation="jobs.list"]').click()
        page.locator('.job-row').wait_for()
        label = 'Publishing private result' if locale == 'en' else 'Publicando resultado privado'
        assert label in page.locator('.job-row').inner_text()
        assert page.locator('[data-required-operation="jobs.control"]').count() == 0
        assert not page.evaluate("window.__requests.some(r=>r.op==='jobs.control')")
        assert errors == []

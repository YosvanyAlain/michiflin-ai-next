import base64
import hashlib
from pathlib import Path

import pytest
from test_miniapp import browser


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('locale', ['en', 'es'])
def test_server_copies_real_upload_hash_export_refresh_and_explicit_delete(tmp_path, engine, locale):
    body = b'Synthetic private bytes for a server copy.\x00\xff'
    name = '<img src=x onerror=alert(1)>.txt'
    with browser(tmp_path, locale, engine) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('.server-files').wait_for()
        assert not page.evaluate("window.__requests.some(r=>r.op.startsWith('files.'))")
        page.locator('.server-files input[type=file]').set_input_files({'name': name, 'mimeType': 'application/octet-stream', 'buffer': body})
        page.locator('[data-required-operation="files.put"]').click()
        row = page.locator('.server-file-row')
        row.wait_for()
        assert row.locator('h3').inner_text() == name
        assert row.locator('img').count() == 0
        row.locator('summary').click()
        assert row.locator('code').inner_text() == hashlib.sha256(body).hexdigest()
        artifact = row.get_attribute('data-file-id')
        for width in (320, 390, 768, 1440):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        row.locator('[data-required-operation="files.read"]').click()
        download_link = page.locator('.server-file-output a[download]')
        download_link.wait_for()
        with page.expect_download() as downloaded:
            download_link.click()
        saved = Path(downloaded.value.path())
        assert saved.read_bytes() == body
        assert downloaded.value.suggested_filename.endswith('.bin')
        assert base64.b64encode(body).decode() not in page.evaluate('JSON.stringify(localStorage)')
        assert page.evaluate('window.__unsafe===undefined')
        page.reload()
        page.locator('.server-files').wait_for()
        assert page.locator('.server-file-row').count() == 0
        assert not page.evaluate("window.__requests.some(r=>r.op.startsWith('files.'))")
        page.locator('[data-required-operation="files.list"]').click()
        row.wait_for()
        assert row.get_attribute('data-file-id') == artifact
        row.locator('[data-required-operation="files.delete"]').click()
        page.locator('.server-delete-dialog .secondary').click()
        assert not page.evaluate("window.__requests.some(r=>r.op==='files.delete')")
        row.locator('[data-required-operation="files.delete"]').click()
        page.locator('.server-delete-dialog .primary').click()
        page.wait_for_function("()=>document.querySelectorAll('.server-file-row').length===0")
        assert saved.read_bytes() == body
        assert not list((tmp_path / 'state' / 'artifacts').glob('*.blob'))
        assert errors == []


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_server_files_are_disabled_offline_and_selection_does_not_upload(tmp_path, engine):
    with browser(tmp_path, engine=engine, connected=False) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('.server-files').wait_for()
        page.locator('input[type=file]').set_input_files({'name': 'private.txt', 'mimeType': 'text/plain', 'buffer': b'private'})
        assert all(control.is_disabled() for control in page.locator('.server-files [data-remote]').all())
        assert page.evaluate('window.webxdc===undefined')
        assert not list((tmp_path / 'state' / 'artifacts').iterdir())
        assert errors == []


def test_oversized_inline_copy_is_refused_before_any_mail_request(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('input[type=file]').set_input_files({'name': 'large.bin', 'mimeType': 'application/octet-stream', 'buffer': b'x' * 12289})
        page.locator('[data-required-operation="files.put"]').click()
        page.locator('.server-file-output [role=alert]').wait_for()
        assert not page.evaluate("window.__requests.some(r=>r.op==='files.put')")
        assert not list((tmp_path / 'state' / 'artifacts').iterdir())
        assert errors == []


def test_older_bot_without_file_operations_does_not_get_executable_controls(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.evaluate("""()=>{const s=Michiflin.transport.service;s.operations=[];
            Michiflin.storage.write('service',s);location.hash='server_files';}""")
        page.locator('.server-files').wait_for()
        assert all(control.is_disabled() for control in page.locator('.server-files [data-remote]').all())
        assert not page.evaluate("window.__requests.some(r=>r.op.startsWith('files.'))")
        assert errors == []


def test_corrupted_inline_response_never_creates_an_export(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='server_files'")
        page.locator('input[type=file]').set_input_files({'name': 'private.txt', 'mimeType': 'text/plain', 'buffer': b'Synthetic original bytes'})
        page.locator('[data-required-operation="files.put"]').click()
        page.locator('.server-file-row').wait_for()

        def corrupt(route):
            response = route.fetch()
            payload = response.json()
            if route.request.post_data_json.get('op') == 'files.read' and payload.get('ok'):
                payload['data']['data'] = base64.b64encode(b'corrupted bytes').decode()
            route.fulfill(response=response, json=payload)

        page.route('**/test/dispatch', corrupt)
        page.locator('[data-required-operation="files.read"]').click()
        page.locator('.server-file-output [role=alert]').wait_for()
        assert page.locator('.server-file-output a[download]').count() == 0
        assert errors == []

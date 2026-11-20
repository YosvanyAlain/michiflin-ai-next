from __future__ import annotations

from pathlib import Path

import pytest
from test_miniapp import browser

from michiflin.errors import AppError
from michiflin.public_http import Document


class WebFixture:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        markup = '<title>&lt;img src=x onerror=alert(1)&gt;</title><main><p>'
        markup += ('Synthetic public reading. A quiet black cat rests beside a window. ' * 130)
        markup += '</p><a href="/next">Next chapter</a><script>window.__unsafe=true</script>'
        markup += '<img src="https://tracker.example/pixel"></main>'
        return Document(url, "text/html; charset=utf-8", markup.encode())


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("locale", ["en", "es"])
def test_reader_uses_real_service_paginates_locally_and_exports_plain_text(tmp_path, engine, locale):
    source = WebFixture()
    with browser(tmp_path, locale, engine, reader_source=source) as (page, errors):
        outbound = []
        page.on("request", lambda req: outbound.append(req.url))
        page.evaluate("location.hash='reader'")
        page.locator('.reader-form input').fill('https://example.com/story')
        page.locator('.reader-form button').click()
        page.locator('.reader-text').wait_for()
        assert source.calls == ['https://example.com/story']
        assert page.locator('.reader-article img').count() == 0
        assert page.locator('.reader-article h2').inner_text() == '<img src=x onerror=alert(1)>'
        assert page.evaluate('window.__unsafe===undefined')
        page.locator('.reader-pagination button').last.click()
        assert '2 / 2' in page.locator('.reader-pagination').inner_text()
        assert len(source.calls) == 1
        page.locator('.reader-links summary').click()
        page.locator('.reader-link').click()
        page.wait_for_function("()=>document.querySelector('.reader-source')?.textContent==='https://example.com/next'")
        assert source.calls == ['https://example.com/story', 'https://example.com/next']
        with page.expect_download() as transfer:
            page.locator('.reader-results a[download]').click()
        text = Path(transfer.value.path()).read_text()
        assert 'https://example.com/next' in text and 'Synthetic public reading.' in text
        assert '<script>' not in text
        page.reload()
        page.locator('.reader-text').wait_for()
        assert len(source.calls) == 2
        for width in [320, 390, 768, 1440]:
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not any('tracker.example' in url for url in outbound)
        assert errors == []


def test_reader_stays_disabled_offline(tmp_path):
    source = WebFixture()
    with browser(tmp_path, connected=False, reader_source=source) as (page, errors):
        page.evaluate("location.hash='reader'")
        assert page.locator('.reader-form button').is_disabled()
        assert source.calls == [] and errors == []


def test_reader_displays_access_denial_without_auto_retry_or_fake_content(tmp_path):
    class Denied:
        def get(self, url):
            raise AppError('web_access_restricted')

    with browser(tmp_path, reader_source=Denied()) as (page, errors):
        page.evaluate("location.hash='reader'")
        page.locator('.reader-form input').fill('https://example.com/restricted')
        page.locator('.reader-form button').click()
        page.locator('.reader-results [role=alert]').wait_for()
        assert page.locator('.reader-text').count() == 0
        assert len(page.evaluate("window.__requests.filter(item=>item.op==='web.read')")) == 1
        assert errors == []

from __future__ import annotations

import pytest
from test_media import VIDEO, fixture_client
from test_miniapp import browser

from michiflin.errors import AppError


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('locale', ['en', 'es'])
def test_youtube_preview_is_plain_metadata_not_a_player_or_download(tmp_path, engine, locale):
    source = fixture_client()
    with browser(tmp_path, locale, engine, media_source=source) as (page, errors):
        requests = []
        page.on('request', lambda req: requests.append(req.url))
        page.evaluate("location.hash='youtube'")
        page.locator('.reader-form input').fill(VIDEO)
        page.locator('.reader-form button').click()
        page.locator('.media-preview h2').wait_for()
        assert page.locator('.media-preview h2').inner_text() == '<img src=x onerror=alert(1)>'
        assert page.locator('.media-preview iframe,.media-preview img,.media-preview video').count() == 0
        assert page.locator('.media-preview [download]').count() == 0
        assert page.locator('.media-preview a').get_attribute('href') == VIDEO
        assert not any('tracker.example' in url for url in requests)
        assert source.get.call_count == 1
        page.reload()
        page.locator('.media-preview h2').wait_for()
        assert source.get.call_count == 1
        for width in [320, 390, 768, 1440]:
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert errors == []


def test_youtube_preview_is_disabled_without_bot(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("location.hash='youtube'")
        assert page.locator('.reader-form button').is_disabled()
        assert errors == []


def test_youtube_preview_reports_source_failure_without_fake_results(tmp_path):
    source = fixture_client()
    source.get.side_effect = AppError('web_access_restricted')
    with browser(tmp_path, media_source=source) as (page, errors):
        page.evaluate("location.hash='youtube'")
        page.locator('.reader-form input').fill(VIDEO)
        page.locator('.reader-form button').click()
        page.locator('.media-preview [role=alert]').wait_for()
        assert page.locator('.media-preview h2').count() == 0
        assert source.get.call_count == 1 and errors == []

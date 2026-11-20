from __future__ import annotations

import functools
import http.server
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from michiflin.config import Settings
from michiflin.identity import Principal
from michiflin.service import Service

playwright_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
WIRE = """
window.__requests=[];
window.webxdc={
  sendUpdate(update){
    window.__requests.push(update.payload);
    fetch('/test/dispatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(update.payload)})
      .then(response=>response.json()).then(body=>window.__listener({serial:++window.__serial,payload:{protocol:1,type:'michiflin.response',body}}));
  },
  setUpdateListener(listener){window.__listener=listener;window.__serial=0;return Promise.resolve();}
};
"""


class SourceFixture:
    def get(self, url, params=None):
        if "gutendex" in url and url.endswith("/123/"):
            return {"id": 123, "copyright": False, "title": "Synthetic Book", "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/123.epub"}}
        if "gutendex" in url:
            return {"count": 1, "next": None, "results": [{"id": 123, "title": "Synthetic Book", "authors": [{"name": "Synthetic Writer"}], "copyright": False, "languages": ["en"], "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/123.epub.images"}}]}
        if "geocoding" in url:
            return {"results": [{"id": 1, "name": "Synthetic City", "country": "Test country", "latitude": 45, "longitude": 1}]}
        if "open-meteo.com/v1/forecast" in url:
            now = datetime.now(UTC).replace(microsecond=0)
            return {"current": {"time": now.isoformat()[:16], "weather_code": 65, "is_day": 1, "temperature_2m": 19, "apparent_temperature": 18, "relative_humidity_2m": 80, "wind_speed_10m": 12, "precipitation": 3}, "utc_offset_seconds": 0, "timezone": "UTC", "hourly": {"time": [(now + timedelta(hours=index)).isoformat()[:16] for index in range(20)], "temperature_2m": [19] * 20, "precipitation_probability": [70] * 20}, "daily": {"time": [(now + timedelta(days=index)).date().isoformat() for index in range(7)], "temperature_2m_min": [15] * 7, "temperature_2m_max": [21] * 7, "precipitation_probability_max": [80] * 7}}
        raise AssertionError("Unexpected fixture source")


@contextmanager
def browser(tmp_path, locale="en", engine="chromium", connected=True, reader_source=None):
    service = Service(Settings(state_dir=tmp_path / "state"), SourceFixture())
    if reader_source is not None:
        service.reader.client = reader_source
    principal = Principal("synthetic@example.invalid", "A" * 40, 20, True, True)
    service.register_instance(principal, 42)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            if self.path != "/test/dispatch":
                self.send_error(404)
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps(service.handle(principal, 42, request)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT / "miniapp")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright_api.sync_playwright() as playwright:
            instance = getattr(playwright, engine).launch()
            page = instance.new_page(locale=locale, viewport={"width": 390, "height": 844})
            page.emulate_media(reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/webxdc.js", lambda route: route.fulfill(status=200, content_type="text/javascript", body=WIRE if connected else ""))
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            if connected:
                page.evaluate("""()=>new Promise((resolve,reject)=>{
                  if(Michiflin.transport.ready){resolve();return;}
                  const timer=setTimeout(()=>{document.removeEventListener('michiflin:transport',listener);reject(new Error('Handshake timed out'));},30000);
                  const listener=()=>{if(Michiflin.transport.ready){clearTimeout(timer);document.removeEventListener('michiflin:transport',listener);resolve();}};
                  document.addEventListener('michiflin:transport',listener);
                })""")
            try:
                yield page, errors
            finally:
                instance.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("locale", ["en", "es"])
def test_responsive_bilingual_workspace(tmp_path, engine, locale):
    with browser(tmp_path, locale, engine) as (page, errors):
        assert page.locator(".feature-hero").is_visible()
        for width in [320, 390, 768, 1024, 1440]:
            page.set_viewport_size({"width": width, "height": 844})
            for route in ["home", "library", "utilities", "weather", "assistant", "activity", "calculator", "reader"]:
                page.evaluate("route=>{location.hash=route}", route)
                page.wait_for_timeout(25)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), (width, route)
        assert errors == []


def test_library_filters_use_real_service_without_inventing_formats(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='library'")
        page.locator(".library-filters input").first.fill("Synthetic")
        page.locator(".library-filters select").nth(2).select_option("epub")
        page.locator(".library-filters button").click()
        page.locator(".book-card").wait_for()
        assert page.locator(".book-card h3").inner_text() == "Synthetic Book"
        assert page.locator(".book-links a").inner_text() == "EPUB"
        query = page.evaluate("window.__requests.filter(item=>item.op==='library.search').at(-1)")
        assert query["args"]["format"] == "epub"
        assert query["args"]["title"] == "Synthetic"
        assert errors == []


def test_weather_requires_a_chosen_place_and_animates_actual_effect(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='weather'")
        assert page.locator(".weather-widget").count() == 0
        page.locator(".search-inline input").fill("Synthetic")
        page.locator(".search-inline button").click()
        page.locator(".place-option").click()
        page.locator(".weather-widget[data-effect=rain]").wait_for()
        assert page.locator(".rain-drop").count() == 15
        assert page.locator(".forecast-row").count() == 7
        assert page.locator(".hour-card").count() >= 12
        page.reload()
        page.locator(".weather-widget[data-effect=rain]").wait_for()
        assert errors == []


def test_offline_tools_never_require_the_bot_or_send_passwords(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("location.hash='calculator'")
        page.locator(".tool-workbench input").fill("17 * 23")
        page.locator(".tool-workbench button").click()
        assert page.locator(".tool-output pre").inner_text() == "391"
        page.evaluate("location.hash='password'")
        page.locator(".tool-workbench button").click()
        password = page.locator(".tool-output pre").inner_text()
        assert len(password) == 20
        assert password not in page.evaluate("JSON.stringify(localStorage)")
        assert errors == []


def test_settings_dialog_and_keyboard_do_not_break_the_workspace(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.locator("#preferences-button").click()
        page.locator("#preferences-content select").first.select_option("dark")
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.keyboard.press("Escape")
        assert not page.locator("#preferences-dialog").is_visible()
        page.evaluate("location.hash='calculator'")
        page.locator(".tool-workbench input").fill("(2+3)*4")
        page.locator(".tool-workbench input").press("Enter")
        assert page.locator(".tool-output pre").inner_text() == "20"
        assert errors == []


def test_offline_math_never_evaluates_javascript(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        results = page.evaluate("""() => ['alert(1)','9**9999','1/0','[1,2]','1e200'].map(value=>{
          try {Michiflin.offline.calculate(value);return false;}catch{return true;}
        })""")
        assert all(results)
        assert errors == []


def test_async_send_failure_is_uncertain_not_silently_pending(tmp_path):
    with browser(tmp_path) as (page, errors):
        result = page.evaluate("""async()=>{
          window.webxdc.sendUpdate=()=>Promise.reject(new Error('synthetic failure'));
          const request=Michiflin.transport.call('utilities.calculate',{expression:'2+2'});
          try{await request.promise;}catch{}
          const task=Michiflin.transport.tasks.find(item=>item.id===request.id);
          return {status:task.status,error:task.error};
        }""")
        assert result == {"status": "uncertain", "error": "send_unconfirmed"}
        assert errors == []


def test_handshake_timeout_allows_reconnect(tmp_path):
    with browser(tmp_path) as (page, errors):
        page.clock.install()
        page.evaluate("window.__normalSend=window.webxdc.sendUpdate;window.webxdc.sendUpdate=()=>{};Michiflin.transport.connect()")
        assert not page.evaluate("Michiflin.transport.ready")
        page.clock.fast_forward(30001)
        ready = page.evaluate("""async()=>{
          window.webxdc.sendUpdate=window.__normalSend;
          const connected=new Promise(resolve=>{
            const listener=()=>{if(Michiflin.transport.ready){document.removeEventListener('michiflin:transport',listener);resolve(true);}};
            document.addEventListener('michiflin:transport',listener);
          });
          Michiflin.transport.connect();return connected;
        }""")
        assert ready
        assert errors == []


def test_chunked_response_is_order_independent_and_identity_bound(tmp_path):
    from michiflin.protocol import encode_response

    with browser(tmp_path) as (page, errors):
        identifier = page.evaluate("""()=>{
          window.webxdc.sendUpdate=()=>{};
          return Michiflin.transport.call('synthetic.large',{}).id;
        }""")
        body = {"id": identifier, "ok": True, "data": {"text": "Cat 🐈 " * 10000}}
        frames = encode_response(body)
        assert len(frames) > 1
        page.evaluate("frames=>frames.reverse().forEach(payload=>window.__listener({serial:++window.__serial,payload}))", frames)
        task = page.evaluate("id=>Michiflin.transport.tasks.find(item=>item.id===id)", identifier)
        assert task["status"] == "done"
        assert task["data"] == body["data"]
        assert errors == []


def test_book_delivery_requires_explicit_confirmation_and_returns_a_receipt(tmp_path, monkeypatch):
    import io
    import zipfile

    from michiflin import downloads

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    monkeypatch.setattr(downloads, "fetch_bytes", lambda _: stream.getvalue())
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='library'")
        page.locator(".library-filters button").click()
        page.locator(".book-card").wait_for()
        page.get_by_role("button", name="Receive in chat").click()
        assert page.locator("dialog[open]").is_visible()
        assert "USA" in page.locator("dialog[open]").inner_text()
        assert not page.evaluate("window.__requests.some(item=>item.op==='library.deliver')")
        page.get_by_role("button", name="Send this edition").click()
        page.evaluate("location.hash='activity'")
        page.get_by_text("Queued for this encrypted chat.", exact=True).wait_for()
        request = page.evaluate("window.__requests.find(item=>item.op==='library.deliver')")
        assert request["args"] == {"book": 123, "format": "epub"}
        assert errors == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_extended_utilities_are_local_and_handle_unicode_and_calendar_edges(tmp_path, engine):
    with browser(tmp_path, engine=engine, connected=False) as (page, errors):
        result = page.evaluate("""()=>{
          const E=Michiflin.extras;
          const input='Hola 🐈 café';
          let malformed=false;
          try{E.dateDays('2026-02-30','2026-03-01');}catch{malformed=true;}
          return {savings:E.compound(1000,50,0,10),days:E.dateDays('2026-03-01','2026-03-31'),
                  unicode:E.encode(E.encode(input,'base64_encode'),'base64_decode'),malformed};
        }""")
        assert result == {"savings": {"balance": 7000, "contributions": 7000, "interest": 0},
                          "days": 30, "unicode": "Hola 🐈 café", "malformed": True}
        for route in ("finance", "date_math", "encoding", "file_inspector", "image_lab"):
            page.evaluate("route=>{location.hash=route}", route)
            page.locator(".tool-workbench").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        assert page.evaluate("Michiflin.transport.tasks.length") == 0
        assert errors == []


def test_file_fingerprint_and_image_optimizer_do_not_upload_inputs(tmp_path):
    import hashlib

    from PIL import Image

    text = tmp_path / "example.txt"
    text.write_bytes(b"hello")
    image = tmp_path / "example.png"
    Image.new("RGB", (100, 80), "#ada2de").save(image)
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("location.hash='file_inspector'")
        page.locator("input[type=file]").set_input_files(text)
        page.get_by_role("button", name="Compute fingerprint").click()
        page.get_by_text(hashlib.sha256(b"hello").hexdigest(), exact=False).wait_for()
        page.evaluate("location.hash='image_lab'")
        page.locator("input[type=file]").set_input_files(image)
        page.locator("input[type=number]").first.fill("64")
        page.get_by_role("button", name="Optimize on this device").click()
        page.locator(".tool-output img").wait_for()
        assert "64 × 51" in page.locator(".tool-output").inner_text()
        assert page.get_by_role("link", name="Save JPEG").get_attribute("download") == "michiflin-image.jpg"
        assert page.evaluate("Michiflin.transport.tasks.length") == 0
        assert errors == []


def test_cached_weather_marks_stale_data_and_pauses_condition_animation(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>Michiflin.storage.write('weather',{
          effect:'rain',season:'winter',as_of:Date.now()/1000-7200,stale:false,
          current:{temperature_2m:8},place:{name:'Synthetic cached city'},timezone:'UTC'
        })""")
        page.reload()
        widget = page.locator(".weather-widget")
        assert widget.get_attribute("data-season") == "winter"
        assert widget.get_attribute("data-stale") == "true"
        assert "Meteorological winter" in widget.inner_text()
        assert page.locator(".weather-stale").is_visible()
        assert page.locator(".rain-drop").first.evaluate("node=>getComputedStyle(node).animationPlayState") == "paused"
        widget.focus()
        page.keyboard.press("Enter")
        assert page.locator("#weather-results").is_visible()
        assert errors == []


def test_upstream_book_failure_offers_an_explicit_catalogue_not_fake_downloads(tmp_path, monkeypatch):
    from michiflin.errors import AppError

    original = SourceFixture.get

    def unavailable(self, url, params=None):
        if "gutendex" in url:
            raise AppError("source_unavailable")
        if "openlibrary" in url:
            return {"numFound": 1, "docs": [{"key": "/works/OL123W", "title": "Synthetic catalogue book", "author_name": ["Writer"], "ebook_access": "no_ebook"}]}
        return original(self, url, params)

    monkeypatch.setattr(SourceFixture, "get", unavailable)
    with browser(tmp_path) as (page, errors):
        page.evaluate("location.hash='library'")
        page.locator(".library-filters button").click()
        page.get_by_role("button", name="Search the Open Library catalogue instead").wait_for()
        assert not page.evaluate("window.__requests.some(item=>item.args?.source==='openlibrary')")
        page.get_by_role("button", name="Search the Open Library catalogue instead").click()
        page.get_by_role("heading", name="Synthetic catalogue book").wait_for()
        assert page.locator(".book-links a").count() == 0
        assert page.get_by_role("button", name="Receive in chat").count() == 0
        assert errors == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("locale", ["en", "es"])
def test_compact_catalogue_filters_search_and_keeps_tools_accessible(tmp_path, engine, locale):
    with browser(tmp_path, locale, engine, connected=False) as (page, errors):
        for width in [320, 390, 768, 1440]:
            page.set_viewport_size({"width": width, "height": 844})
            page.evaluate("location.hash='utilities'")
            page.locator(".utility-toolbar").wait_for()
            assert page.locator(".tool-card").count() == 17
            assert page.locator(".tool-card p").count() == 0
            assert page.locator(".tool-card").first.bounding_box()["height"] <= 105
            page.locator('[data-group="files"]').click()
            assert page.locator(".tool-card").count() == 5
            assert page.locator('[data-group="files"]').get_attribute("aria-pressed") == "true"
            page.locator('.utility-search input').fill("CSV")
            assert page.locator(".tool-card").count() == 1
            page.locator('.utility-search input').fill("")
            page.locator('[data-group="all"]').click()
            assert page.locator(".tool-card").count() == 17
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.evaluate("location.hash='home'")
            page.locator(".feature-hero").wait_for()
            assert page.locator(".feature-hero").bounding_box()["height"] <= 260
        page.evaluate("location.hash='assistant'")
        page.locator(".usage-details summary").click()
        assert page.locator(".usage-details .caption").first.is_visible()
        assert errors == []

from __future__ import annotations

import base64
import io
import json
import math
import struct
import wave
import zipfile
from pathlib import Path

import pytest
from test_miniapp import browser


def encoded(body: bytes) -> str:
    return base64.b64encode(body).decode()


def archive(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    return stream.getvalue()


def audio(*, channels=1, seconds=1, bits=16) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(bits // 8)
        wav.setframerate(8000)
        if bits == 16:
            samples = [round(12000 * math.sin(index * 0.1)) for index in range(8000 * seconds)]
            wav.writeframes(b"".join(struct.pack("<h", value) * channels for value in samples))
        else:
            wav.writeframes(b"\x80" * (8000 * seconds * channels))
    return stream.getvalue()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_csv_roundtrips_quoted_unicode_and_protects_spreadsheet_exports(tmp_path, engine):
    with browser(tmp_path, connected=False, engine=engine) as (page, errors):
        result = page.evaluate("""()=>{
          const F=Michiflin.filetools;
          const rows=[['title','note'],['🐈 café','a,b'],['line','one\\ntwo'],['quote','a"b'],['','']];
          const protectedText=F.writeCsv([['=2+3',' +cmd','@x','\\tformula','safe','-1']],',',true);
          return {rows,roundtrip:F.parseCsv(F.writeCsv(rows,',',false)),
                  protected:F.parseCsv(protectedText),unprotected:F.writeCsv([['=2+3']],',',false),
                  empty:F.parseCsv(''),emptyCell:F.parseCsv(F.writeCsv([['']])),
                  bom:F.parseCsv('\\uFEFFa;b\\r\\n1;2\\r\\n',';'),
                  tab:F.parseCsv('a\\tb\\nc\\td','\\t')};
        }""")
        assert result["rows"] == result["roundtrip"]
        assert result["protected"] == [["'=2+3", "' +cmd", "'@x", "'\tformula", "safe", "'-1"]]
        assert result["unprotected"] == "=2+3"
        assert result["empty"] == [] and result["emptyCell"] == [[""]]
        assert result["bom"] == [["a", "b"], ["1", "2"]]
        assert result["tab"] == [["a", "b"], ["c", "d"]]
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert errors == []


def test_csv_rejects_malformed_and_oversized_tables(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        failures = page.evaluate("""()=>{
          const F=Michiflin.filetools;
          return [()=>F.parseCsv('a,"unterminated'),()=>F.parseCsv('a,b"c'),()=>F.parseCsv('"a"tail'),
            ()=>F.parseCsv('x'.repeat(10001)),()=>F.parseCsv(Array(2001).fill('x').join('\\n')),
            ()=>F.parseCsv(Array(101).fill('x').join(',')),()=>F.parseCsv('x'.repeat(1000001)),
            ()=>F.writeCsv([{key:'not a row'}]),()=>F.writeCsv([[{}]]),()=>F.writeCsv([[Infinity]]),
            ()=>F.writeCsv([[]]),()=>F.parseCsv('a|b','|')].map(run=>{try{run();return false;}catch{return true;}});
        }""")
        assert all(failures)
        assert errors == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_zip_inventory_flags_unsafe_paths_symlinks_and_expansion_without_extraction(tmp_path, engine):
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = 0o120777 << 16
    payload = archive([("readme.txt", b"hello"), ("../outside.txt", b"no"), (symlink, b"/etc/passwd"),
                       ("READme.txt", b"duplicate"), ("compressed.bin", b"0" * 100000)])
    with browser(tmp_path, connected=False, engine=engine) as (page, errors):
        result = page.evaluate("""data=>Michiflin.filetools.inspectZip(Uint8Array.from(atob(data),v=>v.charCodeAt(0)).buffer)""", encoded(payload))
        assert result["metadata_only"] is True
        assert len(result["entries"]) == 5
        flags = [entry["warnings"] for entry in result["entries"]]
        assert "zip_unsafe_path" in flags[1] and "zip_symlink" in flags[2]
        assert "zip_duplicate" in flags[3] and "zip_expansion" in flags[4]
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert errors == []


def test_zip_rejects_inconsistent_headers_truncation_zip64_and_large_inventories(tmp_path):
    good = archive([("readme.txt", b"hello")])
    malformed = []
    central = good.index(b"PK\x01\x02")
    bad = bytearray(good)
    bad[central + 46] ^= 1
    malformed.append(bytes(bad))
    bad = bytearray(good)
    struct.pack_into("<I", bad, central + 42, central)
    malformed.append(bytes(bad))
    bad = bytearray(good)
    struct.pack_into("<I", bad, len(bad) - 6, 0xFFFFFFFF)
    malformed.append(bytes(bad))
    bad = bytearray(good)
    struct.pack_into("<HH", bad, len(bad) - 14, 1001, 1001)
    malformed.append(bytes(bad))
    bad = bytearray(good)
    struct.pack_into("<I", bad, 18, 123456)
    malformed.append(bytes(bad))
    malformed.extend([good[:-1], good + b"unexpected trailing content", b"not a zip"])
    with browser(tmp_path, connected=False) as (page, errors):
        result = page.evaluate("""items=>items.map(data=>{
          try{Michiflin.filetools.inspectZip(Uint8Array.from(atob(data),v=>v.charCodeAt(0)).buffer);return false;}
          catch{return true;}
        })""", [encoded(value) for value in malformed])
        assert all(result)
        assert errors == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_pcm_audio_trim_has_real_valid_samples_normalization_and_fade(tmp_path, engine):
    payload = audio(channels=2)
    with browser(tmp_path, connected=False, engine=engine) as (page, errors):
        result = page.evaluate("""data=>{
          const F=Michiflin.filetools,source=Uint8Array.from(atob(data),v=>v.charCodeAt(0)).buffer;
          const clipped=F.trimWav(source,.25,.75,true,10),info=F.readWav(clipped),samples=new DataView(clipped);
          let peak=0;for(let offset=44;offset<clipped.byteLength;offset+=2)peak=Math.max(peak,Math.abs(samples.getInt16(offset,true)));
          return {source:F.readWav(source).duration,duration:info.duration,channels:info.channels,frames:info.frames,
                  bytes:clipped.byteLength,peak,first:samples.getInt16(44,true),last:samples.getInt16(clipped.byteLength-2,true)};
        }""", encoded(payload))
        assert result == {"source": 1, "duration": 0.5, "channels": 2, "frames": 4000,
                          "bytes": 16044, "peak": 31128, "first": 0, "last": 0}
        assert errors == []


def test_wav_rejects_unsupported_formats_ranges_and_structural_corruption(tmp_path):
    good = audio()
    broken = bytearray(good)
    struct.pack_into("<I", broken, 28, 1)
    with browser(tmp_path, connected=False) as (page, errors):
        result = page.evaluate("""items=>{
          const F=Michiflin.filetools,decode=data=>Uint8Array.from(atob(data),v=>v.charCodeAt(0)).buffer;
          const malformed=items.bad.map(value=>()=>F.readWav(decode(value)));
          const valid=decode(items.good);
          return [...malformed,...[[-1,1],[0,2],[.5,.5],[0,1,false,-1],[0,1,false,1001],[NaN,1]].map(args=>()=>F.trimWav(valid,...args))]
            .map(run=>{try{run();return false;}catch{return true;}});
        }""", {"good": encoded(good), "bad": [encoded(bytes(broken)), encoded(good[:-1]), encoded(audio(bits=8)), encoded(audio(seconds=121))]})
        assert all(result)
        assert errors == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_file_workbenches_keep_data_local_and_export_real_files(tmp_path, engine):
    wav = tmp_path / "sample.wav"
    wav.write_bytes(audio())
    zipped = tmp_path / "sample.zip"
    zipped.write_bytes(archive([("<img src=x onerror=alert(1)>.txt", b"safe text")]))
    with browser(tmp_path, connected=False, engine=engine) as (page, errors):
        page.evaluate("location.hash='csv_lab'")
        page.locator("textarea").fill('name,note\ncat,"Hola, 🐈"')
        page.get_by_role("button", name="CSV to JSON", exact=True).click()
        assert json.loads(page.locator(".tool-output pre").inner_text()) == [["name", "note"], ["cat", "Hola, 🐈"]]
        assert page.get_by_role("link", name="Save file").get_attribute("download") == "michiflin-table.json"
        page.evaluate("location.hash='zip_inspector'")
        page.locator("input[type=file]").set_input_files(zipped)
        page.get_by_role("button", name="Inspect archive", exact=True).click()
        page.locator(".tool-output pre").wait_for()
        assert "<img src=x" in page.locator(".tool-output").inner_text()
        assert page.locator(".tool-output img").count() == 0
        page.evaluate("location.hash='audio_lab'")
        page.locator("input[type=file]").set_input_files(wav)
        page.get_by_text("1.000 s · 8000 Hz · 1 channels", exact=True).wait_for()
        page.get_by_label("Start (seconds)").fill("0.25")
        page.get_by_label("End (seconds)").fill("0.75")
        page.get_by_role("button", name="Create audio clip", exact=True).click()
        page.locator(".tool-output audio").wait_for()
        page.get_by_text("0.500 s · 8044 B", exact=True).wait_for()
        with page.expect_download() as transfer:
            page.get_by_role("link", name="Save file", exact=True).click()
        exported = Path(transfer.value.path()).read_bytes()
        with wave.open(io.BytesIO(exported), "rb") as decoded:
            assert decoded.getnframes() == 4000 and decoded.getnchannels() == 1
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert not page.evaluate("Object.keys(localStorage).some(key=>/csv|audio|zip/.test(key))")
        assert errors == []


def test_native_file_share_is_explicit_a_draft_and_never_uses_bot_status_updates(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>{
          window.__drafts=[];
          window.webxdc={sendToChat:async message=>{
            window.__drafts.push({name:message.file.name,bytes:message.file.blob.size,text:await message.file.blob.text()});
          }};
          location.hash='csv_lab';
        }""")
        page.locator("textarea").fill('[["=1+2","🐈"]]')
        page.get_by_role("button", name="JSON rows to CSV", exact=True).click()
        assert page.evaluate("window.__drafts.length") == 0
        assert page.get_by_role("link", name="Save file").count() == 0
        page.get_by_role("button", name="Prepare a chat draft", exact=True).click()
        page.wait_for_function("()=>window.__drafts.length===1")
        draft = page.evaluate("window.__drafts[0]")
        assert draft["name"] == "michiflin-table.csv" and "'=1+2" in draft["text"] and "🐈" in draft["text"]
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert "sent" not in page.locator("#notifications").inner_text().lower()
        assert errors == []


def test_native_draft_failures_and_pending_double_clicks_do_not_report_delivery(tmp_path):
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>{
          window.__draftCalls=0;
          window.webxdc={sendToChat:()=>{window.__draftCalls++;return new Promise((_,reject)=>window.__failDraft=reject);}};
          location.hash='csv_lab';
        }""")
        page.locator("textarea").fill("a,b")
        page.get_by_role("button", name="CSV to JSON", exact=True).click()
        share = page.get_by_role("button", name="Prepare a chat draft", exact=True)
        share.click()
        assert share.is_disabled()
        assert page.evaluate("window.__draftCalls") == 1
        page.evaluate("window.__failDraft(new Error('synthetic'))")
        page.get_by_text("The draft could not be prepared. Nothing is confirmed sent.", exact=True).wait_for()
        assert share.is_enabled()
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert errors == []


@pytest.mark.parametrize("locale", ["en", "es"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_new_file_tools_are_bilingual_responsive_and_do_not_store_source_files(tmp_path, locale, engine):
    with browser(tmp_path, locale=locale, connected=False, engine=engine) as (page, errors):
        for route in ["csv_lab", "zip_inspector", "audio_lab"]:
            page.evaluate("value=>location.hash=value", route)
            page.locator(".tool-workbench").wait_for()
            for width in [320, 390, 1024]:
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), (route, width)
            assert page.locator(".privacy-note").is_visible()
        assert not page.evaluate("Michiflin.transport.tasks.length")
        assert errors == []


def test_pending_file_reads_cannot_replace_a_new_selection_or_a_different_view(tmp_path):
    zipped = tmp_path / "sample.zip"
    zipped.write_bytes(archive([("first.txt", b"hello")]))
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>{
          window.__readBuffer=Blob.prototype.arrayBuffer;
          Blob.prototype.arrayBuffer=function(){return new Promise(resolve=>{
            window.__releaseRead=()=>window.__readBuffer.call(this).then(resolve);
          });};
          location.hash='zip_inspector';
        }""")
        page.locator("input[type=file]").set_input_files(zipped)
        page.get_by_role("button", name="Inspect archive", exact=True).click()
        assert page.get_by_role("button", name="Inspect archive", exact=True).is_disabled()
        page.locator("input[type=file]").set_input_files([])
        page.evaluate("window.__releaseRead()")
        page.wait_for_function("()=>document.querySelector('.tool-workbench').getAttribute('aria-busy')==='false'")
        assert page.locator(".tool-output").is_hidden()
        page.locator("input[type=file]").set_input_files(zipped)
        page.get_by_role("button", name="Inspect archive", exact=True).click()
        page.evaluate("location.hash='csv_lab'")
        page.locator("textarea").wait_for()
        page.evaluate("window.__releaseRead()")
        assert page.locator(".tool-output").is_hidden()
        assert page.locator("#notifications").inner_text() == ""
        assert errors == []


def test_replaced_exports_revoke_blob_urls_and_clear_audio_previews(tmp_path):
    wav = tmp_path / "sample.wav"
    wav.write_bytes(audio())
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>{
          window.__revoked=[];const revoke=URL.revokeObjectURL.bind(URL);
          URL.revokeObjectURL=value=>{window.__revoked.push(value);revoke(value);};
          location.hash='csv_lab';
        }""")
        page.locator("textarea").fill("first,result")
        page.get_by_role("button", name="CSV to JSON", exact=True).click()
        original = page.get_by_role("link", name="Save file", exact=True).get_attribute("href")
        page.get_by_role("button", name="CSV to JSON", exact=True).click()
        assert original in page.evaluate("window.__revoked")
        page.evaluate("location.hash='audio_lab'")
        page.locator("input[type=file]").set_input_files(wav)
        page.get_by_text("1.000 s · 8000 Hz · 1 channels", exact=True).wait_for()
        page.get_by_role("button", name="Create audio clip", exact=True).click()
        page.locator("audio").wait_for()
        preview = page.locator("audio").get_attribute("src")
        page.get_by_label("Start (seconds)").fill("0.25")
        page.get_by_label("End (seconds)").focus()
        assert page.locator("audio").count() == 0
        assert preview in page.evaluate("window.__revoked")
        assert errors == []


def test_image_export_uses_native_draft_api_instead_of_a_download_link(tmp_path):
    from PIL import Image

    image = tmp_path / "sample.png"
    Image.new("RGB", (80, 64), "#afa6da").save(image)
    with browser(tmp_path, connected=False) as (page, errors):
        page.evaluate("""()=>{
          window.__drafts=[];window.webxdc={sendToChat:async message=>window.__drafts.push({name:message.file.name,type:message.file.blob.type,size:message.file.blob.size})};
          location.hash='image_lab';
        }""")
        page.locator("input[type=file]").set_input_files(image)
        page.get_by_role("button", name="Optimize on this device", exact=True).click()
        page.locator(".tool-output img").wait_for()
        assert page.locator(".tool-output a").count() == 0
        assert page.evaluate("window.__drafts.length") == 0
        page.get_by_role("button", name="Prepare a chat draft", exact=True).click()
        assert page.evaluate("window.__drafts[0].name") == "michiflin-image.jpg"
        assert page.evaluate("window.__drafts[0].type") == "image/jpeg"
        assert errors == []


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_archive_action_waits_for_selection_event_and_clears_when_selection_is_removed(tmp_path, engine):
    zipped = tmp_path / 'synthetic.zip'
    zipped.write_bytes(archive([('file.txt', b'synthetic')]))
    with browser(tmp_path, connected=False, engine=engine) as (page, errors):
        page.evaluate("location.hash='zip_inspector'")
        control = page.get_by_role('button', name='Inspect archive', exact=True)
        assert control.is_disabled()
        page.locator('input[type=file]').evaluate('''file=>{
          const changed=file.onchange;
          file.onchange=event=>{window.__finishSelection=()=>changed(event);};
        }''')
        page.locator('input[type=file]').set_input_files(zipped)
        assert control.is_disabled()
        assert page.locator('.tool-output pre').count() == 0
        page.evaluate('window.__finishSelection()')
        control.click()
        page.locator('.tool-output pre').wait_for()
        page.locator('input[type=file]').set_input_files([])
        page.evaluate('window.__finishSelection()')
        assert control.is_disabled()
        assert page.locator('.tool-output pre').count() == 0
        assert page.evaluate('Michiflin.transport.tasks.length') == 0
        assert errors == []

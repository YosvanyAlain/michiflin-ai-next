from __future__ import annotations

from unittest.mock import Mock

import pytest

from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.identity import Principal
from michiflin.public_http import Document
from michiflin.reader import MAX_TEXT, PAGE_CHARS, Reader
from michiflin.service import Service


def reader(markup, content_type="text/html; charset=utf-8"):
    body = markup.encode() if isinstance(markup, str) else markup
    return Reader(Mock(get=Mock(return_value=Document("https://example.com/story", content_type, body))))


def test_reader_extracts_article_text_without_scripts_forms_navigation_or_images():
    value = reader('''<!doctype html><html><head><title>A small story</title><script>never run</script></head>
      <body><nav>navigation clutter</nav><main><h1>A small story</h1><p>A real paragraph with <b>bold words</b>
      and enough text to be useful in a lightweight mail reader. Cats like quiet corners.</p>
      <script>alert('must not appear')</script><img src="https://tracker.example/pixel">
      <p hidden>private hidden block</p><div aria-hidden="true">aria hidden block</div>
      <section style="display: none">css hidden block</section><form>form tokens</form>
      <p>Another paragraph with a <a href="/next">next chapter</a>.</p></main><footer>footer clutter</footer></body></html>''').read({"url": "https://example.com/story"})
    assert value["title"] == "A small story"
    text = "\n".join(value["pages"])
    assert "bold words" in text and "Another paragraph" in text
    assert not any(word in text for word in ["clutter", "alert", "tracker", "tokens", "hidden block"])
    assert value["links"] == [{"title": "next chapter", "url": "https://example.com/next"}]
    assert value["kind"] == "plain_text_reader" and not value["truncated"]


def test_reader_falls_back_to_body_and_cleans_control_characters():
    value = reader('<p>A page with no main element but enough public text to make a useful reader copy.\u202e</p>').read({})
    assert "no main element" in value["pages"][0] and "\u202e" not in value["pages"][0]


def test_reader_never_returns_executable_link_schemes_credentials_or_literal_private_targets():
    markup = '<main><p>' + "A public page. " * 10 + '</p>'
    for url in ['javascript:alert(1)', 'data:text/html,unsafe', 'http://example.com/', 'https://127.0.0.1/',
                'https://user:password@example.com/', '#top', '/next']:
        markup += f'<a href="{url}">link</a>'
    value = reader(markup + '</main>').read({})
    assert value["links"] == [{"title": "link", "url": "https://example.com/next"}]


def test_reader_limits_output_and_paginates_without_additional_network_calls():
    tool = reader('<main><p>' + ('Readable words and a sentence. ' * 3000) + '</p></main>')
    value = tool.read({})
    assert value["truncated"] and len(value["pages"]) <= 12
    assert all(0 < len(page) <= PAGE_CHARS for page in value["pages"])
    assert value["characters"] <= MAX_TEXT
    assert tool.client.get.call_count == 1


def test_plain_text_charset_and_unsupported_encoding():
    value = reader(('Un texto público en español. ' * 10).encode('latin-1'), 'text/plain; charset=iso-8859-1').read({})
    assert 'público' in value["pages"][0] and value["links"] == []
    with pytest.raises(AppError, match="web_format_unsupported"):
        reader(b"any", "text/html; charset=utf-16").read({})


@pytest.mark.parametrize("markup,error", [
    ("<script>only javascript</script>", "web_no_readable_text"),
    ("<div>" * 300 + "text", "web_response_limit"),
    ("<br>" * 20001, "web_response_limit"),
])
def test_empty_dynamic_or_excessively_complex_pages_fail_honestly(markup, error):
    with pytest.raises(AppError, match=error):
        reader(markup).read({})


def test_links_are_deduplicated_and_bounded():
    markup = "<main><p>" + "A useful public sentence. " * 5 + "</p>"
    markup += ''.join(f'<a href="/{index}">Link {index}</a>' * 2 for index in range(40)) + "</main>"
    value = reader(markup).read({})
    assert len(value["links"]) == 20
    assert len({link["url"] for link in value["links"]}) == 20


def test_reader_is_a_public_non_ai_operation_with_normal_identity_and_replay_controls(tmp_path):
    service = Service(Settings(state_dir=tmp_path))
    principal = Principal("reader@example.invalid", "A" * 40, 20, True, True)
    service.register_instance(principal, 42)
    service.reader = reader("<p>" + "A public page without invoking a model. " * 3 + "</p>")
    service.models.complete_bounded = Mock(side_effect=AssertionError("must not use AI"))
    request = {"id": "reader-test-request-0001", "op": "web.read", "args": {"url": "https://example.com/"},
               "session": service.session, "locale": "en"}
    first = service.handle(principal, 42, request)
    assert first["ok"] and first["data"]["kind"] == "plain_text_reader"
    assert service.handle(principal, 42, request) == first
    assert service.reader.client.get.call_count == 1
    other = Principal("other@example.invalid", "B" * 40, 21, True, True)
    assert not service.handle(other, 42, request)["ok"]


def test_boolean_html_attributes_and_self_closing_elements_do_not_crash_reader():
    value = reader('<main style aria-hidden><p>' + 'Readable public text. ' * 10 + '<br/>Another line.</p></main>').read({})
    assert 'Another line.' in value['pages'][0]

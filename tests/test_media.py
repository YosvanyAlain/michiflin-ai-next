from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.identity import Principal
from michiflin.media import Media, youtube_url
from michiflin.public_http import Document
from michiflin.service import Service

VIDEO = 'https://www.youtube.com/watch?v=BaW_jenozKc'
ENDPOINT = 'https://www.youtube.com/oembed?url=' + VIDEO + '&format=json'


@pytest.mark.parametrize('url', [
    VIDEO, VIDEO + '&t=10&list=ignored', 'https://youtu.be/BaW_jenozKc?t=3',
    'https://www.youtube.com/shorts/BaW_jenozKc/', 'https://m.youtube.com/watch?v=BaW_jenozKc',
    'https://music.youtube.com/watch?v=BaW_jenozKc', 'https://youtube.com/live/BaW_jenozKc',
    'https://www.youtube-nocookie.com/embed/BaW_jenozKc',
])
def test_only_canonical_video_identity_is_sent_to_metadata_source(url):
    assert youtube_url(url) == VIDEO


@pytest.mark.parametrize('url', [
    'https://youtube.com/playlist?list=test', 'https://youtube.com/@channel',
    'https://youtube.com/watch?v=short', VIDEO + '&v=BaW_jenozKc',
    'https://youtube.com.evil.example/watch?v=BaW_jenozKc', 'https://127.0.0.1/watch?v=BaW_jenozKc',
    'http://youtube.com/watch?v=BaW_jenozKc', 'https://user:secret@youtube.com/watch?v=BaW_jenozKc',
    'https://youtube.com:8080/watch?v=BaW_jenozKc', 'https://youtu.be/BaW_jenozKc/extra',
    'https://www.youtube-nocookie.com/shorts/BaW_jenozKc', 'file:///tmp/private', None, 123,
])
def test_non_video_or_untrusted_input_is_rejected_before_network(url):
    client = Mock()
    with pytest.raises(AppError, match='invalid_youtube_url'):
        Media(client).inspect({'url': url})
    client.get.assert_not_called()


def fixture_client(data=None, final=ENDPOINT):
    data = data if data is not None else {
        'type': 'video', 'provider_name': 'YouTube', 'title': '<img src=x onerror=alert(1)>',
        'author_name': 'Test channel', 'html': '<iframe src="https://tracker.example/"></iframe>',
        'thumbnail_url': 'https://tracker.example/pixel', 'author_url': 'javascript:alert(1)',
    }
    body = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return Mock(get=Mock(return_value=Document(final, 'application/json', body)))


def test_preview_returns_bounded_plain_metadata_not_embed_html_or_false_downloads():
    client = fixture_client()
    value = Media(client).inspect({'url': VIDEO})
    assert value['title'] == '<img src=x onerror=alert(1)>' and value['author'] == 'Test channel'
    assert value['url'] == VIDEO and value['download_available'] is False
    assert 'tracker.example' not in str(value) and 'javascript:' not in str(value)
    assert client.get.call_args.kwargs == {'json_response': True}
    assert client.get.call_args.args[0].startswith('https://www.youtube.com/oembed?')
    assert 'iframe' not in value and 'thumbnail_url' not in value


@pytest.mark.parametrize('data', [b'invalid json', [], {}, {'type': 'photo', 'provider_name': 'YouTube'},
    {'type': 'video', 'provider_name': 'Unknown'},
    {'type': 'video', 'provider_name': 'YouTube', 'title': '', 'author_name': 'channel'},
    {'type': 'video', 'provider_name': 'YouTube', 'title': 'title', 'author_name': None}])
def test_invalid_metadata_is_not_treated_as_a_success(data):
    with pytest.raises(AppError, match='media_metadata_invalid'):
        Media(fixture_client(data)).inspect({'url': VIDEO})


def test_metadata_response_must_still_come_from_youtube_oembed():
    with pytest.raises(AppError, match='media_metadata_invalid'):
        Media(fixture_client(final='https://untrusted.example/')).inspect({'url': VIDEO})


def test_blocked_or_private_video_is_an_honest_failure():
    client = Mock(get=Mock(side_effect=AppError('web_access_restricted')))
    with pytest.raises(AppError, match='media_source_unavailable'):
        Media(client).inspect({'url': VIDEO})


def test_public_media_operation_never_uses_a_model_and_is_replay_safe(tmp_path):
    service = Service(Settings(state_dir=tmp_path))
    member = Principal('member@example.invalid', 'A' * 40, 20, True, True)
    service.register_instance(member, 42)
    service.media = Media(fixture_client())
    service.models.complete_bounded = Mock(side_effect=AssertionError('must not use AI'))
    request = {'id': 'media-preview-request-0001', 'op': 'media.inspect', 'args': {'url': VIDEO}, 'session': service.session}
    first = service.handle(member, 42, request)
    assert first['ok'] and first['data']['kind'] == 'youtube_metadata'
    assert service.handle(member, 42, request) == first
    assert service.media.client.get.call_count == 1

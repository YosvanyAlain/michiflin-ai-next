from __future__ import annotations

import gzip
import os
import time
from unittest.mock import Mock

import pytest

from michiflin import agent, downloads, gutenberg, library
from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.gutenberg import FEED, MIRROR, GutenbergClient, mirror_url, parse_metadata
from michiflin.identity import Principal
from michiflin.service import Service

HEADER = 'Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n'
CSV = HEADER + '1342,Text,1998-01-01,Pride and Prejudice,en,Jane Austen,Fiction,PR,Classics\n' + '2000,Text,2004-01-01,Don Quijote,es,Miguel de Cervantes,Fiction,PQ,Classics\n'


def rdf(identifier=1342, rights='Public domain in the USA.'):
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/" xmlns:dcterms="http://purl.org/dc/terms/">
    <pgterms:ebook rdf:about="ebooks/{identifier}"><dcterms:title>Pride and Prejudice</dcterms:title><dcterms:rights>{rights}</dcterms:rights>
    <dcterms:creator><pgterms:agent><pgterms:name>Jane Austen</pgterms:name></pgterms:agent></dcterms:creator>
    <dcterms:language><rdf:Description><rdf:value>en</rdf:value></rdf:Description></dcterms:language>
    <dcterms:hasFormat><pgterms:file rdf:about="https://www.gutenberg.org/ebooks/{identifier}.epub.images"><dcterms:extent>20000000</dcterms:extent><dcterms:format><rdf:Description><rdf:value>application/epub+zip</rdf:value></rdf:Description></dcterms:format></pgterms:file></dcterms:hasFormat>
    <dcterms:hasFormat><pgterms:file rdf:about="https://www.gutenberg.org/ebooks/{identifier}.epub.noimages"><dcterms:extent>600000</dcterms:extent><dcterms:format><rdf:Description><rdf:value>application/epub+zip</rdf:value></rdf:Description></dcterms:format></pgterms:file></dcterms:hasFormat>
    </pgterms:ebook></rdf:RDF>'''.encode()


@pytest.mark.parametrize('path,mapped', [
    ('/ebooks/1342.epub3.images', '/cache/epub/1342/pg1342-images-3.epub'),
    ('/ebooks/1342.epub.noimages', '/cache/epub/1342/pg1342.epub'),
    ('/ebooks/1342.txt.utf-8', '/cache/epub/1342/pg1342.txt'),
    ('/files/1342/1342-0.txt', '/1/3/4/1342/1342-0.txt'),
    ('/files/1/1.txt', '/0/1/1.txt'),
    ('/cache/epub/1342/pg1342.epub', '/cache/epub/1342/pg1342.epub'),
])
def test_known_file_routes_map_to_one_reviewed_mirror(path, mapped):
    assert mirror_url('https://www.gutenberg.org' + path) == MIRROR + mapped
    assert mirror_url(MIRROR + mapped) == MIRROR + mapped


@pytest.mark.parametrize('url', [
    'https://evil.example/ebooks/1342.epub.noimages', 'http://www.gutenberg.org/ebooks/1342.epub.noimages',
    'https://www.gutenberg.org:8443/ebooks/1342.epub.noimages', 'https://user:password@www.gutenberg.org/ebooks/1342.epub.noimages',
    'https://www.gutenberg.org/ebooks/1342.epub.noimages?q=secret',
    MIRROR + '/cache/epub/1342/../../private', MIRROR + '/cache/epub/1342/pg1342%2fsecret',
    MIRROR + '/cache/epub/1342/pg13420.epub', MIRROR + '/1/3/5/1342/1342.txt',
    'https://www.gutenberg.org/files/1342/../private', None,
])
def test_arbitrary_sources_routes_credentials_and_path_traversal_are_refused(url):
    with pytest.raises(AppError, match='source_not_allowed'):
        mirror_url(url)


def test_rdf_rights_identifier_formats_and_smallest_epub_are_verified():
    value = parse_metadata(rdf(), 1342)
    assert value['id'] == 1342 and value['copyright'] is False
    assert value['formats']['application/epub+zip'] == MIRROR + '/cache/epub/1342/pg1342.epub'
    assert parse_metadata(rdf(rights='Copyrighted. Read permission granted.'), 1342)['copyright'] is True
    with pytest.raises(AppError, match='source_response_invalid'):
        parse_metadata(rdf(2000), 1342)


@pytest.mark.parametrize('body', [b'<bad', b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>',
    b'<!DOCTYPE x [<!ENTITY a "expand">]><x>&a;</x>', b'x' * 512001])
def test_rdf_entity_expansion_external_entities_malformed_and_large_payloads_fail(body):
    with pytest.raises(AppError):
        parse_metadata(body, 1342)


@pytest.fixture
def source(tmp_path, monkeypatch):
    fetch = Mock(return_value=gzip.compress(CSV.encode()))
    monkeypatch.setattr(gutenberg, 'fetch', fetch)
    client = GutenbergClient(tmp_path / 'catalog')
    metadata = Mock(side_effect=lambda identifier: {**parse_metadata(rdf(identifier), identifier),
                         'title': 'Don Quijote' if identifier == 2000 else 'Pride and Prejudice',
                         'authors': [{'name': 'Miguel de Cervantes' if identifier == 2000 else 'Jane Austen'}],
                         'languages': ['es' if identifier == 2000 else 'en']})
    monkeypatch.setattr(client, 'metadata', metadata)
    return client, fetch, metadata


def test_catalogue_uses_compressed_feed_once_and_filters_title_author_language(source):
    client, fetch, _ = source
    books = library.search(client, library.BookQuery.parse({'title': 'Pride', 'author': 'Austen', 'format': 'epub'}))
    assert len(books['books']) == 1 and books['books'][0]['access'] == 'public_domain_us'
    assert books['books'][0]['downloads'][0]['url'] == MIRROR + '/cache/epub/1342/pg1342.epub'
    result = library.search(client, library.BookQuery.parse({'title': 'Quijote', 'language': 'es'}))
    assert result['books'][0]['id'] == 'gutenberg:2000'
    assert fetch.call_count == 1 and fetch.call_args.args[0] == FEED
    assert client.database.stat().st_mode & 0o077 == 0


def test_csv_is_not_treated_as_proof_of_rights_or_format(source):
    client, _, metadata = source
    metadata.side_effect = lambda identifier: parse_metadata(rdf(identifier, rights='Copyrighted.'), identifier)
    result = library.search(client, library.BookQuery.parse({'title': 'Pride', 'format': 'epub'}))
    assert result['books'] == []


def test_search_input_never_becomes_sql_and_no_match_is_not_a_network_error(source):
    client, _, metadata = source
    result = library.search(client, library.BookQuery.parse({'title': "' OR 1=1 --"}))
    assert result['books'] == [] and metadata.call_count == 0


def test_catalogue_refresh_is_atomic_and_old_copy_is_explicitly_stale(source):
    client, fetch, _ = source
    client.sync()
    original = client.database.read_bytes()
    old = time.time() - 8 * 86400
    os.utime(client.database, (old, old))
    fetch.return_value = gzip.compress(b'not the expected csv')
    result = client.get('https://gutendex.com/books/', {'title': 'Pride'})
    assert result['catalog_stale'] and result['results']
    assert client.database.read_bytes() == original
    assert not list(client.directory.glob('catalog-*.sqlite3'))


def test_partial_metadata_failures_are_disclosed_and_total_failure_is_an_error(source):
    client, _, metadata = source
    metadata.side_effect = lambda identifier: parse_metadata(rdf(), 1342) if identifier == 1342 else None
    result = client.get('https://gutendex.com/books/', {})
    assert result['partial_results']
    metadata.side_effect = AppError('source_unavailable')
    with pytest.raises(AppError, match='source_unavailable'):
        client.get('https://gutendex.com/books/', {})


@pytest.mark.parametrize('body', [gzip.compress(b''), b'not gzip', gzip.compress((HEADER + 'bad,row\n').encode()),
    gzip.compress((HEADER + '1,Text,date,title,en,author,topic,PR,shelf,extra\n').encode())])
def test_bad_or_empty_feeds_never_replace_catalogue(tmp_path, monkeypatch, body):
    monkeypatch.setattr(gutenberg, 'fetch', lambda *_a, **_k: body)
    client = GutenbergClient(tmp_path)
    with pytest.raises(AppError):
        client.sync()
    assert not client.database.exists() and not list(tmp_path.glob('catalog-*.sqlite3'))


def test_catalogue_symlinks_are_not_followed(tmp_path):
    private = tmp_path / 'private'
    private.write_text('not a catalogue')
    (tmp_path / 'catalog.sqlite3').symlink_to(private)
    with pytest.raises(AppError):
        GutenbergClient(tmp_path).sync()
    assert private.read_text() == 'not a catalogue'


def test_service_rechecks_official_rights_on_each_new_delivery_and_never_silently_requeues(tmp_path, monkeypatch):
    from test_downloads import epub

    service = Service(Settings(state_dir=tmp_path))
    member = Principal('member@example.invalid', 'A' * 40, 20, True, True)
    service.register_instance(member, 42)
    metadata = Mock(side_effect=[parse_metadata(rdf(), 1342), parse_metadata(rdf(rights='Copyrighted.'), 1342)])
    monkeypatch.setattr(service.books, 'metadata', metadata)
    monkeypatch.setattr(downloads, 'fetch_bytes', lambda _: epub())
    request = {'id': 'official-delivery-request-0001', 'op': 'library.deliver', 'session': service.session,
               'args': {'book': 1342, 'format': 'epub'}}
    result = service.handle(member, 42, request)
    assert result['ok'] and service.handle(member, 42, request) == result
    request['id'] = 'official-delivery-request-0002'
    assert service.handle(member, 42, request)['error'] == 'book_rights_unverified'
    assert metadata.call_count == 2


def test_model_book_tool_can_use_the_official_source_but_local_mode_never_exposes_it(source):
    client, _, _ = source
    tools = agent.tools(Mock(), local_only=False, locale='en', book_client=client)
    result = tools['find_books'].run({'title': 'Pride'})
    assert result['books'][0]['title'] == 'Pride and Prejudice'
    assert 'find_books' not in agent.tools(Mock(), local_only=True, locale='en', book_client=client)


def test_gzip_catalogue_expansion_is_bounded_before_csv_parsing(tmp_path, monkeypatch):
    compressed = gzip.compress(b'x' * (32 * 1024 * 1024 + 1))
    monkeypatch.setattr(gutenberg, 'fetch', lambda *_a, **_k: compressed)
    with pytest.raises(AppError, match='source_response_limit'):
        GutenbergClient(tmp_path).sync()
    assert not (tmp_path / 'catalog.sqlite3').exists()


def test_invalid_deflate_is_a_source_error_and_never_replaces_catalogue(tmp_path, monkeypatch):
    invalid = b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07' + b'\x00' * 12
    monkeypatch.setattr(gutenberg, 'fetch', lambda *_a, **_k: invalid)
    with pytest.raises(AppError, match='source_response_invalid'):
        GutenbergClient(tmp_path).sync()
    assert not (tmp_path / 'catalog.sqlite3').exists()


def test_model_openlibrary_tool_keeps_its_separate_catalogue_client(source):
    books, feed, metadata = source
    public = Mock()
    public.get.return_value = {'numFound': 1, 'docs': [{'key': '/works/OL1W', 'title': 'Catalogue only', 'ebook_access': 'no_ebook'}]}
    tool = agent.tools(public, local_only=False, locale='en', book_client=books)['find_books']
    result = tool.run({'source': 'openlibrary', 'title': 'Catalogue'})
    assert result['books'][0]['title'] == 'Catalogue only'
    assert result['books'][0]['downloads'] == []
    assert public.get.call_args.args[0] == 'https://openlibrary.org/search.json'
    feed.assert_not_called()
    metadata.assert_not_called()

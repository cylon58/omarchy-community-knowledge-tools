"""Local marketplace discovery. Listings are attributed claims, never executable code."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

CATALOG_URL = 'https://plugins.omarchy.org/catalog.json'
MAX_BYTES = 32 * 1024 * 1024
MAX_PLUGINS = 20_000
SCHEMA_VERSION = 1


def _now():
    return datetime.now(timezone.utc).isoformat()


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Catalog timestamp is missing')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Catalog timestamp requires a timezone')
    return result


def _text(value, bound=4096):
    if not isinstance(value, str) or len(value) > bound or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid catalog text')
    return value


def _parse(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('Catalog exceeds download limit')
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('plugins'), list):
        raise ValueError('Invalid plugin catalog')
    generated = data.get('generatedAt')
    if (_date(generated) - datetime.now(timezone.utc)).total_seconds() > 86400:
        raise ValueError('Catalog timestamp is in the future')
    if not 0 < len(data['plugins']) <= MAX_PLUGINS:
        raise ValueError('Empty or oversized catalog; retaining previous index')
    warnings = data.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > MAX_PLUGINS:
        raise ValueError('Invalid catalog warnings')
    warnings = [_text(w) for w in warnings]
    rows, ids = [], set()
    for item in data['plugins']:
        if not isinstance(item, dict):
            raise ValueError('Invalid plugin entry')
        row = {k: _text(item.get(k, ''), 4096 if k == 'description' else 512)
               for k in ('id', 'name', 'description', 'author', 'repo', 'sourceType', 'status')}
        if not row['id'] or not row['name'] or row['id'] in ids:
            raise ValueError('Missing or duplicate plugin identity')
        ids.add(row['id'])
        url = urlsplit(row['repo'])
        parts = url.path.strip('/').split('/')
        if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
                or len(parts) != 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p)
                                         or p in {'.', '..'} for p in parts)):
            raise ValueError('Invalid GitHub repository link')
        row['github_owner'] = parts[0]
        row['github_owner_url'] = 'https://github.com/' + parts[0]
        if row['sourceType'] not in ('builtin', 'community'):
            raise ValueError('Unknown catalog source type')
        tags = item.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 64:
            raise ValueError('Invalid plugin tags')
        row['tags'] = [_text(t, 128) for t in tags]
        available = item.get('installAvailable')
        if available is not None and type(available) is not bool:
            raise ValueError('Invalid plugin availability')
        row['installAvailable'] = available
        for key in ('verificationStatus', 'listingValidatedCommit'):
            value = item.get(key)
            row[key] = _text(value, 128) if value is not None else None
        rows.append(row)
    return generated, warnings, rows


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Marketplace redirected; refusing an unreviewed catalog source')


def fetch_catalog(headers):
    """Fetch one fixed public feed; no credentials, query text, proxies or repo code."""
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(CATALOG_URL, headers={**headers, 'Accept': 'application/json',
                                          'User-Agent': 'omarchy-community-knowledge'})
    deadline = time.monotonic() + 20
    try:
        with opener.open(request, timeout=5) as response:
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ValueError('Catalog download exceeded deadline')
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Catalog exceeds download limit')
                chunks.append(chunk)
            return response.status, dict(response.headers), b''.join(chunks)
    except HTTPError as error:
        if error.code == 304:
            return 304, dict(error.headers), b''
        raise ValueError(f'Marketplace returned HTTP {error.code}') from error
    except (URLError, TimeoutError, OSError) as error:
        raise ValueError('Marketplace could not be reached') from error


@contextmanager
def _locked(cache):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink():
        raise ValueError('Plugin cache must be a real directory')
    fd = os.open(cache/'plugins.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Another plugin refresh is still running; retry shortly')
                time.sleep(.05)
        yield cache/'plugins.sqlite3'
    finally:
        os.close(fd)


@contextmanager
def _connection(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('No plugin index; run omarchy-knowledge plugins sync online first')
    db = sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        yield db
    finally:
        db.close()


def _metadata(path):
    try:
        with _connection(path) as db:
            value = json.loads(db.execute('SELECT data FROM metadata').fetchone()[0])
            if value['schema_version'] != SCHEMA_VERSION or value['url'] != CATALOG_URL:
                raise ValueError('Unsupported plugin index')
            for key in ('generated_at', 'downloaded_at', 'checked_at', 'last_attempt_at'):
                _date(value[key])
            if (type(value['count']) is not int or not 0 < value['count'] <= MAX_PLUGINS
                    or not re.fullmatch(r'[a-f0-9]{64}', value['digest'])
                    or not isinstance(value['warnings'], list)):
                raise ValueError('Invalid plugin index metadata')
            _text(value['etag'], 512)
            _text(value['last_modified'], 512)
            return value
    except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError('No plugin index; run omarchy-knowledge plugins sync online first') from error


def _update_metadata(path, metadata):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE metadata SET data=?', (json.dumps(metadata),))


def _replace(path, metadata, rows):
    fd, name = tempfile.mkstemp(prefix='.plugins-', suffix='.sqlite3', dir=path.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(name)) as db, db:
            db.execute('CREATE TABLE metadata (data TEXT NOT NULL)')
            db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
            db.execute('CREATE TABLE plugins (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE VIRTUAL TABLE plugin_search USING fts5(id, name, description, tags, author, github_owner)')
            db.executemany('INSERT INTO plugins VALUES (?, ?)',
                           [(r['id'], json.dumps(r, ensure_ascii=False)) for r in rows])
            db.executemany('INSERT INTO plugin_search VALUES (?, ?, ?, ?, ?, ?)',
                           [(r['id'], r['name'], r['description'], ' '.join(r['tags']),
                             r['author'], r['github_owner']) for r in rows])
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _refresh(path, offline, fetch):
    try:
        previous = _metadata(path)
    except ValueError:
        previous = None
    if offline:
        if previous is None:
            raise ValueError('No plugin index; run omarchy-knowledge plugins sync online first')
        return {**previous, 'state': 'offline'}
    headers = {}
    if previous:
        for key, header in [('etag', 'If-None-Match'), ('last_modified', 'If-Modified-Since')]:
            if previous.get(key):
                headers[header] = _text(previous[key], 512)
    attempted = _now()
    try:
        code, response_headers, raw = fetch(headers)
        if code == 304:
            if previous is None:
                raise ValueError('Marketplace returned 304 without a local index')
            metadata = {**previous, 'checked_at': attempted, 'last_attempt_at': attempted,
                        'refresh_error': None}
            _update_metadata(path, metadata)
            return {**metadata, 'state': 'not-modified'}
        if code != 200:
            raise ValueError(f'Marketplace returned HTTP {code}')
        generated, warnings, rows = _parse(raw)
        if previous and _date(generated) < _date(previous['generated_at']):
            raise ValueError('Marketplace supplied an older catalog')
        response_headers = {k.lower(): v for k, v in response_headers.items()}
        metadata = dict(schema_version=SCHEMA_VERSION, url=CATALOG_URL,
                        generated_at=generated, downloaded_at=attempted, checked_at=attempted,
                        last_attempt_at=attempted, refresh_error=None, count=len(rows), warnings=warnings,
                        digest=hashlib.sha256(raw).hexdigest(),
                        etag=_text(response_headers.get('etag', ''), 512),
                        last_modified=_text(response_headers.get('last-modified', ''), 512))
        if previous and previous['digest'] == metadata['digest']:
            metadata['downloaded_at'] = previous['downloaded_at']
            _update_metadata(path, metadata)
            return {**metadata, 'state': 'unchanged'}
        _replace(path, metadata, rows)
        return {**metadata, 'state': 'refreshed'}
    except (ValueError, OSError, sqlite3.Error, RecursionError) as error:
        # Failed fetches never replace the last valid index or advance checked_at.
        if previous is None:
            raise ValueError(f'No plugin index available: {error}') from error
        metadata = {**previous, 'last_attempt_at': attempted,
                    'refresh_error': str(error)[:500]}
        _update_metadata(path, metadata)
        return {**metadata, 'state': 'stale'}


def sync(cache, *, offline=False, fetch=None):
    with _locked(cache) as path:
        return _refresh(path, offline, fetch or fetch_catalog)


def status(cache):
    with _locked(cache) as path:
        return _metadata(path)


def search(cache, query='', *, limit=5, offline=False, fetch=None):
    if not isinstance(query, str) or len(query) > 512 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Query must be at most 512 characters and limit between 1 and 100')
    tokens = re.findall(r'[^\W_]+', query, re.UNICODE)
    if len(tokens) > 64:
        raise ValueError('Query has too many terms')
    # Remove request wording, never technical constraints such as "without".
    filler = {'a', 'an', 'the', 'i', 'want', 'would', 'like', 'to', 'make',
              'build', 'create', 'plugin', 'plugins', 'that', 'helps', 'help',
              'me', 'please', 'better', 'omarchy'}
    terms = list(dict.fromkeys(t.lower() for t in tokens if t.lower() not in filler))
    mode = 'all-terms' if terms else ('no-terms' if query.strip() else 'browse')
    with _locked(cache) as path:
        source = _refresh(path, offline, fetch or fetch_catalog)
        with _connection(path) as db:
            if terms:
                expression = ' AND '.join('"' + token + '"' for token in terms)
                count = db.execute('SELECT count(*) FROM plugin_search WHERE plugin_search MATCH ?',
                                   (expression,)).fetchone()[0]
                if count == 0 and len(terms) > 1:
                    mode = 'any-term-fallback'
                    expression = ' OR '.join('"' + token + '"' for token in terms)
                    count = db.execute('SELECT count(*) FROM plugin_search WHERE plugin_search MATCH ?',
                                       (expression,)).fetchone()[0]
                matches = db.execute('SELECT plugins.data FROM plugin_search JOIN plugins '
                                     'ON plugin_search.id=plugins.id WHERE plugin_search MATCH ? '
                                     'ORDER BY bm25(plugin_search, 3, 5, 1, 3, 2, 2), plugins.id LIMIT ?',
                                     (expression, limit)).fetchall()
            elif query.strip():
                count, matches = 0, []
            else:
                count = source['count']
                matches = db.execute('SELECT data FROM plugins ORDER BY id LIMIT ?', (limit,)).fetchall()
        return dict(source=source, total_matches=count,
                    match=dict(mode=mode, terms=terms),
                    results=[json.loads(row[0]) for row in matches])


def show(cache, plugin_id, *, offline=False, fetch=None):
    if not isinstance(plugin_id, str) or not 0 < len(plugin_id) <= 512:
        raise ValueError('Invalid plugin ID')
    with _locked(cache) as path:
        source = _refresh(path, offline, fetch or fetch_catalog)
        with _connection(path) as db:
            row = db.execute('SELECT data FROM plugins WHERE id=?', (plugin_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown plugin ID')
        return dict(source=source, plugin=json.loads(row[0]))

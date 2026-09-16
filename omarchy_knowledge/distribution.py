"""Small inert static distribution. No scripts, remote assets, or record execution."""
import hashlib
import html
import os
from pathlib import Path
import tempfile

from .canonical import snapshot_data
from .snapshots import (_canonical, _open_directory, _write_regular_at,
                        _bounded_directory_names, MAX_SNAPSHOT_BYTES)


def build_site(data, output, *, status):
    from .service import safe_status
    status = safe_status(status)
    with tempfile.TemporaryDirectory(prefix='omarchy-export-') as temporary:
        snapshot = Path(temporary) / 'snapshot'
        snapshot_data(data, snapshot)
        files = {name: (snapshot / name).read_bytes()
                 for name in ('manifest.json', 'index.json', 'records.jsonl')}
    envelope = {key: value for key, value in data.items() if key != 'records'}
    files['canonical.json'] = _canonical(envelope)
    coverage = {'records': len(data['records']),
                'receipted_records': len({r['record_id'] for r in data['receipts']})}
    files['status.json'] = _canonical({**status, 'source': data['source'], 'receipt_coverage': coverage,
                                      'upstream': data['upstream'], 'pr_behavior': 'snapshots-imported-prs-remain-open'})
    text = html.escape(_canonical({'source': data['source'], 'intake': status,
                                  'receipt_coverage': coverage}).decode())
    rows = ''.join('<li><code>' + html.escape(r['id']) + '</code> ' + html.escape(r['payload']['title']) + '</li>'
                   for r in data['records'] if r['type'] == 'case')
    files['index.html'] = ('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'">
<title>Omarchy community knowledge</title><h1>Omarchy community knowledge</h1>
<p>Community observations; No Omarchy endorsement. Data: CC BY 4.0. Toolkit: MIT.</p>
<p>Use your browser's Find command or the CLI to search. Records are inert claims;
accounts are not people or machines. Imported journals are not firsthand tests.</p>
<p>Accepted means a PR snapshot was imported. PRs deliberately remain open.
Static hashes provide integrity only; use CLI sync for canonical API verification.</p>
<p><a href="index.json">Index</a> · <a href="records.jsonl">Records</a> ·
<a href="manifest.json">Snapshot manifest</a> · <a href="canonical.json">Receipt envelope</a> ·
<a href="status.json">Status</a> · <a href="distribution.json">Distribution hashes</a></p>
<h2>Cases</h2><ul>''' + rows + '</ul><h2>Revision, freshness and intake</h2><pre>' + text + '</pre></html>\n').encode()
    files['distribution.json'] = _canonical({'version': 1, 'source': data['source'], 'files': {
        name: {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        for name, raw in sorted(files.items())}})
    total = sum(map(len, files.values()))
    if total > MAX_SNAPSHOT_BYTES:
        raise ValueError('Static distribution exceeds bound')
    directory = _open_directory(output, 'Static site', create=True)
    try:
        if _bounded_directory_names(directory, 1):
            raise ValueError('Static output must be empty')
        for name, raw in files.items():
            _write_regular_at(directory, name, raw)
    finally:
        os.close(directory)
    return {'bytes': total, 'distribution_sha256': hashlib.sha256(files['distribution.json']).hexdigest()}

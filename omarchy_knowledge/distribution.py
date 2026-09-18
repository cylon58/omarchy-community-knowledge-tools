"""Small inert static distribution. No scripts, remote assets, or record execution."""
import hashlib
import html
import os
from pathlib import Path
import tempfile

from .canonical import snapshot_data
from .snapshots import (_canonical, _open_directory, _write_regular_at,
                        _bounded_directory_names, MAX_SNAPSHOT_BYTES)


def build_health_projection(data, proof_bundle, *, api_calls=None,
                            response_bytes=None):
    """Measure one successful builder execution without another network read."""
    from .object_bundle import decode

    objects = decode(proof_bundle, data['source']['data_revision'])
    for value in (api_calls, response_bytes):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('Invalid canonical builder measurement')
    return {
        'version': 1,
        'record_count': len(data['records']),
        'receipt_count': len(data['receipts']),
        'proof': {
            'object_count': len(objects),
            'raw_bytes': sum(len(raw) for raw in objects.values()),
            'compressed_bytes': len(proof_bundle),
        },
        'canonical_builder': {
            'scope': 'successful-build-canonical-adapter',
            'request_attempts': api_calls,
            'charged_response_bytes': response_bytes,
            # APIObjects.visits resets per enforcement window; it is not a
            # whole-build total and must remain unknown in this projection.
            'object_visits': None,
        },
    }


def build_site(data, output, *, status, intake_cursor=None,
               cursor_health=None, intake_scan=None, proof_bundle=None,
               update_manifest=None, update_bundle=None, build_health=None):
    from .intake_status import (CursorHealth, IntakeScan,
                                validate_build_health, validate_safe_status)
    from .fair_intake import IntakeCursor
    status = validate_safe_status(status)
    projection = {}
    if any(value is not None for value in
           (intake_cursor, cursor_health, intake_scan)):
        if any(value is None for value in
               (intake_cursor, cursor_health, intake_scan)):
            raise ValueError('Incomplete intake projection')
        projection = {
            'intake_cursor': IntakeCursor.from_mapping(intake_cursor).to_mapping(),
            'cursor_health': CursorHealth.from_mapping(cursor_health).to_mapping(),
            'intake_scan': IntakeScan.from_mapping(intake_scan).to_mapping(),
        }
    if build_health is not None:
        projection['build_health'] = validate_build_health(build_health)
    with tempfile.TemporaryDirectory(prefix='omarchy-export-') as temporary:
        snapshot = Path(temporary) / 'snapshot'
        snapshot_data(data, snapshot)
        files = {name: (snapshot / name).read_bytes()
                 for name in ('manifest.json', 'index.json', 'records.jsonl')}
    envelope = {key: value for key, value in data.items() if key != 'records'}
    files['canonical.json'] = _canonical(envelope)
    coverage = {'records': len(data['records']),
                'receipted_records': len({r['record_id'] for r in data['receipts']})}
    files['status.json'] = _canonical({**status, **projection, 'source': data['source'], 'receipt_coverage': coverage,
                                      'upstream': data['upstream'], 'pr_behavior': 'snapshots-imported-prs-remain-open'})
    if proof_bundle is not None:
        from .object_bundle import MAX_COMPRESSED_BUNDLE
        if not isinstance(proof_bundle, bytes) or not 0 < len(proof_bundle) <= MAX_COMPRESSED_BUNDLE:
            raise ValueError('Invalid canonical object bundle')
        files['canonical-objects.bundle'] = proof_bundle
    from .object_bundle import MAX_COMPRESSED_BUNDLE
    from .update_pack import MAX_MANIFEST
    optional = (isinstance(update_manifest, bytes)
                and 0 < len(update_manifest) <= MAX_MANIFEST
                and isinstance(update_bundle, bytes)
                and 0 < len(update_bundle) <= MAX_COMPRESSED_BUNDLE)
    if optional:
        files['canonical-update.json'] = update_manifest
        files['canonical-update.bundle'] = update_bundle
    text = html.escape(_canonical({'source': data['source'], 'intake': status,
                                  'receipt_coverage': coverage, 'upstream': data['upstream']}).decode())
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
    def finalize():
        files['distribution.json'] = _canonical({
            'version': 1, 'source': data['source'], 'files': {
                name: {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
                for name, raw in sorted(files.items())
                if name != 'distribution.json'
            },
        })
        return sum(map(len, files.values()))

    total = finalize()
    if total > MAX_SNAPSHOT_BYTES and optional:
        del files['canonical-update.json']
        del files['canonical-update.bundle']
        total = finalize()
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

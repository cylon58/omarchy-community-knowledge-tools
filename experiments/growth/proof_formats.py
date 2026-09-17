#!/usr/bin/env python3
"""Experimental warm-proof format comparison; not a production decoder."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback
import zlib

from experiments.growth import cold_postmortem, gates
from experiments.growth.baseline import FIXED_NOW, _cohort


CHUNK_MAGIC = b"OMARCHY-KNOWLEDGE-EXPERIMENTAL-CHUNK\x00\x01"
KIND_TAGS = {"blob": b"B", "commit": b"C", "tree": b"T"}
TAG_KINDS = {tag: kind for kind, tag in KIND_TAGS.items()}
MAX_CHUNK_OBJECTS = 5000
MAX_CHUNK_RAW = 20 * 1024 * 1024
MAX_CHUNK_ENCODED = 24 * 1024 * 1024
MAX_CHUNK_COMPRESSED = 16 * 1024 * 1024
PER_KIND = {"blob": 65536, "commit": 65536, "tree": 1024 * 1024}
ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = ROOT / "experiments/growth/results/native-distributed-500-v1.json"
SOURCE_REPORT_SHA256 = "9a58c3bbfdc6e045e505b8e1759ba6f6d7b8943dac8a5e723079c1cff1b3f92a"
PREREQUISITE_REPORT = ROOT / "experiments/growth/results/native-batched-reads-v2.json"
PREREQUISITE_SHA256 = "cb6679f06c5c7be98a36ed4231eb1a4e60e85cdf3a2faf1c590e1a1b1a3bd1d2"
FAMILIES = ("bucket-64", "bucket-256", "per-object", "update-pack")
PROFILE = "native-distributed-500-proof-format-experiment"
SCHEMA_VERSION = 1
MAX_REPORT_BYTES = 2 * 1024 * 1024
OVERALL_SECONDS = 600
SUCCESSOR_SECONDS = 120
BASE_RECORDS = 500
ADDED_RECORDS = 10
SOURCES = (
    "experiments/growth/proof_formats.py",
    "experiments/growth/baseline.py",
    "experiments/growth/cold_postmortem.py",
    "experiments/growth/gates.py",
    "omarchy_knowledge/canonical.py",
    "omarchy_knowledge/coordinator.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/github_object_batch.py",
    "omarchy_knowledge/object_bundle.py",
    "omarchy_knowledge/service.py",
)


class FormatUnavailable(ValueError):
    """An experimental proof-format artifact failed closed."""


def _require(condition):
    if not condition:
        raise FormatUnavailable()


def _git_hash(kind, raw):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


def _encode_chunk(objects):
    """Encode verified typed objects without embedding a mutable proof head."""
    _require(isinstance(objects, dict) and 1 <= len(objects) <= MAX_CHUNK_OBJECTS)
    rows = []
    total = 0
    for key, raw in objects.items():
        _require(
            isinstance(key, tuple)
            and len(key) == 2
            and key[0] in KIND_TAGS
            and isinstance(key[1], str)
            and len(key[1]) == 40
            and isinstance(raw, bytes)
        )
        kind, oid = key
        try:
            oid_raw = bytes.fromhex(oid)
        except ValueError as exc:
            raise FormatUnavailable() from exc
        _require(
            len(raw) <= PER_KIND[kind]
            and _git_hash(kind, raw) == oid
        )
        total += len(raw)
        _require(total <= MAX_CHUNK_RAW)
        rows.append((kind, oid, oid_raw, raw))
    rows.sort(key=lambda row: (row[0], row[1]))
    output = bytearray(CHUNK_MAGIC)
    output.extend(struct.pack(">I", len(rows)))
    for kind, _oid, oid_raw, raw in rows:
        output.extend(KIND_TAGS[kind])
        output.extend(oid_raw)
        output.extend(struct.pack(">I", len(raw)))
        output.extend(raw)
        _require(len(output) <= MAX_CHUNK_ENCODED)
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=compressed, mtime=0
    ) as stream:
        stream.write(output)
    result = compressed.getvalue()
    _require(len(result) <= MAX_CHUNK_COMPRESSED)
    return result


def _inflate_chunk(value):
    _require(isinstance(value, bytes) and 1 <= len(value) <= MAX_CHUNK_COMPRESSED)
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = bytearray()
        for offset in range(0, len(value), 65536):
            remaining = MAX_CHUNK_ENCODED + 1 - len(output)
            _require(remaining > 0)
            output.extend(inflater.decompress(value[offset:offset + 65536], remaining))
            _require(len(output) <= MAX_CHUNK_ENCODED and not inflater.unconsumed_tail)
        remaining = MAX_CHUNK_ENCODED + 1 - len(output)
        _require(remaining > 0)
        output.extend(inflater.flush(remaining))
        _require(
            inflater.eof
            and not inflater.unused_data
            and len(output) <= MAX_CHUNK_ENCODED
        )
        return bytes(output)
    except zlib.error as exc:
        raise FormatUnavailable() from exc


def _decode_chunk(value):
    raw = _inflate_chunk(value)
    header = len(CHUNK_MAGIC) + 4
    _require(len(raw) >= header and raw.startswith(CHUNK_MAGIC))
    offset = len(CHUNK_MAGIC)
    count = struct.unpack(">I", raw[offset:offset + 4])[0]
    offset += 4
    _require(1 <= count <= MAX_CHUNK_OBJECTS)
    objects = {}
    total = 0
    for _ in range(count):
        _require(offset + 25 <= len(raw))
        kind = TAG_KINDS.get(raw[offset:offset + 1])
        oid = raw[offset + 1:offset + 21].hex()
        size = struct.unpack(">I", raw[offset + 21:offset + 25])[0]
        offset += 25
        _require(kind is not None and size <= PER_KIND[kind] and offset + size <= len(raw))
        content = raw[offset:offset + size]
        offset += size
        total += size
        key = (kind, oid)
        _require(
            total <= MAX_CHUNK_RAW
            and _git_hash(kind, content) == oid
            and key not in objects
        )
        objects[key] = content
    _require(offset == len(raw))
    return objects


def _object_key(key):
    return key[0] + ":" + key[1]


def _parse_object_key(value):
    _require(isinstance(value, str) and ":" in value)
    kind, oid = value.split(":", 1)
    _require(kind in KIND_TAGS and len(oid) == 40)
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise FormatUnavailable() from exc
    return kind, oid


def _compact(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _manifest_value(family, head, base_head, payloads, removed):
    return {
        "b": base_head,
        "c": [[label, _sha256(raw), len(raw)]
              for label, raw in sorted(payloads.items())],
        "f": family,
        "h": head,
        "r": sorted(_object_key(key) for key in removed),
        "v": 1,
    }


def _build_artifact(family, head, objects, *, base_head=None, base_objects=None):
    """Build one compact manifest and its immutable reusable payload chunks."""
    _require(family in FAMILIES and isinstance(objects, dict))
    _require(isinstance(head, str) and ("commit", head) in objects)
    if base_head is None:
        _require(base_objects is None)
        base_objects = {}
    else:
        _require(isinstance(base_objects, dict) and ("commit", base_head) in base_objects)
    payloads = {}
    if family.startswith("bucket-"):
        count = int(family.split("-", 1)[1])
        buckets = {}
        for key, raw in objects.items():
            number = int(key[1][:2], 16) if count == 256 else int(key[1][:2], 16) >> 2
            label = format(number, "02x")
            buckets.setdefault(label, {})[key] = raw
        payloads = {label: _encode_chunk(rows) for label, rows in sorted(buckets.items())}
    elif family == "per-object":
        payloads = {
            _object_key(key): _encode_chunk({key: raw})
            for key, raw in sorted(objects.items())
        }
    else:
        additions = {key: raw for key, raw in objects.items()
                     if key not in base_objects or base_objects[key] != raw}
        if additions:
            payloads = {"update": _encode_chunk(additions)}
    removed = set(base_objects) - set(objects)
    manifest = _compact(_manifest_value(family, head, base_head, payloads, removed))
    return {
        "family": family,
        "head": head,
        "base_head": base_head,
        "manifest": manifest,
        "payloads": payloads,
        "object_keys": frozenset(objects),
    }


def _parse_manifest(raw):
    from omarchy_knowledge.github_native import strict_json

    _require(isinstance(raw, bytes) and 1 <= len(raw) <= MAX_REPORT_BYTES)
    try:
        value = strict_json(raw)
    except (TypeError, ValueError, RecursionError) as exc:
        raise FormatUnavailable() from exc
    _require(
        isinstance(value, dict)
        and set(value) == {"b", "c", "f", "h", "r", "v"}
        and value["v"] == 1
        and value["f"] in FAMILIES
        and isinstance(value["h"], str)
        and len(value["h"]) == 40
        and (value["b"] is None
             or isinstance(value["b"], str) and len(value["b"]) == 40)
        and isinstance(value["c"], list)
        and isinstance(value["r"], list)
    )
    chunks = {}
    previous = None
    for row in value["c"]:
        _require(
            isinstance(row, list)
            and len(row) == 3
            and isinstance(row[0], str)
            and isinstance(row[1], str)
            and len(row[1]) == 64
            and type(row[2]) is int
            and 1 <= row[2] <= MAX_CHUNK_COMPRESSED
            and row[0] not in chunks
            and (previous is None or previous < row[0])
        )
        previous = row[0]
        chunks[row[0]] = (row[1], row[2])
    removed = [_parse_object_key(item) for item in value["r"]]
    _require(value["r"] == sorted(set(value["r"])))
    value["chunks"] = chunks
    value["removed"] = removed
    return value


def _payload_cache(artifact):
    manifest = _parse_manifest(artifact["manifest"])
    cache = {}
    _require(manifest["f"] == artifact["family"] and manifest["h"] == artifact["head"])
    for label, (digest, size) in manifest["chunks"].items():
        raw = artifact["payloads"].get(label)
        _require(isinstance(raw, bytes) and len(raw) == size and _sha256(raw) == digest)
        cache[digest] = raw
    return manifest, cache


def _warm_transfer(base, successor):
    base_manifest, cache = _payload_cache(base)
    successor_manifest, _ = _payload_cache(successor)
    _require(
        base_manifest["f"] == successor_manifest["f"]
        and successor_manifest["b"] == base_manifest["h"]
    )
    payloads = {}
    for label, (digest, _size) in successor_manifest["chunks"].items():
        if digest not in cache:
            payloads[label] = successor["payloads"][label]
    added = sorted(successor["object_keys"] - base["object_keys"])
    removed = sorted(base["object_keys"] - successor["object_keys"])
    changed_payload_bytes = sum(map(len, payloads.values()))
    return {
        "payloads": payloads,
        "manifest_bytes": len(successor["manifest"]),
        "changed_payload_bytes": changed_payload_bytes,
        "total_warm_bytes": len(successor["manifest"]) + changed_payload_bytes,
        "proof_chunk_requests": len(payloads),
        "total_requests": 1 + len(payloads),
        "additional_requests_vs_full": len(payloads),
        "changed_objects": len(added),
        "removed_objects": len(removed),
        "added_keys": [_object_key(key) for key in added],
        "removed_keys": [_object_key(key) for key in removed],
    }


def _reconstruct(base_head, base_objects, base, successor, transferred):
    base_manifest, cache = _payload_cache(base)
    target = _parse_manifest(successor["manifest"])
    _require(
        base_manifest["h"] == base_head
        and target["b"] == base_head
        and target["f"] == base_manifest["f"]
        and isinstance(base_objects, dict)
        and isinstance(transferred, dict)
    )
    needed = {}
    for label, (digest, size) in target["chunks"].items():
        if digest in cache:
            continue
        raw = transferred.get(label)
        _require(isinstance(raw, bytes) and len(raw) == size and _sha256(raw) == digest)
        cache[digest] = raw
        needed[label] = raw
    _require(set(transferred) == set(needed))
    if target["f"] == "update-pack":
        objects = dict(base_objects)
        for key in target["removed"]:
            _require(key in objects)
            del objects[key]
    else:
        _require(not target["removed"] or target["f"] in FAMILIES)
        objects = {}
    for label, (digest, _size) in target["chunks"].items():
        decoded = _decode_chunk(cache[digest])
        for key, raw in decoded.items():
            _require(key not in objects or objects[key] == raw)
            objects[key] = raw
    _require(("commit", target["h"]) in objects)
    return objects


def _load_report(path, expected_digest):
    from omarchy_knowledge.snapshots import _read_regular

    raw = _read_regular(Path(path), MAX_REPORT_BYTES)
    digest = _sha256(raw)
    if digest != expected_digest:
        raise ValueError("Unexpected report identity")
    return {"digest": digest, "value": cold_postmortem._parse_source_report(raw)}


def _load_inputs(source_report, prerequisite_report):
    source = _load_report(source_report, SOURCE_REPORT_SHA256)
    prerequisite = _load_report(prerequisite_report, PREREQUISITE_SHA256)
    source_value = source["value"]
    prerequisite_value = prerequisite["value"]
    if not (
        source_value.get("profile") == "distributed-500x100"
        and source_value.get("status") == "failure"
        and isinstance(source_value.get("imports"), list)
        and len(source_value["imports"]) == 100
        and prerequisite_value.get("profile") == "native-distributed-500-batched-reads"
        and prerequisite_value.get("status") == "success"
        and prerequisite_value.get("source_report", {}).get("sha256") == SOURCE_REPORT_SHA256
        and prerequisite_value.get("environment", {}).get("source_revision")
            == "784f7e363ad7c97a83436866e2b911d97ac3fc5c"
        and prerequisite_value.get("cold_comparison", {}).get("records") == 500
        and prerequisite_value.get("cold_comparison", {}).get("receipts") == 500
        and prerequisite_value.get("cold_comparison", {}).get("offline_proof_replay_equal") is True
    ):
        raise ValueError("Unsupported experiment input")
    return source, prerequisite


def _sanitized_trace(error):
    terminal = error
    while terminal.__cause__ is not None:
        terminal = terminal.__cause__
    result = []
    for frame in traceback.extract_tb(terminal.__traceback__):
        path = Path(frame.filename).resolve()
        try:
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        except ValueError:
            module = path.stem
        result.append({"module": module, "function": frame.name, "line": frame.lineno})
    return result


def _write_json_output(path, value):
    target = gates._validate_new_target(Path(path))
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    gates._exclusive_write(target, raw)


def _source_hashes():
    return {path: _sha256((ROOT / path).read_bytes()) for path in SOURCES}


def _comparable(value):
    from omarchy_knowledge.coordinator import canonical

    return {**value, "receipts": sorted(value["receipts"], key=canonical)}


def _native_proof(fixture, policy):
    """Generate an ordinary validated proof through the current native reader."""
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.github_native import GitHubRead
    from omarchy_knowledge.service import _validated_proof

    request_start = len(fixture.requests)
    started = time.monotonic()
    adapter = GitHubRead(
        read_token=gates.SYNTHETIC_TOKEN,
        connection_factory=fixture.connection_factory,
    )
    data = read_canonical(adapter, policy, now=FIXED_NOW)
    proof = _validated_proof(adapter, policy, data)
    return data, proof, {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "elapsed_scope": "native-canonical-read-plus-proof-export-and-offline-replay",
        "emulated_requests": len(fixture.requests) - request_start,
        "adapter_calls": adapter.http.calls,
        "graphql_calls": adapter.http.graphql_calls,
        "graphql_points": adapter.http.graphql_points,
        "response_bytes": adapter.http.bytes,
    }


def _append_successors(fixture, policy, numbers, budget, alarm):
    """Append cohorts through the real plan/publish/build wrappers and native fixture."""
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.coordinator import canonical
    from omarchy_knowledge.github_native import GitHubRead, GitHubWriter, strict_json
    from omarchy_knowledge.service import _validated_proof, plan_run, publish_run

    results = []
    final_data = final_proof = None
    for number in numbers:
        with gates._phase_deadline(budget, alarm, SUCCESSOR_SECONDS):
            started = time.monotonic()
            mutation_start = len(fixture.successful_mutations)
            fixture.add_candidate(number, _cohort(number, 1))
            planning = GitHubRead(
                read_token=gates.SYNTHETIC_TOKEN,
                connection_factory=fixture.connection_factory,
            )

            def plan(api):
                value = plan_run(
                    api, policy, pull_request=number,
                    run_number=number - 1, run_id=number, run_attempt=1,
                )
                return strict_json(canonical(value) + b"\n")

            batch, planning_metrics = gates._run_job(fixture, planning, plan)
            if batch["status"]["status"] != "planned" or batch["plan"] is None:
                raise RuntimeError("Successor planning failed")
            writer = GitHubWriter(
                gates.SYNTHETIC_TOKEN,
                connection_factory=fixture.connection_factory,
            )
            status, publish_metrics = gates._run_job(
                fixture,
                writer,
                lambda api: publish_run(
                    api, policy, batch, run_id=number, run_attempt=1
                ),
            )
            if status["status"] != "accepted":
                raise RuntimeError("Successor publishing failed")
            builder = GitHubRead(
                read_token=gates.SYNTHETIC_TOKEN,
                connection_factory=fixture.connection_factory,
            )

            def build(api):
                data = read_canonical(api, policy, now=FIXED_NOW)
                return data, _validated_proof(api, policy, data)

            built, build_metrics = gates._run_job(fixture, builder, build)
            final_data, final_proof = built
            fixture.publish_pages(final_proof, number)
            mutations = fixture.successful_mutations[mutation_start:]
            if not (
                len(mutations) == 2
                and [row["addition_count"] for row in mutations] == [5, 5]
                and {row["headline"] for row in mutations}
                    == {"Omarchy knowledge snapshot import",
                        "Record source-bound ingestion receipt"}
            ):
                raise RuntimeError("Successor mutation audit failed")
            results.append({
                "number": number,
                "records_added": 5,
                "receipts_added": 5,
                "mutations": mutations,
                "planning": planning_metrics,
                "publish": publish_metrics,
                "build": build_metrics,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "elapsed_scope": "candidate-creation-plus-plan-publish-canonical-proof",
                "phase_limit_seconds": SUCCESSOR_SECONDS,
            })
    return final_data, final_proof, results


def _ordinary_replay(proof, head, policy, verified_at):
    """Use the production full-bundle decoder and ordinary canonical traversal."""
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.github_native import APIObjects, NativeUnavailable

    class Replay:
        def repository(self):
            return {"id": policy.repository_id, "full_name": policy.repository,
                    "default_branch": "main"}

        def branch(self):
            return head

        def git_commit(self, _oid):
            raise NativeUnavailable()

        git_tree = git_commit
        git_blob = git_commit

        def commit_info(self, oid):
            return self.objects.info(oid)

    replay = Replay()
    replay.objects = APIObjects(replay)
    replay.objects.load_bundle(proof, head)
    return read_canonical(replay, policy, now=verified_at)


def _measure_family(family, base_head, base_objects, successor_head,
                    successor_objects, successor_proof, successor_data, policy):
    from omarchy_knowledge.object_bundle import encode

    base = _build_artifact(family, base_head, base_objects)
    successor = _build_artifact(
        family, successor_head, successor_objects,
        base_head=base_head, base_objects=base_objects,
    )
    transfer = _warm_transfer(base, successor)
    decode_started = time.monotonic()
    rebuilt = _reconstruct(
        base_head, base_objects, base, successor, transfer["payloads"]
    )
    ordinary = encode(successor_head, rebuilt)
    decode_seconds = round(time.monotonic() - decode_started, 6)
    replay_started = time.monotonic()
    replayed = _ordinary_replay(
        ordinary, successor_head, policy, successor_data["source"]["verified_at"]
    )
    replay_seconds = round(time.monotonic() - replay_started, 6)
    same = _build_artifact(
        family, base_head, base_objects,
        base_head=base_head, base_objects=base_objects,
    )
    same_transfer = _warm_transfer(base, same)
    exact_map = rebuilt == successor_objects
    proof_equal = ordinary == successor_proof
    canonical_equal = _comparable(replayed) == _comparable(successor_data)
    if not (exact_map and proof_equal and canonical_equal):
        raise RuntimeError("Proof-format parity failed")
    return {
        "family": family,
        "base_full_proof_bytes": None,
        "full_successor_bytes": len(successor_proof),
        "manifest_bytes": transfer["manifest_bytes"],
        "changed_payload_bytes": transfer["changed_payload_bytes"],
        "total_warm_bytes": transfer["total_warm_bytes"],
        "warm_to_full_ratio": round(
            transfer["total_warm_bytes"] / len(successor_proof), 6
        ),
        "published_candidate_payload_bytes": sum(
            len(raw) for raw in successor["payloads"].values()
        ),
        "current_published_bytes_including_retained_full": (
            len(successor_proof)
            + len(successor["manifest"])
            + sum(len(raw) for raw in successor["payloads"].values())
        ),
        "proof_chunk_requests": transfer["proof_chunk_requests"],
        "total_requests": transfer["total_requests"],
        "additional_requests_vs_full": transfer["additional_requests_vs_full"],
        "changed_objects": transfer["changed_objects"],
        "removed_objects": transfer["removed_objects"],
        "changed_chunk_count": len(transfer["payloads"]),
        "total_successor_chunks": len(successor["payloads"]),
        "round_trip": {
            "exact_typed_object_map": exact_map,
            "ordinary_full_proof_equal": proof_equal,
            "ordinary_canonical_replay_equal": canonical_equal,
            "source_equal": replayed["source"] == successor_data["source"],
        },
        "timings_seconds": {
            "decode_reconstruct_and_full_encode": decode_seconds,
            "ordinary_canonical_replay": replay_seconds,
            "scope": (
                "Local CPU/wall time: candidate chunk verification, exact map reconstruction, "
                "production full-bundle encode, then separate production full-bundle decode and canonical replay."
            ),
        },
        "same_head": {
            "manifest_bytes": same_transfer["manifest_bytes"],
            "proof_chunk_requests": same_transfer["proof_chunk_requests"],
            "changed_payload_bytes": same_transfer["changed_payload_bytes"],
            "total_warm_bytes": same_transfer["total_warm_bytes"],
            "total_requests": same_transfer["total_requests"],
        },
    }


def _measure_candidates(measurement, base_head, base_objects, base_proof,
                        successor_head, successor_objects, successor_proof,
                        successor_data, policy):
    """Append each completed candidate and preserve a sanitized failed candidate."""
    for family in FAMILIES:
        try:
            item = _measure_family(
                family, base_head, base_objects, successor_head,
                successor_objects, successor_proof, successor_data, policy,
            )
        except (gates._DeadlineExpired, KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            measurement["formats"].append({
                "family": family,
                "status": "failure",
                "failure_kind": type(error).__name__,
                "failure_traceback": _sanitized_trace(error),
            })
            raise
        item["status"] = "success"
        item["base_full_proof_bytes"] = len(base_proof)
        measurement["formats"].append(item)


def _fixture_metrics(fixture):
    return {
        "git_commands": fixture.repository.commands,
        "contract_violations": fixture.contract_violations,
        "emulated_https_requests": len(fixture.requests),
        "emulated_response_bytes": sum(
            row["response_bytes"] for row in fixture.requests
        ),
        "pages_publications": fixture.pages_publications,
        "object_reader": {
            "cached_objects": len(fixture.reader.cache),
            "cached_object_bytes": fixture.reader.bytes,
            "object_cap": fixture.reader._max_objects,
            "byte_cap": fixture.reader._max_bytes,
        },
    }


def _run_fixture_comparison(source, budget, alarm, measurement=None):
    from omarchy_knowledge.coordinator import Policy
    from omarchy_knowledge.object_bundle import decode

    measurement = {} if measurement is None else measurement
    with tempfile.TemporaryDirectory(prefix="omarchy-proof-formats-") as temporary:
        with gates._NativeFixture(Path(temporary)) as fixture:
            policy = Policy("a" * 40, "b" * 40)
            reconstruction_started = time.monotonic()
            reconstructed = cold_postmortem._reconstruct(fixture, source)
            reconstructed["elapsed_seconds"] = round(
                time.monotonic() - reconstruction_started, 6
            )
            measurement["reconstruction"] = reconstructed
            base_data, base_proof, base_native = _native_proof(fixture, policy)
            base_head = base_data["source"]["data_revision"]
            base_objects = decode(base_proof, base_head)
            measurement["base"] = {
                "head": base_head,
                "records": len(base_data["records"]),
                "receipts": len(base_data["receipts"]),
                "full_proof_bytes": len(base_proof),
                "full_proof_sha256": _sha256(base_proof),
                "typed_objects": len(base_objects),
                "native_generation": base_native,
            }
            fixture.publish_pages(base_proof, len(source["imports"]))
            successor_data, successor_proof, successors = _append_successors(
                fixture,
                policy,
                range(len(source["imports"]) + 1, len(source["imports"]) + 3),
                budget,
                alarm,
            )
            successor_head = successor_data["source"]["data_revision"]
            successor_objects = decode(successor_proof, successor_head)
            expected_records = sum(item["record_count"] for item in source["imports"]) + 10
            if not (
                reconstructed["data_head"] == base_head
                and len(base_data["records"]) == expected_records - 10
                and len(base_data["receipts"]) == expected_records - 10
                and len(successor_data["records"]) == expected_records
                and len(successor_data["receipts"]) == expected_records
                and len(successors) == 2
                and fixture.contract_violations == 0
            ):
                raise RuntimeError("Fixture comparison count or identity mismatch")
            added = set(successor_objects) - set(base_objects)
            removed = set(base_objects) - set(successor_objects)
            measurement["successor"] = {
                "head": successor_head,
                "records": len(successor_data["records"]),
                "receipts": len(successor_data["receipts"]),
                "records_added": 10,
                "receipts_added": 10,
                "full_proof_bytes": len(successor_proof),
                "full_proof_sha256": _sha256(successor_proof),
                "typed_objects": len(successor_objects),
                "changed_objects": len(added),
                "removed_objects": len(removed),
                "imports": successors,
            }
            measurement["formats"] = []
            try:
                _measure_candidates(
                    measurement, base_head, base_objects, base_proof,
                    successor_head, successor_objects, successor_proof,
                    successor_data, policy,
                )
                full_replay_started = time.monotonic()
                replayed = _ordinary_replay(
                    successor_proof, successor_head, policy,
                    successor_data["source"]["verified_at"],
                )
                full_replay_seconds = round(time.monotonic() - full_replay_started, 6)
                measurement["formats"].append({
                    "family": "existing-full-download-control",
                    "status": "success",
                    "base_full_proof_bytes": len(base_proof),
                    "full_successor_bytes": len(successor_proof),
                    "manifest_bytes": 0,
                    "changed_payload_bytes": len(successor_proof),
                    "total_warm_bytes": len(successor_proof),
                    "warm_to_full_ratio": 1.0,
                    "current_published_bytes_including_retained_full": len(successor_proof),
                    "proof_chunk_requests": 1,
                    "total_requests": 1,
                    "additional_requests_vs_full": 0,
                    "changed_objects": len(added),
                    "removed_objects": len(removed),
                    "round_trip": {
                        "exact_typed_object_map": True,
                        "ordinary_full_proof_equal": True,
                        "ordinary_canonical_replay_equal": (
                            _comparable(replayed) == _comparable(successor_data)
                        ),
                        "source_equal": replayed["source"] == successor_data["source"],
                    },
                    "timings_seconds": {
                        "ordinary_canonical_replay": full_replay_seconds,
                        "scope": "Local production full-bundle decode and canonical replay.",
                    },
                    "same_head": {
                        "proof_chunk_requests": 0,
                        "changed_payload_bytes": 0,
                        "total_warm_bytes": 0,
                        "total_requests": 0,
                    },
                })
            finally:
                measurement["fixture"] = _fixture_metrics(fixture)
            return measurement


def run_comparison(*, source_report=SOURCE_REPORT,
                   prerequisite_report=PREREQUISITE_REPORT):
    started = time.monotonic()
    result = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": "failure",
        "failure_stage": "input-binding",
        "failure_kind": None,
        "configuration": {
            "base_records": BASE_RECORDS,
            "successor_imports": 2,
            "records_per_successor": 5,
            "added_records": ADDED_RECORDS,
            "overall_seconds": OVERALL_SECONDS,
            "successor_phase_seconds": SUCCESSOR_SECONDS,
            "successor_scenarios": 1,
        },
        "environment": {
            "python_version": sys.version.split()[0],
            "source_revision": gates._revision(),
            "source_sha256": _source_hashes(),
            "working_tree": gates._dirty_state(),
        },
        "network": {"real_network_requests": 0},
        "measurement": {},
        "limitations": [
            "One deterministic successor scenario varies updates against one synthetic history; it is not evidence from many independent histories.",
            "The original 100-import admission timing pipeline was not rerun; its immutable graph was reconstructed from the hash-bound saved report.",
            "Fixture graph reuse and local codec timings are not admission throughput claims.",
            "All payloads and HTTPS exchanges are inert local fixtures; no external request, credential, public record, or production write occurred.",
            "Published-byte totals retain the complete ordinary full proof as fallback and include candidate manifests and all current candidate chunks.",
            "Warm ratios use the actual compressed complete successor proof as denominator, never the larger sum of candidate chunks.",
            "The prototype codec is experimental tooling, not an accepted distribution parser or selected production format.",
            "Any production format still requires threat modeling, negative tests, review, backward-compatible full-download fallback, and atomic cache implementation.",
        ],
    }
    try:
        source, prerequisite = _load_inputs(source_report, prerequisite_report)
        result["source_report"] = {
            "path": "experiments/growth/results/native-distributed-500-v1.json",
            "sha256": source["digest"],
            "imports": len(source["value"]["imports"]),
        }
        result["prerequisite"] = {
            "path": "experiments/growth/results/native-batched-reads-v2.json",
            "sha256": prerequisite["digest"],
            "source_revision": prerequisite["value"]["environment"]["source_revision"],
            "status": prerequisite["value"]["status"],
        }
        result["failure_stage"] = "fixture-comparison"
        budget = gates._DeadlineBudget(OVERALL_SECONDS)
        with gates._alarm_handler(budget) as alarm:
            _run_fixture_comparison(
                source["value"], budget, alarm, result["measurement"]
            )
        result["status"] = "success"
        result["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        result["status"] = (
            "incomplete" if isinstance(error, gates._DeadlineExpired) else "failure"
        )
        result["failure_kind"] = type(error).__name__
        result["failure_traceback"] = _sanitized_trace(error)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=SOURCE_REPORT)
    parser.add_argument("--prerequisite-report", type=Path, default=PREREQUISITE_REPORT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        gates._validate_new_target(arguments.output)
    result = run_comparison(
        source_report=arguments.source_report,
        prerequisite_report=arguments.prerequisite_report,
    )
    raw = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if arguments.output is not None:
        gates._exclusive_write(arguments.output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

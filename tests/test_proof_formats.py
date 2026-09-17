"""Focused tests for the inert warm-proof format experiment."""
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def git_object(kind, raw):
    oid = hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    return (kind, oid), raw


class ProofFormatExperimentTests(unittest.TestCase):
    def test_typed_chunk_is_deterministic_head_independent_and_hash_verified(self):
        """Break caught: reusable chunk bytes depend on input order/head or trust labels."""
        from experiments.growth import proof_formats

        commit = git_object("commit", b"tree " + b"0" * 40 + b"\n\nfixture\n")
        blob = git_object("blob", b"fixture record")
        forward = dict((commit, blob))
        reverse = dict((blob, commit))

        first = proof_formats._encode_chunk(forward)
        second = proof_formats._encode_chunk(reverse)
        self.assertEqual(first, second)
        self.assertEqual(proof_formats._decode_chunk(first), forward)

        corrupt = bytearray(gzip.decompress(first))
        corrupt[-1] ^= 1
        with self.assertRaises(proof_formats.FormatUnavailable):
            proof_formats._decode_chunk(gzip.compress(bytes(corrupt), mtime=0))

    def test_warm_plans_select_exact_changes_and_count_manifest_bytes(self):
        """Break caught: reuse omits metadata cost or transfers unchanged object chunks."""
        from experiments.growth import proof_formats

        base_commit = git_object("commit", b"tree " + b"0" * 40 + b"\n\nbase\n")
        next_commit = git_object("commit", b"tree " + b"1" * 40 + b"\n\nnext\n")
        retained = git_object("blob", b"retained")
        added = git_object("blob", b"added")
        base_objects = dict((base_commit, retained))
        next_objects = dict((next_commit, retained, added))
        base_head, next_head = base_commit[0][1], next_commit[0][1]
        expected_added = sorted(
            ["blob:" + added[0][1], "commit:" + next_commit[0][1]]
        )
        expected_removed = ["commit:" + base_commit[0][1]]

        for family in ("bucket-64", "bucket-256", "per-object", "update-pack"):
            with self.subTest(family=family):
                base = proof_formats._build_artifact(family, base_head, base_objects)
                successor = proof_formats._build_artifact(
                    family, next_head, next_objects,
                    base_head=base_head, base_objects=base_objects,
                )
                transfer = proof_formats._warm_transfer(base, successor)
                self.assertEqual(transfer["added_keys"], expected_added)
                self.assertEqual(transfer["removed_keys"], expected_removed)
                self.assertEqual(
                    transfer["total_warm_bytes"],
                    transfer["manifest_bytes"] + transfer["changed_payload_bytes"],
                )
                rebuilt = proof_formats._reconstruct(
                    base_head, base_objects, base, successor, transfer["payloads"]
                )
                self.assertEqual(rebuilt, next_objects)

                same = proof_formats._build_artifact(
                    family, base_head, base_objects,
                    base_head=base_head, base_objects=base_objects,
                )
                same_transfer = proof_formats._warm_transfer(base, same)
                self.assertEqual(same_transfer["proof_chunk_requests"], 0)
                self.assertEqual(same_transfer["changed_payload_bytes"], 0)

    def test_round_trip_rejects_corrupt_missing_and_wrong_base_data(self):
        """Break caught: a partial or stale warm artifact is treated as trusted proof."""
        from experiments.growth import proof_formats

        base_commit = git_object("commit", b"tree " + b"0" * 40 + b"\n\nbase\n")
        next_commit = git_object("commit", b"tree " + b"1" * 40 + b"\n\nnext\n")
        addition = git_object("blob", b"addition")
        base_objects = dict((base_commit,))
        next_objects = dict((next_commit, addition))
        base_head, next_head = base_commit[0][1], next_commit[0][1]
        base = proof_formats._build_artifact("update-pack", base_head, base_objects)
        successor = proof_formats._build_artifact(
            "update-pack", next_head, next_objects,
            base_head=base_head, base_objects=base_objects,
        )
        transfer = proof_formats._warm_transfer(base, successor)
        label = next(iter(transfer["payloads"]))

        with self.assertRaises(proof_formats.FormatUnavailable):
            proof_formats._reconstruct(
                "f" * 40, base_objects, base, successor, transfer["payloads"]
            )
        with self.assertRaises(proof_formats.FormatUnavailable):
            proof_formats._reconstruct(base_head, base_objects, base, successor, {})
        corrupt = dict(transfer["payloads"])
        corrupt[label] = corrupt[label][:-1] + bytes([corrupt[label][-1] ^ 1])
        with self.assertRaises(proof_formats.FormatUnavailable):
            proof_formats._reconstruct(
                base_head, base_objects, base, successor, corrupt
            )

    def test_frozen_source_and_prerequisite_reports_are_hash_bound(self):
        """Break caught: measurement silently switches history or prerequisite evidence."""
        from experiments.growth import proof_formats

        source, prerequisite = proof_formats._load_inputs(
            proof_formats.SOURCE_REPORT, proof_formats.PREREQUISITE_REPORT
        )
        self.assertEqual(source["digest"], proof_formats.SOURCE_REPORT_SHA256)
        self.assertEqual(prerequisite["digest"], proof_formats.PREREQUISITE_SHA256)
        self.assertEqual(len(source["value"]["imports"]), 100)
        self.assertEqual(prerequisite["value"]["status"], "success")
        with tempfile.TemporaryDirectory(prefix="proof-formats-input-") as temporary:
            changed = Path(temporary) / "changed.json"
            changed.write_text(json.dumps(source["value"]), encoding="utf-8")
            with self.assertRaises(ValueError):
                proof_formats._load_inputs(changed, proof_formats.PREREQUISITE_REPORT)

    def test_failed_result_is_serializable_sanitized_and_exclusive(self):
        """Break caught: failed evidence is discarded, leaks details, or overwrites a run."""
        from experiments.growth import proof_formats

        result = proof_formats.run_comparison(source_report=Path("/missing/private.json"))
        self.assertEqual(result["status"], "failure")
        encoded = json.dumps(result)
        self.assertNotIn("/missing/private", encoded)
        with tempfile.TemporaryDirectory(prefix="proof-formats-output-") as temporary:
            output = Path(temporary) / "result.json"
            proof_formats._write_json_output(output, result)
            with self.assertRaises(FileExistsError):
                proof_formats._write_json_output(output, result)

    def test_later_candidate_failure_preserves_completed_measurements(self):
        """Break caught: a later format failure erases valid base and earlier-format evidence."""
        from experiments.growth import proof_formats

        source = {"digest": "1" * 64, "value": {"imports": []}}
        prerequisite = {
            "digest": "2" * 64,
            "value": {
                "environment": {"source_revision": "3" * 40},
                "status": "success",
            },
        }
        expected_measurement = {
            "base": {"records": 500},
            "successor": {"records": 510},
            "formats": [
                {"family": "bucket-64", "status": "success"},
                {
                    "family": "bucket-256",
                    "status": "failure",
                    "failure_kind": "RuntimeError",
                },
            ],
        }

        def fail_later(_source, _budget, _alarm, measurement):
            measurement.update(json.loads(json.dumps(expected_measurement)))
            raise RuntimeError("private candidate detail /home/person/secret")

        with patch.object(proof_formats, "_load_inputs", return_value=(source, prerequisite)), \
                patch.object(proof_formats, "_run_fixture_comparison", side_effect=fail_later):
            result = proof_formats.run_comparison()

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_kind"], "RuntimeError")
        self.assertEqual(result["measurement"], expected_measurement)
        self.assertNotIn("private candidate detail", json.dumps(result))
        self.assertNotIn("/home/person", json.dumps(result))

    def test_candidate_loop_records_late_failure_but_propagates_stop_signals(self):
        """Break caught: candidate-loop failure either erases successes or captures a stop signal."""
        from experiments.growth import gates, proof_formats

        measurement = {"formats": []}
        outcomes = [
            {"family": "bucket-64"},
            RuntimeError("private candidate detail /home/person/secret"),
        ]
        with patch.object(proof_formats, "_measure_family", side_effect=outcomes):
            with self.assertRaises(RuntimeError):
                proof_formats._measure_candidates(
                    measurement, "base", {}, b"base-proof", "next", {},
                    b"next-proof", {"source": {"verified_at": "now"}}, object(),
                )
        self.assertEqual(measurement["formats"][0]["status"], "success")
        self.assertEqual(measurement["formats"][1]["family"], "bucket-256")
        self.assertEqual(measurement["formats"][1]["status"], "failure")
        self.assertEqual(measurement["formats"][1]["failure_kind"], "RuntimeError")
        encoded = json.dumps(measurement)
        self.assertNotIn("private candidate detail", encoded)
        self.assertNotIn("/home/person", encoded)

        for error in (gates._DeadlineExpired("overall"), KeyboardInterrupt(), SystemExit()):
            stopped = {"formats": []}
            with self.subTest(error=type(error).__name__), \
                    patch.object(proof_formats, "_measure_family", side_effect=error), \
                    self.assertRaises(type(error)):
                proof_formats._measure_candidates(
                    stopped, "base", {}, b"base-proof", "next", {},
                    b"next-proof", {"source": {"verified_at": "now"}}, object(),
                )
            self.assertEqual(stopped["formats"], [])

    def test_successor_phase_deadline_covers_candidate_creation_and_propagates(self):
        """Break caught: expensive candidate creation runs before the 120-second phase guard."""
        from experiments.growth import gates, proof_formats

        active = False

        @contextmanager
        def phase(_budget, _alarm, _seconds):
            nonlocal active
            active = True
            try:
                yield
            finally:
                active = False

        class DeadlineFixture:
            successful_mutations = []

            def add_candidate(self, _number, _records):
                if not active:
                    raise AssertionError("candidate creation escaped phase deadline")
                raise gates._DeadlineExpired("phase")

        with patch.object(gates, "_phase_deadline", phase):
            with self.assertRaises(gates._DeadlineExpired):
                proof_formats._append_successors(
                    DeadlineFixture(), object(), [101], object(), object()
                )
        self.assertFalse(active)

    def test_small_native_fixture_appends_real_successors_and_replays_formats(self):
        """Break caught: format numbers come from fabricated maps instead of workflow proofs."""
        from experiments.growth import gates, proof_formats

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        budget = gates._DeadlineBudget(120)
        with gates._alarm_handler(budget) as alarm:
            result = proof_formats._run_fixture_comparison(source, budget, alarm)

        self.assertEqual((result["base"]["records"], result["base"]["receipts"]), (5, 5))
        self.assertEqual(
            (result["successor"]["records"], result["successor"]["receipts"]),
            (15, 15),
        )
        self.assertEqual(len(result["successor"]["imports"]), 2)
        self.assertEqual(result["fixture"]["contract_violations"], 0)
        self.assertEqual(
            [item["family"] for item in result["formats"]],
            [*proof_formats.FAMILIES, "existing-full-download-control"],
        )
        for item in result["formats"]:
            self.assertTrue(all(item["round_trip"].values()), item)


if __name__ == "__main__":
    unittest.main()

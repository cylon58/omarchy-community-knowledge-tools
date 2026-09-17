"""Exact-graph bounded comparison for authenticated batched object reads."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


class BatchedReadsExperimentTests(unittest.TestCase):
    def test_frozen_source_is_bound_by_saved_sha_before_reconstruction(self):
        """Break caught: candidate silently accepts a different historical graph report."""
        from experiments.growth import batched_reads

        source, digest = batched_reads._load_frozen_source(batched_reads.SOURCE_REPORT)
        self.assertEqual(digest, batched_reads.SOURCE_REPORT_SHA256)
        self.assertEqual(len(source["imports"]), 100)
        with tempfile.TemporaryDirectory(prefix="batched-source-test-") as temporary:
            changed = Path(temporary) / "changed.json"
            value = copy.deepcopy(source)
            value["imports"][0]["number"] = 2
            changed.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                batched_reads._load_frozen_source(changed)

    def test_small_exact_graph_uses_native_batches_and_matches_local_reference(self):
        """Break caught: comparison uses a semantic fake or compares only counts."""
        from experiments.growth import batched_reads, cold_postmortem, gates
        from omarchy_knowledge.coordinator import Policy

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        with tempfile.TemporaryDirectory(prefix="batched-candidate-test-") as temporary:
            with gates._NativeFixture(Path(temporary)) as fixture:
                reconstructed = cold_postmortem._reconstruct(fixture, source)
                result = batched_reads._cold_compare(
                    fixture, Policy("a" * 40, "b" * 40))

        self.assertEqual(reconstructed["mutation_heads_checked"], 2)
        self.assertEqual(result["outcome"], "success", result)
        self.assertEqual((result["records"], result["receipts"]), (5, 5))
        self.assertTrue(result["canonical_equal"])
        self.assertTrue(result["offline_proof_replay_equal"])
        self.assertGreater(result["graphql_calls"], 0)
        self.assertLessEqual(result["graphql_calls"], 192)
        self.assertLessEqual(result["graphql_points"], 192)
        self.assertGreater(result["object_visit_count"], 0)
        self.assertLessEqual(result["object_visit_count"],
                             result["reference_object_visit_count"])
        self.assertGreater(result["adverse_failure_records"], 0)

    def test_source_provenance_includes_new_codec_and_experiment(self):
        """Break caught: the measured decoder or candidate harness is absent from provenance."""
        from experiments.growth import batched_reads

        hashes = batched_reads._source_hashes()
        from experiments.growth import gates
        for path in ("experiments/growth/batched_reads.py",
                     "omarchy_knowledge/github_object_batch.py"):
            self.assertEqual(
                hashes[path],
                hashlib.sha256((batched_reads.ROOT / path).read_bytes()).hexdigest(),
            )
        self.assertIn("omarchy_knowledge/github_object_batch.py", gates.MEASURED_SOURCES)

    def test_output_is_exclusive_and_traceback_is_sanitized(self):
        """Break caught: measurement overwrites evidence or leaks paths/error text."""
        from experiments.growth import batched_reads

        with tempfile.TemporaryDirectory(prefix="batched-output-test-") as temporary:
            output = Path(temporary) / "candidate.json"
            batched_reads._write_json_output(output, {"status": "failure"})
            with self.assertRaises(FileExistsError):
                batched_reads._write_json_output(output, {"status": "success"})
        try:
            raise RuntimeError("private /home/person/secret")
        except RuntimeError as error:
            trace = batched_reads._sanitized_trace(error)
        encoded = json.dumps(trace)
        self.assertNotIn("/home/person", encoded)
        self.assertNotIn("private", encoded)


if __name__ == "__main__":
    unittest.main()

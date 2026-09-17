import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest


class ColdPostmortemTests(unittest.TestCase):
    def test_native_cold_propagates_deadlines_and_process_interrupts(self):
        """Break caught: the expected-failure collector swallows its enclosing stop signal."""
        from experiments.growth import cold_postmortem, gates
        from omarchy_knowledge.coordinator import Policy

        class InterruptingFixture:
            def __init__(self, error):
                self.error = error
                self.requests = []

            def connection_factory(self, _host, timeout=5):
                raise self.error

        policy = Policy("a" * 40, "b" * 40)
        for error in (gates._DeadlineExpired("overall"), KeyboardInterrupt(), SystemExit()):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                cold_postmortem._native_cold(InterruptingFixture(error), policy)

    def test_source_hashes_bind_direct_baseline_dependency(self):
        """Break caught: cohort or fixed-time changes are absent from provenance."""
        from experiments.growth import cold_postmortem

        path = "experiments/growth/baseline.py"
        expected = hashlib.sha256((cold_postmortem.ROOT / path).read_bytes()).hexdigest()

        self.assertEqual(cold_postmortem._source_hashes()[path], expected)

    def test_small_native_run_reconstructs_exact_heads_and_replays_full_proof(self):
        """Break caught: reconstruction builds a similar graph without identical commits."""
        from experiments.growth import cold_postmortem, gates
        from omarchy_knowledge.coordinator import Policy

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        with tempfile.TemporaryDirectory(prefix="cold-postmortem-test-") as temporary:
            with gates._NativeFixture(Path(temporary)) as fixture:
                reconstructed = cold_postmortem._reconstruct(fixture, source)
                replay = cold_postmortem._local_replay(
                    fixture, Policy("a" * 40, "b" * 40))

        self.assertEqual(reconstructed["mutation_heads_checked"], 2)
        self.assertEqual(reconstructed["data_head"],
                         source["imports"][-1]["mutations"][-1]["result_head"])
        self.assertEqual(replay["outcome"], "success")
        self.assertEqual(replay["records"], 5)
        self.assertEqual(replay["receipts"], 5)
        self.assertEqual(replay["adverse_failure_records"], 1)
        self.assertTrue(replay["offline_proof_replay_equal"])

    def test_tampered_saved_mutation_head_aborts_exact_reconstruction(self):
        """Break caught: a recorded identity mismatch is logged and tolerated."""
        from experiments.growth import cold_postmortem, gates

        source = gates.run_gate("one-import")
        source["imports"][0]["mutations"][1]["result_head"] = "f" * 40
        with tempfile.TemporaryDirectory(prefix="cold-postmortem-test-") as temporary:
            with gates._NativeFixture(Path(temporary)) as fixture:
                with self.assertRaises(cold_postmortem.IdentityMismatch):
                    cold_postmortem._reconstruct(fixture, source)

    def test_source_report_rejects_malformed_and_unsupported_input(self):
        """Break caught: malformed or unknown reports reach fixture construction."""
        from experiments.growth import cold_postmortem

        with self.assertRaises(ValueError):
            cold_postmortem._parse_source_report(b'{"schema_version":1,')
        with self.assertRaises(ValueError):
            cold_postmortem._validate_source_report({"schema_version": 2})

    def test_counter_report_distinguishes_refused_513th_call_from_512_sent(self):
        """Break caught: an adapter attempt rejected before exchange is called sent traffic."""
        from experiments.growth import cold_postmortem, gates
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable

        with tempfile.TemporaryDirectory(prefix="cold-postmortem-test-") as temporary:
            with gates._NativeFixture(Path(temporary)) as fixture:
                adapter = GitHubRead(read_token=gates.SYNTHETIC_TOKEN,
                                     connection_factory=fixture.connection_factory)
                request_start = len(fixture.requests)
                started = time.monotonic()
                error = None
                for _ in range(513):
                    try:
                        adapter.repository()
                    except NativeUnavailable as caught:
                        error = caught
                        break
                metrics = cold_postmortem._native_failure_metrics(
                    adapter, fixture, request_start, started, error)

        self.assertEqual(metrics["outcome"], "failure")
        self.assertEqual(metrics["failure_kind"], "NativeUnavailable")
        self.assertEqual(metrics["adapter_calls_attempted"], 513)
        self.assertEqual(metrics["actual_emulated_requests_sent"], 512)
        self.assertEqual(metrics["refused_before_send"], 1)

    def test_traceback_is_structural_and_does_not_echo_paths_or_exception_text(self):
        """Break caught: diagnostic output leaks a local path or arbitrary exception text."""
        from experiments.growth import cold_postmortem

        try:
            raise RuntimeError("private text /home/person/secret")
        except RuntimeError as error:
            trace = cold_postmortem._sanitized_trace(error)

        encoded = json.dumps(trace)
        self.assertNotIn("/home/person", encoded)
        self.assertNotIn("private text", encoded)
        self.assertTrue(trace)
        self.assertEqual(set(trace[-1]), {"module", "function", "line"})

    def test_output_is_exclusive_and_rejects_symlink_target(self):
        """Break caught: a postmortem output silently overwrites or follows a link."""
        from experiments.growth import cold_postmortem

        with tempfile.TemporaryDirectory(prefix="cold-postmortem-output-") as temporary:
            parent = Path(temporary)
            output = parent / "report.json"
            cold_postmortem._write_json_output(output, {"schema_version": 1})
            with self.assertRaises(FileExistsError):
                cold_postmortem._write_json_output(output, {"schema_version": 1})
            linked = parent / "linked.json"
            linked.symlink_to(output)
            with self.assertRaises(FileExistsError):
                cold_postmortem._write_json_output(linked, {"schema_version": 1})

    def test_artifact_is_exclusive_and_manifest_marks_postmortem(self):
        """Break caught: retained proof looks like a passing gate or overwrites a run."""
        from experiments.growth import cold_postmortem

        with tempfile.TemporaryDirectory(prefix="cold-postmortem-artifact-") as temporary:
            output = Path(temporary) / "retained"
            manifest = cold_postmortem._export_postmortem_artifact(
                output, b"bounded-proof", source_report_sha256="1" * 64,
                data_head="2" * 40, source_sha256={"example.py": "3" * 64})
            names = sorted(path.name for path in output.iterdir())
            stored = json.loads((output / "manifest.json").read_text())

            with self.assertRaises(FileExistsError):
                cold_postmortem._export_postmortem_artifact(
                    output, b"other", source_report_sha256="1" * 64,
                    data_head="2" * 40, source_sha256={"example.py": "3" * 64})

        self.assertEqual(names, ["canonical-objects.bundle", "manifest.json"])
        self.assertEqual(stored, manifest)
        self.assertEqual(stored["purpose"], "cold-recovery-postmortem")
        self.assertEqual(stored["original_growth_gate_status"], "failure")
        self.assertFalse(stored["establishes_growth_gate_pass"])
        self.assertEqual(stored["source_report_sha256"], "1" * 64)
        self.assertEqual(stored["data_head"], "2" * 40)
        self.assertEqual(stored["proof"]["bytes"], len(b"bounded-proof"))


if __name__ == "__main__":
    unittest.main()

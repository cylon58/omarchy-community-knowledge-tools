"""Native-adapter synthetic growth gate contracts."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch


class GrowthGateTests(unittest.TestCase):
    def test_profiles_generate_the_predeclared_record_batches(self):
        """Break caught: a named gate silently measures a smaller/easier corpus."""
        from experiments.growth.gates import _profile_batches

        one = _profile_batches("one-import")
        ten = _profile_batches("ten-imports")
        distributed = _profile_batches("distributed-500x100")
        concentrated = _profile_batches("concentrated-100-reports")

        self.assertEqual((len(one), [len(batch) for batch in one]), (1, [5]))
        self.assertEqual((len(ten), sum(map(len, ten))), (10, 50))
        self.assertEqual((len(distributed), sum(map(len, distributed))), (100, 500))
        self.assertEqual((len(concentrated), [len(batch) for batch in concentrated]),
                         (11, [10] + [10] * 9 + [4]))
        records = [record for batch in concentrated for record in batch]
        self.assertEqual(sum(record["type"] == "report" for record in records), 100)
        self.assertEqual(sum(record["type"] == "case" for record in records), 1)
        self.assertEqual(sum(record["type"] == "change" for record in records), 1)
        self.assertEqual(sum(record["type"] == "event" for record in records), 2)
        resolution = next(record for record in records
                          if record["type"] == "event"
                          and record["payload"]["event_kind"] == "upstream-resolution")
        self.assertLessEqual(len(resolution["payload"]["supporting_reports"]), 24)
        reports = [record for record in records if record["type"] == "report"]
        self.assertEqual({report["payload"]["case_id"] for report in reports},
                         {records[0]["id"]})
        self.assertEqual({report["payload"]["change_id"] for report in reports},
                         {records[1]["id"]})

    def test_current_deadline_never_restores_a_longer_relative_timer(self):
        """Break caught: each phase reset extends the overall workload budget."""
        from experiments.growth.gates import _DeadlineBudget, _DeadlineExpired

        clock = iter((100.0, 100.0, 105.0, 111.0))
        budget = _DeadlineBudget(10, clock=lambda: next(clock))
        self.assertEqual(budget.current(120), 10.0)
        self.assertEqual(budget.current(120), 5.0)
        with self.assertRaises(_DeadlineExpired):
            budget.current(120)

    def test_overall_deadline_covers_initial_fixture_and_reports_incomplete(self):
        """Break caught: repository setup runs before the overall alarm is armed."""
        from experiments.growth import gates

        original = gates._NativeFixture

        def slow_fixture(root):
            time.sleep(.05)
            return original(root)

        profiles = {**gates.PROFILES, "one-import": {"overall_seconds": .01}}
        with patch.object(gates, "PROFILES", profiles), \
                patch.object(gates, "_NativeFixture", slow_fixture):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "incomplete", result)
        self.assertEqual(result["failure_stage"], "initial-fixture")
        self.assertEqual(result["deadline_scope"], "overall")

    def test_phase_deadline_is_failure_not_overall_incomplete(self):
        """Break caught: a 120-second phase miss is mislabeled overall exhaustion."""
        from experiments.growth import gates

        @contextmanager
        def phase_failure(_budget, _seconds=gates.PHASE_SECONDS):
            raise gates._DeadlineExpired("phase")
            yield

        with patch.object(gates, "_phase_deadline", phase_failure):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["failure_stage"], "planning")
        self.assertEqual(result["deadline_scope"], "phase")

    def test_gate_uses_service_plan_and_publish_wrappers_with_same_run_ids(self):
        """Break caught: direct publish omits production reconciliation semantics."""
        from experiments.growth import gates
        from omarchy_knowledge import service

        planned, published = [], []
        original_plan, original_publish = service.plan_run, service.publish_run

        def plan(*args, **kwargs):
            value = original_plan(*args, **kwargs)
            planned.append((kwargs, value))
            return value

        def publish(*args, **kwargs):
            published.append((kwargs, args[2]))
            return original_publish(*args, **kwargs)

        with patch.object(service, "plan_run", plan), patch.object(service, "publish_run", publish):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(len(planned), 1)
        self.assertEqual(len(published), 1)
        self.assertEqual(planned[0][0]["run_id"], published[0][0]["run_id"])
        self.assertEqual(planned[0][0]["run_attempt"], published[0][0]["run_attempt"])
        self.assertEqual(published[0][1]["status"]["status"], "planned")
        self.assertGreater(result["imports"][0]["plan_artifact_bytes"], 0)

    def test_one_import_uses_real_native_jobs_and_reports_proof_parity(self):
        """Break caught: the gate substitutes a semantic fake or shares job budgets."""
        from experiments.growth.gates import run_gate

        result = run_gate("one-import")

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["counts"]["imports_requested"], 1)
        self.assertEqual(result["counts"]["successful_publish_returns"], 1)
        self.assertEqual(result["counts"]["known_mutation_acceptances"], 1)
        self.assertEqual(result["counts"]["full_build_completions"], 1)
        self.assertEqual(result["counts"]["records"], 5)
        self.assertEqual(result["counts"]["receipts"], 5)
        self.assertEqual(result["network"]["real_network_requests"], 0)
        self.assertGreater(result["network"]["emulated_https_requests"], 0)
        self.assertEqual(result["network"]["contract_violations"], 0)
        jobs = result["imports"][0]["jobs"]
        self.assertEqual(set(jobs), {"planning", "publish", "build"})
        self.assertEqual([jobs[name]["seed_outcome"] for name in jobs],
                         [False, False, False])
        for job in jobs.values():
            self.assertLessEqual(job["calls"], 512)
            self.assertLessEqual(job["response_bytes"], 32 * 1024 * 1024)
            self.assertTrue(job["completed_return"])
        self.assertEqual([mutation["addition_count"] for mutation in
                          result["imports"][0]["mutations"]], [5, 5])
        self.assertTrue(result["imports"][0]["publish_completed_return"])
        self.assertTrue(result["imports"][0]["known_mutation_acceptance"])
        self.assertTrue(result["imports"][0]["full_build_completion"])
        self.assertTrue(result["proof"]["published_after_complete_build"])
        self.assertTrue(result["proof"]["cold_warm_canonical_equal"])
        self.assertTrue(result["proof"]["cold_warm_proof_equal"])
        self.assertTrue(result["proof"]["source_equal"])
        self.assertGreater(result["proof"]["adverse_failure_records"], 0)
        self.assertEqual(set(result["recovery"]), {"cold", "warm"})
        self.assertIsNone(result["recovery"]["cold"]["seed_outcome"])
        self.assertTrue(result["recovery"]["warm"]["seed_outcome"])
        self.assertLessEqual(result["recovery"]["cold"]["calls"], 512)
        self.assertLessEqual(result["recovery"]["warm"]["calls"], 512)
        self.assertTrue(result["search"]["cold_warm_equal"])
        self.assertEqual(result["search"]["cold_cache_scope"], "fresh-per-query")
        self.assertEqual([query["name"] for query in result["search"]["queries"]],
                         ["exact-case", "symptom-domain", "no-hit"])
        self.assertEqual(result["search"]["queries"][-1]["result_count"], 0)
        self.assertTrue(result["search"]["queries"][0]["adverse_evidence_visible"])
        self.assertLess(result["search"]["worst_warm_seconds"], 1.0)

    def test_searches_use_one_fixed_status_time_without_freezing_elapsed_clocks(self):
        """Break caught: six search status reads cross wall-clock second boundaries."""
        from experiments.growth import gates
        from omarchy_knowledge import discovery

        status_times = []
        monotonic_reads = []
        original_status = discovery.snapshot_status
        original_monotonic = gates.time.monotonic

        def record_status(snapshot, **kwargs):
            status_times.append(kwargs.get("now"))
            return original_status(snapshot, **kwargs)

        def record_monotonic():
            value = original_monotonic()
            monotonic_reads.append(value)
            return value

        with patch.object(discovery, "snapshot_status", record_status), \
                patch.object(gates.time, "monotonic", record_monotonic):
            result = gates.run_gate("one-import")

        expected = datetime(2026, 9, 16, 16, tzinfo=timezone.utc)
        self.assertEqual(status_times, [expected] * 6)
        self.assertEqual(result["status"], "success", result)
        self.assertGreater(len(set(monotonic_reads)), 1)

    def test_search_mismatch_fails_and_preserves_per_query_evidence(self):
        """Break caught: a result mismatch raises before observations reach the report."""
        from experiments.growth import gates
        from omarchy_knowledge import discovery

        original_search = discovery.search_snapshot
        calls = 0

        def unequal_search(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original_search(*args, **kwargs)
            if calls == 2:
                result = deepcopy(result)
                result["results"][0]["case_evidence"]["report_count"] += 1
            return result

        with patch.object(discovery, "search_snapshot", unequal_search):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["failure_stage"], "recovery-distribution")
        self.assertEqual(result["failure_kind"], "RuntimeError")
        self.assertFalse(result["search"]["cold_warm_equal"])
        self.assertEqual(len(result["search"]["queries"]), 3)
        self.assertFalse(result["search"]["queries"][0]["cold_warm_equal"])
        self.assertTrue(result["search"]["queries"][0]["adverse_evidence_visible"])
        self.assertGreater(result["search"]["queries"][0]["result_count"], 0)

    def test_interruption_after_mutation_preserves_known_acceptance_without_inference(self):
        """Break caught: interrupted return erases mutation facts or invents counts."""
        from experiments.growth import gates
        from omarchy_knowledge import service

        original = service.publish_run

        def interrupt_after_mutation(*args, **kwargs):
            value = original(*args, **kwargs)
            if value["status"] == "accepted":
                raise gates._DeadlineExpired("phase")
            return value

        with patch.object(service, "publish_run", interrupt_after_mutation):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["failure_stage"], "publish")
        self.assertEqual(result["counts"]["successful_publish_returns"], 0)
        self.assertEqual(result["counts"]["known_mutation_acceptances"], 1)
        self.assertEqual(result["counts"]["full_build_completions"], 0)
        self.assertIsNone(result["counts"]["records"])
        self.assertIsNone(result["counts"]["receipts"])
        self.assertEqual([row["addition_count"] for row in result["imports"][0]["mutations"]],
                         [5, 5])

    def test_later_failure_preserves_prior_successful_proof_publication(self):
        """Break caught: a later failure rewrites a factual prior publication to false."""
        from experiments.growth import gates
        from omarchy_knowledge import distribution

        batches = gates._profile_batches("ten-imports")[:2]
        original = distribution.build_site
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("second build failed")
            return original(*args, **kwargs)

        with patch.object(gates, "_profile_batches", return_value=batches), \
                patch.object(distribution, "build_site", fail_second):
            result = gates.run_gate("ten-imports")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["counts"]["successful_publish_returns"], 2)
        self.assertEqual(result["counts"]["known_mutation_acceptances"], 2)
        self.assertEqual(result["counts"]["full_build_completions"], 1)
        self.assertEqual(result["proof"]["pages_publications"], 1)
        self.assertTrue(result["proof"]["published_after_complete_build"])
        self.assertEqual(result["proof"]["published_imports"], [1])

    def test_pages_publication_state_changes_atomically(self):
        """Break caught: bundle replacement can precede its publication history."""
        from experiments.growth.gates import _NativeFixture

        with tempfile.TemporaryDirectory(prefix="native-gate-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.publish_pages(b"bounded-proof", 7)
                state = fixture.pages_state

                self.assertEqual(state.bundle, b"bounded-proof")
                self.assertEqual(state.published_imports, (7,))
                self.assertEqual(fixture.pages_bundle, state.bundle)
                self.assertEqual(fixture.pages_publications,
                                 len(state.published_imports))

    def test_interruption_immediately_after_pages_state_change_recovers_facts(self):
        """Break caught: caller bookkeeping loses an already-published proof."""
        from experiments.growth import gates

        original = gates._NativeFixture.publish_pages

        def interrupt_after_state_change(fixture, proof, *identity):
            original(fixture, proof, *identity)
            raise gates._DeadlineExpired("phase")

        with patch.object(gates._NativeFixture, "publish_pages",
                          interrupt_after_state_change):
            result = gates.run_gate("one-import")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["failure_stage"], "build")
        self.assertEqual(result["counts"]["successful_publish_returns"], 1)
        self.assertEqual(result["counts"]["known_mutation_acceptances"], 1)
        self.assertEqual(result["counts"]["full_build_completions"], 1)
        self.assertTrue(result["imports"][0]["full_build_completion"])
        self.assertEqual(result["proof"]["pages_publications"], 1)
        self.assertEqual(result["proof"]["published_imports"], [1])
        self.assertTrue(result["proof"]["published_after_complete_build"])

    def test_strict_https_boundary_rejects_wrong_host_and_token_on_pages(self):
        """Break caught: the fixture accepts routes production must never issue."""
        from experiments.growth.gates import _NativeFixture

        with tempfile.TemporaryDirectory(prefix="native-gate-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                with self.assertRaises(AssertionError):
                    fixture.connection_factory("example.invalid", timeout=5)
                connection = fixture.connection_factory("cylon58.github.io", timeout=5)
                with self.assertRaises(AssertionError):
                    connection.request(
                        "GET", "/omarchy-community-knowledge/canonical-objects.bundle",
                        headers={"Authorization": "Bearer synthetic-gate-token"})

    def test_writer_rejects_eleven_and_mixed_addition_lanes_before_https(self):
        """Break caught: the harness evades the production mutation caps."""
        from experiments.growth.gates import _NativeFixture
        from omarchy_knowledge.github_native import GitHubWriter, NativeUnavailable

        with tempfile.TemporaryDirectory(prefix="native-gate-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                writer = GitHubWriter("synthetic-gate-token",
                                      connection_factory=fixture.connection_factory)
                receipt = "provenance/ingestion/00000000-0000-4000-8000-000000000000.json"
                record = "records/cases/00000000-0000-4000-8000-000000000001.json"
                eleven = {
                    f"provenance/ingestion/00000000-0000-4000-8000-{index:012d}.json": b"{}"
                    for index in range(11)
                }
                with self.assertRaises(NativeUnavailable):
                    writer.create_commit("b" * 40, eleven,
                                         "Record source-bound ingestion receipt")
                with self.assertRaises(NativeUnavailable):
                    writer.create_commit("b" * 40, {receipt: b"{}", record: b"{}"},
                                         "Record source-bound ingestion receipt")
                self.assertEqual(fixture.requests, [])

    def test_stale_expected_head_does_not_mutate_or_publish_proof(self):
        """Break caught: the fake GraphQL endpoint ignores expected-head CAS."""
        from experiments.growth.gates import _NativeFixture, _profile_batches
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        from omarchy_knowledge.github_native import GitHubRead, GitHubWriter

        with tempfile.TemporaryDirectory(prefix="native-gate-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.add_candidate(1, _profile_batches("one-import")[0])
                policy = Policy("a" * 40, "b" * 40)
                reader = GitHubRead(read_token="synthetic-gate-token",
                                    connection_factory=fixture.connection_factory)
                reader.seed_canonical()
                plan = prepare(reader, policy, 1)
                fixture.advance_main_for_test()
                stale_main = fixture.main
                old_pages = fixture.pages_bundle
                writer = GitHubWriter("synthetic-gate-token",
                                      connection_factory=fixture.connection_factory)
                writer.seed_canonical()
                result = publish(writer, policy, plan)

                self.assertEqual(result.status, "retry")
                self.assertEqual(fixture.main, stale_main)
                self.assertIs(fixture.pages_bundle, old_pages)
                self.assertEqual(fixture.successful_mutations, [])

    def test_mixed_valid_and_invalid_receipts_fail_closed(self):
        """Break caught: one valid receipt lane masks malformed canonical evidence."""
        from experiments.growth.gates import (_NativeFixture, SYNTHETIC_TOKEN,
                                               _profile_batches)
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        from omarchy_knowledge.github_native import GitHubRead, GitHubWriter

        with tempfile.TemporaryDirectory(prefix="native-gate-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.add_candidate(1, _profile_batches("one-import")[0])
                policy = Policy("a" * 40, "b" * 40)
                reader = GitHubRead(read_token=SYNTHETIC_TOKEN,
                                    connection_factory=fixture.connection_factory)
                plan = prepare(reader, policy, 1)
                writer = GitHubWriter(SYNTHETIC_TOKEN,
                                      connection_factory=fixture.connection_factory)
                self.assertEqual(publish(writer, policy, plan).status, "accepted")
                mutations = len(fixture.successful_mutations)
                invalid = "provenance/ingestion/ffffffff-ffff-4fff-8fff-ffffffffffff.json"
                fixture.main = fixture.repository.commit(
                    {invalid: b"{}\n"}, (fixture.main,), "Synthetic invalid receipt fixture")
                checker = GitHubWriter(SYNTHETIC_TOKEN,
                                       connection_factory=fixture.connection_factory)

                self.assertEqual(reconcile(checker, policy).status, "retry")
                self.assertEqual(len(fixture.successful_mutations), mutations)

    def test_failed_build_retains_preceding_pages_proof(self):
        """Break caught: a newly exported proof is published before the site succeeds."""
        from experiments.growth.gates import run_gate

        with patch("omarchy_knowledge.distribution.build_site",
                   side_effect=ValueError("synthetic build failure")):
            result = run_gate("one-import")

        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["failure_stage"], "build")
        self.assertFalse(result["proof"]["published_after_complete_build"])
        self.assertEqual(result["proof"]["pages_publications"], 0)

    def test_optional_artifact_exports_only_the_bounded_final_proof(self):
        """Break caught: retaining a proof leaks corpus JSON or changes gate counts."""
        from experiments.growth.gates import run_gate

        with tempfile.TemporaryDirectory(prefix="native-gate-artifact-") as temporary:
            output = Path(temporary) / "retained-proof"
            result = run_gate("one-import", artifact_output=output)
            names = sorted(path.name for path in output.iterdir())
            manifest = json.loads((output / "manifest.json").read_text())
            bundle = (output / "canonical-objects.bundle").read_bytes()

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["counts"]["records"], 5)
        self.assertEqual(names, ["canonical-objects.bundle", "manifest.json"])
        self.assertEqual(manifest["profile"], "one-import")
        self.assertEqual(manifest["proof"]["bytes"], len(bundle))
        self.assertEqual(manifest["source_sha256"], result["environment"]["source_sha256"])
        self.assertNotIn(str(output), json.dumps(result))
        self.assertTrue(result["artifact"]["exported"])
        self.assertGreaterEqual(result["artifact"]["export_seconds"], 0)

    def test_artifact_export_never_follows_or_overwrites_target(self):
        """Break caught: an opt-in local artifact replaces an existing path."""
        from experiments.growth.gates import _export_artifact

        with tempfile.TemporaryDirectory(prefix="native-gate-artifact-") as temporary:
            root = Path(temporary)
            existing = root / "existing"
            existing.mkdir()
            marker = existing / "marker"
            marker.write_text("keep")
            with self.assertRaises(FileExistsError):
                _export_artifact(existing, b"proof", {"profile": "one-import"})
            link = root / "link"
            os.symlink(existing, link)
            with self.assertRaises(FileExistsError):
                _export_artifact(link, b"proof", {"profile": "one-import"})
            self.assertEqual(marker.read_text(), "keep")

    def test_cli_output_is_exclusive_and_never_follows_symlinks(self):
        """Break caught: --output truncates an existing file or follows a symlink."""
        from experiments.growth import gates

        result = {"status": "success", "value": 1}
        with tempfile.TemporaryDirectory(prefix="native-gate-output-") as temporary:
            root = Path(temporary)
            output = root / "report.json"
            with patch.object(gates, "run_gate", return_value=result), redirect_stdout(io.StringIO()):
                self.assertEqual(gates.main(["one-import", "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text()), result)
            output.write_text("keep")
            run = patch.object(gates, "run_gate", side_effect=AssertionError("must reject first"))
            with run, self.assertRaises(FileExistsError):
                gates.main(["one-import", "--output", str(output)])
            target = root / "target"
            target.write_text("target")
            link = root / "linked-report"
            os.symlink(target, link)
            with patch.object(gates, "run_gate", side_effect=AssertionError("must reject first")), \
                    self.assertRaises(FileExistsError):
                gates.main(["one-import", "--output", str(link)])
            self.assertEqual(target.read_text(), "target")


if __name__ == "__main__":
    unittest.main()

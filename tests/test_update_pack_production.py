"""Actual production publisher/client update-pack evidence harness."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class UpdatePackProductionTests(unittest.TestCase):
    def test_actual_saved_failed_source_is_hash_bound_and_accepted(self):
        """Break caught: harness rejects the reviewed completed 500-import graph."""
        from experiments.growth import update_pack_production

        source = update_pack_production._load_source()
        self.assertEqual(source["digest"],
                         update_pack_production.SOURCE_REPORT_SHA256)
        self.assertEqual(source["value"]["status"], "failure")
        self.assertEqual(source["value"]["counts"]["imports_completed"], 100)
        self.assertEqual(len(source["value"]["imports"]), 100)

    def test_measured_sources_include_every_new_production_participant(self):
        """Break caught: result claims hash binding while omitting deployed code."""
        from experiments.growth import update_pack_production

        required = {
            "omarchy_knowledge/canonical.py",
            "omarchy_knowledge/distribution.py",
            "omarchy_knowledge/github_native.py",
            "omarchy_knowledge/object_bundle.py",
            "omarchy_knowledge/proof_cache.py",
            "omarchy_knowledge/service.py",
            "omarchy_knowledge/update_pack.py",
        }
        hashes = update_pack_production._source_hashes()
        self.assertTrue(required <= set(hashes))
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))

    def test_failure_retains_partial_results_and_only_exception_class(self):
        """Break caught: late failure erases evidence or leaks machine details."""
        from experiments.growth import update_pack_production

        source = {
            "digest": "1" * 64,
            "value": {"status": "success", "imports": [{"record_count": 5}]},
        }

        def fail(_source, _budget, _alarm, measurement, **_kwargs):
            measurement["base"] = {"records": 5}
            raise RuntimeError("private /home/person/value")

        with patch.object(update_pack_production, "_load_source", return_value=source), \
                patch.object(update_pack_production, "_run_fixture_measurement",
                             side_effect=fail):
            result = update_pack_production.run_measurement()

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_kind"], "RuntimeError")
        self.assertEqual(result["measurement"]["base"], {"records": 5})
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("/home/person", json.dumps(result))

    def test_deadline_scope_and_active_phase_survive_failure(self):
        """Break caught: phase timeout is mislabeled overall-incomplete or loses stage."""
        from experiments.growth import gates, update_pack_production

        source = {
            "digest": "1" * 64,
            "value": {"status": "failure", "imports": [{"record_count": 5}]},
        }

        def expire(_source, _budget, _alarm, measurement, **_kwargs):
            measurement["active_phase"] = "matching-client"
            raise gates._DeadlineExpired("phase")

        with patch.object(update_pack_production, "_load_source", return_value=source), \
                patch.object(update_pack_production, "_run_fixture_measurement",
                             side_effect=expire):
            result = update_pack_production.run_measurement()

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_kind"], "_DeadlineExpired")
        self.assertEqual(result["failure_stage"], "matching-client")
        self.assertEqual(result["deadline_scope"], "phase")

    def test_cli_rejects_existing_output_before_measurement(self):
        """Break caught: evidence output is overwritten or measured before validation."""
        from experiments.growth import update_pack_production

        with tempfile.TemporaryDirectory(prefix="update-production-output-") as temporary:
            output = Path(temporary) / "result.json"
            output.write_text("preserve\n")
            with patch.object(
                update_pack_production, "run_measurement",
                side_effect=AssertionError("must not run"),
            ), self.assertRaises(FileExistsError):
                update_pack_production.main(["--output", str(output)])
            self.assertEqual(output.read_text(), "preserve\n")

    def test_small_fixture_uses_real_publisher_sync_and_cache_paths(self):
        """Break caught: benchmark measures prototype assembly instead of real clients."""
        from experiments.growth import gates, update_pack_production

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        budget = gates._DeadlineBudget(300)
        measurement = {}
        with gates._alarm_handler(budget) as alarm:
            update_pack_production._run_fixture_measurement(
                source, budget, alarm, measurement,
            )

        self.assertEqual(measurement["base"]["records"], 5)
        self.assertEqual(measurement["target"]["records"], 15)
        self.assertEqual(measurement["history"]["imports"], 2)
        self.assertEqual(measurement["history"]["contributions_in_final_pr"], 2)
        self.assertEqual(measurement["publication"]["update_artifacts"], [
            "canonical-update.json", "canonical-update.bundle",
        ])
        matching = measurement["clients"]["matching_base"]
        self.assertEqual(matching["transport"]["identity"]["requests"], 2)
        self.assertEqual(matching["transport"]["api_objects"]["requests"], 0)
        self.assertEqual(matching["transport"]["proof"]["requests"], 2)
        self.assertEqual(matching["transport"]["proof"]["artifacts"], [
            "canonical-update.json", "canonical-update.bundle",
        ])
        self.assertEqual(
            matching["transport"]["proof"]["response_bytes"],
            measurement["publication"]["manifest_bytes"]
            + measurement["publication"]["pack_bytes"],
        )
        self.assertTrue(all(matching["parity"].values()), matching)
        same = measurement["clients"]["same_head"]
        self.assertEqual(same["transport"]["identity"]["requests"], 2)
        self.assertEqual(same["transport"]["proof"]["requests"], 0)
        wrong = measurement["clients"]["wrong_base"]
        self.assertEqual(wrong["transport"]["proof"]["artifacts"], [
            "canonical-update.json", "canonical-objects.bundle",
        ])
        self.assertTrue(wrong["full_fallback"])
        self.assertTrue(all(wrong["parity"].values()), wrong)
        self.assertEqual(measurement["fixture"]["contract_violations"], 0)

    def test_small_skipped_intermediate_publication_uses_full_fallback(self):
        """Break caught: a direct pack is incorrectly treated as a patch chain."""
        from experiments.growth import gates, update_pack_production

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        budget = gates._DeadlineBudget(300)
        with gates._alarm_handler(budget) as alarm:
            result = update_pack_production._run_skipped_intermediate_fixture(
                source, budget, alarm,
            )

        self.assertEqual(result["base_records"], 5)
        self.assertEqual(result["intermediate_records"], 10)
        self.assertEqual(result["target_records"], 15)
        self.assertEqual(result["publications"], 2)
        self.assertNotEqual(result["base_head"], result["intermediate_head"])
        self.assertNotEqual(result["intermediate_head"], result["target_head"])
        self.assertEqual(
            result["retained_pack_base_head"], result["intermediate_head"],
        )
        self.assertNotEqual(result["retained_pack_base_head"], result["base_head"])
        self.assertEqual(result["transport"]["proof"]["artifacts"], [
            "canonical-update.json", "canonical-objects.bundle",
        ])
        self.assertTrue(result["full_fallback"])
        self.assertTrue(all(result["parity"].values()), result)
        self.assertEqual(result["fixture"]["contract_violations"], 0)
        self.assertEqual(result["fixture"]["pages_publications"], 3)

    def test_small_late_failure_keeps_completed_base_publication_and_client(self):
        """Break caught: a later scenario failure erases completed actual-client facts."""
        from experiments.growth import gates, update_pack_production

        source = gates.run_gate("one-import")
        measurement = {}
        budget = gates._DeadlineBudget(300)

        def fail_after_matching(phase, _measurement):
            if phase == "matching-client-recorded":
                raise RuntimeError("late private detail")

        with gates._alarm_handler(budget) as alarm, self.assertRaises(RuntimeError):
            update_pack_production._run_fixture_measurement(
                source, budget, alarm, measurement,
                checkpoint=fail_after_matching,
            )

        self.assertEqual(measurement["active_phase"], "matching-client")
        self.assertIn("base", measurement)
        self.assertIn("publication", measurement)
        self.assertIn("matching_base", measurement["clients"])
        self.assertIn("fixture", measurement)

    def test_small_build_failure_keeps_real_publish_return_and_mutation_audit(self):
        """Break caught: post-acceptance build failure erases observed publication facts."""
        from experiments.growth import gates, update_pack_production

        source = gates.run_gate("one-import")
        measurement = {}
        budget = gates._DeadlineBudget(300)
        from omarchy_knowledge import service
        original_build = service._publisher_build
        build_calls = 0

        def fail_after_bootstrap(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 2:
                raise RuntimeError("synthetic build stop")
            return original_build(*args, **kwargs)

        with gates._alarm_handler(budget) as alarm, \
                patch("omarchy_knowledge.service._publisher_build",
                      side_effect=fail_after_bootstrap), \
                self.assertRaises(RuntimeError):
            update_pack_production._run_fixture_measurement(
                source, budget, alarm, measurement,
            )

        publication = measurement["publication"]
        self.assertTrue(publication["publish_completed_return"])
        self.assertEqual(set(publication["jobs"]), {"planning", "publish", "build"})
        self.assertEqual(len(publication["mutation_audit"]), 2)
        self.assertTrue(publication["known_mutation_acceptance"])
        self.assertEqual(measurement["fixture"]["observed_main"],
                         publication["mutation_audit"][-1]["result_head"])
        self.assertEqual(measurement["fixture"]["observed_mutations"],
                         publication["mutation_audit"])


if __name__ == "__main__":
    unittest.main()

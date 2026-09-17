"""Focused tests for the bounded update-pack variation experiment."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@dataclass(frozen=True)
class Pages:
    value: str


def git_object(kind, raw):
    oid = hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    return (kind, oid), raw


class UpdatePackVariationTests(unittest.TestCase):
    def test_scenarios_are_fixed_and_restore_only_mutable_fixture_baseline(self):
        """Break caught: scenarios inherit prior heads or reset cumulative limits."""
        from experiments.growth import update_pack_variants

        class Fixture:
            main = "base"
            pages_state = Pages("base-pages")
            requests = ["base-request"]

        fixture = Fixture()
        baseline = {
            "main": fixture.main,
            "pages_state": fixture.pages_state,
        }
        seen = []

        def run_scenario(active_fixture, numbers, _context):
            seen.append({
                "numbers": numbers,
                "main": active_fixture.main,
                "pages_state": active_fixture.pages_state,
                "request_count": len(active_fixture.requests),
            })
            active_fixture.main = "changed"
            active_fixture.pages_state = Pages("changed-pages")
            active_fixture.requests.append(numbers)
            return {"warm_to_full_ratio": numbers[0] / 1000}

        measurement = {"scenarios": []}
        update_pack_variants._measure_scenarios(
            fixture, baseline, {}, measurement, run_scenario=run_scenario,
        )

        self.assertEqual(
            update_pack_variants.SCENARIOS,
            ((201, 202), (301, 302), (401, 402)),
        )
        self.assertEqual(
            [(row["main"], row["pages_state"]) for row in seen],
            [("base", Pages("base-pages"))] * 3,
        )
        self.assertEqual([row["request_count"] for row in seen], [1, 2, 3])
        self.assertEqual(
            [row["numbers"] for row in seen], list(update_pack_variants.SCENARIOS)
        )

    def test_later_failure_preserves_completed_scenario_and_sanitized_accounting(self):
        """Break caught: a late scenario erases results or leaks exception details."""
        from experiments.growth import update_pack_variants

        class Fixture:
            main = "base"
            pages_state = Pages("base-pages")

        calls = 0

        def run_scenario(_fixture, _numbers, _context):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("private detail /home/person/secret")
            return {"warm_to_full_ratio": 0.06}

        measurement = {"scenarios": []}
        with self.assertRaises(RuntimeError):
            update_pack_variants._measure_scenarios(
                Fixture(), {"main": "base", "pages_state": Pages("base-pages")},
                {}, measurement, run_scenario=run_scenario,
            )

        self.assertEqual(
            [(item["numbers"], item["status"]) for item in measurement["scenarios"]],
            [([201, 202], "success"), ([301, 302], "failure")],
        )
        self.assertEqual(
            measurement["warm_to_full_ratio_range"],
            {"completed_scenarios": 1, "minimum": 0.06, "maximum": 0.06},
        )
        encoded = json.dumps(measurement)
        self.assertNotIn("private detail", encoded)
        self.assertNotIn("/home/person", encoded)

    def test_update_pack_measurement_rejects_wrong_base_through_reviewed_codec(self):
        """Break caught: the variation bypasses the codec's base-head binding."""
        from experiments.growth import proof_formats, update_pack_variants

        base_commit = git_object("commit", b"tree " + b"0" * 40 + b"\n\nbase\n")
        next_commit = git_object("commit", b"tree " + b"1" * 40 + b"\n\nnext\n")
        base_objects = dict((base_commit,))
        next_objects = dict((next_commit,))

        with self.assertRaises(proof_formats.FormatUnavailable):
            update_pack_variants._measure_update_pack(
                "f" * 40,
                base_objects,
                b"base-proof",
                next_commit[0][1],
                next_objects,
                b"next-proof",
                {"source": {"verified_at": "2026-09-16T12:00:00Z"}},
                object(),
            )

    def test_cli_preserves_existing_output_without_running_measurement(self):
        """Break caught: the CLI overwrites evidence or runs before target validation."""
        from experiments.growth import update_pack_variants

        with tempfile.TemporaryDirectory(prefix="update-pack-variants-output-") as root:
            output = Path(root) / "result.json"
            original = b"preserved-result\n"
            output.write_bytes(original)
            with patch.object(
                update_pack_variants,
                "run_variation",
                side_effect=AssertionError("measurement must not start"),
            ), self.assertRaises(FileExistsError):
                update_pack_variants.main(["--output", str(output)])
            self.assertEqual(output.read_bytes(), original)

    def test_saved_first_measurement_and_base_inputs_are_hash_bound(self):
        """Break caught: the follow-up silently changes its history or prerequisite."""
        from experiments.growth import update_pack_variants

        source, prerequisite, first = update_pack_variants._load_inputs(
            update_pack_variants.SOURCE_REPORT,
            update_pack_variants.PREREQUISITE_REPORT,
            update_pack_variants.FIRST_RESULT,
        )

        self.assertEqual(source["digest"], update_pack_variants.SOURCE_REPORT_SHA256)
        self.assertEqual(
            prerequisite["digest"], update_pack_variants.PREREQUISITE_REPORT_SHA256
        )
        self.assertEqual(first["digest"], update_pack_variants.FIRST_RESULT_SHA256)
        self.assertEqual(first["value"]["status"], "success")
        update_pack = next(
            item for item in first["value"]["measurement"]["formats"]
            if item["family"] == "update-pack"
        )
        self.assertEqual(update_pack["warm_to_full_ratio"], 0.054835)

    def test_small_fixture_runs_all_real_successors_from_one_base(self):
        """Break caught: variation numbers come from fabricated or chained proofs."""
        from experiments.growth import gates, update_pack_variants

        source = gates.run_gate("one-import")
        self.assertEqual(source["status"], "success", source)
        budget = gates._DeadlineBudget(300)
        with gates._alarm_handler(budget) as alarm:
            result = update_pack_variants._run_fixture_variants(
                source, budget, alarm
            )

        self.assertEqual((result["base"]["records"], result["base"]["receipts"]), (5, 5))
        self.assertEqual(
            [item["numbers"] for item in result["scenarios"]],
            [[201, 202], [301, 302], [401, 402]],
        )
        self.assertEqual([item["status"] for item in result["scenarios"]], ["success"] * 3)
        self.assertEqual([item["records"] for item in result["scenarios"]], [15] * 3)
        self.assertEqual([item["receipts"] for item in result["scenarios"]], [15] * 3)
        self.assertEqual(
            [item["base_head"] for item in result["scenarios"]],
            [result["base"]["head"]] * 3,
        )
        self.assertEqual(len({item["head"] for item in result["scenarios"]}), 3)
        for item in result["scenarios"]:
            self.assertEqual(item["family"], "update-pack")
            self.assertEqual(
                item["base_full_proof_bytes"], result["base"]["full_proof_bytes"]
            )
            self.assertTrue(all(item["round_trip"].values()), item)
        self.assertEqual(result["fixture"]["contract_violations"], 0)

    def test_top_level_failure_keeps_partial_scenarios_and_sanitizes_details(self):
        """Break caught: top-level failure replaces incremental scenario evidence."""
        from experiments.growth import update_pack_variants

        source = {"digest": "1" * 64, "value": {"imports": []}}
        prerequisite = {"digest": "2" * 64, "value": {"status": "success"}}
        first = {
            "digest": "3" * 64,
            "value": {
                "status": "success",
                "measurement": {
                    "formats": [
                        {"family": "update-pack", "warm_to_full_ratio": 0.054835}
                    ]
                },
            },
        }

        def fail_later(_source, _budget, _alarm, measurement):
            measurement["scenarios"] = [
                {"numbers": [201, 202], "status": "success", "warm_to_full_ratio": 0.06},
                {"numbers": [301, 302], "status": "failure", "failure_kind": "RuntimeError"},
            ]
            raise RuntimeError("private detail /home/person/secret")

        with patch.object(
            update_pack_variants,
            "_load_inputs",
            return_value=(source, prerequisite, first),
        ), patch.object(
            update_pack_variants, "_run_fixture_variants", side_effect=fail_later
        ):
            result = update_pack_variants.run_variation()

        self.assertEqual(result["status"], "failure")
        self.assertEqual(len(result["measurement"]["scenarios"]), 2)
        encoded = json.dumps(result)
        self.assertNotIn("private detail", encoded)
        self.assertNotIn("/home/person", encoded)


if __name__ == "__main__":
    unittest.main()

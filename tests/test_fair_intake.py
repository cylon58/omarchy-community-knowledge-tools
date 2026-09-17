"""Pure bounded fair-intake traversal contracts."""
from copy import deepcopy
import unittest


NOW = "2026-09-17T16:00:00Z"
MAX_INT = 2_147_483_647


def cursor(*, page=1, offset=0, after=None, cycle=0, completed=None):
    return {
        "version": 1,
        "page": page,
        "offset": offset,
        "after_pull_request": after,
        "cycle": cycle,
        "last_full_cycle_at": completed,
    }


def oid(number):
    return f"{number:040x}"


def opened(number):
    return {"pull_request": number, "state": "open", "head": oid(number)}


def closed(number):
    return {"pull_request": number, "state": "closed"}


class FairIntakeTests(unittest.TestCase):
    def test_cursor_requires_exact_schema_bounds_anchor_and_utc_timestamp(self):
        """Break caught: malformed or attacker-extended scheduling state is trusted."""
        from omarchy_knowledge.fair_intake import IntakeCursor

        valid = cursor(page=MAX_INT, offset=20, after=MAX_INT,
                       cycle=MAX_INT, completed="2026-09-17T16:00:00.123Z")
        parsed = IntakeCursor.from_mapping(valid)
        self.assertEqual(parsed.to_mapping(), valid)

        invalid = [
            {**cursor(), "extra": 1},
            {key: value for key, value in cursor().items() if key != "cycle"},
            cursor(page=True), cursor(page=0), cursor(page=MAX_INT + 1),
            cursor(offset=True), cursor(offset=-1), cursor(offset=21),
            cursor(offset=0, after=1), cursor(offset=1, after=None),
            cursor(offset=1, after=True), cursor(offset=1, after=MAX_INT + 1),
            cursor(cycle=True), cursor(cycle=-1), cursor(cycle=MAX_INT + 1),
            cursor(completed=False), cursor(completed="2026-09-17T16:00:00+00:00"),
            cursor(completed="not-a-time"),
            {**cursor(), "version": True}, {**cursor(), "version": 2},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                IntakeCursor.from_mapping(value)

    def test_two_plans_on_one_short_page_are_returned_on_successive_runs(self):
        """Break caught: stopping at one plan loses the following eligible item."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, ScanOutcome, StopReason, scan,
        )

        original = cursor()
        rows = [opened(11), opened(12), closed(13)]
        evaluated = []

        def evaluate(row):
            evaluated.append((row.pull_request, row.head))
            return CandidateEvaluation(EvaluationOutcome.PLAN,
                                       {"selected": row.pull_request})

        first = scan(original, imported_heads=frozenset(),
                     fetch_page=lambda page: rows, evaluate=evaluate, now=NOW)
        second = scan(first.proposed_after.to_mapping(), imported_heads=frozenset(),
                      fetch_page=lambda page: rows, evaluate=evaluate, now=NOW)

        self.assertEqual((first.outcome, first.stop_reason, first.plan),
                         (ScanOutcome.PLANNED, StopReason.PLAN, {"selected": 11}))
        self.assertEqual(first.before.to_mapping(), original)
        self.assertEqual(first.proposed_after.to_mapping(), cursor(offset=1, after=11))
        self.assertEqual(second.plan, {"selected": 12})
        self.assertEqual(second.proposed_after.to_mapping(), cursor(offset=2, after=12))
        self.assertEqual(evaluated, [(11, oid(11)), (12, oid(12))])
        self.assertEqual(original, cursor())

    def test_closed_imported_and_deterministic_results_advance_with_fixed_counters(self):
        """Break caught: stable non-candidates consume prepare slots or pin the cursor."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, ScanOutcome, StopReason, scan,
        )

        rows = [closed(1), opened(2), opened(3), opened(4)]
        outcomes = iter((EvaluationOutcome.REJECTED, EvaluationOutcome.NOT_READY))
        evaluated = []

        def evaluate(row):
            evaluated.append(row.pull_request)
            return CandidateEvaluation(next(outcomes))

        result = scan(cursor(), imported_heads={(2, oid(2))},
                      fetch_page=lambda page: rows, evaluate=evaluate, now=NOW)

        self.assertEqual((result.outcome, result.stop_reason),
                         (ScanOutcome.NO_ELIGIBLE, StopReason.CYCLE_COMPLETE))
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(cycle=1, completed=NOW))
        self.assertEqual(evaluated, [3, 4])
        self.assertEqual(
            result.counters,
            result.counters.__class__(page_fetches=1, rows_returned=4,
                                      rows_consumed=4, evaluations=2, closed=1,
                                      imported=1, rejected=1, not_ready=1,
                                      plans=0, cursor_drifts=0),
        )

    def test_fetch_and_row_limits_include_whole_anchor_and_restart_responses(self):
        """Break caught: re-anchors or drift restarts reset budgets or count only suffixes."""
        from omarchy_knowledge.fair_intake import ScanOutcome, StopReason, scan

        calls = []

        def fetch(page):
            calls.append(page)
            if len(calls) == 1:
                return [closed(number) for number in range(1, 21)]
            start = (page - 1) * 20 + 1
            return [closed(number) for number in range(start, start + 20)]

        result = scan(cursor(page=9, offset=1, after=999), imported_heads=set(),
                      fetch_page=fetch, evaluate=lambda row: self.fail("closed"), now=NOW)

        self.assertEqual(calls, [9, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual((result.outcome, result.stop_reason),
                         (ScanOutcome.NO_ELIGIBLE, StopReason.FETCH_LIMIT))
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(page=9, offset=20, after=180))
        self.assertEqual((result.counters.page_fetches, result.counters.rows_returned,
                          result.counters.rows_consumed, result.counters.cursor_drifts),
                         (10, 200, 180, 1))

    def test_evaluation_limit_stops_after_twentieth_consumed_candidate(self):
        """Break caught: the scanner prepares a twenty-first candidate in one run."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, StopReason, scan,
        )

        calls = []

        def evaluate(row):
            calls.append(row.pull_request)
            return CandidateEvaluation(EvaluationOutcome.REJECTED)

        result = scan(cursor(), imported_heads=set(),
                      fetch_page=lambda page: [opened(number) for number in range(1, 21)],
                      evaluate=evaluate, now=NOW)

        self.assertEqual(calls, list(range(1, 21)))
        self.assertEqual(result.stop_reason, StopReason.PREPARATION_LIMIT)
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(offset=20, after=20))
        self.assertEqual((result.counters.page_fetches, result.counters.rows_returned,
                          result.counters.evaluations), (1, 20, 20))

    def test_evaluation_limit_on_short_page_retains_after_item_cursor(self):
        """Break caught: a partially consumed short page is mistaken for cycle completion."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, StopReason, scan,
        )

        first = [opened(number) for number in range(1, 20)] + [closed(20)]
        second = [opened(21), closed(22)]
        result = scan(
            cursor(), imported_heads=set(),
            fetch_page=lambda page: first if page == 1 else second,
            evaluate=lambda row: CandidateEvaluation(EvaluationOutcome.REJECTED),
            now=NOW,
        )

        self.assertEqual(result.stop_reason, StopReason.PREPARATION_LIMIT)
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(page=2, offset=1, after=21))
        self.assertEqual((result.counters.page_fetches, result.counters.rows_returned,
                          result.counters.rows_consumed, result.counters.evaluations),
                         (2, 22, 21, 20))

    def test_drift_is_the_only_allowed_in_run_repetition_and_restarts_at_page_one(self):
        """Break caught: an anchor mismatch resumes locally or repeated content always fails."""
        from omarchy_knowledge.fair_intake import ScanOutcome, StopReason, scan

        repeated = [closed(number) for number in range(1, 20)]
        calls = []

        def fetch(page):
            calls.append(page)
            return repeated

        result = scan(cursor(page=7, offset=5, after=700), imported_heads=set(),
                      fetch_page=fetch, evaluate=lambda row: self.fail("closed"), now=NOW)

        self.assertEqual(calls, [7, 1])
        self.assertEqual((result.outcome, result.stop_reason),
                         (ScanOutcome.NO_ELIGIBLE, StopReason.CYCLE_COMPLETE))
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(cycle=1, completed=NOW))
        self.assertEqual((result.counters.page_fetches, result.counters.rows_returned,
                          result.counters.rows_consumed, result.counters.cursor_drifts),
                         (2, 38, 19, 1))

    def test_repeated_content_on_a_new_page_fails_without_transition(self):
        """Break caught: API page repetition silently skips later lifetime PRs."""
        from omarchy_knowledge.fair_intake import scan

        first = [closed(number) for number in range(1, 21)]
        with self.assertRaises(ValueError):
            scan(cursor(), imported_heads=set(), fetch_page=lambda page: first,
                 evaluate=lambda row: self.fail("closed"), now=NOW)

    def test_short_and_empty_tails_complete_cycle_only_after_consumption(self):
        """Break caught: a known tail fails to complete or completes before its rows are consumed."""
        from omarchy_knowledge.fair_intake import scan

        short = scan(cursor(page=3), imported_heads=set(),
                     fetch_page=lambda page: [closed(41), closed(42)],
                     evaluate=lambda row: self.fail("closed"), now=NOW)
        self.assertEqual(short.proposed_after.to_mapping(), cursor(cycle=1, completed=NOW))
        self.assertEqual(short.counters.rows_consumed, 2)

        full = [closed(number) for number in range(41, 61)]
        empty = scan(cursor(page=3, offset=20, after=60), imported_heads=set(),
                     fetch_page=lambda page: full if page == 3 else [],
                     evaluate=lambda row: self.fail("closed"), now=NOW)
        self.assertEqual(empty.proposed_after.to_mapping(), cursor(cycle=1, completed=NOW))
        self.assertEqual((empty.counters.page_fetches, empty.counters.rows_returned,
                          empty.counters.rows_consumed), (2, 20, 0))

    def test_plan_on_last_item_of_known_short_page_completes_cycle(self):
        """Break caught: a last-tail plan needlessly retains an already consumed page."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, ScanOutcome, StopReason, scan,
        )

        rows = [closed(1), opened(2)]
        result = scan(cursor(), imported_heads=set(), fetch_page=lambda page: rows,
                      evaluate=lambda row: CandidateEvaluation(
                          EvaluationOutcome.PLAN, {"pull_request": row.pull_request}), now=NOW)

        self.assertEqual((result.outcome, result.stop_reason, result.plan),
                         (ScanOutcome.PLANNED, StopReason.PLAN, {"pull_request": 2}))
        self.assertEqual(result.proposed_after.to_mapping(),
                         cursor(cycle=1, completed=NOW))

    def test_callback_uncertainty_and_malformed_evaluation_propagate_without_state(self):
        """Break caught: uncertainty is caught and converted into publishable progress."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, scan,
        )

        class Uncertain(RuntimeError):
            pass

        original = cursor()
        for fetch, evaluate, error in (
            (lambda page: (_ for _ in ()).throw(Uncertain("network")),
             lambda row: None, Uncertain),
            (lambda page: [opened(1)],
             lambda row: (_ for _ in ()).throw(Uncertain("object")), Uncertain),
            (lambda page: [opened(1)], lambda row: "rejected", ValueError),
            (lambda page: [opened(1)],
             lambda row: CandidateEvaluation(EvaluationOutcome.REJECTED, {"bad": True}),
             ValueError),
        ):
            with self.subTest(error=error), self.assertRaises(error):
                scan(original, imported_heads=set(), fetch_page=fetch,
                     evaluate=evaluate, now=NOW)
            self.assertEqual(original, cursor())

    def test_malformed_lists_rows_duplicates_and_import_facts_fail_closed(self):
        """Break caught: ambiguous identities or malformed open heads advance scheduling."""
        from omarchy_knowledge.fair_intake import scan

        bad_pages = [
            None, tuple(), [closed(number) for number in range(1, 22)],
            [closed(1), closed(1)],
            [{"pull_request": True, "state": "closed"}],
            [{"pull_request": 0, "state": "closed"}],
            [{"pull_request": 1, "state": "unknown"}],
            [{"pull_request": 1, "state": "closed", "head": oid(1)}],
            [{"pull_request": 1, "state": "open"}],
            [{"pull_request": 1, "state": "open", "head": "main"}],
            [{"pull_request": 1, "state": "open", "head": oid(1), "extra": 1}],
        ]
        for page in bad_pages:
            with self.subTest(page=page), self.assertRaises(ValueError):
                scan(cursor(), imported_heads=set(), fetch_page=lambda number, p=page: p,
                     evaluate=lambda row: self.fail("malformed"), now=NOW)

        for facts in ({(True, oid(1))}, {(1, "main")}, {(0, oid(1))}, [(1, oid(1))]):
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                scan(cursor(), imported_heads=facts, fetch_page=lambda page: [],
                     evaluate=lambda row: None, now=NOW)

    def test_page_and_cycle_bounds_never_wrap(self):
        """Break caught: advancing a maximum page or cycle wraps trusted scheduling state."""
        from omarchy_knowledge.fair_intake import scan

        full = [closed(number) for number in range(1, 21)]
        with self.assertRaises(ValueError):
            scan(cursor(page=MAX_INT, offset=20, after=20), imported_heads=set(),
                 fetch_page=lambda page: full, evaluate=lambda row: None, now=NOW)
        with self.assertRaises(ValueError):
            scan(cursor(cycle=MAX_INT), imported_heads=set(), fetch_page=lambda page: [],
                 evaluate=lambda row: None, now=NOW)

    def test_deterministic_thousand_pr_fixture_eventually_inspects_every_eligible_head(self):
        """Break caught: bounded resumptions starve a stable eligible lifetime position."""
        from omarchy_knowledge.fair_intake import (
            CandidateEvaluation, EvaluationOutcome, ScanOutcome, scan,
        )

        rows = []
        imported = set()
        expected_eligible = []
        for number in range(1, 1001):
            kind = number % 5
            rows.append(closed(number) if kind == 0 else opened(number))
            if kind == 1:
                imported.add((number, oid(number)))
            elif kind == 4:
                expected_eligible.append((number, oid(number)))

        evaluated = []

        def fetch(page):
            start = (page - 1) * 20
            return deepcopy(rows[start:start + 20])

        def evaluate(row):
            evaluated.append((row.pull_request, row.head))
            kind = row.pull_request % 5
            if kind == 2:
                return CandidateEvaluation(EvaluationOutcome.REJECTED)
            if kind == 3:
                return CandidateEvaluation(EvaluationOutcome.NOT_READY)
            self.assertEqual(kind, 4)
            return CandidateEvaluation(EvaluationOutcome.PLAN,
                                       (row.pull_request, row.head))

        persisted = cursor()
        plans = []
        per_run = []
        for _ in range(201):
            transition = scan(persisted, imported_heads=imported,
                              fetch_page=fetch, evaluate=evaluate, now=NOW)
            per_run.append((transition.counters.page_fetches,
                            transition.counters.rows_returned,
                            transition.counters.evaluations))
            self.assertLessEqual(per_run[-1][0], 10)
            self.assertLessEqual(per_run[-1][1], 200)
            self.assertLessEqual(per_run[-1][2], 20)
            persisted = transition.proposed_after.to_mapping()
            if transition.outcome is ScanOutcome.PLANNED:
                plans.append(transition.plan)
            else:
                break
        else:
            self.fail("stable 1,000-PR cycle did not finish in 201 bounded runs")

        self.assertEqual(plans, expected_eligible)
        self.assertEqual([item for item in evaluated if item in expected_eligible],
                         expected_eligible)
        self.assertEqual(len(evaluated), 600)
        self.assertEqual(sum(number % 5 == 2 for number, _head in evaluated), 200)
        self.assertEqual(sum(number % 5 == 3 for number, _head in evaluated), 200)
        self.assertEqual(persisted, cursor(cycle=1, completed=NOW))
        self.assertEqual(len(per_run), 201)
        self.assertEqual(tuple(map(max, zip(*per_run))), (2, 40, 3))
        self.assertTrue(all(fetches >= 1 and rows_returned <= fetches * 20
                            for fetches, rows_returned, _evaluations in per_run))


if __name__ == "__main__":
    unittest.main()

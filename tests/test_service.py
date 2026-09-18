"""Native service boundaries exercised through offline GitHub API fixtures."""
import json
import unittest
from tests import test_admission
from tests.test_coordinator import FakeAPI


class Service(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        test_admission.TreeAdmission.setUp(self)
        for target in ('omarchy_knowledge.github_public.GitHubPublicRead.repository',
                       'omarchy_knowledge.catalog.CatalogPublicRead.catalog'):
            stub = patch(target, side_effect=OSError('offline fixture'))
            stub.start()
            self.addCleanup(stub.stop)
    git = test_admission.TreeAdmission.git
    commit = test_admission.TreeAdmission.commit

    def current_intake_status(self, *, page=9, offset=2, after=42,
                              cycle=3, drift_runs=1):
        from omarchy_knowledge.fair_intake import IntakeCursor
        from omarchy_knowledge.intake_status import (
            CursorHealth, PublicIntakeStatus, PublicStatusKind,
        )
        return PublicIntakeStatus(
            PublicStatusKind.CURRENT, "e" * 40,
            IntakeCursor(1, page, offset, after, cycle,
                         "2026-09-16T00:00:00Z"),
            CursorHealth(1, "2026-09-17T08:00:00Z", drift_runs),
            None,
        )

    def test_direct_plan_and_publish_preserve_known_cursor_in_strict_envelopes(self):
        """Direct admission can write, but cannot advance scheduling state."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.service import plan_run, publish_run

        api = FakeAPI(self)
        api.intake_status = self.current_intake_status
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, pull_request=1, run_id=44, run_attempt=2,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(set(planned), {
            "version", "stage", "run_id", "run_attempt", "deployment",
            "trusted_lane", "prior_state", "before_cursor",
            "proposed_cursor", "prior_cursor_health", "selected_action",
            "scan_outcome", "stop_reason", "scan_time", "counters", "plan",
            "status",
        })
        self.assertEqual((planned["version"], planned["stage"]), (2, "plan"))
        self.assertEqual((planned["trusted_lane"], planned["prior_state"],
                          planned["selected_action"]),
                         ("direct", "current", "before"))
        self.assertEqual(planned["before_cursor"], planned["proposed_cursor"])
        self.assertEqual(planned["scan_outcome"], "direct")
        self.assertEqual(planned["stop_reason"], "direct")
        self.assertIsNone(planned["scan_time"])
        self.assertTrue(all(value == 0 for value in planned["counters"].values()))

        published = publish_run(
            api, policy, planned, run_id=44, run_attempt=2,
            trusted_lane="direct",
        )
        self.assertEqual(set(published), {
            "version", "stage", "run_id", "run_attempt", "deployment",
            "trusted_lane", "prior_state", "before_cursor",
            "proposed_cursor", "prior_cursor_health", "selected_action",
            "scan_outcome", "stop_reason", "scan_time", "counters", "status",
            "pages_publishable", "cursor", "cursor_health",
        })
        self.assertEqual((published["stage"], published["status"]["status"],
                          published["selected_action"],
                          published["pages_publishable"]),
                         ("publish", "accepted", "before", True))
        self.assertEqual(published["cursor"], planned["before_cursor"])
        self.assertEqual(published["cursor_health"],
                         planned["prior_cursor_health"])

    def test_scheduled_scan_skips_not_ready_then_advances_only_after_acceptance(self):
        """Deterministic readiness is skippable; accepted scheduled work advances."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.service import plan_run, publish_run

        api = FakeAPI(self)
        api.intake_status = lambda: self.current_intake_status(
            page=1, offset=0, after=None, cycle=3, drift_runs=4,
        )
        api.intake_page = lambda page: ([
            {"pull_request": 1, "state": "open", "head": self.head},
            {"pull_request": 2, "state": "open", "head": self.head},
        ] if page == 1 else [])
        real_pull = api.pull

        def pull(number):
            value = real_pull(number)
            if number == 1:
                value["draft"] = True
            return value

        api.pull = pull
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, run_id=45, run_attempt=1,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual((planned["trusted_lane"], planned["prior_state"],
                          planned["selected_action"], planned["scan_outcome"],
                          planned["stop_reason"]),
                         ("scheduled", "current", "after", "planned", "plan"))
        self.assertEqual(planned["status"]["outcomes"], [
            {"status": "not-ready", "pull_request": 1},
            {"status": "planned", "pull_request": 2, "head": self.head},
        ])
        self.assertEqual(planned["counters"], {
            "page_fetches": 1, "rows_returned": 2, "rows_consumed": 2,
            "evaluations": 2, "closed": 0, "imported": 0, "rejected": 0,
            "not_ready": 1, "plans": 1, "cursor_drifts": 0,
        })
        self.assertEqual(planned["proposed_cursor"], {
            "version": 1, "page": 1, "offset": 0,
            "after_pull_request": None, "cycle": 4,
            "last_full_cycle_at": "2026-09-17T12:00:00Z",
        })

        published = publish_run(
            api, policy, planned, run_id=45, run_attempt=1,
            trusted_lane="scheduled",
        )
        self.assertEqual((published["status"]["status"],
                          published["selected_action"],
                          published["pages_publishable"]),
                         ("accepted", "after", True))
        self.assertEqual(published["cursor"], planned["proposed_cursor"])
        self.assertEqual(published["cursor_health"], {
            "version": 1,
            "last_progress_at": "2026-09-17T12:00:00Z",
            "consecutive_drift_runs": 0,
        })

    def test_scheduled_exhaustion_bootstraps_legacy_but_unknown_prior_aborts(self):
        """Only an authenticated legacy status authorizes page-one bootstrap."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.intake_status import (
            PublicIntakeStatus, PublicStatusKind,
        )
        from omarchy_knowledge.service import plan_run, publish_run

        api = FakeAPI(self)
        api.intake_status = lambda: PublicIntakeStatus(
            PublicStatusKind.LEGACY, self.base, None, None, None,
        )
        api.intake_page = lambda page: []
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, run_id=46, run_attempt=1,
            now="2026-09-17T12:01:00Z",
        )
        self.assertEqual((planned["prior_state"], planned["before_cursor"],
                          planned["prior_cursor_health"]), (
            "legacy",
            {"version": 1, "page": 1, "offset": 0,
             "after_pull_request": None, "cycle": 0,
             "last_full_cycle_at": None},
            {"version": 1, "last_progress_at": None,
             "consecutive_drift_runs": 0},
        ))
        published = publish_run(
            api, policy, planned, run_id=46, run_attempt=1,
            trusted_lane="scheduled",
        )
        self.assertEqual((published["status"]["status"],
                          published["selected_action"],
                          published["cursor"]["cycle"]),
                         ("idle", "after", 1))
        self.assertEqual(published["cursor_health"], {
            "version": 1, "last_progress_at": "2026-09-17T12:01:00Z",
            "consecutive_drift_runs": 0,
        })

        unavailable = FakeAPI(self)
        unavailable.intake_status = lambda: (_ for _ in ()).throw(
            NativeUnavailable())
        unavailable.intake_page = lambda _page: (_ for _ in ()).throw(
            AssertionError("unknown prior must abort before scanning"))
        with self.assertRaises(NativeUnavailable):
            plan_run(
                unavailable, policy, run_id=47, run_attempt=1,
                now="2026-09-17T12:02:00Z",
            )

    def test_scheduled_scan_uncertainty_aborts_without_a_transition(self):
        """API/shape uncertainty is not a deterministic skip or progress event."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.service import plan_run

        policy = Policy("a" * 40, "b" * 40)
        for error in (NativeUnavailable(), ValueError("invalid page shape")):
            api = FakeAPI(self)
            api.intake_status = lambda: self.current_intake_status(
                page=1, offset=0, after=None,
            )
            api.intake_page = lambda _page, error=error: (
                _ for _ in ()).throw(error)
            with self.subTest(error=type(error).__name__), \
                    self.assertRaises((NativeUnavailable, ValueError)):
                plan_run(
                    api, policy, run_id=48, run_attempt=1,
                    now="2026-09-17T12:02:30Z",
                )

    def test_scheduled_envelopes_reject_forged_planless_acceptance(self):
        """A caller terminal status cannot replace the scanner's admission plan."""
        from copy import deepcopy
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.service import (
            _validate_publish_envelope, plan_run, publish_run,
        )

        api = FakeAPI(self)
        api.intake_status = lambda: self.current_intake_status(
            page=1, offset=0, after=None,
        )
        api.intake_page = lambda page: ([
            {"pull_request": 1, "state": "open", "head": self.head},
        ] if page == 1 else [])
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, run_id=49, run_attempt=1,
            now="2026-09-17T12:02:40Z",
        )
        forged_plan = deepcopy(planned)
        forged_plan["plan"] = None
        forged_plan["status"] = {
            "status": "accepted", "outcomes": [], "scanned": 1,
            "scan_truncated": False,
        }
        with self.assertRaises(NativeUnavailable):
            publish_run(
                api, policy, forged_plan, run_id=49, run_attempt=1,
                trusted_lane="scheduled",
            )

        exhausted_api = FakeAPI(self)
        exhausted_api.intake_status = lambda: self.current_intake_status(
            page=1, offset=0, after=None,
        )
        exhausted_api.intake_page = lambda _page: []
        exhausted = plan_run(
            exhausted_api, policy, run_id=50, run_attempt=1,
            now="2026-09-17T12:02:50Z",
        )
        published = publish_run(
            exhausted_api, policy, exhausted, run_id=50, run_attempt=1,
            trusted_lane="scheduled",
        )
        forged_publish = deepcopy(published)
        forged_publish["scan_outcome"] = "planned"
        forged_publish["stop_reason"] = "plan"
        forged_publish["counters"] = {
            "page_fetches": 1, "rows_returned": 1, "rows_consumed": 1,
            "evaluations": 1, "closed": 0, "imported": 0,
            "rejected": 0, "not_ready": 0, "plans": 1,
            "cursor_drifts": 0,
        }
        forged_publish["status"] = {
            "status": "accepted", "outcomes": [], "scanned": 1,
            "scan_truncated": False,
        }
        with self.assertRaises(NativeUnavailable):
            _validate_publish_envelope(
                forged_publish, policy=policy, run_id=50, run_attempt=1,
                trusted_lane="scheduled",
            )

    def test_legacy_envelopes_require_exact_initial_cursor_and_health(self):
        """Recognized legacy can bootstrap only from null/zero initial state."""
        from copy import deepcopy
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.intake_status import (
            PublicIntakeStatus, PublicStatusKind,
        )
        from omarchy_knowledge.service import (
            _validate_publish_envelope, plan_run, publish_run,
        )

        api = FakeAPI(self)
        api.intake_status = lambda: PublicIntakeStatus(
            PublicStatusKind.LEGACY, self.base, None, None, None,
        )
        api.intake_page = lambda _page: []
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, run_id=51, run_attempt=1,
            now="2026-09-17T12:02:55Z",
        )
        published = publish_run(
            api, policy, planned, run_id=51, run_attempt=1,
            trusted_lane="scheduled",
        )
        for value, validate in (
            (deepcopy(planned), lambda item: publish_run(
                api, policy, item, run_id=51, run_attempt=1,
                trusted_lane="scheduled")),
            (deepcopy(published), lambda item: _validate_publish_envelope(
                item, policy=policy, run_id=51, run_attempt=1,
                trusted_lane="scheduled")),
        ):
            value["before_cursor"] = {
                "version": 1, "page": 500, "offset": 0,
                "after_pull_request": None, "cycle": 100,
                "last_full_cycle_at": "2026-09-16T00:00:00Z",
            }
            value["prior_cursor_health"] = {
                "version": 1,
                "last_progress_at": "2026-09-16T00:00:00Z",
                "consecutive_drift_runs": 7,
            }
            with self.subTest(stage=value["stage"]), \
                    self.assertRaises(NativeUnavailable):
                validate(value)

    def test_direct_legacy_or_unavailable_prior_withholds_after_admission(self):
        """Successful admission and successful Pages publication are distinct."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.intake_status import (
            PublicIntakeStatus, PublicStatusKind,
        )
        from omarchy_knowledge.service import plan_run, publish_run

        policy = Policy("a" * 40, "b" * 40)
        observations = (
            lambda: PublicIntakeStatus(
                PublicStatusKind.LEGACY, self.base, None, None, None),
            lambda: (_ for _ in ()).throw(NativeUnavailable()),
        )
        for run_id, observation in enumerate(observations, 50):
            api = FakeAPI(self)
            api.intake_status = observation
            planned = plan_run(api, policy, pull_request=1, run_id=run_id)
            published = publish_run(
                api, policy, planned, run_id=run_id,
                trusted_lane="direct",
            )
            with self.subTest(prior=planned["prior_state"]):
                self.assertEqual(published["status"]["status"], "accepted")
                self.assertEqual(published["selected_action"], "withhold")
                self.assertFalse(published["pages_publishable"])
                self.assertIsNone(published["cursor"])
                self.assertIsNone(published["cursor_health"])
                self.assertEqual(api.writes, 2)

    def test_direct_no_plan_preserves_outcome_and_uses_publication_matrix(self):
        """Readiness/rejection/uncertainty do not invent scheduled exhaustion."""
        from unittest.mock import patch
        from omarchy_knowledge.coordinator import (
            NativeNotReady, NativeRejected, Policy,
        )
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.intake_status import (
            PublicIntakeStatus, PublicStatusKind,
        )
        from omarchy_knowledge.service import plan_run, publish_run

        policy = Policy("a" * 40, "b" * 40)
        prior_states = {
            "current": self.current_intake_status,
            "legacy": lambda: PublicIntakeStatus(
                PublicStatusKind.LEGACY, self.base, None, None, None),
            "unavailable": lambda: (_ for _ in ()).throw(
                NativeUnavailable()),
        }
        outcomes = {
            "not-ready": NativeNotReady("draft"),
            "rejected": NativeRejected(),
            "unavailable": NativeUnavailable(),
        }
        run_id = 52
        for prior_state, observation in prior_states.items():
            for outcome, error in outcomes.items():
                api = FakeAPI(self)
                api.intake_status = observation
                with patch("omarchy_knowledge.service.prepare",
                           side_effect=error):
                    planned = plan_run(
                        api, policy, pull_request=1, run_id=run_id,
                    )
                with self.subTest(prior=prior_state, outcome=outcome), \
                        patch("omarchy_knowledge.service.publish") as admit:
                    published = publish_run(
                        api, policy, planned, run_id=run_id,
                        trusted_lane="direct",
                    )
                    admit.assert_not_called()
                    self.assertEqual(published["status"]["status"], outcome)
                    expected = "before" if prior_state == "current" else "withhold"
                    self.assertEqual(published["selected_action"], expected)
                    self.assertEqual(published["pages_publishable"],
                                     prior_state == "current")
                    if prior_state == "current":
                        self.assertEqual(published["cursor"],
                                         planned["before_cursor"])
                    else:
                        self.assertIsNone(published["cursor"])
                run_id += 1

    def test_scheduled_retry_and_receipt_pending_preserve_before_and_health(self):
        """Writer degradation never commits a proposed cursor or health update."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.service import plan_run, publish_run

        policy = Policy("a" * 40, "b" * 40)
        for mode, run_id in (("receipt-pending", 60), ("retry", 61)):
            api = FakeAPI(self)
            api.intake_status = lambda: self.current_intake_status(
                page=1, offset=0, after=None, cycle=3, drift_runs=4,
            )
            api.intake_page = lambda page: ([
                {"pull_request": 1, "state": "open", "head": self.head},
            ] if page == 1 else [])
            planned = plan_run(
                api, policy, run_id=run_id,
                now="2026-09-17T12:03:00Z",
            )
            if mode == "receipt-pending":
                api.fail_receipt = True
            else:
                api.base = self.commit(
                    {"README.md": ("100644", b"competing update")}, self.base)
            published = publish_run(
                api, policy, planned, run_id=run_id,
                trusted_lane="scheduled",
            )
            with self.subTest(mode=mode):
                self.assertEqual(published["status"]["status"], mode)
                self.assertEqual(published["selected_action"], "before")
                self.assertEqual(published["cursor"], planned["before_cursor"])
                self.assertEqual(published["cursor_health"],
                                 planned["prior_cursor_health"])

    def test_persisted_drift_saturates_streak_without_inventing_progress_time(self):
        """A reset can advance the cursor but is not non-drift progress."""
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.service import plan_run, publish_run

        api = FakeAPI(self)
        api.intake_status = lambda: self.current_intake_status(
            page=9, offset=1, after=42, cycle=3,
            drift_runs=2_147_483_647,
        )
        api.intake_page = lambda page: ([
            {"pull_request": 41, "state": "closed"},
        ] if page == 9 else [])
        policy = Policy("a" * 40, "b" * 40)
        planned = plan_run(
            api, policy, run_id=62,
            now="2026-09-17T12:04:00Z",
        )
        published = publish_run(
            api, policy, planned, run_id=62, trusted_lane="scheduled",
        )
        self.assertEqual(planned["counters"]["cursor_drifts"], 1)
        self.assertEqual(published["selected_action"], "after")
        self.assertEqual(published["cursor_health"], {
            "version": 1, "last_progress_at": "2026-09-17T08:00:00Z",
            "consecutive_drift_runs": 2_147_483_647,
        })

    def test_build_rejects_withheld_replayed_unknown_or_wrong_lane_before_io(self):
        """Build validates the same-run envelope before reader/site creation."""
        import contextlib
        import io
        import os
        from copy import deepcopy
        from unittest.mock import patch
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.service import main, plan_run, publish_run

        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        api.intake_status = self.current_intake_status
        planned = plan_run(api, policy, pull_request=1, run_id=70)
        publishable = publish_run(
            api, policy, planned, run_id=70, trusted_lane="direct",
        )

        event_value = {
            "repository": api.repository(), "action": "opened", "number": 1,
            "pull_request": {"base": {
                "ref": "main", "repo": api.repository(),
            }},
        }
        event = self.root / "build-event.json"
        event.write_text(json.dumps(event_value))
        artifact = self.root / "build-status.json"
        common_env = {
            "GITHUB_REPOSITORY": policy.repository,
            "GITHUB_REPOSITORY_ID": str(policy.repository_id),
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "pull_request_target",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_RUN_ID": "70", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_NUMBER": "1", "GITHUB_TOKEN": "fixture-token",
        }
        common_args = [
            "build", "--deployment", "production",
            "--policy-revision", "a" * 40,
            "--toolkit-revision", "b" * 40,
            "--input", str(artifact), "--output", str(self.root / "site"),
        ]
        variants = []
        value = deepcopy(publishable); value["unknown"] = 1
        variants.append(("unknown", value, common_env))
        variants.append(("replay", publishable,
                         {**common_env, "GITHUB_RUN_ID": "71"}))
        value = deepcopy(publishable); value["trusted_lane"] = "scheduled"
        variants.append(("lane", value, common_env))

        unavailable = FakeAPI(self)
        unavailable.intake_status = lambda: (_ for _ in ()).throw(
            NativeUnavailable())
        withheld_plan = plan_run(
            unavailable, policy, pull_request=1, run_id=70,
        )
        withheld = publish_run(
            unavailable, policy, withheld_plan, run_id=70,
            trusted_lane="direct",
        )
        variants.append(("withheld", withheld, common_env))

        for name, value, environment in variants:
            artifact.write_text(json.dumps(value))
            with self.subTest(name=name), patch.dict(os.environ, environment), \
                    patch("omarchy_knowledge.service.GitHubRead") as reader, \
                    patch("omarchy_knowledge.distribution.build_site") as builder, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(common_args), 2)
                reader.assert_not_called()
                builder.assert_not_called()

    def test_twenty_not_ready_candidates_persist_progress_then_reach_next_page(self):
        from omarchy_knowledge.fair_intake import IntakeCursor
        from omarchy_knowledge.intake_status import PublicIntakeStatus
        from omarchy_knowledge.service import plan_run, publish_run
        from omarchy_knowledge.coordinator import Policy
        api = FakeAPI(self)
        prior = [self.current_intake_status(
            page=1, offset=0, after=None, cycle=3, drift_runs=0)]
        api.intake_status = lambda: prior[0]
        api.intake_page = lambda page: [
            {'pull_request': n, 'state': 'open', 'head': self.head}
            for n in range((page - 1) * 20 + 1, min(page * 20 + 1, 42))
        ]
        real_pull = api.pull
        def pull(number):
            value = real_pull(number)
            if number <= 20:
                value['draft'] = True
            return value
        api.pull = pull
        policy = Policy('a' * 40, 'b' * 40)
        first = plan_run(api, policy, run_id=80,
                         now='2026-09-17T12:05:00Z')
        self.assertIsNone(first['plan'])
        self.assertEqual(first['stop_reason'], 'preparation-limit')
        self.assertEqual(first['counters']['evaluations'], 20)
        persisted = publish_run(
            api, policy, first, run_id=80, trusted_lane='scheduled')
        prior[0] = PublicIntakeStatus(
            prior[0].kind, prior[0].source_revision,
            IntakeCursor.from_mapping(persisted['cursor']),
            type(prior[0].cursor_health).from_mapping(
                persisted['cursor_health']),
            prior[0].intake_scan,
        )
        second = plan_run(api, policy, run_id=81,
                          now='2026-09-17T12:06:00Z')
        self.assertEqual(second['plan']['pull_request'], 21)
        self.assertEqual(second['counters']['evaluations'], 1)

    def test_publish_reconciles_first_and_halts_on_receipt_pending(self):
        from omarchy_knowledge.service import plan_run, publish_run
        from omarchy_knowledge.coordinator import Policy
        api = FakeAPI(self); policy = Policy('a' * 40, 'b' * 40)
        api.intake_status = self.current_intake_status
        batch = plan_run(api, policy, pull_request=1)
        api.fail_receipt = True
        status = publish_run(api, policy, batch, trusted_lane='direct')
        self.assertEqual(status['status']['status'], 'receipt-pending')
        self.assertEqual(status['status']['outcomes'][-1]['head'], self.head)
        self.assertEqual(status['status']['outcomes'][-1]['pull_request'], 1)
        self.assertEqual(api.writes, 1)
        self.assertEqual(publish_run(
            api, policy, batch, trusted_lane='direct')['status']['status'],
            'retry')
        self.assertEqual(api.writes, 1)

    def test_stale_or_wrong_run_plan_cannot_write(self):
        from omarchy_knowledge.service import plan_run, publish_run
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self); policy = Policy('a' * 40, 'b' * 40)
        api.intake_status = self.current_intake_status
        batch = plan_run(api, policy, pull_request=1, run_id=44, run_attempt=1)
        with self.assertRaises(NativeUnavailable):
            publish_run(api, policy, batch, run_id=45, run_attempt=1,
                        trusted_lane='direct')
        api.base = self.commit({'README.md': ('100644', b'update')}, self.base)
        self.assertEqual(publish_run(
            api, policy, batch, run_id=44, run_attempt=1,
            trusted_lane='direct')['status']['status'], 'retry')
        self.assertEqual(api.writes, 0)

    def test_event_guard_rejects_fork_repository_and_nonmain_context(self):
        from omarchy_knowledge.service import guard_event
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy('a' * 40, 'b' * 40)
        repo = FakeAPI(self).repository()
        env = {'GITHUB_REPOSITORY': policy.repository, 'GITHUB_REPOSITORY_ID': str(policy.repository_id),
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'pull_request_target'}
        event = {'repository': repo, 'action': 'opened', 'number': 1,
                 'pull_request': {'base': {'ref': 'main', 'repo': repo}}}
        self.assertEqual(guard_event(policy, env, event), 1)
        for key, value in [('GITHUB_REF', 'refs/pull/1/merge'), ('GITHUB_REPOSITORY_ID', '1'),
                           ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REPOSITORY', 'attacker/fork')]:
            with self.assertRaises(NativeUnavailable):
                guard_event(policy, {**env, key: value}, event)
        event['pull_request']['base']['ref'] = 'other'
        with self.assertRaises(NativeUnavailable):
            guard_event(policy, env, event)

    def test_rejected_content_is_safe_status_and_later_candidate_can_plan(self):
        from omarchy_knowledge.service import plan_run, safe_status
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self)
        bad = self.commit({'secret-private-name.txt': ('100644', b'raw bad prose')}, self.base)
        bad_merge = self.git('commit-tree', self.reader.commit(bad), '-p', self.base, '-p', bad, data=b'test merge\n')
        actual_pull = api.pull
        def pull(number):
            value = actual_pull(number)
            if number == 1:
                value['head']['sha'] = bad
                value['merge_commit_sha'] = bad_merge
            return value
        api.pull = pull
        api.intake_status = lambda: self.current_intake_status(
            page=1, offset=0, after=None)
        api.intake_page = lambda page: ([
            {'pull_request': 1, 'state': 'open', 'head': bad},
            {'pull_request': 2, 'state': 'open', 'head': self.head},
        ] if page == 1 else [])
        batch = plan_run(api, Policy('a' * 40, 'b' * 40),
                         now='2026-09-17T12:07:00Z')
        self.assertEqual(batch['plan']['pull_request'], 2)
        self.assertEqual(batch['status']['outcomes'][0], {'status': 'rejected', 'pull_request': 1})
        self.assertNotIn('secret-private', json.dumps(batch['status']))
        self.assertNotIn('raw bad prose', json.dumps(batch['status']))
        with self.assertRaises(NativeUnavailable):
            safe_status({'status': 'accepted', 'outcomes': [{'status': 'accepted', 'pull_request': 1, 'head': '<script>'}]})

    def test_schedule_and_manual_guards_accept_only_main(self):
        from omarchy_knowledge.service import guard_event
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy('a' * 40, 'b' * 40)
        env = {'GITHUB_REPOSITORY': policy.repository, 'GITHUB_REPOSITORY_ID': str(policy.repository_id),
               'GITHUB_REF': 'refs/heads/main'}
        event = {'repository': FakeAPI(self).repository()}
        for name in ('schedule', 'workflow_dispatch'):
            self.assertIsNone(guard_event(policy, {**env, 'GITHUB_EVENT_NAME': name}, event))
            with self.assertRaises(NativeUnavailable):
                guard_event(policy, {**env, 'GITHUB_EVENT_NAME': name, 'GITHUB_REF': 'refs/heads/other'}, event)

    def test_real_entrypoints_produce_bounded_plan_status_and_empty_site(self):
        import contextlib
        import io
        import os
        from unittest.mock import patch
        from omarchy_knowledge.intake_status import (
            PublicIntakeStatus, PublicStatusKind, validate_intake_status,
        )
        from omarchy_knowledge.service import main
        api = FakeAPI(self)
        api.intake_status = lambda: PublicIntakeStatus(
            PublicStatusKind.LEGACY, self.base, None, None, None,
        )
        api.intake_page = lambda page: []
        event = self.root / 'event.json'; event.write_text(json.dumps({'repository': api.repository()}))
        env = {'GITHUB_REPOSITORY': 'cylon58/omarchy-community-knowledge', 'GITHUB_REPOSITORY_ID': '1373429914',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '55', 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_RUN_NUMBER': '1', 'GITHUB_TOKEN': 'fixture-token'}
        common = ['--deployment', 'production', '--policy-revision', 'a' * 40, '--toolkit-revision', 'b' * 40]
        plan = self.root / 'plan/plan.json'; status = self.root / 'status/status.json'; site = self.root / 'site'
        api.objects.export_bundle = lambda revision: b'proof-bundle-fixture'
        seed_calls = []
        api.seed_canonical = lambda: seed_calls.append(api.base) or False
        for command, arguments in [('plan', ['--output', str(plan)]),
                                   ('publish', ['--input', str(plan), '--output', str(status)]),
                                   ('build', ['--input', str(status), '--output', str(site)])]:
            with patch.dict(os.environ, env), patch('omarchy_knowledge.service.GitHubRead', return_value=api), \
                    patch('omarchy_knowledge.service.GitHubWriter', return_value=api), \
                    patch('omarchy_knowledge.service._validated_proof', return_value=b'proof-bundle-fixture'), \
                    patch('omarchy_knowledge.service._successful_build_health', return_value={
                        'version': 1, 'record_count': 0, 'receipt_count': 0,
                        'proof': {'object_count': 1, 'raw_bytes': 1,
                                  'compressed_bytes': 20},
                        'canonical_builder': {
                            'scope': 'successful-build-canonical-adapter',
                            'request_attempts': None,
                            'charged_response_bytes': None,
                            'object_visits': None,
                        },
                    }), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([command, *common, *arguments]), 0)
                self.assertNotIn('GITHUB_TOKEN', os.environ)
        self.assertLessEqual(plan.stat().st_size, 1024 * 1024)
        self.assertLessEqual(status.stat().st_size, 64 * 1024)
        published = json.loads(status.read_bytes())
        self.assertEqual(published['status']['status'], 'idle')
        self.assertEqual((published['prior_state'], published['selected_action']),
                         ('legacy', 'after'))
        self.assertEqual((site / 'records.jsonl').read_bytes(), b'')
        self.assertEqual((site / 'canonical-objects.bundle').read_bytes(), b'proof-bundle-fixture')
        self.assertEqual(seed_calls, [api.base, api.base, api.base])
        public = json.loads((site / 'status.json').read_bytes())
        self.assertEqual(public['upstream']['status'], 'unknown')
        validated = validate_intake_status(
            public, repository='cylon58/omarchy-community-knowledge',
            repository_id=1373429914, deployment='production',
        )
        self.assertEqual(validated.cursor.cycle, 1)
        self.assertEqual(validated.intake_scan.selected_action, 'after')
        self.assertEqual(validated.build_health['record_count'], 0)
        self.assertIn('CATALOG_UNAVAILABLE', (site / 'index.html').read_text())
        self.assertEqual(api.writes, 0)


if __name__ == '__main__':
    unittest.main()

"""Semantic and executable guards for the unattended read-only health workflow."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/service-health.yml"


def workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


class HealthWorkflowTests(unittest.TestCase):
    def test_workflow_has_fixed_triggers_identity_permissions_pins_and_artifact(self):
        value = workflow()
        self.assertEqual(set(value), {
            "name", "on", "permissions", "concurrency", "jobs",
        })
        self.assertEqual(value["name"], "Public service health")
        self.assertEqual(value["on"], {
            "schedule": [{"cron": "43 * * * *"}],
            "workflow_dispatch": {},
        })
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertEqual(value["concurrency"], {
            "group": "omarchy-knowledge-service-health",
            "cancel-in-progress": True,
        })
        self.assertEqual(set(value["jobs"]), {"health"})
        job = value["jobs"]["health"]
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], 10)
        self.assertEqual(job["if"], (
            "${{ github.repository_id == '1373429982' && "
            "github.repository == 'cylon58/omarchy-community-knowledge-tools' && "
            "github.ref == 'refs/heads/main' && "
            "github.event.repository.default_branch == 'main' && "
            "(github.event_name == 'schedule' || "
            "github.event_name == 'workflow_dispatch') }}"
        ))

        steps = job["steps"]
        self.assertEqual([step["name"] for step in steps], [
            "Check out the exact trusted main revision",
            "Provide pinned Python",
            "Install the fixed toolkit runtime",
            "Capture both deployment reports",
            "Retain same-run data-only reports",
            "Expose required-check failures",
        ])
        checkout, setup, install, capture, upload, gate = steps
        self.assertEqual(
            checkout["uses"],
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        )
        self.assertEqual(checkout["with"], {
            "ref": "${{ github.sha }}",
            "persist-credentials": False,
            "fetch-depth": 1,
        })
        self.assertEqual(
            setup["uses"],
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        )
        self.assertEqual(setup["with"], {"python-version": "3.13"})
        for pin in (
            "jsonschema==4.26.0", "attrs==26.1.0",
            "jsonschema-specifications==2025.9.1",
            "referencing==0.37.0", "rpds-py==2026.6.3",
        ):
            self.assertIn(pin, install["run"])
        self.assertIn("--only-binary=:all: --no-deps", install["run"])
        self.assertIn("--no-build-isolation --no-deps .", install["run"])
        self.assertIn("health --deployment production", capture["run"])
        self.assertIn("health --deployment pilot", capture["run"])
        self.assertIn("set +e", capture["run"])
        self.assertEqual(
            upload["uses"],
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        )
        self.assertEqual(upload["if"], "${{ always() }}")
        self.assertEqual(upload["with"], {
            "name": "service-health-${{ github.run_id }}-${{ github.run_attempt }}",
            "path": "${{ runner.temp }}/service-health/*.json",
            "retention-days": 7,
            "if-no-files-found": "error",
            "compression-level": 6,
        })
        self.assertEqual(gate["if"], "${{ always() }}")

        encoded = json.dumps(value).casefold()
        for forbidden in (
            "pull_request", "pull_request_target", "workflow_run", "self-hosted",
            "secrets.", "github.token", "actions/cache", "download-artifact",
            "issue", "model", "provider", "deploy-pages", "release",
        ):
            self.assertNotIn(forbidden, encoded)
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"^actions/[a-z-]+@[a-f0-9]{40}$")
            self.assertNotIn("env", step)

    def test_capture_runs_both_checks_preserves_reports_then_gate_fails(self):
        value = workflow()
        steps = value["jobs"]["health"]["steps"]
        capture = steps[3]["run"]
        gate = steps[5]["run"]
        with tempfile.TemporaryDirectory(prefix="health-workflow-") as temporary:
            root = Path(temporary)
            executable = root / "runtime/bin/omarchy-knowledge"
            executable.parent.mkdir(parents=True)
            executable.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *production*) printf '%s\\n' '{\"version\":1,\"deployment\":\"production\",\"checked_at\":\"now\",\"overall\":\"failure\",\"source\":null,\"availability\":{},\"intake\":{},\"capacity\":{},\"evidence_freshness\":{},\"warnings\":[],\"failures\":[],\"unknowns\":[],\"transport\":{}}'; exit 1;;\n"
                "  *pilot*) printf '%s\\n' '{\"version\":1,\"deployment\":\"pilot\",\"checked_at\":\"now\",\"overall\":\"ok\",\"source\":null,\"availability\":{},\"intake\":{},\"capacity\":{},\"evidence_freshness\":{},\"warnings\":[],\"failures\":[],\"unknowns\":[],\"transport\":{}}'; exit 0;;\n"
                "  *) exit 2;;\n"
                "esac\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            (root / "runtime/bin/python").symlink_to(sys.executable)
            result = subprocess.run(
                ["bash", "-c", capture], cwd=root,
                env={**os.environ, "RUNNER_TEMP": str(root)},
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reports = root / "service-health"
            self.assertEqual(json.loads(
                (reports / "production.json").read_text())["overall"], "failure")
            self.assertEqual(json.loads(
                (reports / "pilot.json").read_text())["overall"], "ok")
            self.assertEqual(json.loads((reports / "exit-codes.json").read_text()),
                             {"production": 1, "pilot": 0})

            failed = subprocess.run(
                ["bash", "-c", gate], cwd=root,
                env={**os.environ, "RUNNER_TEMP": str(root)},
                text=True, capture_output=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            production = json.loads((reports / "production.json").read_text())
            production["overall"] = "warning"
            (reports / "production.json").write_text(
                json.dumps(production) + "\n", encoding="utf-8")
            (reports / "exit-codes.json").write_text(
                '{"production":0,"pilot":0}\n', encoding="utf-8")
            passed = subprocess.run(
                ["bash", "-c", gate], cwd=root,
                env={**os.environ, "RUNNER_TEMP": str(root)},
                text=True, capture_output=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)

            (reports / "pilot.json").unlink()
            missing = subprocess.run(
                ["bash", "-c", gate], cwd=root,
                env={**os.environ, "RUNNER_TEMP": str(root)},
                text=True, capture_output=True,
            )
            self.assertNotEqual(missing.returncode, 0)
            (reports / "pilot.json").write_text(
                '{"version":1,"deployment":"pilot","overall":"ok",'
                '"source":null,"availability":{},"intake":{},"capacity":{},'
                '"evidence_freshness":{},"warnings":[],"failures":[],'
                '"unknowns":[],"transport":{},"checked_at":"now"}\n',
                encoding="utf-8",
            )
            (reports / "exit-codes.json").write_text(
                '{"production":false,"pilot":0}\n', encoding="utf-8")
            malformed = subprocess.run(
                ["bash", "-c", gate], cwd=root,
                env={**os.environ, "RUNNER_TEMP": str(root)},
                text=True, capture_output=True,
            )
            self.assertNotEqual(malformed.returncode, 0)

    def test_offline_installed_entrypoint_preserves_json_and_exit_contract_without_network(self):
        """Exercise the installed entry point with the public health seam mocked."""
        wheelhouse_value = os.environ.get("OMARCHY_KNOWLEDGE_TEST_WHEELHOUSE")
        if not wheelhouse_value:
            self.skipTest("set OMARCHY_KNOWLEDGE_TEST_WHEELHOUSE for offline runtime smoke")
        wheelhouse = Path(wheelhouse_value)
        if not wheelhouse.is_dir():
            self.skipTest("configured offline wheelhouse is unavailable")
        with tempfile.TemporaryDirectory(prefix="health-installed-") as temporary:
            root = Path(temporary)
            environment = root / "venv"
            created = subprocess.run(
                [sys.executable, "-m", "venv", str(environment)],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            python = str(environment / "bin/python")
            installed = subprocess.run([
                python, "-m", "pip", "install", "--no-index",
                "--find-links", str(wheelhouse), "--no-build-isolation", str(ROOT),
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(installed.returncode, 0,
                             installed.stdout + installed.stderr)
            help_result = subprocess.run([
                str(environment / "bin/omarchy-knowledge"),
                "health", "--help",
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("{production,pilot}", help_result.stdout)

            smoke = subprocess.run([
                python, "-I", "-c",
                "import io,json;"
                "from contextlib import redirect_stdout;"
                "from importlib.metadata import distribution;"
                "from unittest.mock import patch;"
                "ep=next(e for e in distribution('omarchy-community-knowledge-tools').entry_points "
                "if e.name=='omarchy-knowledge');"
                "main=ep.load();"
                "report={'version':1,'deployment':'production','overall':'warning'};"
                "out=io.StringIO();"
                "ctx=patch('omarchy_knowledge.health.check',return_value=report);"
                "ctx.start();"
                "exec('with redirect_stdout(out):\\n rc=main([\"health\",\"--deployment\",\"production\"])');"
                "ctx.stop();"
                "assert rc==0 and json.loads(out.getvalue())==report",
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(smoke.returncode, 0, smoke.stderr)


if __name__ == "__main__":
    unittest.main()

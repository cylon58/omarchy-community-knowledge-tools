import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout


class FakeCommands:
    def __init__(self, *, fail_pip=False, fail_sync=False,
                 fail_skill_install=False, fail_skill_remove=False):
        self.fail_pip = fail_pip
        self.fail_sync = fail_sync
        self.fail_skill_install = fail_skill_install
        self.fail_skill_remove = fail_skill_remove
        self.calls = []

    def __call__(self, argv, **kwargs):
        argv = [str(value) for value in argv]
        self.calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            venv = Path(argv[3])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").write_text("python", encoding="utf-8")
            (venv / "bin/omarchy-knowledge").write_text("cli", encoding="utf-8")
        if "pip" in argv and self.fail_pip:
            raise subprocess.CalledProcessError(1, argv, stderr="dependency failed")
        if "skills" in argv and "install" in argv:
            from omarchy_knowledge.skill_exposure import install_for_agent
            install_for_agent(
                Path(argv[argv.index("--home") + 1]),
                argv[argv.index("--agent") + 1],
            )
            if self.fail_skill_install:
                raise subprocess.CalledProcessError(1, argv, stderr="skill install failed")
        if "skills" in argv and "remove" in argv:
            if self.fail_skill_remove:
                raise subprocess.CalledProcessError(1, argv, stderr="skill cleanup failed")
            from omarchy_knowledge.skill_exposure import remove_for_agent
            remove_for_agent(
                Path(argv[argv.index("--home") + 1]),
                argv[argv.index("--agent") + 1],
            )
        if "sync" in argv and self.fail_sync:
            raise subprocess.CalledProcessError(1, argv, stderr="offline")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.prefix = self.root / "prefix"
        self.wheel = self.root / "omarchy_community_knowledge_tools-0.2.0-py3-none-any.whl"
        self.wheel.write_bytes(b"reviewed-wheel")

    def test_dry_run_prints_explicit_actions_without_mutation(self):
        from scripts.setup_companion import main

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--wheel", str(self.wheel), "--agent", "codex", "--dry-run",
                "--home", str(self.home), "--prefix", str(self.prefix),
            ])

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertIn("Create isolated Python environment", rendered)
        self.assertIn("Install two Codex skills", rendered)
        self.assertIn("Attempt initial public knowledge refresh", rendered)
        self.assertFalse(self.prefix.exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_install_and_remove_owned_artifacts(self):
        from scripts.setup_companion import install, remove, setup_receipt_path

        installed = install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        launcher = self.prefix / "bin/omarchy-knowledge"
        self.assertTrue(launcher.is_symlink())
        self.assertTrue((self.prefix / "share/omarchy-knowledge-next/venv").is_dir())
        self.assertTrue((self.home / ".agents/skills/omarchy-knowledge-research").is_symlink())
        self.assertEqual(installed["refresh"], "complete")

        result = remove(home=self.home, prefix=self.prefix, command=FakeCommands())
        self.assertEqual(result["status"], "removed")
        self.assertFalse(os.path.lexists(launcher))
        self.assertFalse((self.prefix / "share/omarchy-knowledge-next/venv").exists())
        self.assertFalse(setup_receipt_path(self.home).exists())

    def test_repeating_an_intact_install_is_a_noop(self):
        from scripts.setup_companion import install

        install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        repeated_commands = FakeCommands()
        result = install(self.wheel, home=self.home, prefix=self.prefix, command=repeated_commands)

        self.assertEqual(result["status"], "already-installed")
        self.assertEqual(repeated_commands.calls, [])

    def test_dependency_failure_rolls_back_only_new_paths(self):
        from scripts.setup_companion import install

        marker = self.prefix / "share/keep.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands(fail_pip=True))

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.prefix / "share/omarchy-knowledge-next/venv").exists())
        self.assertFalse((self.prefix / "bin/omarchy-knowledge").exists())

    def test_existing_launcher_or_unsafe_parent_is_preserved(self):
        from scripts.setup_companion import SetupConflict, install

        launcher = self.prefix / "bin/omarchy-knowledge"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"keep")
        with self.assertRaises(SetupConflict):
            install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        self.assertEqual(launcher.read_bytes(), b"keep")

        other_home = self.root / "other-home"
        other_home.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (other_home / ".local").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SetupConflict):
            install(self.wheel, home=other_home, prefix=other_home / ".local", command=FakeCommands())
        self.assertEqual(list(outside.iterdir()), [])

    def test_preexisting_skill_install_is_preserved_on_setup_conflict(self):
        from omarchy_knowledge.skill_exposure import install_for_codex
        from scripts.setup_companion import SetupConflict, install

        prior = install_for_codex(self.home)
        links = [Path(item["path"]) for item in prior["actions"]]
        targets = [os.readlink(path) for path in links]

        with self.assertRaises(SetupConflict):
            install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())

        self.assertEqual([os.readlink(path) for path in links], targets)
        self.assertFalse((self.prefix / "share/omarchy-knowledge-next/venv").exists())

    def test_first_sync_outage_keeps_install_and_reports_pending(self):
        from scripts.setup_companion import install

        result = install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands(fail_sync=True))

        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["refresh"], "pending")
        self.assertTrue((self.prefix / "bin/omarchy-knowledge").is_symlink())
        self.assertTrue((self.home / ".agents/skills/omarchy-knowledge-contribution").is_symlink())

    def test_setup_never_requires_github_cli_for_reading_or_drafting(self):
        from scripts.setup_companion import install

        commands = FakeCommands()
        install(self.wheel, home=self.home, prefix=self.prefix, command=commands)
        flattened = [part for argv in commands.calls for part in argv]
        self.assertNotIn("gh", flattened)
        self.assertNotIn("sudo", flattened)

    def test_remove_refuses_prefix_symlink_substituted_after_install(self):
        from scripts.setup_companion import SetupConflict, install, remove

        commands = FakeCommands()
        install(self.wheel, home=self.home, prefix=self.prefix, command=commands)
        calls_before_remove = len(commands.calls)
        owned_prefix = self.root / "owned-prefix"
        self.prefix.rename(owned_prefix)
        outside = self.root / "outside-prefix"
        executable = outside / "share/omarchy-knowledge-next/venv/bin/omarchy-knowledge"
        executable.parent.mkdir(parents=True)
        executable.write_text("do not execute", encoding="utf-8")
        launcher = outside / "bin/omarchy-knowledge"
        launcher.parent.mkdir()
        launcher.symlink_to(self.prefix / "share/omarchy-knowledge-next/venv/bin/omarchy-knowledge")
        self.prefix.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(SetupConflict):
            remove(home=self.home, prefix=self.prefix, command=commands)

        self.assertEqual(len(commands.calls), calls_before_remove)
        self.assertEqual(executable.read_text(encoding="utf-8"), "do not execute")
        self.assertTrue(launcher.is_symlink())
        self.assertTrue((owned_prefix / "share/omarchy-knowledge-next/venv").is_dir())

    def test_failed_skill_cleanup_preserves_recovery_target_and_both_errors(self):
        from scripts.setup_companion import install, setup_receipt_path

        commands = FakeCommands(fail_skill_install=True, fail_skill_remove=True)
        with self.assertRaises(RuntimeError) as raised:
            install(self.wheel, home=self.home, prefix=self.prefix, command=commands)

        message = str(raised.exception)
        self.assertIn("skill install failed", message)
        self.assertIn("skill cleanup failed", message)
        self.assertTrue((self.prefix / "bin/omarchy-knowledge").is_symlink())
        self.assertTrue((self.prefix / "share/omarchy-knowledge-next/venv").is_dir())
        self.assertTrue((self.home / ".agents/skills/omarchy-knowledge-research").is_symlink())
        self.assertTrue(setup_receipt_path(self.home).is_file())

    def test_standalone_mapping_and_auto_detection_cover_all_documented_agents(self):
        from scripts import setup_companion
        self.assertTrue(hasattr(setup_companion, "AGENT_SKILL_PATHS"), "portable agent mapping is missing")
        self.assertIn("agy", setup_companion.AGENT_SKILL_PATHS, "current Omarchy Antigravity target is missing")
        SetupConflict, plan = setup_companion.SetupConflict, setup_companion.plan

        expected = {
            "codex": ".agents/skills",
            "claude": ".claude/skills",
            "opencode": ".agents/skills",
            "gemini": ".agents/skills",
            "agy": ".gemini/antigravity-cli/skills",
        }
        for agent, relative in expected.items():
            with self.subTest(agent=agent):
                result = plan(self.wheel, agent=agent, home=self.home, prefix=self.prefix)
                self.assertEqual(
                    {Path(item["path"]).parent for item in result["skills"]["actions"]},
                    {self.home / relative},
                )

        calls = []
        auto = plan(
            self.wheel, agent="auto", home=self.home, prefix=self.prefix,
            detect_command=lambda argv: calls.append(argv) or "claude\n",
        )
        self.assertEqual(calls, [["omarchy-default-agent"]])
        self.assertTrue(all("/.claude/skills/" in item["path"] for item in auto["skills"]["actions"]))

        with self.assertRaisesRegex(SetupConflict, "--agent"):
            plan(
                self.wheel, agent="auto", home=self.home, prefix=self.prefix,
                detect_command=lambda argv: "other\n",
            )
        self.assertFalse(self.prefix.exists())
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_repair_stages_install_and_preserves_old_environment_on_failure(self):
        from scripts import setup_companion
        self.assertTrue(hasattr(setup_companion, "repair"), "explicit repair is missing")
        install, repair = setup_companion.install, setup_companion.repair

        install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        venv = self.prefix / "share/omarchy-knowledge-next/venv"
        old = venv / "old-marker"
        old.write_text("keep old", encoding="utf-8")
        cache = self.home / ".cache/omarchy-knowledge-next/accepted.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("cache", encoding="utf-8")
        draft = self.home / "drafts/keep.json"
        draft.parent.mkdir()
        draft.write_text("draft", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            repair(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands(fail_pip=True))

        self.assertEqual(old.read_text(encoding="utf-8"), "keep old")
        self.assertEqual(cache.read_text(encoding="utf-8"), "cache")
        self.assertEqual(draft.read_text(encoding="utf-8"), "draft")
        self.assertTrue((self.prefix / "bin/omarchy-knowledge").is_symlink())

    def test_repair_replaces_only_owned_environment_using_system_python(self):
        from scripts import setup_companion
        self.assertTrue(hasattr(setup_companion, "repair"), "explicit repair is missing")
        install, repair = setup_companion.install, setup_companion.repair

        install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        venv = self.prefix / "share/omarchy-knowledge-next/venv"
        (venv / "old-marker").write_text("broken", encoding="utf-8")
        commands = FakeCommands()

        result = repair(self.wheel, agent="claude", home=self.home, prefix=self.prefix, command=commands)

        self.assertEqual(result["status"], "repaired")
        self.assertFalse((venv / "old-marker").exists())
        self.assertEqual(commands.calls[0][:3], [os.fspath(__import__("sys").executable), "-m", "venv"])
        self.assertTrue((self.home / ".claude/skills/omarchy-knowledge-research").is_symlink())
        self.assertTrue((self.home / ".agents/skills/omarchy-knowledge-research").is_symlink())

    def test_repair_reports_a_retained_backup_when_post_success_cleanup_fails(self):
        from unittest.mock import patch
        from scripts.setup_companion import install, repair

        install(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        venv = self.prefix / "share/omarchy-knowledge-next/venv"
        (venv / "old-marker").write_text("old", encoding="utf-8")
        real_rmtree = __import__("shutil").rmtree

        def cleanup(path):
            if Path(path).name.startswith(".venv-backup-"):
                raise OSError("cleanup denied")
            return real_rmtree(path)

        try:
            with patch("scripts.setup_companion.shutil.rmtree", side_effect=cleanup):
                repair(self.wheel, home=self.home, prefix=self.prefix, command=FakeCommands())
        except Exception as error:
            caught = error
        else:
            caught = None

        self.assertIsInstance(caught, RuntimeError)
        self.assertIn("replacement is working", str(caught))
        self.assertIn("retained", str(caught))
        backups = list(venv.parent.glob(".venv-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old-marker").read_text(encoding="utf-8"), "old")
        self.assertFalse((venv / "old-marker").exists())


if __name__ == "__main__":
    unittest.main()

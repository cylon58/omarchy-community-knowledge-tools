import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch


class SkillExposureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def test_dry_run_uses_documented_codex_location_without_changes(self):
        from omarchy_knowledge.skill_exposure import plan_for_codex

        result = plan_for_codex(self.home)

        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(len(result["actions"]), 2)
        self.assertTrue(all("/.agents/skills/" in item["path"] for item in result["actions"]))
        self.assertTrue(all(item["result"] == "would-install" for item in result["actions"]))
        self.assertFalse((self.home / ".agents").exists())

    def test_install_is_idempotent_and_remove_touches_only_owned_links(self):
        from omarchy_knowledge.skill_exposure import install_for_codex, remove_for_codex

        first = install_for_codex(self.home)
        links = [Path(item["path"]) for item in first["actions"]]
        self.assertTrue(all(path.is_symlink() for path in links))
        second = install_for_codex(self.home)
        self.assertEqual({item["result"] for item in second["actions"]}, {"already-installed"})

        replacement = self.home / "replacement"
        replacement.mkdir()
        links[0].unlink()
        links[0].symlink_to(replacement, target_is_directory=True)
        removed = remove_for_codex(self.home)

        self.assertEqual({item["result"] for item in removed["actions"]}, {"preserved-modified", "removed"})
        self.assertEqual(os.readlink(links[0]), str(replacement))
        self.assertFalse(os.path.lexists(links[1]))

    def test_install_repairs_an_owned_link_that_became_absent(self):
        from omarchy_knowledge.skill_exposure import install_for_codex

        installed = install_for_codex(self.home)
        missing = Path(installed["actions"][0]["path"])
        missing.unlink()

        repaired = install_for_codex(self.home)

        self.assertTrue(missing.is_symlink())
        self.assertEqual(
            {item["result"] for item in repaired["actions"]},
            {"installed", "already-installed"},
        )

    def test_occupied_current_or_legacy_skill_aborts_without_overwrite(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, install_for_codex

        for root in (".agents/skills", ".codex/skills"):
            with self.subTest(root=root):
                home = self.home / root.replace("/", "-")
                occupied = home / root / "omarchy-knowledge-research"
                occupied.parent.mkdir(parents=True)
                occupied.write_bytes(b"keep")
                before = occupied.read_bytes()

                with self.assertRaises(ExposureConflict):
                    install_for_codex(home)

                self.assertEqual(occupied.read_bytes(), before)
                self.assertFalse(os.path.lexists(home / ".agents/skills/omarchy-knowledge-contribution"))

    def test_unsafe_parent_symlink_is_refused(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, install_for_codex

        outside = self.home / "outside"
        outside.mkdir()
        home = self.home / "home"
        home.mkdir()
        (home / ".agents").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ExposureConflict):
            install_for_codex(home)

        self.assertEqual(list(outside.iterdir()), [])

    def test_tampered_receipt_cannot_authorize_unrelated_removal(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, receipt_path, remove_for_codex

        outside = self.home / "outside"
        outside.mkdir()
        link = self.home / "unrelated"
        link.symlink_to(outside, target_is_directory=True)
        receipt_path(self.home).parent.mkdir(parents=True)
        receipt_path(self.home).write_text(json.dumps({
            "version": 1,
            "links": [{
                "agent": "codex",
                "name": "omarchy-knowledge-research",
                "path": str(link),
                "target": str(outside),
            }],
        }), encoding="utf-8")

        with self.assertRaises(ExposureConflict):
            remove_for_codex(self.home)
        self.assertTrue(link.is_symlink())

    def test_remove_refuses_parent_symlinks_substituted_after_install(self):
        from omarchy_knowledge.skill_exposure import (
            ExposureConflict, install_for_codex, receipt_path, remove_for_codex,
        )

        for replaced_parent in (".agents", ".local"):
            with self.subTest(replaced_parent=replaced_parent):
                home = self.home / replaced_parent.removeprefix(".")
                home.mkdir()
                installed = install_for_codex(home)
                links = [Path(item["path"]) for item in installed["actions"]]
                outside = home / "outside"
                outside.mkdir()
                original = home / replaced_parent
                saved = home / (replaced_parent.removeprefix(".") + "-owned")
                original.rename(saved)
                if replaced_parent == ".agents":
                    outside_skills = outside / "skills"
                    outside_skills.mkdir()
                    for item in installed["actions"]:
                        (outside_skills / item["name"]).symlink_to(item["target"])
                else:
                    external_receipt = outside / "state/omarchy-knowledge-next/skill-exposure.json"
                    external_receipt.parent.mkdir(parents=True)
                    external_receipt.write_bytes(
                        saved.joinpath("state/omarchy-knowledge-next/skill-exposure.json").read_bytes()
                    )
                original.symlink_to(outside, target_is_directory=True)

                with self.assertRaises(ExposureConflict):
                    remove_for_codex(home)

                if replaced_parent == ".agents":
                    self.assertTrue(all(path.is_symlink() for path in outside.joinpath("skills").iterdir()))
                else:
                    self.assertTrue(receipt_path(home).is_file())
                self.assertTrue(all(path.is_symlink() for path in links) if replaced_parent == ".local"
                                else all((saved / "skills" / path.name).is_symlink() for path in links))

    def test_cli_requires_explicit_codex_selection(self):
        from omarchy_knowledge.cli import main

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["skills", "dry-run", "--agent", "codex", "--home", str(self.home)])

        self.assertEqual(code, 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["mode"], "dry-run")
        self.assertTrue(all("/.agents/skills/" in item["path"] for item in value["actions"]))

    def test_each_supported_agent_uses_its_documented_skill_location(self):
        from omarchy_knowledge import skill_exposure
        self.assertTrue(hasattr(skill_exposure, "plan_for_agent"), "generic agent planning is missing")
        from omarchy_knowledge.agents import AGENT_SKILL_PATHS
        self.assertIn("agy", AGENT_SKILL_PATHS, "current Omarchy Antigravity target is missing")
        plan_for_agent = skill_exposure.plan_for_agent

        expected = {
            "codex": ".agents/skills",
            "claude": ".claude/skills",
            "opencode": ".agents/skills",
            "gemini": ".agents/skills",
            "agy": ".gemini/antigravity-cli/skills",
        }
        for agent, relative in expected.items():
            with self.subTest(agent=agent):
                result = plan_for_agent(self.home, agent)
                self.assertEqual({item["agent"] for item in result["actions"]}, {agent})
                self.assertEqual(
                    {Path(item["path"]).parent for item in result["actions"]},
                    {self.home / relative},
                )

    def test_auto_detection_is_injected_and_unknown_or_empty_output_never_mutates(self):
        from omarchy_knowledge import skill_exposure
        self.assertTrue(hasattr(skill_exposure, "install_for_agent"), "generic agent installation is missing")
        ExposureConflict = skill_exposure.ExposureConflict
        install_for_agent = skill_exposure.install_for_agent

        calls = []

        def detect(argv):
            calls.append(argv)
            return "unknown-agent\n"

        with self.assertRaisesRegex(ExposureConflict, "--agent"):
            install_for_agent(self.home, "auto", detect_command=detect)
        self.assertEqual(calls, [["omarchy-default-agent"]])
        self.assertEqual(list(self.home.iterdir()), [])

        with self.assertRaisesRegex(ExposureConflict, "could not be detected"):
            install_for_agent(self.home, "auto", detect_command=lambda argv: "\n")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_auto_agy_install_and_remove_use_the_owned_antigravity_path(self):
        from omarchy_knowledge import skill_exposure
        from omarchy_knowledge.agents import AGENT_SKILL_PATHS
        self.assertIn("agy", AGENT_SKILL_PATHS, "current Omarchy Antigravity target is missing")

        detect = lambda argv: "agy\n"
        installed = skill_exposure.install_for_agent(self.home, "auto", detect_command=detect)
        paths = [Path(item["path"]) for item in installed["actions"]]
        self.assertEqual({path.parent for path in paths}, {self.home / ".gemini/antigravity-cli/skills"})
        self.assertTrue(all(path.is_symlink() for path in paths))

        removed = skill_exposure.remove_for_agent(self.home, "auto", detect_command=detect)

        self.assertEqual({item["result"] for item in removed["actions"]}, {"removed"})
        self.assertTrue(all(not os.path.lexists(path) for path in paths))
        self.assertFalse(skill_exposure.receipt_path(self.home).exists())

    def test_shared_path_is_idempotent_then_switching_tracks_all_owned_links(self):
        from omarchy_knowledge import skill_exposure
        self.assertTrue(hasattr(skill_exposure, "install_for_agent"), "generic agent installation is missing")
        install_for_agent = skill_exposure.install_for_agent
        receipt_path = skill_exposure.receipt_path
        remove_for_agent = skill_exposure.remove_for_agent

        codex = install_for_agent(self.home, "codex")
        shared = install_for_agent(self.home, "opencode")
        claude = install_for_agent(self.home, "claude")

        self.assertEqual({item["result"] for item in codex["actions"]}, {"installed"})
        self.assertEqual({item["result"] for item in shared["actions"]}, {"already-installed"})
        self.assertEqual({item["result"] for item in claude["actions"]}, {"installed"})
        receipt = json.loads(receipt_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(len(receipt["links"]), 4)

        removed = remove_for_agent(self.home, "gemini")
        self.assertEqual({item["result"] for item in removed["actions"]}, {"removed"})
        self.assertFalse(receipt_path(self.home).exists())
        self.assertFalse(any(os.path.lexists(item["path"]) for item in [*codex["actions"], *claude["actions"]]))

    def test_switching_agent_preserves_a_conflicting_destination_and_existing_owned_links(self):
        from omarchy_knowledge import skill_exposure
        self.assertTrue(hasattr(skill_exposure, "install_for_agent"), "generic agent installation is missing")
        ExposureConflict = skill_exposure.ExposureConflict
        install_for_agent = skill_exposure.install_for_agent

        installed = install_for_agent(self.home, "codex")
        conflict = self.home / ".claude/skills/omarchy-knowledge-research"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("keep", encoding="utf-8")

        with self.assertRaises(ExposureConflict):
            install_for_agent(self.home, "claude")

        self.assertEqual(conflict.read_text(encoding="utf-8"), "keep")
        self.assertTrue(all(Path(item["path"]).is_symlink() for item in installed["actions"]))
        self.assertFalse(os.path.lexists(self.home / ".claude/skills/omarchy-knowledge-contribution"))

    def test_cli_defaults_to_auto_and_accepts_each_explicit_agent(self):
        from omarchy_knowledge.cli import main

        output = io.StringIO()
        with patch("omarchy_knowledge.skill_exposure.resolve_agent", return_value="claude") as detect, \
                redirect_stdout(output):
            try:
                code = main(["skills", "dry-run", "--home", str(self.home)])
            except SystemExit as error:
                code = error.code
        self.assertEqual(code, 0)
        detect.assert_called_once()
        self.assertTrue(all("/.claude/skills/" in item["path"] for item in json.loads(output.getvalue())["actions"]))

        for agent in ("codex", "claude", "opencode", "gemini", "agy"):
            with self.subTest(agent=agent), redirect_stdout(io.StringIO()):
                try:
                    code = main(["skills", "dry-run", "--agent", agent, "--home", str(self.home)])
                except SystemExit as error:
                    code = error.code
                self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

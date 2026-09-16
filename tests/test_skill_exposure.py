import json
import os
from pathlib import Path
import tempfile
import unittest


class SkillExposureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def test_bundled_skills_are_the_single_install_source(self):
        from omarchy_knowledge.skill_exposure import bundled_skills

        skills = bundled_skills()
        self.assertEqual(
            set(skills),
            {"omarchy-knowledge-research", "omarchy-knowledge-contribution"},
        )
        for source in skills.values():
            self.assertTrue((source / "SKILL.md").is_file())
            self.assertIn("omarchy_knowledge/skills", source.as_posix())

    def test_dry_run_selects_only_requested_agent_and_changes_nothing(self):
        from omarchy_knowledge.skill_exposure import expose_skills

        result = expose_skills(self.home, agents=("codex",), mode="dry-run")

        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual({item["agent"] for item in result["actions"]}, {"codex"})
        self.assertEqual(len(result["actions"]), 2)
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_install_and_remove_touch_only_matching_owned_links(self):
        from omarchy_knowledge.skill_exposure import expose_skills

        installed = expose_skills(self.home, agents=("codex",), mode="install")
        links = [Path(item["path"]) for item in installed["actions"]]
        self.assertTrue(all(path.is_symlink() for path in links))
        self.assertFalse((self.home / ".agents").exists())

        replacement = self.home / "replacement"
        replacement.mkdir()
        links[0].unlink()
        links[0].symlink_to(replacement)

        removed = expose_skills(self.home, agents=("codex",), mode="remove")
        self.assertTrue(links[0].is_symlink())
        self.assertEqual(links[0].resolve(), replacement)
        self.assertFalse(links[1].exists())
        self.assertEqual(
            {item["result"] for item in removed["actions"]},
            {"preserved-modified", "removed"},
        )

    def test_existing_destination_aborts_install_without_overwrite(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, expose_skills

        destination = self.home / ".codex" / "skills" / "omarchy-knowledge-research"
        destination.mkdir(parents=True)
        marker = destination / "user.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(ExposureConflict):
            expose_skills(self.home, agents=("codex",), mode="install")

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(
            (self.home / ".codex" / "skills" / "omarchy-knowledge-contribution").exists()
        )

    def test_profiles_are_never_inferred(self):
        from omarchy_knowledge.skill_exposure import expose_skills

        expose_skills(self.home, agents=("hermes",), mode="install")

        self.assertTrue((self.home / ".hermes" / "skills").is_dir())
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_named_hermes_profile_does_not_select_default_or_sibling(self):
        from omarchy_knowledge.skill_exposure import expose_skills

        coder = self.home / ".hermes/profiles/coder"
        sibling = self.home / ".hermes/profiles/research"
        coder.mkdir(parents=True)
        sibling.mkdir(parents=True)

        result = expose_skills(
            self.home, agents=(), hermes_profiles=("coder",), mode="install"
        )

        self.assertEqual({item["profile"] for item in result["actions"]}, {"coder"})
        self.assertTrue((coder / "skills/omarchy-knowledge-research").is_symlink())
        self.assertFalse((sibling / "skills").exists())
        self.assertFalse((self.home / ".hermes/skills").exists())

    def test_default_and_named_hermes_profiles_must_both_be_explicit(self):
        from omarchy_knowledge.skill_exposure import expose_skills

        profile = self.home / ".hermes/profiles/coder"
        profile.mkdir(parents=True)
        result = expose_skills(
            self.home,
            agents=("hermes",),
            hermes_profiles=("coder",),
            mode="install",
        )

        self.assertEqual(len(result["actions"]), 4)
        self.assertTrue((self.home / ".hermes/skills/omarchy-knowledge-research").is_symlink())
        self.assertTrue((profile / "skills/omarchy-knowledge-research").is_symlink())

    def test_hermes_profile_rejects_missing_symlink_and_traversal(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, expose_skills

        profiles = self.home / ".hermes/profiles"
        profiles.mkdir(parents=True)
        real = self.home / "real-profile"
        real.mkdir()
        (profiles / "linked").symlink_to(real, target_is_directory=True)
        linked_skills = profiles / "linked-skills"
        linked_skills.mkdir()
        (linked_skills / "skills").symlink_to(real, target_is_directory=True)

        with self.assertRaises(ExposureConflict):
            expose_skills(self.home, agents=(), hermes_profiles=("missing",), mode="install")
        with self.assertRaises(ExposureConflict):
            expose_skills(self.home, agents=(), hermes_profiles=("linked",), mode="install")
        with self.assertRaises(ExposureConflict):
            expose_skills(
                self.home, agents=(), hermes_profiles=("linked-skills",), mode="install"
            )
        for invalid in ("../escape", "nested/profile", "Uppercase", ".", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                expose_skills(self.home, agents=(), hermes_profiles=(invalid,), mode="install")
        self.assertFalse((real / "skills").exists())

    def test_removing_one_named_hermes_profile_preserves_the_other(self):
        from omarchy_knowledge.skill_exposure import expose_skills, receipt_path

        first = self.home / ".hermes/profiles/first"
        second = self.home / ".hermes/profiles/second"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        expose_skills(
            self.home, agents=(), hermes_profiles=("first", "second"), mode="install"
        )

        expose_skills(self.home, agents=(), hermes_profiles=("first",), mode="remove")

        self.assertFalse((first / "skills/omarchy-knowledge-research").exists())
        self.assertTrue((second / "skills/omarchy-knowledge-research").is_symlink())
        receipt = json.loads(receipt_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual({item.get("profile") for item in receipt["links"]}, {"second"})

    def test_remove_without_a_receipt_does_not_create_state(self):
        from omarchy_knowledge.skill_exposure import expose_skills, receipt_path

        result = expose_skills(self.home, agents=("generic",), mode="remove")

        self.assertEqual({item["result"] for item in result["actions"]}, {"not-owned"})
        self.assertFalse(receipt_path(self.home).parent.exists())

    def test_receipt_is_bounded_and_contains_only_owned_links(self):
        from omarchy_knowledge.skill_exposure import expose_skills, receipt_path

        expose_skills(self.home, agents=("pi",), mode="install")
        receipt = json.loads(receipt_path(self.home).read_text(encoding="utf-8"))

        self.assertEqual(receipt["version"], 1)
        self.assertEqual(len(receipt["links"]), 2)
        self.assertTrue(all(item["agent"] == "pi" for item in receipt["links"]))
        self.assertTrue(all(".pi/agent/skills" in item["path"] for item in receipt["links"]))

    def test_cumulative_receipt_limit_is_preflighted_without_mutation(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, expose_skills, receipt_path

        profiles = self.home / ".hermes/profiles"
        first = tuple(f"first-{number}" for number in range(8))
        proposed = tuple(f"proposed-{number}" for number in range(5))
        for profile in first + proposed:
            (profiles / profile).mkdir(parents=True)
        expose_skills(self.home, hermes_profiles=first, mode="install")
        receipt_before = receipt_path(self.home).read_bytes()
        existing_links = [
            profiles / profile / "skills" / skill
            for profile in first
            for skill in ("omarchy-knowledge-research", "omarchy-knowledge-contribution")
        ]
        self.assertTrue(all(path.is_symlink() for path in existing_links))

        with self.assertRaises(ExposureConflict):
            expose_skills(self.home, hermes_profiles=proposed, mode="install")

        self.assertEqual(receipt_path(self.home).read_bytes(), receipt_before)
        self.assertTrue(all(path.is_symlink() for path in existing_links))
        self.assertTrue(all(not (profiles / profile / "skills").exists() for profile in proposed))

        expose_skills(self.home, hermes_profiles=first, mode="remove")
        self.assertTrue(all(not path.exists() for path in existing_links))
        self.assertFalse(receipt_path(self.home).exists())

    def test_remove_uses_the_recorded_target_after_toolkit_relocation(self):
        from omarchy_knowledge.skill_exposure import expose_skills, receipt_path

        expose_skills(self.home, agents=("codex",), mode="install")
        receipt = json.loads(receipt_path(self.home).read_text(encoding="utf-8"))
        entry = receipt["links"][0]
        link = Path(entry["path"])
        old_target = self.home / "old-venv/omarchy_knowledge/skills" / entry["name"]
        old_target.mkdir(parents=True)
        link.unlink()
        link.symlink_to(old_target)
        entry["target"] = str(old_target)
        receipt_path(self.home).write_text(json.dumps(receipt), encoding="utf-8")

        result = expose_skills(self.home, agents=("codex",), mode="remove")

        self.assertFalse(link.exists())
        self.assertIn("removed", {item["result"] for item in result["actions"]})

    def test_tampered_receipt_cannot_authorize_an_unrelated_removal(self):
        from omarchy_knowledge.skill_exposure import ExposureConflict, expose_skills, receipt_path

        outside_target = self.home / "outside-target"
        outside_target.mkdir()
        outside_link = self.home / "unrelated-link"
        outside_link.symlink_to(outside_target)
        receipt_path(self.home).parent.mkdir(parents=True)
        receipt_path(self.home).write_text(json.dumps({
            "version": 1,
            "links": [{
                "agent": "codex",
                "name": "omarchy-knowledge-research",
                "path": str(outside_link),
                "target": str(outside_target),
            }],
        }), encoding="utf-8")

        with self.assertRaises(ExposureConflict):
            expose_skills(self.home, agents=("codex",), mode="remove")
        self.assertTrue(outside_link.is_symlink())


if __name__ == "__main__":
    unittest.main()

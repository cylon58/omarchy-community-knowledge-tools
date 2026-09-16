import json
from pathlib import Path
import unittest


class CompanionPluginFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_manifest_declares_the_real_panel_identity(self):
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["id"], "community.knowledge")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["kinds"], ["panel"])
        self.assertEqual(manifest["entryPoints"], {"panel": "Panel.qml"})

    def test_panel_uses_official_lifecycle_plain_text_and_bounded_bridge(self):
        panel = (self.root / "Panel.qml").read_text(encoding="utf-8")
        self.assertIn("function open(payloadJson)", panel)
        self.assertIn("function close()", panel)
        self.assertIn('bridge.command = ["omarchy-knowledge-panel"', panel)
        self.assertIn("textFormat: TextEdit.PlainText", panel)
        self.assertIn("responseLimit", panel)
        self.assertIn("property bool responseAccepted", panel)
        self.assertIn("!root.responseAccepted", panel)
        self.assertNotIn("shell -c", panel)
        self.assertNotIn("--approve", panel)


if __name__ == "__main__":
    unittest.main()

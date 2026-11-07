"""Testes do instalador. Execute com: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "setup"))

import desktop  # noqa: E402

WINGS = 'Some(([\n    "A",\n], [\n    "com.system76.CosmicAppletInputSources",\n    "com.system76.CosmicAppletA11y",\n    "B",\n]))'
BACKGROUND = '(\n    output: "all",\n    source: Path("/antigo.jpg"),\n    filter_by_theme: true,\n)'


class TextTest(unittest.TestCase):
    def test_removes_only_the_two_applets(self):
        result = desktop.without_applets(WINGS)
        self.assertNotIn("InputSources", result)
        self.assertNotIn("A11y", result)
        self.assertIn('"A"', result)
        self.assertIn('"B"', result)

    def test_wallpaper_keeps_the_other_fields(self):
        result = desktop.with_wallpaper(BACKGROUND, "/novo.jpg")
        self.assertIn('source: Path("/novo.jpg")', result)
        self.assertIn("filter_by_theme: true", result)

    def test_wallpaper_replaces_a_solid_color(self):
        text = '(\n    source: Color(Single((0.1, 0.2, 0.3))),\n    filter_by_theme: true,\n)'
        result = desktop.with_wallpaper(text, "/novo.jpg")
        self.assertIn('source: Path("/novo.jpg"),', result)
        self.assertIn("filter_by_theme: true", result)

    def test_wallpaper_replaces_a_color_on_several_lines(self):
        text = '(\n    source: Color(Single((\n        0.1,\n        0.2,\n    ))),\n    filter_by_theme: true,\n)'
        self.assertEqual(desktop.with_wallpaper(text, "/novo.jpg"),
                         '(\n    source: Path("/novo.jpg"),\n    filter_by_theme: true,\n)')

    def test_vscode_colors_are_scoped_to_the_theme(self):
        settings = desktop.merge_vscode({"workbench.colorTheme": "T", "editor.fontSize": 14}, {"a": "#111111"})
        self.assertEqual(settings[desktop.CUSTOMIZATIONS], {"[T]": {"a": "#111111"}})
        self.assertEqual(settings["editor.fontSize"], 14)

    def test_vscode_removal_keeps_colors_of_the_user(self):
        settings = {desktop.CUSTOMIZATIONS: {"[T]": {"a": "#111111", "minha": "#222222"}}}
        result = desktop.strip_vscode(settings, {"a": "#111111"})
        self.assertEqual(result[desktop.CUSTOMIZATIONS], {"[T]": {"minha": "#222222"}})

    def test_vscode_removal_cleans_an_empty_block(self):
        settings = {"x": 1, desktop.CUSTOMIZATIONS: {"[T]": {"a": "#111111"}}}
        self.assertEqual(desktop.strip_vscode(settings, {"a": "#111111"}), {"x": 1})


class ApplyTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = self.root / "config"
        self.panel = self.config / "cosmic/com.system76.CosmicPanel.Panel/v1"
        self.dock = self.config / "cosmic/com.system76.CosmicPanel.Dock/v1"
        self.background = self.config / "cosmic/com.system76.CosmicBackground/v1"
        for folder_path in (self.panel, self.dock, self.background, self.config / "Code/User"):
            folder_path.mkdir(parents=True)
        (self.panel / "opacity").write_text("1.0")
        (self.panel / "plugins_wings").write_text(WINGS)
        (self.background / "all").write_text(BACKGROUND)
        (self.config / "Code/User/settings.json").write_text('{"workbench.colorTheme": "T"}')
        self.desktop = desktop.Desktop(self.config, self.root / "data")

    def test_apply_then_restore_returns_to_the_original(self):
        self.desktop.apply()
        self.assertEqual((self.panel / "opacity").read_text(), "0.55")
        self.assertNotIn("A11y", (self.panel / "plugins_wings").read_text())
        self.assertIn("twilight-journey", (self.background / "all").read_text())

        self.desktop.restore()
        self.assertEqual((self.panel / "opacity").read_text(), "1.0")
        self.assertEqual((self.panel / "plugins_wings").read_text(), WINGS)
        self.assertEqual((self.background / "all").read_text(), BACKGROUND)
        self.assertFalse((self.panel / "background").exists())  # não existia antes
        self.assertEqual(json.loads((self.config / "Code/User/settings.json").read_text()),
                         {"workbench.colorTheme": "T"})

    def test_second_apply_keeps_the_first_backup(self):
        self.desktop.apply()
        self.desktop.apply()
        self.desktop.restore()
        self.assertEqual((self.panel / "opacity").read_text(), "1.0")

    def test_vscode_settings_with_comments_are_left_alone(self):
        path = self.config / "Code/User/settings.json"
        path.write_text('{\n  // meu comentário\n  "a": 1\n}')
        self.desktop.apply_vscode()
        self.assertIn("meu comentário", path.read_text())

    def test_machine_without_vscode(self):
        (self.config / "Code/User/settings.json").unlink()
        (self.config / "Code/User").rmdir()
        (self.config / "Code").rmdir()
        self.desktop.apply_vscode()
        self.assertFalse((self.config / "Code").exists())


if __name__ == "__main__":
    unittest.main()

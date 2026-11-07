#!/usr/bin/env python3
"""Aplica e desfaz o visual no COSMIC: painel, Dock, bateria, wallpaper e VS Code.

Antes de alterar um arquivo, guarda o original uma única vez. Rodar de novo não
sobrescreve o backup, então o desinstalador sempre volta ao estado de antes.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from thinkpad_widgets.theme import latest_version  # noqa: E402

GLASS = {
    "background": "Color((0.11, 0.039, 0.141))",
    "opacity": "0.55",
    "border_width": "1.0",
}
REMOVED_APPLETS = ("com.system76.CosmicAppletInputSources", "com.system76.CosmicAppletA11y")
WALLPAPER_TEMPLATE = """(
    output: "all",
    source: Path("{path}"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
"""
CUSTOMIZATIONS = "workbench.colorCustomizations"


def without_applets(text, applets=REMOVED_APPLETS):
    for applet in applets:
        text = re.sub(r'\s*"' + re.escape(applet) + r'",', "", text)
    return text


def with_wallpaper(text, path):
    if not text.strip():
        return WALLPAPER_TEMPLATE.format(path=path)
    # O valor pode ocupar várias linhas: vai até o próximo campo do mesmo nível.
    return re.sub(r"source:.*?,\n(?=    \w+:|\))", f'source: Path("{path}"),\n', text, count=1, flags=re.S)


def merge_vscode(settings, colors):
    """Limita as cores ao tema atual, para não atrapalhar se ele for trocado."""
    customizations = settings.setdefault(CUSTOMIZATIONS, {})
    theme = settings.get("workbench.colorTheme")
    if theme:
        customizations.setdefault(f"[{theme}]", {}).update(colors)
    else:
        customizations.update(colors)
    return settings


def strip_vscode(settings, colors):
    customizations = settings.get(CUSTOMIZATIONS, {})
    for key in list(customizations):
        if isinstance(customizations[key], dict):
            for name in colors:
                customizations[key].pop(name, None)
            if not customizations[key]:
                del customizations[key]
        elif key in colors:
            del customizations[key]
    if not customizations:
        settings.pop(CUSTOMIZATIONS, None)
    return settings


class Desktop:
    def __init__(self, config_home, data_home, project=PROJECT):
        self.config = Path(config_home)
        self.cosmic = self.config / "cosmic"
        self.backups = self.config / "thinkpad-monitor/panel-backup"
        self.wallpaper_dir = Path(data_home) / "thinkpad-widgets/wallpaper"
        self.vscode = self.config / "Code/User/settings.json"
        self.project = Path(project)

    def _backup(self, path, name):
        self.backups.mkdir(parents=True, exist_ok=True)
        copy = self.backups / name
        missing = self.backups / f"{name}.ausente"
        if copy.exists() or missing.exists():
            return
        if path.exists():
            shutil.copy2(path, copy)
        else:
            missing.touch()

    def _restore(self, path, name):
        copy = self.backups / name
        if copy.exists():
            shutil.copy2(copy, path)
        elif (self.backups / f"{name}.ausente").exists():
            path.unlink(missing_ok=True)

    def _write(self, path, text, name):
        self._backup(path, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _panel_files(self):
        for panel in ("Panel", "Dock"):
            folder = latest_version(self.cosmic / f"com.system76.CosmicPanel.{panel}")
            for field in GLASS:
                yield folder / field, f"{panel}.{field}", GLASS[field]

    def _plugins_file(self):
        return latest_version(self.cosmic / "com.system76.CosmicPanel.Panel") / "plugins_wings"

    def _battery_file(self):
        return latest_version(self.cosmic / "com.system76.CosmicAppletBattery") / "show_percentage"

    def _mode_file(self):
        return latest_version(self.cosmic / "com.system76.CosmicTheme.Mode") / "is_dark"

    def _background_files(self):
        folder = latest_version(self.cosmic / "com.system76.CosmicBackground")
        return folder / "all", folder / "same-on-all"

    def apply_panel(self):
        for path, name, value in self._panel_files():
            self._write(path, value, name)
        plugins = self._plugins_file()
        if plugins.exists():
            self._write(plugins, without_applets(plugins.read_text()), "Panel.plugins_wings")
        print("Painel e Dock: vidro aplicado; applets de teclado e acessibilidade removidos.")

    def apply_battery(self):
        path = self._battery_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("true")
        print("Bateria: porcentagem ligada.")

    def apply_dark_mode(self):
        self._write(self._mode_file(), "true", "Mode.is_dark")
        print("Modo escuro ligado (o tema só vale nele).")

    def apply_wallpaper(self):
        images = sorted((self.project / "wallpaper").glob("*.jpg"))
        if not images:
            print("Wallpaper: nenhuma imagem no projeto; mantendo o atual.")
            return
        self.wallpaper_dir.mkdir(parents=True, exist_ok=True)
        target = self.wallpaper_dir / images[0].name
        shutil.copy2(images[0], target)
        settings, same = self._background_files()
        current = settings.read_text() if settings.exists() else ""
        self._write(settings, with_wallpaper(current, target), "Background.all")
        self._write(same, "true", "Background.same-on-all")
        print("Wallpaper aplicado.")

    def apply_vscode(self):
        if not self.vscode.parent.exists():
            print("VS Code: não encontrado; pulando.")
            return
        try:
            settings = json.loads(self.vscode.read_text()) if self.vscode.exists() else {}
        except ValueError:
            print("VS Code: settings.json tem comentários; pulando para não estragá-lo.")
            print(f"  Cole as cores de {self.project / 'config/vscode-colors.json'} manualmente.")
            return
        colors = json.loads((self.project / "config/vscode-colors.json").read_text())
        self.vscode.write_text(json.dumps(merge_vscode(settings, colors), indent=4, ensure_ascii=False) + "\n")
        print("VS Code: cores aplicadas.")

    def apply(self):
        self.apply_panel()
        self.apply_battery()
        self.apply_dark_mode()
        self.apply_wallpaper()
        self.apply_vscode()

    def restore(self):
        for path, name, _value in self._panel_files():
            self._restore(path, name)
        self._restore(self._plugins_file(), "Panel.plugins_wings")
        self._battery_file().unlink(missing_ok=True)
        self._restore(self._mode_file(), "Mode.is_dark")
        settings, same = self._background_files()
        self._restore(settings, "Background.all")
        self._restore(same, "Background.same-on-all")
        if self.vscode.exists():
            try:
                current = json.loads(self.vscode.read_text())
            except ValueError:
                print("VS Code: settings.json tem comentários; remova as cores manualmente.")
            else:
                colors = json.loads((self.project / "config/vscode-colors.json").read_text())
                self.vscode.write_text(json.dumps(strip_vscode(current, colors), indent=4, ensure_ascii=False) + "\n")
        print("Painel, Dock, bateria, wallpaper e VS Code restaurados.")


def main():
    home = Path.home()
    desktop = Desktop(os.environ.get("XDG_CONFIG_HOME", home / ".config"),
                      os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "apply":
        desktop.apply()
    elif action == "restore":
        desktop.restore()
    else:
        sys.exit("uso: desktop.py apply|restore")


if __name__ == "__main__":
    main()

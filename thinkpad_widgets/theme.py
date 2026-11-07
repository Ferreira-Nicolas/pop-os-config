"""Cores do painel Aparência do COSMIC, lidas dos arquivos de configuração."""

import colorsys
import os
import re
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_ROOT = CONFIG_HOME / "cosmic"
OVERRIDES_FILE = CONFIG_HOME / "thinkpad-monitor/widgets.conf"
OVERRIDE_NAMES = {
    "PRIMARY_COLOR": "primary",
    "SECONDARY_COLOR": "secondary",
    "COOL_COLOR": "cool",
    "GLASS_COLOR": "glass",
}
HEX_COLOR = re.compile(r'"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?"')
FLOAT_COLOR = re.compile(r"red:\s*([\d.]+),\s*green:\s*([\d.]+),\s*blue:\s*([\d.]+)")

WARNING_HUES = (30, 75)  # graus

# Padrões do COSMIC, usados só se a configuração não puder ser lida.
FALLBACK = {
    "accent": (0.388, 0.816, 0.875),
    "success": (0.573, 0.812, 0.612),
    "warning": (0.969, 0.878, 0.384),
    "destructive": (0.992, 0.631, 0.627),
}


def parse_color(text):
    """Devolve o primeiro RGB de um arquivo do tema (o campo `base`).

    A pasta v2 grava "#RRGGBBAA"; a v1 grava red, green e blue separados.
    """
    match = HEX_COLOR.search(text)
    if match:
        digits = match.group(1)
        return tuple(int(digits[index:index + 2], 16) / 255 for index in (0, 2, 4))
    match = FLOAT_COLOR.search(text)
    if match:
        return tuple(float(value) for value in match.groups())
    raise ValueError("nenhuma cor encontrada")


def read_overrides(path):
    """Lê as cores escolhidas em widgets.conf; valores inválidos são ignorados."""
    overrides = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return overrides
    for line in lines:
        key, _, value = line.partition("=")
        name = OVERRIDE_NAMES.get(key.strip())
        match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
        if name and match:
            digits = match.group(1)
            overrides[name] = tuple(int(digits[index:index + 2], 16) / 255 for index in (0, 2, 4))
    return overrides


def latest_version(folder):
    """Pasta vN mais recente: o COSMIC deixa as antigas para trás, sem atualizar."""
    versions = [path for path in folder.glob("v*") if path.name[1:].isdigit()]
    return max(versions, key=lambda path: int(path.name[1:]), default=folder / "v1")


def shifted(color, hue_degrees=0.0, lightness=None, saturation=None):
    """Gira o matiz e ajusta a luminosidade mantendo a identidade da cor."""
    hue, light, sat = colorsys.rgb_to_hls(*color)
    hue = (hue + hue_degrees / 360) % 1
    return colorsys.hls_to_rgb(
        hue,
        light if lightness is None else lightness,
        sat if saturation is None else saturation,
    )


def companion(accent, hue_degrees, lightness):
    """Cor vizinha do acento, sem cair no amarelo reservado aos alertas."""
    color = shifted(accent, hue_degrees, lightness, 1.0)
    hue = colorsys.rgb_to_hls(*color)[0] * 360
    if WARNING_HUES[0] <= hue <= WARNING_HUES[1]:
        return shifted(accent, -hue_degrees, lightness, 1.0)
    return color


def mix(first, second, amount):
    return tuple(a + (b - a) * amount for a, b in zip(first, second))


class Theme:
    def __init__(self, root=CONFIG_ROOT, overrides=OVERRIDES_FILE):
        self.root = Path(root)
        self.overrides = Path(overrides)
        self.signature = None
        self.reload()

    def _variant(self):
        try:
            mode = latest_version(self.root / "com.system76.CosmicTheme.Mode")
            is_dark = (mode / "is_dark").read_text().strip()
        except OSError:
            is_dark = "true"
        return "Dark" if is_dark == "true" else "Light"

    def _files(self):
        folder = latest_version(self.root / f"com.system76.CosmicTheme.{self._variant()}")
        return {name: folder / name for name in FALLBACK}

    def _current_signature(self):
        signature = [self._variant()]
        for path in (*self._files().values(), self.overrides):
            try:
                signature.append(path.stat().st_mtime_ns)
            except OSError:
                signature.append(None)
        return signature

    def changed(self):
        """Indica se o usuário mudou a aparência desde a última leitura."""
        return self._current_signature() != self.signature

    def reload(self):
        colors = dict(FALLBACK)
        for name, path in self._files().items():
            try:
                colors[name] = parse_color(path.read_text())
            except (OSError, ValueError):
                pass
        self.signature = self._current_signature()

        self.accent = colors["accent"]
        self.success = colors["success"]
        self.warning = colors["warning"]
        self.destructive = colors["destructive"]

        # Um acento escuro como violeta some sobre vidro escuro: a versão clara
        # mantém o matiz e dá contraste. As demais giram o matiz do próprio
        # acento, então acompanham qualquer cor escolhida na Aparência.
        self.primary = shifted(self.accent, lightness=0.74, saturation=1.0)
        self.secondary = companion(self.accent, 48, 0.74)
        self.cool = companion(self.accent, -62, 0.72)
        self.success_end = shifted(self.success, hue_degrees=40)
        self.glass = mix(self.accent, (0.0, 0.0, 0.0), 0.86)

        for name, color in read_overrides(self.overrides).items():
            setattr(self, name, color)

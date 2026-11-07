"""Laço principal: uma superfície por cartão, redesenhadas a cada amostra.

Superfícies separadas mantêm os buffers pequenos e evitam compor o espaço
transparente entre os cartões.
"""

import argparse
import os
import sys
import time

from . import bar, cards
from .media import AudioCapture, Media
from .metrics import Metrics
from .theme import Theme
from .wayland import (ANCHOR_LEFT, ANCHOR_RIGHT, ANCHOR_TOP, LAYER_BOTTOM, LAYER_TOP, Connection,
                      Globals, LayerSurface)

NAMESPACE = "thinkpad-widgets"
EDGE_MARGIN = 16
TOP_MARGIN = 12
THEME_CHECK_SECONDS = 5
RESTART_DELAY_SECONDS = 1
PANEL_RESTART_DELAY_SECONDS = 3  # dá tempo de o painel novo aparecer na tela
FRAME_SECONDS = 0.04


def battery_cards(metrics):
    return [cards.BatteryCard()] if metrics.battery else []


def left_column(metrics):
    return [cards.ClockCard(), *battery_cards(metrics), cards.ControlsCard(), cards.NetworkCard()]


def right_column(metrics):
    storage = cards.StorageCard()
    storage.rows = max(len(metrics.disks), 1)
    return [cards.CpuCard(), cards.TemperatureCard(), cards.MemoryCard(), storage, cards.ProcessCard()]


def compact_column(metrics):
    return [cards.ClockCard(), *battery_cards(metrics), cards.TemperatureCard()]


def panel_restarted(previous, current):
    """Um painel novo (ou o primeiro) ficaria por cima das faixas."""
    return current is not None and current != previous


def choose_primary(outputs, requested):
    """A tela externa é a principal; sem ela, a do notebook assume tudo."""
    for output in outputs:
        if output.name == requested:
            return output
    external = [output for output in outputs if not output.name.startswith(("eDP", "LVDS"))]
    return (external or outputs)[0]


class Application:
    def __init__(self, arguments):
        self.interval = arguments.interval
        self.connection = Connection()
        self.connection.roundtrip()
        self.shared = Globals(self.connection)
        self.theme = Theme()
        self.metrics = Metrics()
        self.metrics.sample()
        self.panel_pid = self.metrics.panel_pid
        self.panels = []
        self.status_strips = []
        self.media_strips = []
        self.media = Media()
        self.capture = AudioCapture(arguments.audio_device)
        self.restart_at = None
        self.dirty = True

        # Sem nenhuma tela (tampa fechada, por exemplo), espera uma aparecer.
        if self.shared.outputs:
            primary = choose_primary(self.shared.outputs, arguments.primary)
            self.add(primary, left_column(self.metrics), ANCHOR_LEFT)
            self.add(primary, right_column(self.metrics), ANCHOR_RIGHT)
            for output in self.shared.outputs:
                if output is not primary:
                    self.add(output, compact_column(self.metrics), ANCHOR_LEFT)
            # Criadas por último para ficarem acima do painel, na mesma camada.
            for output in self.shared.outputs:
                self.add_status_strip(output)
            self.add_media_strip(primary)

        self.connection.on_global_change = self.on_global_change
        if not self.shared.blur_supported:
            print("aviso: o compositor não oferece blur; usando só transparência", file=sys.stderr)

    def add(self, output, column, side):
        top = TOP_MARGIN
        for card in column:
            surface = LayerSurface(
                self.shared, output, cards.WIDTH, card.height, ANCHOR_TOP | side,
                (top, EDGE_MARGIN, 0, EDGE_MARGIN), LAYER_BOTTOM, NAMESPACE,
                cards.blur_rectangles(card), self.schedule_restart,
            )
            self.panels.append((surface, card))
            top += card.height + cards.GAP

    def add_strip(self, output, strip, x):
        return LayerSurface(
            self.shared, output, strip.width, bar.HEIGHT, ANCHOR_TOP | ANCHOR_LEFT,
            (bar.TOP_MARGIN, 0, 0, x), LAYER_TOP, NAMESPACE, [], self.schedule_restart,
        ), strip

    def add_status_strip(self, output):
        strip = bar.StatusStrip(self.metrics)
        x = output.layout_width // 2 + bar.CLOCK_CLEARANCE
        self.status_strips.append(self.add_strip(output, strip, x))

    def add_media_strip(self, output):
        # Só na tela principal: animar as duas dobraria o custo enquanto algo toca.
        strip = bar.MediaStrip(self.media, self.capture)
        x = output.layout_width // 2 - bar.CLOCK_CLEARANCE - strip.width
        self.media_strips.append(self.add_strip(output, strip, x))

    def on_global_change(self, interface):
        if interface == "wl_output":
            self.schedule_restart()

    def schedule_restart(self, delay=RESTART_DELAY_SECONDS):
        """Monitor conectado ou removido: recomeça com o layout novo."""
        self.restart_at = time.monotonic() + delay

    def check_panel(self):
        if panel_restarted(self.panel_pid, self.metrics.panel_pid):
            self.schedule_restart(PANEL_RESTART_DELAY_SECONDS)
        self.panel_pid = self.metrics.panel_pid or self.panel_pid

    def draw(self):
        pending = False
        for surface, card in self.panels:
            if surface.can_draw():
                surface.draw(lambda context: cards.paint_card(context, self.theme, self.metrics, card))
            else:
                pending = True
        for surface, strip in self.status_strips:
            if surface.can_draw():
                surface.draw(lambda context: strip.paint(context, self.theme))
            else:
                pending = True
        self.draw_media()
        self.dirty = pending

    def draw_media(self):
        for surface, strip in self.media_strips:
            if surface.can_draw():
                surface.draw(lambda context: strip.paint(context, self.theme))

    def update_capture(self):
        """Só captura áudio enquanto algo toca, para não gastar bateria à toa."""
        if self.media.playing:
            self.capture.start()
        else:
            self.capture.stop()

    def run(self):
        next_sample = time.monotonic()
        next_theme_check = next_sample + THEME_CHECK_SECONDS
        next_frame = next_sample
        fresh_audio = False
        while True:
            now = time.monotonic()
            if self.restart_at and now >= self.restart_at:
                self.capture.stop()
                os.execv(sys.executable, [sys.executable, "-m", "thinkpad_widgets", *sys.argv[1:]])
            if now >= next_theme_check:
                next_theme_check = now + THEME_CHECK_SECONDS
                if self.theme.changed():
                    self.theme.reload()
                    self.dirty = True
            if now >= next_sample:
                next_sample = now + self.interval
                self.metrics.sample()
                self.check_panel()
                self.media.sample()
                self.update_capture()
                self.dirty = True
            if self.dirty:
                self.draw()

            descriptor = self.capture.descriptor
            animating = descriptor is not None or any(self.capture.spectrum.levels)
            timeout = max(next_sample - time.monotonic(), 0)
            if animating:
                timeout = min(timeout, max(next_frame - time.monotonic(), 0))
            if self.connection.wait(timeout, () if descriptor is None else (descriptor,)):
                self.capture.read()
                fresh_audio = True

            # O ritmo vem do relógio: eventos do compositor chegam na taxa do monitor,
            # que pode ser 180 Hz, e derrubariam as barras antes de serem vistas.
            now = time.monotonic()
            if animating and now >= next_frame:
                next_frame = now + FRAME_SECONDS
                if not fresh_audio:
                    self.capture.spectrum.settle()
                fresh_audio = False
                self.draw_media()


def main():
    parser = argparse.ArgumentParser(description="Widgets de sistema para o desktop COSMIC")
    parser.add_argument("--interval", type=float, default=2.0, help="segundos entre atualizações")
    parser.add_argument("--primary", help="saída que recebe o painel completo, por exemplo eDP-1")
    parser.add_argument("--audio-device", help="fonte de áudio das barras; o padrão é a saída atual")
    Application(parser.parse_args()).run()

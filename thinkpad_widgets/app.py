"""Laço principal: uma superfície por cartão, redesenhadas a cada amostra.

Superfícies separadas mantêm os buffers pequenos e evitam compor o espaço
transparente entre os cartões.
"""

import argparse
import os
import sys
import time

from . import bar, cards, popup
from .media import AudioCapture, Media
from .metrics import Metrics
from .theme import Theme
from .volume import PlayerVolume
from .wayland import (ANCHOR_LEFT, ANCHOR_RIGHT, ANCHOR_TOP, CURSOR_DEFAULT, CURSOR_HAND, LAYER_BOTTOM,
                      LAYER_TOP, Connection, Globals, LayerSurface)

NAMESPACE = "thinkpad-widgets"
EDGE_MARGIN = 16
TOP_MARGIN = 12
THEME_CHECK_SECONDS = 5
RESTART_DELAY_SECONDS = 1
PANEL_RESTART_DELAY_SECONDS = 3  # dá tempo de o painel novo aparecer na tela
FRAME_SECONDS = 0.04
AFTER_CONTROL_SECONDS = 0.3  # tempo de o player reagir antes de reler o estado


def battery_cards(metrics):
    return [cards.BatteryCard()] if metrics.battery else []


def left_column(metrics):
    return [cards.ClockCard(), *battery_cards(metrics), cards.ControlsCard(), cards.NetworkCard()]


def right_column(metrics):
    storage = cards.StorageCard()
    storage.rows = max(len(metrics.disks), 1)
    gpu = [cards.GpuCard()] if metrics.gpu else []
    return [cards.CpuCard(), *gpu, cards.TemperatureCard(), cards.MemoryCard(), storage, cards.ProcessCard()]


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
        self.painted = []  # (superfície, função de pintura), atualizadas a cada amostra
        self.stale = []  # as que ainda precisam ser redesenhadas
        self.media_strips = []
        self.media = Media()
        self.capture = AudioCapture(arguments.audio_device)
        self.volume = PlayerVolume()
        self.popup = popup.MediaPopup(self.media, self.capture, self.volume)
        self.hover = popup.Hover()
        self.popup_surface = None
        self.strip_surface = None
        self.strip_output = None
        self.strip_x = 0
        self.strip_receives_mouse = False
        self.next_sample = time.monotonic()
        self.restart_at = None

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
        pointer = self.shared.pointer
        if pointer:
            pointer.on_enter = self.on_pointer_enter
            pointer.on_leave = self.on_pointer_leave
            pointer.on_motion = self.on_pointer_motion
            pointer.on_press = self.on_pointer_press
            pointer.on_release = self.on_pointer_release
            pointer.on_scroll = self.on_pointer_scroll
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
            self.painted.append((surface, self.card_painter(card)))
            top += card.height + cards.GAP

    def card_painter(self, card):
        return lambda context: cards.paint_card(context, self.theme, self.metrics, card)

    def strip_painter(self, strip):
        return lambda context: strip.paint(context, self.theme)

    def add_strip(self, output, strip, x):
        return LayerSurface(
            self.shared, output, strip.width, bar.HEIGHT, ANCHOR_TOP | ANCHOR_LEFT,
            (bar.TOP_MARGIN, 0, 0, x), LAYER_TOP, NAMESPACE, [], self.schedule_restart,
        ), strip

    def add_status_strip(self, output):
        strip = bar.StatusStrip(self.metrics)
        x = output.layout_width // 2 + bar.CLOCK_CLEARANCE
        surface, _strip = self.add_strip(output, strip, x)
        self.painted.append((surface, self.strip_painter(strip)))

    def add_media_strip(self, output):
        # Só na tela principal: animar as duas dobraria o custo enquanto algo toca.
        strip = bar.MediaStrip(self.media, self.capture)
        x = output.layout_width // 2 - bar.CLOCK_CLEARANCE - strip.width
        entry = self.add_strip(output, strip, x)
        self.media_strips.append(entry)
        self.strip_surface, self.strip_output, self.strip_x = entry[0], output, x

    def area_of(self, surface_id):
        if self.strip_surface and surface_id == self.strip_surface.surface.id:
            return "strip"
        if self.popup_surface and surface_id == self.popup_surface.surface.id:
            return "popup"
        return None

    def on_pointer_enter(self, surface_id, x, y):
        area = self.area_of(surface_id)
        if area:
            self.hover.enter(area, time.monotonic())
        if area == "popup":
            self.popup.move(x, y)

    def on_pointer_leave(self, surface_id):
        # O pop-up pode já ter sido destruído: nesse caso sobra só limpar o estado.
        area = self.area_of(surface_id)
        if area:
            self.hover.leave(area, time.monotonic())
        if area == "popup":
            self.popup.hovered = None

    def on_pointer_motion(self, surface_id, x, y):
        if self.area_of(surface_id) == "popup":
            self.popup.move(x, y)
            self.shared.pointer.set_cursor(CURSOR_HAND if self.popup.hovered else CURSOR_DEFAULT)

    def on_pointer_press(self, surface_id):
        if self.area_of(surface_id) != "popup":
            return
        if self.popup.press():
            self.next_sample = time.monotonic() + AFTER_CONTROL_SECONDS

    def on_pointer_release(self, _surface_id):
        self.popup.release()

    def on_pointer_scroll(self, surface_id, amount):
        if self.area_of(surface_id) == "popup":
            self.popup.scroll(amount)

    def show_popup(self):
        if self.popup_surface or self.media.current is None or self.strip_surface is None:
            return
        x = self.strip_x + bar.MEDIA_WIDTH - popup.WIDTH
        self.popup_surface = LayerSurface(
            self.shared, self.strip_output, popup.WIDTH, popup.HEIGHT, ANCHOR_TOP | ANCHOR_LEFT,
            (popup.TOP_GAP, 0, 0, x), LAYER_TOP, NAMESPACE,
            cards.rounded_rows(0, 0, popup.WIDTH, popup.HEIGHT, cards.RADIUS),
            self.schedule_restart, interactive=True,
        )

    def hide_popup(self):
        self.popup.release()
        if self.popup_surface:
            self.popup_surface.destroy()
            self.popup_surface = None
        self.popup.hovered = None
        self.hover.inside.discard("popup")

    def sync_popup(self):
        if self.hover.open:
            self.show_popup()
        else:
            self.hide_popup()

    def update_strip_input(self):
        """A faixa só captura o mouse enquanto há o que mostrar."""
        wanted = self.media.current is not None
        if self.strip_surface is None or wanted == self.strip_receives_mouse:
            return
        self.strip_receives_mouse = wanted
        self.strip_surface.set_input([(0, 0, bar.MEDIA_WIDTH, bar.HEIGHT)] if wanted else [])
        if not wanted:
            self.hover.reset()
            self.hide_popup()

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

    def invalidate(self):
        self.stale = list(self.painted)

    def draw(self):
        """Desenha só o que está pendente.

        Uma superfície que não pode desenhar agora, como um cartão coberto por uma
        janela, espera sozinha: nunca pode forçar o redesenho das outras.
        """
        waiting = []
        for surface, painter in self.stale:
            if surface.can_draw():
                surface.draw(painter)
            else:
                waiting.append((surface, painter))
        self.stale = waiting

    def draw_media(self):
        for surface, strip in self.media_strips:
            if surface.can_draw():
                surface.draw(lambda context: strip.paint(context, self.theme))
        if self.popup_surface and self.popup_surface.can_draw():
            self.popup_surface.draw(lambda context: self.popup.paint(context, self.theme))

    def update_capture(self):
        """Só captura áudio enquanto algo toca, para não gastar bateria à toa."""
        if self.media.playing:
            self.capture.start()
        else:
            self.capture.stop()

    def run(self):
        next_theme_check = self.next_sample + THEME_CHECK_SECONDS
        next_frame = self.next_sample
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
                    self.invalidate()
            if now >= self.next_sample:
                self.next_sample = now + self.interval
                self.metrics.sample()
                self.check_panel()
                self.media.sample()
                self.volume.update(self.media.current["player"] if self.media.current else None)
                self.update_strip_input()
                self.update_capture()
                self.invalidate()
                self.draw_media()
            self.volume.flush()
            if self.stale:
                self.draw()

            descriptor = self.capture.descriptor
            animating = (descriptor is not None or any(self.capture.spectrum.levels)
                         or self.popup_surface is not None)
            timeout = max(self.next_sample - time.monotonic(), 0)
            if animating:
                timeout = min(timeout, max(next_frame - time.monotonic(), 0))
            if self.hover.deadline is not None:
                timeout = min(timeout, max(self.hover.deadline - time.monotonic(), 0))
            if self.connection.wait(timeout, () if descriptor is None else (descriptor,)):
                self.capture.read()
                fresh_audio = True

            # O ritmo vem do relógio: eventos do compositor chegam na taxa do monitor,
            # que pode ser 180 Hz, e derrubariam as barras antes de serem vistas.
            now = time.monotonic()
            if self.hover.tick(now):
                self.sync_popup()
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

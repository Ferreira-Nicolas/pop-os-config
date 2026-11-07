"""Faixas desenhadas sobre o espaço livre do painel do COSMIC.

Não são applets: ficam na mesma camada, acima do painel, com input vazio, então
cliques atravessam. Applets de verdade exigiriam Rust e a libcosmic.
"""

import cairo

from . import cards

HEIGHT = 32  # altura do painel no tamanho XS
# A margem conta a partir do fim da área reservada pelo painel. Com o valor negativo, a
# faixa sobe para cima dele e acompanha quando ele cola no topo, com janela maximizada.
TOP_MARGIN = -HEIGHT
CLOCK_CLEARANCE = 112  # metade do relógio, mais uma folga
MEDIA_WIDTH = 460
STATUS_WIDTH = 400
BAR_WIDTH = 4
BAR_GAP = 3
BAR_MAX_HEIGHT = 18
TEXT_SIZE = 12.5


def track_text(track):
    if track["artist"]:
        return f"{track['title']}  ·  {track['artist']}"
    return track["title"]


class MediaStrip:
    width = MEDIA_WIDTH

    def __init__(self, media, capture):
        self.media = media
        self.capture = capture

    def paint(self, context, theme):
        track = self.media.current
        if track is None:
            return
        canvas = cards.Canvas(context, theme)
        levels = self.capture.spectrum.levels
        bars_width = len(levels) * (BAR_WIDTH + BAR_GAP) - BAR_GAP
        right = self.width - 4
        left = right - bars_width
        middle = HEIGHT / 2
        for index, level in enumerate(levels):
            height = max(level * BAR_MAX_HEIGHT, BAR_WIDTH)
            x = left + index * (BAR_WIDTH + BAR_GAP)
            canvas.rounded(x, middle - height / 2, BAR_WIDTH, height, BAR_WIDTH / 2)
            gradient = cairo.LinearGradient(0, middle + BAR_MAX_HEIGHT / 2, 0, middle - BAR_MAX_HEIGHT / 2)
            gradient.add_color_stop_rgb(0, *theme.primary)
            gradient.add_color_stop_rgb(1, *theme.secondary)
            context.set_source(gradient)
            context.fill()

        alpha = cards.TEXT if track["playing"] else cards.MUTED
        text = canvas.fitted(track_text(track), left - 14, TEXT_SIZE)
        canvas.text(left - 10, 21, text, TEXT_SIZE, alpha, align="right")


class StatusStrip:
    width = STATUS_WIDTH

    def __init__(self, metrics):
        self.metrics = metrics

    def paint(self, context, theme):
        canvas = cards.Canvas(context, theme)
        metrics = self.metrics
        items = [(cards.icon_cpu, f"{metrics.cpu_percent:.0f}%"),
                 (cards.icon_memory, f"{metrics.memory[2]:.0f}%")]
        if metrics.cpu_temperature is not None:
            items.append((cards.icon_thermometer, f"{metrics.cpu_temperature:.0f}°C"))
        if metrics.network:
            download = cards.data_size(metrics.network["download"])
            items.append((cards.icon_network, f"{download}/s"))
        x = 4
        for icon, text in items:
            cards.draw_icon(canvas, icon, x, 9, theme.primary, 14)
            x += 20 + canvas.text(x + 19, 21, text, TEXT_SIZE, cards.TEXT) + 16

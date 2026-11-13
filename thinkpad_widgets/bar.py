"""Faixas desenhadas sobre o espaço livre do painel do COSMIC.

Não são applets: ficam na mesma camada, acima do painel. Applets de verdade
exigiriam Rust e a libcosmic. Só a faixa de música recebe o mouse, para abrir o
pop-up; na faixa de sistema os cliques atravessam.
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
ART_SIZE = 24
ART_RADIUS = 5
ART_GAP = 8


def track_text(track):
    if track["artist"]:
        return f"{track['title']}  ·  {track['artist']}"
    return track["title"]


def draw_art(canvas, surface, x, y, size, radius, alpha):
    context = canvas.context
    context.save()
    canvas.rounded(x, y, size, size, radius)
    context.clip()
    context.translate(x, y)
    scale = size / surface.get_width()
    context.scale(scale, scale)
    context.set_source_surface(surface, 0, 0)
    context.paint_with_alpha(alpha)
    context.restore()


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
        art = self.media.art.surface
        reserved = ART_SIZE + ART_GAP if art else 0
        text = canvas.fitted(track_text(track), left - 14 - reserved, TEXT_SIZE)
        advance = canvas.text(left - 10, 21, text, TEXT_SIZE, alpha, align="right")
        if art:
            draw_art(canvas, art, left - 10 - advance - reserved, (HEIGHT - ART_SIZE) / 2,
                     ART_SIZE, ART_RADIUS, alpha)


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

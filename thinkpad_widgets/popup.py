"""Pop-up do que está tocando: abre ao passar o mouse sobre a faixa do painel."""

import math

import cairo

from . import cards
from .bar import draw_art
from .theme import mix

WIDTH = 340
HEIGHT = 214
PADDING = 14
TOP_GAP = 8
ART_SIZE = 76
ART_RADIUS = 8
TEXT_LEFT = PADDING + ART_SIZE + 12
BUTTON_Y = 151
COLUMN_COUNT = 40
COLUMN_GAP = 3
COLUMN_INSET = 6
COLUMN_MAX_HEIGHT = HEIGHT - 16
VEIL_TOP = 0.78  # o texto fica em cima: precisa de mais contraste
VEIL_BOTTOM = 0.42
BUTTONS = (
    ("shuffle", 34, 15),
    ("previous", WIDTH // 2 - 46, 15),
    ("play-pause", WIDTH // 2, 19),
    ("next", WIDTH // 2 + 46, 15),
    ("repeat", WIDTH - 34, 15),
)
PROGRESS_Y = 100
PROGRESS_HEIGHT = 5
PROGRESS_WIDTH = WIDTH - 2 * PADDING
PROGRESS_HIT_MARGIN = 7  # a área clicável é mais alta que a barra: dispensa pontaria
VOLUME_Y = 193
VOLUME_ICON_X = PADDING + 8
VOLUME_LEFT = PADDING + 30
VOLUME_WIDTH = WIDTH - PADDING - 44 - VOLUME_LEFT
VOLUME_HEIGHT = 5
VOLUME_HIT_MARGIN = 9
HOVER_MARGIN = 4
OPEN_DELAY_SECONDS = 0.25
CLOSE_DELAY_SECONDS = 0.35


def time_text(seconds):
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def columns(levels, count):
    """Expande as bandas em mais colunas, interpolando para formar uma curva suave."""
    last = len(levels) - 1
    result = []
    for index in range(count):
        position = index / (count - 1) * last
        low = min(int(position), last - 1)
        amount = position - low
        amount = amount * amount * (3 - 2 * amount)  # suaviza a passagem entre bandas
        result.append(levels[low] + (levels[low + 1] - levels[low]) * amount)
    return result


class Hover:
    """Decide quando abrir e fechar.

    Os atrasos evitam abrir sem querer e dão tempo de levar o mouse da faixa até
    o pop-up, atravessando o vão entre os dois.
    """

    def __init__(self):
        self.inside = set()
        self.open = False
        self.deadline = None

    def _update(self, now):
        wanted = bool(self.inside)
        if wanted == self.open:
            self.deadline = None
        elif self.deadline is None:
            self.deadline = now + (OPEN_DELAY_SECONDS if wanted else CLOSE_DELAY_SECONDS)

    def enter(self, area, now):
        self.inside.add(area)
        self._update(now)

    def leave(self, area, now):
        self.inside.discard(area)
        self._update(now)

    def tick(self, now):
        """Devolve True quando o estado muda."""
        if self.deadline is None or now < self.deadline:
            return False
        self.deadline = None
        self.open = bool(self.inside)
        return True

    def reset(self):
        self.inside.clear()
        self.open = False
        self.deadline = None


def icon_play(context, x, y):
    context.move_to(x - 5, y - 8)
    context.line_to(x + 8, y)
    context.line_to(x - 5, y + 8)
    context.close_path()
    context.fill()


def icon_pause(context, x, y):
    context.rectangle(x - 6, y - 7, 4, 14)
    context.rectangle(x + 2, y - 7, 4, 14)
    context.fill()


def icon_skip(context, x, y, direction):
    context.move_to(x - 5 * direction, y - 6)
    context.line_to(x + 4 * direction, y)
    context.line_to(x - 5 * direction, y + 6)
    context.close_path()
    context.fill()
    context.rectangle(x + (4 if direction > 0 else -6), y - 6, 2, 12)
    context.fill()


def icon_shuffle(context, x, y):
    context.set_line_width(1.6)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    for top, bottom in ((-5, 5), (5, -5)):
        context.move_to(x - 8, y + top)
        context.curve_to(x - 2, y + top, x + 1, y + bottom, x + 7, y + bottom)
        context.move_to(x + 4, y + bottom - 3)
        context.line_to(x + 7, y + bottom)
        context.line_to(x + 4, y + bottom + 3)
    context.stroke()


def icon_repeat(context, x, y):
    context.set_line_width(1.6)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    context.move_to(x - 8, y + 1)
    context.line_to(x - 8, y - 2)
    context.curve_to(x - 8, y - 5, x - 6, y - 6, x - 3, y - 6)
    context.line_to(x + 7, y - 6)
    context.move_to(x + 4, y - 9)
    context.line_to(x + 7, y - 6)
    context.line_to(x + 4, y - 3)
    context.move_to(x + 8, y - 1)
    context.line_to(x + 8, y + 2)
    context.curve_to(x + 8, y + 5, x + 6, y + 6, x + 3, y + 6)
    context.line_to(x - 7, y + 6)
    context.move_to(x - 4, y + 9)
    context.line_to(x - 7, y + 6)
    context.line_to(x - 4, y + 3)
    context.stroke()


class MediaPopup:
    def __init__(self, media, capture, volume):
        self.media = media
        self.capture = capture
        self.volume = volume
        self.hovered = None
        self.pointer_x = 0.0
        self.dragging = False

    def enabled(self, action):
        track = self.media.current
        if track is None:
            return False
        if action == "shuffle":
            return track["shuffle"] is not None
        if action == "repeat":
            return track["loop"] is not None
        if action == "seek":
            return track["length"] > 0  # uma transmissão ao vivo não tem duração
        if action in ("volume", "mute"):
            return self.volume.available
        return True

    def seek_target(self):
        """Segundos correspondentes ao ponto da barra sob o mouse."""
        fraction = (self.pointer_x - PADDING) / PROGRESS_WIDTH
        return max(0.0, min(fraction, 1.0)) * self.media.current["length"]

    def volume_target(self):
        return (self.pointer_x - VOLUME_LEFT) / VOLUME_WIDTH

    def press(self):
        """Botão do mouse apertado. No slider de volume começa a arrastar."""
        if self.hovered == "volume":
            self.dragging = True
            self.volume.set(self.volume_target())
            return False  # não mexe no player: nada para reler
        return self.click()

    def release(self):
        self.dragging = False

    def click(self):
        """Executa o que está sob o mouse. Devolve True se o player foi acionado."""
        if self.hovered is None:
            return False
        if self.hovered == "seek":
            self.media.seek(self.seek_target())
        elif self.hovered == "mute":
            self.volume.toggle_mute()
            return False
        elif self.hovered == "volume":
            self.volume.set(self.volume_target())
            return False
        else:
            self.media.control(self.hovered)
        return True

    def scroll(self, amount):
        self.volume.scroll(amount)

    def hit(self, x, y):
        """Devolve a ação nesse ponto, se houver."""
        if (self.enabled("seek") and PADDING <= x <= PADDING + PROGRESS_WIDTH
                and abs(y - (PROGRESS_Y + PROGRESS_HEIGHT / 2)) <= PROGRESS_HIT_MARGIN):
            return "seek"
        if self.enabled("volume"):
            if (VOLUME_LEFT - VOLUME_HIT_MARGIN <= x <= VOLUME_LEFT + VOLUME_WIDTH + VOLUME_HIT_MARGIN
                    and abs(y - VOLUME_Y) <= VOLUME_HIT_MARGIN):
                return "volume"
            if math.hypot(x - VOLUME_ICON_X, y - VOLUME_Y) <= 13:
                return "mute"
        for action, center_x, radius in BUTTONS:
            if self.enabled(action) and math.hypot(x - center_x, y - BUTTON_Y) <= radius + HOVER_MARGIN:
                return action
        return None

    def move(self, x, y):
        self.pointer_x = x
        if self.dragging:
            # Fora do slider o arrasto continua: o Wayland segue mandando o movimento.
            self.volume.set(self.volume_target())
            return
        self.hovered = self.hit(x, y)

    def paint_button(self, canvas, action, center_x, radius):
        context = canvas.context
        theme = canvas.theme
        track = self.media.current
        if action == "play-pause":
            context.arc(center_x, BUTTON_Y, radius, 0, 2 * math.pi)
            canvas.color(mix(theme.primary, (1, 1, 1), 0.2 if self.hovered == action else 0))
            context.fill()
            canvas.color(theme.glass)
            (icon_pause if track["playing"] else icon_play)(context, center_x, BUTTON_Y)
            return
        if action in ("shuffle", "repeat"):
            # Fundo próprio: sem ele, o estado ligado se perde sobre as colunas do som.
            context.arc(center_x, BUTTON_Y, radius + 2, 0, 2 * math.pi)
            canvas.color(theme.glass, 0.8)
            context.fill()
        if self.hovered == action:
            context.arc(center_x, BUTTON_Y, radius, 0, 2 * math.pi)
            canvas.color(cards.WHITE, 0.14)
            context.fill()
        if action == "shuffle":
            active = bool(track["shuffle"])
            alpha = 1 if self.enabled(action) else 0.3
            canvas.color(theme.secondary if active else cards.WHITE, alpha if active else alpha * 0.7)
            icon_shuffle(context, center_x, BUTTON_Y)
            if active:
                context.arc(center_x, BUTTON_Y + 12, 1.6, 0, 2 * math.pi)
                context.fill()
            return
        if action == "repeat":
            mode = track["loop"]
            active = mode in ("Track", "Playlist")
            alpha = 1 if self.enabled(action) else 0.3
            canvas.color(theme.secondary if active else cards.WHITE, alpha if active else alpha * 0.7)
            icon_repeat(context, center_x, BUTTON_Y)
            if active:
                context.arc(center_x, BUTTON_Y + 13, 1.6, 0, 2 * math.pi)
                context.fill()
            if mode == "Track":
                canvas.text(center_x, BUTTON_Y + 3.5, "1", 9.5, 1, cards.FONT_STRONG, "center", theme.secondary)
            return
        canvas.color(cards.WHITE, cards.TEXT)
        icon_skip(context, center_x, BUTTON_Y, 1 if action == "next" else -1)

    def paint_volume(self, canvas):
        context = canvas.context
        theme = canvas.theme
        volume = self.volume
        if not volume.available:
            canvas.text(VOLUME_LEFT, VOLUME_Y + 4, "Volume aparece quando o player tocar som", 11, 0.7)
            return
        silent = volume.muted or volume.level == 0
        if self.hovered == "mute":
            context.arc(VOLUME_ICON_X, VOLUME_Y, 13, 0, 2 * math.pi)
            canvas.color(cards.WHITE, 0.14)
            context.fill()
        icon = cards.icon_speaker_muted if silent else cards.icon_speaker
        cards.draw_icon(canvas, icon, VOLUME_ICON_X - 8, VOLUME_Y - 8,
                        theme.secondary if volume.muted else cards.WHITE)

        fraction = 0 if volume.muted else volume.level
        top = VOLUME_Y - VOLUME_HEIGHT / 2
        canvas.bar(VOLUME_LEFT, top, VOLUME_WIDTH, VOLUME_HEIGHT, fraction)
        active = self.dragging or self.hovered == "volume"
        context.arc(VOLUME_LEFT + VOLUME_WIDTH * fraction, VOLUME_Y, 6.5 if active else 5, 0, 2 * math.pi)
        canvas.color(cards.WHITE, 1 if active else 0.9)
        context.fill()

        label = "Mudo" if volume.muted else f"{volume.level * 100:.0f}%"
        canvas.text(WIDTH - PADDING, VOLUME_Y + 4, label, 11.5, cards.TEXT, cards.FONT_STRONG, align="right")

    def paint_visualizer(self, canvas):
        """O som como papel de parede: colunas finas por toda a largura."""
        context = canvas.context
        theme = canvas.theme
        step = (WIDTH - 2 * COLUMN_INSET + COLUMN_GAP) / COLUMN_COUNT
        width = step - COLUMN_GAP
        for index, level in enumerate(columns(self.capture.spectrum.levels, COLUMN_COUNT)):
            height = max(level * COLUMN_MAX_HEIGHT, width)
            canvas.rounded(COLUMN_INSET + index * step, HEIGHT - height, width, height, width / 2)
        # Um gradiente só, da base ao topo: colunas altas alcançam a segunda cor.
        gradient = cairo.LinearGradient(0, HEIGHT, 0, HEIGHT - COLUMN_MAX_HEIGHT)
        gradient.add_color_stop_rgb(0, *theme.primary)
        gradient.add_color_stop_rgb(1, *theme.secondary)
        context.set_source(gradient)
        context.fill()

    def paint_background(self, canvas):
        """Camadas de trás para frente: fundo escuro, som e véu transparente."""
        context = canvas.context
        theme = canvas.theme
        canvas.glass(0, 0, WIDTH, HEIGHT)
        # Fica sobre janelas, não só sobre o wallpaper: precisa de mais tinta para ler.
        canvas.rounded(0, 0, WIDTH, HEIGHT, cards.RADIUS)
        canvas.color(theme.glass, 0.45)
        context.fill()

        context.save()
        canvas.rounded(0, 0, WIDTH, HEIGHT, cards.RADIUS)
        context.clip()
        self.paint_visualizer(canvas)
        veil = cairo.LinearGradient(0, 0, 0, HEIGHT)
        veil.add_color_stop_rgba(0, *theme.glass, VEIL_TOP)
        veil.add_color_stop_rgba(1, *theme.glass, VEIL_BOTTOM)
        context.set_source(veil)
        context.paint()
        context.restore()

    def paint(self, context, theme):
        track = self.media.current
        canvas = cards.Canvas(context, theme)
        self.paint_background(canvas)
        if track is None:
            return

        art = self.media.art.surface
        if art:
            draw_art(canvas, art, PADDING, PADDING, ART_SIZE, ART_RADIUS, 1)
        else:
            canvas.rounded(PADDING, PADDING, ART_SIZE, ART_SIZE, ART_RADIUS)
            canvas.color(cards.WHITE, 0.08)
            context.fill()

        width = WIDTH - TEXT_LEFT - PADDING
        canvas.text(TEXT_LEFT, 33, canvas.fitted(track["title"], width, 14.5, cards.FONT_STRONG),
                    14.5, cards.TEXT, cards.FONT_STRONG)
        canvas.text(TEXT_LEFT, 53, canvas.fitted(track["artist"], width, 12.5), 12.5, cards.TEXT)
        canvas.text(TEXT_LEFT, 72, canvas.fitted(track["album"], width, 11.5), 11.5, 0.8)

        length = track["length"]
        position = self.media.position
        canvas.bar(PADDING, PROGRESS_Y, PROGRESS_WIDTH, PROGRESS_HEIGHT, position / length if length else 0)
        if self.hovered == "seek":
            context.arc(self.pointer_x, PROGRESS_Y + PROGRESS_HEIGHT / 2, 6, 0, 2 * math.pi)
            canvas.color(cards.WHITE)
            context.fill()
            canvas.text(WIDTH / 2, 121, time_text(self.seek_target()), 11, cards.TEXT, cards.FONT_STRONG, "center")
        if length:
            canvas.text(PADDING, 121, time_text(position), 11, cards.TEXT)
            canvas.text(WIDTH - PADDING, 121, time_text(length), 11, cards.TEXT, align="right")

        for action, center_x, radius in BUTTONS:
            self.paint_button(canvas, action, center_x, radius)
        self.paint_volume(canvas)

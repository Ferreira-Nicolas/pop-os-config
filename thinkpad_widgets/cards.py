"""Os cartões de vidro: desenho com Cairo sobre o blur do compositor."""

import datetime
import math

import cairo

from .metrics import HISTORY_SAMPLES
from .theme import mix

WIDTH = 320
RADIUS = 16  # radius_m do tema COSMIC
PADDING = 16
GAP = 12

FONT = "Open Sans"
FONT_LIGHT = "Open Sans Light"
FONT_STRONG = "Open Sans Semibold"

WHITE = (1.0, 1.0, 1.0)
TEXT = 0.95
MUTED = 0.68
FAINT = 0.42

WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo")
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro")


def decimal(value, places=1):
    return f"{value:.{places}f}".replace(".", ",")


def data_size(value):
    """Formata bytes em unidades binárias, com vírgula decimal."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{value:.0f} B"
    return f"{decimal(value, 0 if value >= 100 else 1)} {units[index]}"


def duration(hours):
    total = int(hours * 60)
    if total >= 60:
        return f"{total // 60}h {total % 60:02d}min"
    return f"{total}min"


def uptime_text(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}min"
    return f"{minutes}min"


def rounded_rows(x, y, width, height, radius):
    """Retângulos cuja união é um retângulo arredondado.

    Uma wl_region só aceita retângulos; linhas de 1 px nos cantos fazem o blur
    acompanhar a curva do cartão.
    """
    rows = [(x, y + radius, width, height - 2 * radius)]
    for line in range(radius):
        offset = radius - line - 0.5
        inset = math.ceil(radius - math.sqrt(radius * radius - offset * offset))
        rows.append((x + inset, y + line, width - 2 * inset, 1))
        rows.append((x + inset, y + height - 1 - line, width - 2 * inset, 1))
    return rows


class Canvas:
    """Primitivas de desenho compartilhadas pelos cartões."""

    def __init__(self, context, theme):
        self.context = context
        self.theme = theme
        options = cairo.FontOptions()
        options.set_antialias(cairo.ANTIALIAS_GRAY)
        options.set_hint_style(cairo.HINT_STYLE_SLIGHT)
        options.set_hint_metrics(cairo.HINT_METRICS_OFF)
        context.set_font_options(options)

    def color(self, color, alpha=1.0):
        self.context.set_source_rgba(*color, alpha)

    def rounded(self, x, y, width, height, radius):
        context = self.context
        radius = min(radius, width / 2, height / 2)
        context.new_sub_path()
        context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
        context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
        context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
        context.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
        context.close_path()

    def glass(self, x, y, width, height):
        context = self.context
        self.rounded(x, y, width, height, RADIUS)
        fill = cairo.LinearGradient(0, y, 0, y + height)
        fill.add_color_stop_rgba(0, *self.theme.glass, 0.46)
        fill.add_color_stop_rgba(1, *self.theme.glass, 0.60)
        context.set_source(fill)
        context.fill()

        self.rounded(x + 0.5, y + 0.5, width - 1, height - 1, RADIUS - 0.5)
        edge = cairo.LinearGradient(0, y, 0, y + height)
        edge.add_color_stop_rgba(0, 1, 1, 1, 0.26)
        edge.add_color_stop_rgba(1, 1, 1, 1, 0.06)
        context.set_source(edge)
        context.set_line_width(1)
        context.stroke()

    def font(self, size, family=FONT):
        self.context.select_font_face(family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        self.context.set_font_size(size)

    def measure(self, text, size, family=FONT):
        self.font(size, family)
        return self.context.text_extents(text).x_advance

    def text(self, x, y, text, size=12, alpha=TEXT, family=FONT, align="left", color=WHITE):
        self.font(size, family)
        advance = self.context.text_extents(text).x_advance
        if align == "right":
            x -= advance
        elif align == "center":
            x -= advance / 2
        self.color(color, alpha)
        self.context.move_to(x, y)
        self.context.show_text(text)
        self.context.new_path()  # show_text deixa um ponto corrente para trás
        return advance

    def fitted(self, text, width, size, family=FONT):
        """Corta o texto com reticências para caber na largura."""
        if self.measure(text, size, family) <= width:
            return text
        while text and self.measure(text + "…", size, family) > width:
            text = text[:-1]
        return text + "…"

    def caps(self, x, y, text, size=10.5, alpha=MUTED, spacing=1.1):
        """Rótulo em caixa alta com espaçamento entre letras."""
        self.font(size, FONT_STRONG)
        self.color(WHITE, alpha)
        for letter in text.upper():
            self.context.move_to(x, y)
            self.context.show_text(letter)
            x += self.context.text_extents(letter).x_advance + spacing
        self.context.new_path()

    def bar(self, x, y, width, height, fraction, start=None, end=None):
        context = self.context
        start = start or self.theme.primary
        end = end or self.theme.secondary
        self.rounded(x, y, width, height, height / 2)
        self.color(WHITE, 0.12)
        context.fill()
        filled = width * max(0.0, min(fraction, 1.0))
        if filled < 0.5:
            return
        filled = max(filled, height)
        self.rounded(x, y, filled, height, height / 2)
        # O gradiente cobre só a parte preenchida: as duas cores aparecem em qualquer nível.
        gradient = cairo.LinearGradient(x, 0, x + filled, 0)
        gradient.add_color_stop_rgb(0, *start)
        gradient.add_color_stop_rgb(1, *end)
        context.set_source(gradient)
        context.fill()

    def ring(self, center_x, center_y, radius, thickness, fraction):
        """Medidor circular de 270° com gradiente ao longo do arco."""
        context = self.context
        begin, sweep = 0.75 * math.pi, 1.5 * math.pi
        context.set_line_width(thickness)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        self.color(WHITE, 0.12)
        context.arc(center_x, center_y, radius, begin, begin + sweep)
        context.stroke()

        fraction = max(0.0, min(fraction, 1.0))
        steps = max(int(fraction * 72), 1)
        context.set_line_cap(cairo.LINE_CAP_BUTT if steps > 1 else cairo.LINE_CAP_ROUND)
        for step in range(steps):
            position = step / 72
            self.color(mix(self.theme.primary, self.theme.secondary, step / steps))
            context.arc(center_x, center_y, radius, begin + sweep * position,
                        begin + sweep * (position + 1.3 / 72))
            context.stroke()
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        for position, progress in ((0.0, 0.0), (fraction, 1.0)):
            angle = begin + sweep * position
            self.color(mix(self.theme.primary, self.theme.secondary, progress))
            context.arc(center_x + radius * math.cos(angle), center_y + radius * math.sin(angle),
                        thickness / 2, 0, 2 * math.pi)
            context.fill()
        context.set_line_cap(cairo.LINE_CAP_BUTT)

    def spark(self, x, y, width, height, values, maximum, color):
        """Gráfico de área alinhado à direita: o histórico cresce para a esquerda."""
        context = self.context
        values = list(values)
        if len(values) < 2:
            return
        step = width / (HISTORY_SAMPLES - 1)
        scale = height / maximum if maximum > 0 else 0
        base = y + height
        start = x + width - step * (len(values) - 1)
        points = [(start + index * step, base - min(value * scale, height))
                  for index, value in enumerate(values)]

        context.move_to(start, base)
        for point in points:
            context.line_to(*point)
        context.line_to(x + width, base)
        context.close_path()
        fade = cairo.LinearGradient(0, base, 0, y)
        fade.add_color_stop_rgba(0, *color, 0.05)
        fade.add_color_stop_rgba(1, *color, 0.42)
        context.set_source(fade)
        context.fill()

        context.move_to(*points[0])
        for point in points[1:]:
            context.line_to(*point)
        self.color(color, 0.95)
        context.set_line_width(1.3)
        context.set_line_join(cairo.LINE_JOIN_ROUND)
        context.stroke()

    def level_color(self, fraction, warning=0.75, critical=0.9):
        """Par de cores de uma barra conforme o uso se aproxima do limite."""
        if fraction >= critical:
            return self.theme.destructive, self.theme.destructive
        if fraction >= warning:
            return self.theme.warning, self.theme.warning
        return self.theme.primary, self.theme.secondary


# Ícones em traço, desenhados numa caixa de 16x16.

def _stroke(canvas, color, width=1.4):
    context = canvas.context
    canvas.color(color)
    context.set_line_width(width)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    return context


def icon_cpu(canvas, color):
    context = _stroke(canvas, color)
    canvas.rounded(3.5, 3.5, 9, 9, 2)
    context.stroke()
    canvas.rounded(6.3, 6.3, 3.4, 3.4, 0.8)
    context.fill()
    for offset in (5.5, 10.5):
        for start, end in ((0.8, 3.5), (12.5, 15.2)):
            context.move_to(offset, start)
            context.line_to(offset, end)
            context.move_to(start, offset)
            context.line_to(end, offset)
    context.stroke()


def icon_gpu(canvas, color):
    context = _stroke(canvas, color)
    canvas.rounded(1, 3.5, 14, 9, 1.6)
    context.stroke()
    context.arc(5.8, 8, 2.2, 0, 2 * math.pi)
    context.stroke()
    context.move_to(10.5, 6)
    context.line_to(12.8, 6)
    context.move_to(10.5, 8)
    context.line_to(12.8, 8)
    context.move_to(10.5, 10)
    context.line_to(12.8, 10)
    context.move_to(3.5, 12.5)
    context.line_to(3.5, 14.5)
    context.line_to(8, 14.5)
    context.stroke()


def icon_thermometer(canvas, color):
    context = _stroke(canvas, color)
    context.arc(8, 11.6, 3.1, -1.1, math.pi + 1.1)
    context.line_to(6.5, 3)
    context.arc(8, 3, 1.5, math.pi, 2 * math.pi)
    context.close_path()
    context.stroke()
    context.arc(8, 11.6, 1.4, 0, 2 * math.pi)
    context.fill()
    context.move_to(8, 5.5)
    context.line_to(8, 10.5)
    context.stroke()


def icon_memory(canvas, color):
    context = _stroke(canvas, color)
    canvas.rounded(1, 4, 14, 7, 1.2)
    context.stroke()
    for x in (4, 7, 10):
        canvas.rounded(x, 6, 2, 3, 0.4)
        context.fill()
    for x in (3.5, 6.5, 9.5, 12.5):
        context.move_to(x, 11)
        context.line_to(x, 13.5)
    context.stroke()


def icon_disk(canvas, color):
    context = _stroke(canvas, color)
    canvas.rounded(1.5, 3, 13, 10, 2)
    context.stroke()
    context.move_to(1.5, 9)
    context.line_to(14.5, 9)
    context.stroke()
    context.arc(11.5, 11, 0.8, 0, 2 * math.pi)
    context.fill()


def icon_list(canvas, color):
    context = _stroke(canvas, color)
    for y in (4, 8, 12):
        context.move_to(5.5, y)
        context.line_to(14.5, y)
        context.stroke()
        context.arc(2.3, y, 1, 0, 2 * math.pi)
        context.fill()


def icon_network(canvas, color):
    context = _stroke(canvas, color)
    for x, tip, tail in ((5, 2, 14), (11, 14, 2)):
        side = 2.6 if tip < tail else -2.6
        context.move_to(x, tail)
        context.line_to(x, tip)
        context.move_to(x - 2.6, tip + side)
        context.line_to(x, tip)
        context.line_to(x + 2.6, tip + side)
    context.stroke()


def icon_battery(canvas, color):
    context = _stroke(canvas, color)
    canvas.rounded(1, 4.5, 12.5, 7, 1.6)
    context.stroke()
    context.move_to(15, 7)
    context.line_to(15, 9)
    context.stroke()
    canvas.rounded(3, 6.5, 6, 3, 0.6)
    context.fill()


def icon_speaker(canvas, color):
    context = _stroke(canvas, color)
    context.move_to(1.5, 6)
    context.line_to(4, 6)
    context.line_to(8, 2.8)
    context.line_to(8, 13.2)
    context.line_to(4, 10)
    context.line_to(1.5, 10)
    context.close_path()
    context.stroke()
    context.arc(9, 8, 2.8, -0.9, 0.9)
    context.stroke()
    context.arc(9, 8, 5.4, -0.9, 0.9)
    context.stroke()


def icon_speaker_muted(canvas, color):
    context = _stroke(canvas, color)
    context.move_to(1.5, 6)
    context.line_to(4, 6)
    context.line_to(8, 2.8)
    context.line_to(8, 13.2)
    context.line_to(4, 10)
    context.line_to(1.5, 10)
    context.close_path()
    context.stroke()
    context.move_to(10.5, 6)
    context.line_to(14.5, 10)
    context.move_to(14.5, 6)
    context.line_to(10.5, 10)
    context.stroke()


def icon_sun(canvas, color):
    context = _stroke(canvas, color)
    context.arc(8, 8, 3, 0, 2 * math.pi)
    context.stroke()
    for index in range(8):
        angle = index * math.pi / 4
        context.move_to(8 + 5.2 * math.cos(angle), 8 + 5.2 * math.sin(angle))
        context.line_to(8 + 7 * math.cos(angle), 8 + 7 * math.sin(angle))
    context.stroke()


def icon_bolt(canvas, color):
    context = canvas.context
    canvas.color(color)
    for x, y in ((9, 1), (3.5, 9), (7.5, 9), (6.5, 15), (12.5, 6.5), (8.5, 6.5)):
        context.line_to(x, y)
    context.close_path()
    context.fill()


def draw_icon(canvas, painter, x, y, color, size=16):
    context = canvas.context
    context.save()
    context.translate(x, y)
    context.scale(size / 16, size / 16)
    context.new_path()
    painter(canvas, color)
    context.restore()


class Card:
    height = 100

    def header(self, canvas, title, icon, right=None):
        draw_icon(canvas, icon, PADDING, PADDING - 1, canvas.theme.primary)
        canvas.caps(PADDING + 23, PADDING + 11, title)
        if right:
            canvas.text(WIDTH - PADDING, PADDING + 11, right, 12, MUTED, align="right")

    def paint(self, canvas, metrics):
        raise NotImplementedError


class ClockCard(Card):
    height = 128

    def paint(self, canvas, metrics):
        now = datetime.datetime.now()
        canvas.text(PADDING - 2, 72, now.strftime("%H:%M"), 62, TEXT, FONT_LIGHT)
        canvas.text(WIDTH - PADDING, 40, str(now.year), 12, MUTED, align="right")
        canvas.text(WIDTH - PADDING, 58, f"semana {now.isocalendar().week}", 12, FAINT + 0.1, align="right")
        date = f"{WEEKDAYS[now.weekday()]}, {now.day} de {MONTHS[now.month - 1]}"
        canvas.text(PADDING, 96, date, 14, TEXT, FONT_STRONG)
        canvas.text(PADDING, 114, f"{metrics.model}  ·  ligado há {uptime_text(metrics.uptime)}", 11.5, MUTED)


class CpuCard(Card):
    height = 176

    def paint(self, canvas, metrics):
        self.header(canvas, "CPU", icon_cpu, metrics.cpu_name)
        center_x, center_y = PADDING + 41, 88
        canvas.ring(center_x, center_y, 37, 7, metrics.cpu_percent / 100)
        canvas.text(center_x, center_y + 7, f"{metrics.cpu_percent:.0f}%", 21, TEXT, FONT_STRONG, "center")
        canvas.text(center_x, center_y + 34, "em uso", 10.5, MUTED, align="center")

        left = PADDING + 98
        width = WIDTH - PADDING - left
        canvas.spark(left, 46, width, 50, metrics.cpu_history, 100, canvas.theme.primary)
        if metrics.cpu_frequency:
            canvas.text(left, 116, f"{decimal(metrics.cpu_frequency)} GHz", 13, TEXT, FONT_STRONG)
        load = "  ".join(decimal(value, 2) for value in metrics.load)
        canvas.text(WIDTH - PADDING, 116, load, 11.5, MUTED, align="right")

        threads = metrics.cpu_threads
        gap = 5
        width = (WIDTH - 2 * PADDING - gap * (len(threads) - 1)) / max(len(threads), 1)
        for index, value in enumerate(threads):
            canvas.bar(PADDING + index * (width + gap), 146, width, 6, value / 100)
        canvas.text(PADDING, 166, f"{len(threads)} threads", 10.5, FAINT)


class GpuCard(Card):
    height = 132

    def paint(self, canvas, metrics):
        self.header(canvas, "GPU", icon_gpu, metrics.gpu_name)
        center_x, center_y = PADDING + 41, 88
        canvas.ring(center_x, center_y, 37, 7, metrics.gpu_percent / 100)
        canvas.text(center_x, center_y + 7, f"{metrics.gpu_percent:.0f}%", 21, TEXT, FONT_STRONG, "center")
        canvas.text(center_x, center_y + 34, "em uso", 10.5, MUTED, align="center")

        left = PADDING + 98
        width = WIDTH - PADDING - left
        canvas.spark(left, 46, width, 50, metrics.gpu_history, 100, canvas.theme.primary)
        if metrics.gpu_frequency:
            canvas.text(left, 116, f"{decimal(metrics.gpu_frequency)} GHz", 13, TEXT, FONT_STRONG)
        if metrics.gpu_max_frequency:
            canvas.text(WIDTH - PADDING, 116, f"máx. {decimal(metrics.gpu_max_frequency)} GHz", 11.5, MUTED,
                        align="right")


class TemperatureCard(Card):
    height = 168
    scale_low, scale_high = 25, 100

    def paint(self, canvas, metrics):
        fan = None
        if metrics.fan_rpm is not None:
            fan = f"{metrics.fan_rpm} rpm" if metrics.fan_rpm else "Ventoinha parada"
        self.header(canvas, "Temperatura", icon_thermometer, fan)
        limit = metrics.temperature_limit
        y = 54
        for label, value in metrics.temperatures[:4]:
            canvas.text(PADDING, y, label, 12, MUTED)
            fraction = (value - self.scale_low) / (self.scale_high - self.scale_low)
            if value >= limit:
                start = end = canvas.theme.destructive
            elif value >= limit - 10:
                start = end = canvas.theme.warning
            else:
                start, end = canvas.theme.primary, canvas.theme.secondary
            canvas.bar(PADDING + 52, y - 7, WIDTH - 2 * PADDING - 100, 6, fraction, start, end)
            canvas.text(WIDTH - PADDING, y, f"{value:.0f} °C", 13, TEXT, FONT_STRONG, align="right")
            y += 21
        # Escala relativa ao período: a variação aparece mesmo em repouso.
        history = metrics.temperature_history
        lowest = min(history, default=0)
        relative = [value - lowest + 4 for value in history]
        canvas.spark(PADDING, y - 6, WIDTH - 2 * PADDING, self.height - y - 6, relative,
                     max(max(relative, default=0), 14), canvas.theme.secondary)


class MemoryCard(Card):
    height = 122

    def paint(self, canvas, metrics):
        used, total, percent = metrics.memory
        self.header(canvas, "Memória", icon_memory, f"{percent:.0f}%")
        advance = canvas.text(PADDING, 70, data_size(used), 26, TEXT, FONT_LIGHT)
        canvas.text(PADDING + advance + 8, 70, f"de {data_size(total)}", 12, MUTED)
        canvas.bar(PADDING, 80, WIDTH - 2 * PADDING, 6, percent / 100,
                   *canvas.level_color(percent / 100, 0.8, 0.92))
        swap_used, swap_total, swap_percent = metrics.swap
        if swap_total:
            canvas.text(PADDING, 108, "Swap", 12, MUTED)
            canvas.bar(PADDING + 52, 101, WIDTH - 2 * PADDING - 136, 6, swap_percent / 100,
                       canvas.theme.cool, canvas.theme.primary)
            canvas.text(WIDTH - PADDING, 108, f"{data_size(swap_used)} de {data_size(swap_total)}",
                        11.5, MUTED, align="right")


class StorageCard(Card):
    row = 38

    @property
    def height(self):
        return 74 + self.row * self.rows

    def __init__(self):
        self.rows = 1

    def paint(self, canvas, metrics):
        self.header(canvas, "Armazenamento", icon_disk)
        y = 54
        for label, used, total, percent in metrics.disks[:self.rows]:
            canvas.text(PADDING, y, label, 12.5, TEXT, FONT_STRONG)
            canvas.text(WIDTH - PADDING, y, f"{data_size(used)} de {data_size(total)}", 12, MUTED, align="right")
            canvas.bar(PADDING, y + 8, WIDTH - 2 * PADDING, 6, percent / 100,
                       *canvas.level_color(percent / 100, 0.85, 0.95))
            y += self.row
        canvas.text(PADDING, y + 6, f"Leitura  {data_size(metrics.disk_read)}/s", 11.5, MUTED)
        canvas.text(WIDTH - PADDING, y + 6, f"Escrita  {data_size(metrics.disk_write)}/s", 11.5, MUTED, align="right")


class ProcessCard(Card):
    height = 170

    def paint(self, canvas, metrics):
        self.header(canvas, "Processos", icon_list, f"{metrics.process_count} ativos")
        canvas.text(WIDTH - PADDING - 72, 50, "CPU", 10.5, FAINT, align="right")
        canvas.text(WIDTH - PADDING, 50, "RAM", 10.5, FAINT, align="right")
        y = 70
        for index, (name, cpu, memory) in enumerate(metrics.processes):
            alpha = TEXT if index == 0 else 0.84
            canvas.text(PADDING, y, canvas.fitted(name, 150, 12.5), 12.5, alpha)
            canvas.text(WIDTH - PADDING - 72, y, f"{decimal(cpu)}%", 12.5, alpha, align="right")
            canvas.text(WIDTH - PADDING, y, data_size(memory), 12, MUTED, align="right")
            y += 21


class BatteryCard(Card):
    height = 118

    def paint(self, canvas, metrics):
        battery = metrics.battery
        if battery is None:
            self.header(canvas, "Energia", icon_battery)
            canvas.text(PADDING, 62, "Sem bateria", 13, MUTED)
            return
        status = battery["status"]
        if battery["hours"] is not None:
            verb = "restam" if battery["discharging"] else "cheia em"
            status += f"  ·  {verb} {duration(battery['hours'])}"
        self.header(canvas, "Bateria", icon_battery)
        canvas.text(WIDTH - PADDING, PADDING + 11, f"{decimal(battery['watts'])} W", 12, MUTED, align="right")

        percent = battery["percent"]
        advance = canvas.text(PADDING, 72, f"{percent}%", 30, TEXT, FONT_LIGHT)
        if battery["charging"]:
            draw_icon(canvas, icon_bolt, PADDING + advance + 6, 50, canvas.theme.warning, 20)
        canvas.text(WIDTH - PADDING, 70, status, 12, TEXT, align="right")

        if percent <= 15:
            colors = (canvas.theme.destructive, canvas.theme.destructive)
        elif percent <= 30:
            colors = (canvas.theme.warning, canvas.theme.warning)
        else:
            colors = (canvas.theme.success, canvas.theme.success_end)
        canvas.bar(PADDING, 82, WIDTH - 2 * PADDING, 7, percent / 100, *colors)

        details = []
        if battery["health"] is not None:
            details.append(f"Saúde {battery['health']:.0f}%")
        if battery["cycles"]:
            details.append(f"{battery['cycles']} ciclos")
        canvas.text(PADDING, 106, "  ·  ".join(details), 11.5, MUTED)


class ControlsCard(Card):
    height = 86

    def row(self, canvas, y, icon, fraction, label):
        draw_icon(canvas, icon, PADDING, y - 8, canvas.theme.primary)
        canvas.bar(PADDING + 26, y - 3, WIDTH - 2 * PADDING - 74, 6, fraction or 0)
        canvas.text(WIDTH - PADDING, y + 4, label, 12.5, TEXT, FONT_STRONG, align="right")

    def paint(self, canvas, metrics):
        volume = metrics.volume
        if volume is None:
            self.row(canvas, 28, icon_speaker, 0, "—")
        elif metrics.muted or volume == 0:
            self.row(canvas, 28, icon_speaker_muted, 0, "Mudo" if metrics.muted else "0%")
        else:
            self.row(canvas, 28, icon_speaker, min(volume, 1), f"{volume * 100:.0f}%")
        brightness = metrics.brightness
        self.row(canvas, 58, icon_sun, brightness,
                 "—" if brightness is None else f"{brightness * 100:.0f}%")


class NetworkCard(Card):
    height = 156

    def paint(self, canvas, metrics):
        network = metrics.network
        if network is None:
            self.header(canvas, "Rede", icon_network, "Sem conexão")
            return
        name = network["name"]
        if network["speed"] and network["speed"] > 0:
            speed = network["speed"]
            name += f"  ·  {speed / 1000:g} Gb/s" if speed >= 1000 else f"  ·  {speed} Mb/s"
        self.header(canvas, "Rede", icon_network, name)

        half = (WIDTH - 2 * PADDING - 12) / 2
        canvas.caps(PADDING, 54, "Download", 9.5, FAINT + 0.08, 0.8)
        canvas.caps(PADDING + half + 12, 54, "Upload", 9.5, FAINT + 0.08, 0.8)
        canvas.text(PADDING, 74, f"{data_size(network['download'])}/s", 15, TEXT, FONT_STRONG)
        canvas.text(PADDING + half + 12, 74, f"{data_size(network['upload'])}/s", 15, TEXT, FONT_STRONG)

        peak = max(*metrics.download_history, *metrics.upload_history, 64 * 1024)
        canvas.spark(PADDING, 84, half, 40, metrics.download_history, peak, canvas.theme.cool)
        canvas.spark(PADDING + half + 12, 84, half, 40, metrics.upload_history, peak, canvas.theme.secondary)
        if network["address"]:
            canvas.text(PADDING, 142, network["address"], 11.5, MUTED)


def blur_rectangles(card):
    return rounded_rows(0, 0, WIDTH, card.height, RADIUS)


def paint_card(context, theme, metrics, card):
    canvas = Canvas(context, theme)
    canvas.glass(0, 0, WIDTH, card.height)
    card.paint(canvas, metrics)

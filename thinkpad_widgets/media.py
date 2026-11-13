"""O que está tocando (MPRIS, via playerctl) e o nível de som por frequência."""

import array
import cmath
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import cairo

try:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
except (ImportError, ValueError):
    GdkPixbuf = None  # sem a biblioteca, a faixa só não mostra a capa

SAMPLE_RATE = 8000
BLOCK_SAMPLES = 256  # potência de 2, exigida pela FFT: 32 ms de áudio
BAND_COUNT = 8
LOWEST_HZ = 60
HIGHEST_HZ = 3400
FALL_PER_FRAME = 0.07  # as barras sobem na hora e descem devagar
PEAK_DECAY = 0.995  # ganho automático: música baixa também movimenta as barras
SILENCE_FLOOR = 40.0
TREBLE_TILT = 0.15
RANGE_DB = 42  # o que está 42 dB abaixo do pico já aparece como barra vazia
FORMAT = ("{{status}}\t{{playerInstance}}\t{{artist}}\t{{title}}\t{{mpris:artUrl}}\t{{album}}"
          "\t{{mpris:length}}\t{{position}}\t{{shuffle}}\t{{loop}}")
# O primeiro clique repete a música atual, que é o uso mais comum.
NEXT_LOOP = {"None": "Track", "Track": "Playlist", "Playlist": "None"}
COMMANDS = {
    "play-pause": ("play-pause",),
    "next": ("next",),
    "previous": ("previous",),
    "shuffle": ("shuffle", "Toggle"),
}
ART_PIXELS = 152  # o dobro do tamanho no pop-up, para ficar nítida com escala
ART_MAX_BYTES = 5 * 1024 * 1024
ART_TIMEOUT_SECONDS = 5


def clean_artist(artist):
    """Tira o sufixo dos canais automáticos do YouTube: "Daft Punk - Topic"."""
    return artist.removesuffix(" - Topic")


def microseconds(text):
    """O player informa tempo em microssegundos, ou nada."""
    try:
        return int(text) / 1_000_000
    except ValueError:
        return 0.0


def parse_shuffle(text):
    """Devolve None quando o player não tem modo aleatório."""
    return {"true": True, "false": False}.get(text.lower())


def parse_loop(text):
    """Devolve None quando o player não tem repetição."""
    return text if text in NEXT_LOOP else None


def parse_players(output):
    """Escolhe o player que está tocando, ou o primeiro pausado."""
    players = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split("\t")]
        fields += [""] * (10 - len(fields))
        status, player, artist, title, art, album, length, position, shuffle, loop = fields[:10]
        if title:
            players.append({"playing": status == "Playing", "paused": status == "Paused",
                            "player": player, "artist": clean_artist(artist), "title": title,
                            "art": art, "album": album, "length": microseconds(length),
                            "position": microseconds(position), "shuffle": parse_shuffle(shuffle),
                            "loop": parse_loop(loop)})
    return next((p for p in players if p["playing"]), next((p for p in players if p["paused"]), None))


def pick_sink(streams, sinks, default):
    """Escolhe a saída onde o áudio toca de fato, que pode não ser a padrão."""
    names = dict(line.split()[:2] for line in sinks.splitlines() if len(line.split()) >= 2)
    active = [names[sink] for sink, corked in re.findall(r"Sink: (\d+).*?Corked: (yes|no)", streams, re.S)
              if corked == "no" and sink in names]
    if default in active or not active:
        return default
    return active[0]


def band_frequencies():
    ratio = (HIGHEST_HZ / LOWEST_HZ) ** (1 / (BAND_COUNT - 1))
    return [LOWEST_HZ * ratio ** index for index in range(BAND_COUNT)]


def band_edges():
    """Limites de cada banda em bins da FFT, com ao menos um bin por banda."""
    ratio = (HIGHEST_HZ / LOWEST_HZ) ** (1 / (BAND_COUNT - 1))
    bin_width = SAMPLE_RATE / BLOCK_SAMPLES
    edges = []
    for center in band_frequencies():
        low = int(center / math.sqrt(ratio) / bin_width)
        high = int(center * math.sqrt(ratio) / bin_width) + 1
        low = max(low, 1)
        edges.append((low, min(max(high, low + 1), BLOCK_SAMPLES // 2)))
    return edges


class Spectrum:
    """Energia de cada banda, somada em uma FFT feita em Python puro."""

    def __init__(self):
        size = BLOCK_SAMPLES
        bits = size.bit_length() - 1
        self.order = [int(f"{index:0{bits}b}"[::-1], 2) for index in range(size)]
        self.twiddles = [cmath.exp(-2j * math.pi * index / size) for index in range(size // 2)]
        self.window = [0.5 - 0.5 * math.cos(2 * math.pi * index / (size - 1)) for index in range(size)]
        self.edges = band_edges()
        # Bandas logarítmicas já equilibram a música; só falta um leve reforço nos agudos.
        self.tilt = [(center / LOWEST_HZ) ** TREBLE_TILT for center in band_frequencies()]
        self.levels = [0.0] * BAND_COUNT
        self.peak = SILENCE_FLOOR

    def transform(self, samples):
        size = BLOCK_SAMPLES
        values = [complex(samples[index] * self.window[index]) for index in self.order]
        span = 2
        while span <= size:
            half = span // 2
            stride = size // span
            for start in range(0, size, span):
                for offset in range(half):
                    low = start + offset
                    high = low + half
                    product = self.twiddles[offset * stride] * values[high]
                    values[high] = values[low] - product
                    values[low] = values[low] + product
            span *= 2
        return values

    def magnitudes(self, samples):
        spectrum = self.transform(samples)
        result = []
        for (low, high), tilt in zip(self.edges, self.tilt):
            energy = sum(abs(spectrum[index]) ** 2 for index in range(low, high))
            result.append(math.sqrt(energy) / BLOCK_SAMPLES * tilt)
        return result

    def feed(self, samples):
        magnitudes = self.magnitudes(samples)
        self.peak = max(max(magnitudes), self.peak * PEAK_DECAY, SILENCE_FLOOR)
        for index, magnitude in enumerate(magnitudes):
            target = 0.0
            if magnitude > 0:
                target = 1 + 20 * math.log10(min(magnitude / self.peak, 1.0)) / RANGE_DB
            self.levels[index] = max(target, self.levels[index] - FALL_PER_FRAME, 0.0)

    def settle(self):
        """Faz as barras caírem quando não há áudio novo."""
        self.levels = [max(level - FALL_PER_FRAME, 0.0) for level in self.levels]
        return any(self.levels)


class AudioCapture:
    """Lê o que sai para as caixas de som; nunca o microfone."""

    def __init__(self, device=None):
        self.device = device
        self.process = None
        self.source = None
        self.pending = bytearray()
        self.spectrum = Spectrum()

    @property
    def descriptor(self):
        return self.process.stdout.fileno() if self.process else None

    def _pactl(self, *arguments):
        # LC_ALL=C: em português, o pactl traduz os rótulos que são lidos aqui.
        result = subprocess.run(["pactl", *arguments], capture_output=True, text=True, timeout=2,
                                env={**os.environ, "LC_ALL": "C"})
        return result.stdout

    def _monitor_source(self):
        sink = pick_sink(self._pactl("list", "sink-inputs"), self._pactl("list", "short", "sinks"),
                         self._pactl("get-default-sink").strip())
        return f"{sink}.monitor" if sink else None

    def start(self):
        """Inicia a captura, ou a muda de lugar se o áudio trocou de saída."""
        try:
            device = self.device or self._monitor_source()
            if not device or (self.process and device == self.source):
                return
            self.stop()
            self.source = device
            self.process = subprocess.Popen(
                ["parec", f"--device={device}", "--format=s16le", f"--rate={SAMPLE_RATE}",
                 "--channels=1", "--latency-msec=40", "--raw", "--client-name=thinkpad-widgets"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            self.process = None
            return
        os.set_blocking(self.process.stdout.fileno(), False)

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process.stdout.close()
            self.process = None
        self.pending.clear()

    def read(self):
        """Consome o áudio disponível. Devolve False se a captura terminou."""
        try:
            data = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return True
        if not data:
            self.stop()
            return False
        self.pending += data
        size = BLOCK_SAMPLES * 2
        # Se o laço atrasou, descarta o velho e analisa só o bloco mais recente.
        if len(self.pending) >= size:
            usable = len(self.pending) // size * size
            block = bytes(self.pending[usable - size:usable])
            del self.pending[:usable]
            self.spectrum.feed(array.array("h", block))
        return True


def read_art(url):
    """Lê a capa de um arquivo local ou da internet. O link vem do player."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        with open(urllib.parse.unquote(parsed.path), "rb") as image:
            return image.read(ART_MAX_BYTES + 1)
    if parsed.scheme in ("http", "https"):
        with urllib.request.urlopen(url, timeout=ART_TIMEOUT_SECONDS) as response:
            return response.read(ART_MAX_BYTES + 1)
    raise ValueError(f"esquema não aceito: {parsed.scheme}")


def art_surface(data):
    """Converte a imagem em uma superfície quadrada do Cairo, cortando pelo centro."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    image = loader.get_pixbuf()
    side = min(image.get_width(), image.get_height())
    image = image.new_subpixbuf((image.get_width() - side) // 2, (image.get_height() - side) // 2,
                                side, side)
    image = image.scale_simple(ART_PIXELS, ART_PIXELS, GdkPixbuf.InterpType.BILINEAR).add_alpha(False, 0, 0, 0)

    # O GdkPixbuf entrega RGBA e o Cairo guarda BGRA. Capas são opacas: alfa cheio.
    pixels = image.get_pixels()
    data = bytearray(len(pixels))
    data[0::4] = pixels[2::4]
    data[1::4] = pixels[1::4]
    data[2::4] = pixels[0::4]
    data[3::4] = b"\xff" * (len(pixels) // 4)
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, ART_PIXELS, ART_PIXELS,
                                              image.get_rowstride())


class AlbumArt:
    """Carrega a capa em segundo plano: a animação nunca espera a rede."""

    def __init__(self):
        self.url = ""
        self.surface = None

    def request(self, url):
        if url == self.url:
            return
        self.url = url
        self.surface = None
        if url and GdkPixbuf:
            threading.Thread(target=self._load, args=(url,), daemon=True).start()

    def _load(self, url):
        try:
            data = read_art(url)
            if len(data) > ART_MAX_BYTES:
                return
            surface = art_surface(data)
        except (OSError, ValueError, gi.repository.GLib.Error):
            return  # sem capa, a faixa continua só com o texto
        if url == self.url:  # a música pode ter mudado enquanto baixava
            self.surface = surface


class Media:
    def __init__(self):
        self.current = None
        self.sampled_at = 0.0
        self.art = AlbumArt()

    def sample(self):
        try:
            result = subprocess.run(
                ["playerctl", "--all-players", "metadata", "--format", FORMAT],
                capture_output=True, text=True, timeout=1,
            )
            self.current = parse_players(result.stdout)
            self.sampled_at = time.monotonic()
        except (OSError, subprocess.SubprocessError):
            self.current = None
        self.art.request(self.current["art"] if self.current else "")

    @property
    def playing(self):
        return bool(self.current and self.current["playing"])

    @property
    def position(self):
        """Posição atual, avançando entre uma leitura e outra."""
        if self.current is None:
            return 0.0
        position = self.current["position"]
        if self.current["playing"]:
            position += time.monotonic() - self.sampled_at
        length = self.current["length"]
        return min(position, length) if length else position

    def control(self, action):
        """Envia o comando em segundo plano, com resposta imediata na tela."""
        if self.current is None:
            return
        if action == "repeat" and self.current["loop"] is not None:
            self.current["loop"] = NEXT_LOOP[self.current["loop"]]
            self._send("loop", self.current["loop"])
            return
        if action not in COMMANDS:
            return
        if action == "play-pause":
            self.current["position"] = self.position
            self.sampled_at = time.monotonic()
            self.current["playing"] = not self.current["playing"]
        elif action == "shuffle" and self.current["shuffle"] is not None:
            self.current["shuffle"] = not self.current["shuffle"]
        self._send(*COMMANDS[action])

    def seek(self, seconds):
        """Pula para um ponto da música."""
        if self.current is None:
            return
        self.current["position"] = seconds
        self.sampled_at = time.monotonic()
        self._send("position", f"{seconds:.2f}")

    def _send(self, *arguments):
        command = ["playerctl", f"--player={self.current['player']}", *arguments]
        threading.Thread(target=subprocess.run, args=(command,), daemon=True,
                         kwargs={"capture_output": True, "timeout": 3}).start()

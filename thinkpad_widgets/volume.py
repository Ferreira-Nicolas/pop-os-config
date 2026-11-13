"""Volume do player que está tocando, sem mexer no resto do som do sistema.

Muda o fluxo de áudio do próprio aplicativo no PipeWire, como o mixer por
aplicativo faz. Funciona com qualquer player, inclusive navegadores, que em
geral ignoram o volume pedido pelo MPRIS. O PipeWire lembra o nível de cada
aplicativo, então ele continua valendo nas próximas músicas.
"""

import json
import os
import re
import subprocess

SCROLL_UNIT = 15  # um "dente" da rodinha do mouse, em unidades do Wayland
SCROLL_STEP = 0.02
# O Chrome e os navegadores derivados dele se anunciam como "chromium" no MPRIS.
ALIASES = {"chromium": ("chrome", "chromium", "brave", "vivaldi", "opera", "edge")}
NAME_KEYS = ("application.name", "application.process.binary", "node.name")


def clamp(level):
    # Acima de 100% o som distorce: o slider para ali.
    return max(0.0, min(level, 1.0))


def spawn(command):
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_streams(text):
    """Os fluxos de áudio de `pactl -f json list sink-inputs`."""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    streams = []
    for item in data:
        percents = [re.match(r"(\d+)", channel.get("value_percent", "")) for channel in item.get("volume", {}).values()]
        percents = [int(match.group(1)) for match in percents if match]
        if not percents or "index" not in item:
            continue
        properties = item.get("properties", {})
        pid = str(properties.get("application.process.id", ""))
        streams.append({
            "index": item["index"],
            "level": max(percents) / 100,
            "muted": bool(item.get("mute")),
            "pid": int(pid) if pid.isdigit() else None,
            "names": {str(properties.get(key, "")).lower() for key in NAME_KEYS} - {""},
        })
    return streams


def read_streams():
    try:
        result = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                                timeout=2, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_streams(result.stdout)


def player_pid(player):
    """O processo que publica o player no D-Bus."""
    try:
        result = subprocess.run(
            ["busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
             "GetConnectionUnixProcessID", "s", f"org.mpris.MediaPlayer2.{player}"],
            capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"u (\d+)", result.stdout)
    return int(match.group(1)) if match else None


def parent_pid(pid):
    try:
        with open(f"/proc/{pid}/stat") as stat:
            # O nome do processo pode ter espaços: os campos começam depois do último ")".
            return int(stat.read().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def descends_from(pid, ancestor, parent=parent_pid):
    for _ in range(64):
        if pid == ancestor:
            return True
        if pid is None or pid <= 1:
            return False
        pid = parent(pid)
    return False


def player_streams(streams, player, pid=None, parent=parent_pid):
    """Os fluxos do player.

    Pelo processo quando dá: o Chrome toca o som num processo filho. Senão pelo
    nome, que é o que sobra para apps em Flatpak, como o Spotify, cujos números
    de processo não valem fora da caixa de areia.
    """
    if pid:
        own = [stream for stream in streams if stream["pid"] and descends_from(stream["pid"], pid, parent)]
        if own:
            return own
    base = player.split(".")[0].lower()
    names = (base, *ALIASES.get(base, ()))
    return [stream for stream in streams if any(name in found for name in names for found in stream["names"])]


class PlayerVolume:
    """Muda o volume na hora na tela e aplica em segundo plano.

    Só um comando roda por vez e, enquanto ele roda, vale só o pedido mais
    recente: arrastar o slider não enfileira centenas de processos.
    """

    def __init__(self, run=spawn, read=read_streams, find_pid=player_pid, parent=parent_pid):
        self.run = run
        self.read = read
        self.find_pid = find_pid
        self.parent = parent
        self.player = None
        self.pid = None
        self.streams = []
        self.level = 0.0
        self.muted = False
        self.process = None
        self.pending = {}  # (tipo, fluxo) -> comando, na ordem em que chegaram
        self.wanted = None  # (nível, mudo) enquanto o sistema ainda não alcançou

    @property
    def available(self):
        return bool(self.streams)

    @property
    def busy(self):
        return bool(self.pending) or (self.process is not None and self.process.poll() is None)

    def update(self, player):
        """Relê o volume dos fluxos do player atual."""
        if player != self.player:
            self.player = player
            self.pid = self.find_pid(player) if player else None
            self.wanted = None
        self.streams = player_streams(self.read(), player, self.pid, self.parent) if player else []
        if self.streams:
            self.level = clamp(max(stream["level"] for stream in self.streams))
            self.muted = all(stream["muted"] for stream in self.streams)
        if self.busy and self.wanted:
            self.level, self.muted = self.wanted  # leitura de antes do comando terminar
        elif not self.busy:
            self.wanted = None

    def set(self, level):
        if not self.available:
            return
        self.level = clamp(level)
        for stream in self.streams:
            self.pending[("volume", stream["index"])] = [
                "pactl", "set-sink-input-volume", str(stream["index"]), f"{round(self.level * 100)}%"]
        if self.muted:  # mexer no volume tira do mudo
            self._mute(False)
        self._remember()

    def scroll(self, amount):
        """`amount` positivo é rolar para baixo, que abaixa o volume."""
        self.set(self.level - amount / SCROLL_UNIT * SCROLL_STEP)

    def toggle_mute(self):
        if self.available:
            self._mute(not self.muted)
            self._remember()

    def _mute(self, muted):
        self.muted = muted
        for stream in self.streams:
            self.pending[("mute", stream["index"])] = [
                "pactl", "set-sink-input-mute", str(stream["index"]), "1" if muted else "0"]

    def _remember(self):
        self.wanted = (self.level, self.muted)
        self.flush()

    def flush(self):
        """Envia o próximo comando se o anterior já terminou."""
        if self.process is not None and self.process.poll() is None:
            return
        self.process = None
        if self.pending:
            key = next(iter(self.pending))
            try:
                self.process = self.run(self.pending.pop(key))
            except OSError:
                self.pending.clear()

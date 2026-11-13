"""Uso da GPU sem precisar de root.

Cada fabricante expõe isso de um jeito. Os leitores devolvem (uso em %, GHz atual,
GHz máximo), com None no que não souberem.
"""

import re
import shutil
import subprocess

from .sysfs import read_int, read_text


def parse_gpu_name(output):
    """Tira o nome comercial do lspci: o último trecho entre colchetes."""
    for line in output.splitlines():
        if re.search(r"VGA compatible|3D controller|Display controller", line):
            names = re.findall(r"\[([^\]]+)\]", line)
            if names:
                return names[-1].removesuffix(" Graphics").split("/")[0]
    return None


def gpu_name():
    try:
        result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_gpu_name(result.stdout)


def parse_clocks(text):
    """Interpreta pp_dpm_sclk: uma linha por estado, com * no atual."""
    current = maximum = None
    for match in re.finditer(r"(\d+)\s*Mhz(\s*\*)?", text or "", re.I):
        value = int(match.group(1)) / 1000
        maximum = max(maximum or 0, value)
        if match.group(2):
            current = value
    return current, maximum


class IntelGpu:
    """RC6 é o sono da GPU: o uso é o tempo que ela passou acordada."""

    def __init__(self, folder):
        self.folder = folder
        self.last_sleep = None

    def read(self, elapsed):
        sleep = read_int(self.folder / "rc6_residency_ms")
        percent = None
        if sleep is not None and self.last_sleep is not None and elapsed > 0:
            slept = (sleep - self.last_sleep) / 1000 / elapsed
            percent = 100 * max(0.0, min(1 - slept, 1.0))
        self.last_sleep = sleep
        # A frequência real marca 0 enquanto dorme; a pedida segue disponível.
        frequency = read_int(self.folder / "rps_act_freq_mhz") or read_int(self.folder / "rps_cur_freq_mhz")
        maximum = read_int(self.folder / "rps_max_freq_mhz")
        return percent, frequency and frequency / 1000, maximum and maximum / 1000


class AmdGpu:
    def __init__(self, folder):
        self.folder = folder

    def read(self, _elapsed):
        current, maximum = parse_clocks(read_text(self.folder / "pp_dpm_sclk"))
        return read_int(self.folder / "gpu_busy_percent"), current, maximum


class NvidiaGpu:
    QUERY = "--query-gpu=utilization.gpu,clocks.gr,clocks.max.gr"

    @staticmethod
    def parse(output):
        fields = output.splitlines()[0].split(",") if output.strip() else []
        try:
            percent, current, maximum = (float(field) for field in fields[:3])
        except ValueError:
            return None, None, None
        return percent, current / 1000, maximum / 1000

    def read(self, _elapsed):
        try:
            result = subprocess.run(["nvidia-smi", self.QUERY, "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return None, None, None
        return self.parse(result.stdout)


def detect_gpu(sysfs):
    """Prefere a placa dedicada, que é onde roda o trabalho pesado."""
    if shutil.which("nvidia-smi"):
        return NvidiaGpu()
    cards = sorted(sysfs.glob("class/drm/card[0-9]*"))
    for card in cards:
        if (card / "device/gpu_busy_percent").exists():
            return AmdGpu(card / "device")
    for card in cards:
        if (card / "gt/gt0/rc6_residency_ms").exists():
            return IntelGpu(card / "gt/gt0")
    return None

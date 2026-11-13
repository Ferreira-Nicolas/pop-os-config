"""Coleta das informações do sistema.

Lê o sysfs do kernel diretamente, como o temperature-monitor; `SYSFS_ROOT`
permite apontar para uma árvore falsa nos testes.
"""

import collections
import os
import re
import shlex
import socket
import subprocess
import time
from pathlib import Path

import psutil

from .gpu import detect_gpu, gpu_name
from .sysfs import read_int, read_text

HISTORY_SAMPLES = 90
PROCESS_EVERY = 2  # amostras entre leituras da lista de processos
SSID_EVERY = 15
TOP_PROCESSES = 5
DEFAULT_TEMPERATURE_LIMIT = 85
GENERIC_MACHINE_NAME = re.compile(r"o\.?e\.?m|default|system (version|product)|^\d+(\.\d+)*$", re.I)
BATTERY_STATUS = {
    "Charging": "Carregando",
    "Discharging": "Na bateria",
    "Full": "Carregada",
    "Not charging": "Conectada",
}


def parse_volume(output):
    """Interpreta a saída de `wpctl get-volume`. Devolve (nível, mudo)."""
    match = re.search(r"Volume:\s*([\d.]+)", output or "")
    if not match:
        return None, False
    return float(match.group(1)), "[MUTED]" in output


def read_temperature_limit(path=None):
    """Usa o mesmo limite das notificações do temperature-monitor."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = path or config / "thinkpad-monitor/temperature.conf"
    try:
        for line in Path(path).read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if parts and parts[0].startswith("TEMPERATURE_LIMIT_C="):
                return int(parts[0].split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return DEFAULT_TEMPERATURE_LIMIT


def cpu_model():
    text = read_text("/proc/cpuinfo") or ""
    match = re.search(r"model name\s*:\s*(.+)", text)
    if not match:
        return "CPU"
    model = match.group(1)
    short = re.search(r"(i[3579]-\w+|Ryzen \d \w+|Ultra \d \w+)", model)
    return short.group(1) if short else model.split("@")[0].strip()


def machine_name(sysfs):
    """Nome do computador; placas-mãe de desktop costumam trazer texto genérico."""
    for name in ("product_version", "product_name"):
        text = read_text(Path(sysfs) / "class/dmi/id" / name)
        if text and not GENERIC_MACHINE_NAME.search(text):
            return text
    return None


class Metrics:
    def __init__(self, sysfs=None):
        self.sysfs = Path(sysfs or os.environ.get("SYSFS_ROOT", "/sys"))
        self.hostname = socket.gethostname()
        self.model = machine_name(self.sysfs) or self.hostname
        self.cpu_name = cpu_model()
        self.core_count = psutil.cpu_count() or 1
        self.temperature_limit = read_temperature_limit()
        self.boot_time = psutil.boot_time()

        history = lambda: collections.deque(maxlen=HISTORY_SAMPLES)
        self.cpu_history = history()
        self.gpu_history = history()
        self.temperature_history = history()
        self.download_history = history()
        self.upload_history = history()

        self.cpu_percent = 0.0
        self.cpu_threads = [0.0] * self.core_count
        self.cpu_frequency = None
        self.load = (0.0, 0.0, 0.0)
        self.gpu = detect_gpu(self.sysfs)
        self.gpu_name = (gpu_name() or "GPU") if self.gpu else None
        self.gpu_percent = 0.0
        self.gpu_frequency = None
        self.gpu_max_frequency = None
        self.temperatures = []
        self.cpu_temperature = None
        self.fan_rpm = None
        self.memory = None
        self.swap = None
        self.disks = []
        self.disk_read = 0.0
        self.disk_write = 0.0
        self.battery = None
        self.volume = None
        self.muted = False
        self.brightness = None
        self.network = None
        self.processes = []
        self.process_count = 0
        self.panel_pid = None

        self.sample_count = 0
        self.last_time = time.monotonic()
        self.last_network = {}
        self.last_disk = None
        self.ssid = None
        psutil.cpu_percent(percpu=True)

    @property
    def uptime(self):
        return time.time() - self.boot_time

    def sample(self):
        now = time.monotonic()
        elapsed = max(now - self.last_time, 0.001)
        self.last_time = now

        self._sample_cpu()
        self._sample_gpu(elapsed)
        self._sample_temperatures()
        self._sample_memory()
        self._sample_disks(elapsed)
        self._sample_battery()
        self._sample_controls()
        self._sample_network(elapsed)
        if self.sample_count % PROCESS_EVERY == 0:
            self._sample_processes()
        self.sample_count += 1

    def _sample_cpu(self):
        self.cpu_threads = psutil.cpu_percent(percpu=True)
        self.cpu_percent = sum(self.cpu_threads) / max(len(self.cpu_threads), 1)
        self.cpu_history.append(self.cpu_percent)
        frequency = psutil.cpu_freq()
        self.cpu_frequency = frequency.current / 1000 if frequency else None
        self.load = os.getloadavg()

    def _sample_gpu(self, elapsed):
        if self.gpu is None:
            return
        percent, self.gpu_frequency, self.gpu_max_frequency = self.gpu.read(elapsed)
        if percent is not None:
            self.gpu_percent = percent
            self.gpu_history.append(percent)

    def _hwmon_sensors(self):
        """Devolve {nome do driver: [(rótulo, °C)]} e a primeira ventoinha."""
        sensors, fan = {}, None
        for folder in sorted((self.sysfs / "class/hwmon").glob("hwmon*")):
            name = read_text(folder / "name")
            if not name:
                continue
            for path in sorted(folder.glob("temp*_input")):
                value = read_int(path)
                if value is None or value <= 0:
                    continue
                label = read_text(path.with_name(path.name.replace("_input", "_label")))
                sensors.setdefault(name, []).append((label, value / 1000))
            if fan is None:
                for path in sorted(folder.glob("fan*_input")):
                    fan = read_int(path)
                    if fan is not None:
                        break
        return sensors, fan

    def _sample_temperatures(self):
        sensors, self.fan_rpm = self._hwmon_sensors()
        rows = []

        cpu = [value for name in ("coretemp", "k10temp", "zenpower", "peci-cputemp")
               for _label, value in sensors.get(name, [])]
        self.cpu_temperature = max(cpu) if cpu else None
        if cpu:
            rows.append(("CPU", self.cpu_temperature))
        if self.cpu_temperature is not None:
            self.temperature_history.append(self.cpu_temperature)

        for name, label in (("nvme", "SSD"), ("acpitz", "Placa"), ("iwlwifi", "Wi-Fi")):
            values = [value for driver, readings in sensors.items()
                      if driver.startswith(name) for _label, value in readings]
            if values:
                rows.append((label, max(values)))
        self.temperatures = rows

    def _sample_memory(self):
        memory = psutil.virtual_memory()
        self.memory = (memory.total - memory.available, memory.total, memory.percent)
        swap = psutil.swap_memory()
        self.swap = (swap.used, swap.total, swap.percent)

    def _sample_disks(self, elapsed):
        disks = []
        for partition in psutil.disk_partitions():
            mount = partition.mountpoint
            if mount == "/" or mount == "/home" or mount.startswith(("/media/", "/mnt/", "/run/media/")):
                try:
                    usage = psutil.disk_usage(mount)
                except OSError:
                    continue
                label = "Sistema" if mount == "/" else "Home" if mount == "/home" else Path(mount).name
                disks.append((label, usage.used, usage.total, usage.percent))
        self.disks = disks

        counters = psutil.disk_io_counters(perdisk=True) or {}
        physical = [value for name, value in counters.items()
                    if (self.sysfs / "block" / name / "device").exists()]
        total = (sum(c.read_bytes for c in physical), sum(c.write_bytes for c in physical))
        if self.last_disk:
            self.disk_read = max(total[0] - self.last_disk[0], 0) / elapsed
            self.disk_write = max(total[1] - self.last_disk[1], 0) / elapsed
        self.last_disk = total

    def _sample_battery(self):
        batteries = sorted((self.sysfs / "class/power_supply").glob("BAT*"))
        if not batteries:
            self.battery = None
            return
        folder = batteries[0]
        status = read_text(folder / "status") or "Unknown"
        energy = read_int(folder / "energy_now")
        full = read_int(folder / "energy_full")
        design = read_int(folder / "energy_full_design")
        power = read_int(folder / "power_now") or 0

        hours = None
        if power > 0 and energy is not None:
            if status == "Discharging":
                hours = energy / power
            elif status == "Charging" and full:
                hours = max(full - energy, 0) / power

        self.battery = {
            "percent": read_int(folder / "capacity") or 0,
            "status": BATTERY_STATUS.get(status, status),
            "charging": status == "Charging",
            "discharging": status == "Discharging",
            "watts": power / 1_000_000,
            "hours": hours,
            "health": 100 * full / design if full and design else None,
            "cycles": read_int(folder / "cycle_count"),
        }

    def _sample_controls(self):
        try:
            result = subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=1,
            )
            self.volume, self.muted = parse_volume(result.stdout)
        except (OSError, subprocess.SubprocessError):
            self.volume, self.muted = None, False

        self.brightness = None
        for folder in sorted((self.sysfs / "class/backlight").glob("*")):
            value = read_int(folder / "brightness")
            maximum = read_int(folder / "max_brightness")
            if value is not None and maximum:
                self.brightness = value / maximum
                break

    def _default_interface(self):
        text = read_text("/proc/net/route") or ""
        best = None
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) > 6 and fields[1] == "00000000":
                metric = int(fields[6])
                if best is None or metric < best[0]:
                    best = (metric, fields[0])
        return best[1] if best else None

    def _read_ssid(self, interface):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-g", "GENERAL.CONNECTION", "device", "show", interface],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _sample_network(self, elapsed):
        counters = psutil.net_io_counters(pernic=True)
        interface = self._default_interface()
        download = upload = 0.0
        if interface in counters:
            current = counters[interface]
            previous = self.last_network.get(interface)
            if previous:
                download = max(current.bytes_recv - previous.bytes_recv, 0) / elapsed
                upload = max(current.bytes_sent - previous.bytes_sent, 0) / elapsed
            self.last_network[interface] = current
        self.download_history.append(download)
        self.upload_history.append(upload)

        if interface is None:
            self.network = None
            return
        wireless = (self.sysfs / "class/net" / interface / "wireless").exists()
        if wireless and self.sample_count % SSID_EVERY == 0:
            self.ssid = self._read_ssid(interface)
        address = next((a.address for a in psutil.net_if_addrs().get(interface, [])
                        if a.family == socket.AF_INET), None)
        self.network = {
            "name": (self.ssid or "Wi-Fi") if wireless else "Cabeada",
            "wireless": wireless,
            "address": address,
            "speed": None if wireless else read_int(self.sysfs / "class/net" / interface / "speed"),
            "download": download,
            "upload": upload,
        }

    def _sample_processes(self):
        grouped = {}
        count = 0
        panel_pid = None
        for process in psutil.process_iter(["name", "memory_info"]):
            count += 1
            try:
                cpu = process.cpu_percent(None)
            except psutil.Error:
                continue
            memory = process.info["memory_info"]
            if memory is None or memory.rss == 0:
                continue  # threads do kernel
            name = process.info["name"] or "?"
            if name == "cosmic-panel":
                panel_pid = process.pid
            entry = grouped.setdefault(name, [0.0, 0])
            entry[0] += cpu
            entry[1] += memory.rss
        self.process_count = count
        self.panel_pid = panel_pid
        ranked = sorted(grouped.items(), key=lambda item: (item[1][0], item[1][1]), reverse=True)
        self.processes = [(name, cpu / self.core_count, memory)
                          for name, (cpu, memory) in ranked[:TOP_PROCESSES]]

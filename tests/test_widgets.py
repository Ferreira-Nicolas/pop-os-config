"""Testes dos widgets. Execute com: python3 -m unittest discover tests"""

import math
import os
import random
import socket
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thinkpad_widgets import app, cards, media, metrics, theme, wayland  # noqa: E402

ACCENT = "(\n    base: (\n        red: 0.4352984,\n        green: 0.0,\n        blue: 0.816159,\n        alpha: 1.0,\n    ),\n)"


NO_OVERRIDES = "/nao/existe"


def write(root, relative, text):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class ThemeTest(unittest.TestCase):
    def test_reads_the_accent_of_the_active_variant(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "com.system76.CosmicTheme.Mode/v1/is_dark", "true")
            write(root, "com.system76.CosmicTheme.Dark/v1/accent", ACCENT)
            loaded = theme.Theme(root, NO_OVERRIDES)
        self.assertAlmostEqual(loaded.accent[0], 0.4352984)
        self.assertAlmostEqual(loaded.accent[2], 0.816159)

    def test_reads_the_hexadecimal_format_of_v2(self):
        color = theme.parse_color('(\n    base: "#860909FF",\n    hover: "#7F1B1BFF",\n)')
        self.assertEqual(color, (0x86 / 255, 0x09 / 255, 0x09 / 255))

    def test_newest_version_wins_over_a_stale_folder(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "com.system76.CosmicTheme.Mode/v1/is_dark", "true")
            write(root, "com.system76.CosmicTheme.Dark/v1/accent", ACCENT)
            write(root, "com.system76.CosmicTheme.Dark/v2/accent", '(base: "#860909FF")')
            loaded = theme.Theme(root, NO_OVERRIDES)
        self.assertAlmostEqual(loaded.accent[0], 0x86 / 255)

    def test_version_ten_is_newer_than_version_two(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "x/v2/accent", "")
            write(root, "x/v10/accent", "")
            self.assertEqual(theme.latest_version(Path(root) / "x").name, "v10")

    def test_missing_configuration_uses_the_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(theme.Theme(root, NO_OVERRIDES).accent, theme.FALLBACK["accent"])

    def test_notices_a_changed_accent(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "com.system76.CosmicTheme.Dark/v1/accent", ACCENT)
            loaded = theme.Theme(root, NO_OVERRIDES)
            self.assertFalse(loaded.changed())
            path.write_text(ACCENT.replace("0.816159", "0.5"))
            os.utime(path, ns=(1, 1))
            self.assertTrue(loaded.changed())

    def test_light_tint_keeps_the_hue_and_gains_contrast(self):
        accent = (0.4352984, 0.0, 0.816159)
        tint = theme.shifted(accent, lightness=0.74, saturation=1.0)
        self.assertGreater(sum(tint), sum(accent))
        self.assertAlmostEqual(theme.colorsys.rgb_to_hls(*tint)[0],
                               theme.colorsys.rgb_to_hls(*accent)[0])


    def test_overrides_replace_only_the_listed_colors(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "com.system76.CosmicTheme.Dark/v1/accent", ACCENT)
            automatic = theme.Theme(root, NO_OVERRIDES)
            path = write(root, "widgets.conf", "# comentário\nPRIMARY_COLOR=#7DBEFF\n")
            loaded = theme.Theme(root, path)
        self.assertEqual(loaded.primary, (0x7D / 255, 0xBE / 255, 1.0))
        self.assertEqual(loaded.secondary, automatic.secondary)

    def test_invalid_override_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "widgets.conf", "PRIMARY_COLOR=azul\nOUTRA=#123456\n")
            self.assertEqual(theme.read_overrides(path), {})

    def test_notices_an_edited_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "widgets.conf", "PRIMARY_COLOR=#7DBEFF\n")
            loaded = theme.Theme(root, path)
            self.assertFalse(loaded.changed())
            os.utime(path, ns=(1, 1))
            self.assertTrue(loaded.changed())

    def test_example_configuration_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "config/widgets.conf"
        self.assertEqual(set(theme.read_overrides(example)), set(theme.OVERRIDE_NAMES.values()))

    def test_companion_of_red_avoids_the_warning_yellow(self):
        color = theme.companion((0x86 / 255, 0x09 / 255, 0x09 / 255), 48, 0.74)
        hue = theme.colorsys.rgb_to_hls(*color)[0] * 360
        self.assertFalse(theme.WARNING_HUES[0] <= hue <= theme.WARNING_HUES[1])

    def test_companion_of_violet_keeps_the_rotation(self):
        accent = (0.4352984, 0.0, 0.816159)
        self.assertEqual(theme.companion(accent, 48, 0.74),
                         theme.shifted(accent, 48, 0.74, 1.0))


class ParsingTest(unittest.TestCase):
    def test_volume(self):
        self.assertEqual(metrics.parse_volume("Volume: 0.45\n"), (0.45, False))
        self.assertEqual(metrics.parse_volume("Volume: 0.30 [MUTED]\n"), (0.30, True))
        self.assertEqual(metrics.parse_volume(""), (None, False))

    def test_temperature_limit_comes_from_the_monitor_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "t.conf", "# limite\nTEMPERATURE_LIMIT_C=75\nCOOLDOWN_SECONDS=300\n")
            self.assertEqual(metrics.read_temperature_limit(path), 75)

    def test_temperature_limit_falls_back_when_missing(self):
        self.assertEqual(metrics.read_temperature_limit("/nao/existe"),
                         metrics.DEFAULT_TEMPERATURE_LIMIT)


class SysfsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name

    def test_cpu_uses_the_highest_coretemp_reading(self):
        write(self.root, "class/hwmon/hwmon0/name", "coretemp")
        write(self.root, "class/hwmon/hwmon0/temp1_input", "47000")
        write(self.root, "class/hwmon/hwmon0/temp2_input", "52000")
        write(self.root, "class/hwmon/hwmon1/name", "nvme")
        write(self.root, "class/hwmon/hwmon1/temp1_input", "36000")
        write(self.root, "class/hwmon/hwmon2/name", "thinkpad")
        write(self.root, "class/hwmon/hwmon2/fan1_input", "2500")
        sample = metrics.Metrics(self.root)
        sample._sample_temperatures()
        self.assertEqual(sample.cpu_temperature, 52)
        self.assertEqual(sample.temperatures, [("CPU", 52), ("SSD", 36)])
        self.assertEqual(sample.fan_rpm, 2500)

    def test_battery_time_left(self):
        for name, value in (("status", "Discharging"), ("capacity", "80"), ("energy_now", "30000000"),
                            ("energy_full", "40000000"), ("energy_full_design", "50000000"),
                            ("power_now", "10000000"), ("cycle_count", "93")):
            write(self.root, f"class/power_supply/BAT0/{name}", value)
        sample = metrics.Metrics(self.root)
        sample._sample_battery()
        self.assertEqual(sample.battery["status"], "Na bateria")
        self.assertEqual(sample.battery["hours"], 3)
        self.assertEqual(sample.battery["health"], 80)

    def test_machine_without_battery(self):
        sample = metrics.Metrics(self.root)
        sample._sample_battery()
        self.assertIsNone(sample.battery)


class MachineNameTest(unittest.TestCase):
    def name(self, **files):
        with tempfile.TemporaryDirectory() as root:
            for key, value in files.items():
                write(root, f"class/dmi/id/{key}", value)
            return metrics.machine_name(root)

    def test_thinkpad_uses_the_product_version(self):
        self.assertEqual(self.name(product_version="ThinkPad L14 Gen 2", product_name="20X2006BBO"),
                         "ThinkPad L14 Gen 2")

    def test_generic_text_falls_back_to_the_product_name(self):
        self.assertEqual(self.name(product_version="To Be Filled By O.E.M.", product_name="B550 AORUS"),
                         "B550 AORUS")

    def test_numeric_version_is_not_a_name(self):
        self.assertEqual(self.name(product_version="1.0", product_name="MS-7C91"), "MS-7C91")

    def test_nothing_useful(self):
        self.assertIsNone(self.name(product_version="Default string", product_name="System Product Name"))


class LayoutTest(unittest.TestCase):
    def names(self, column):
        return [type(card).__name__ for card in column]

    def test_desktop_without_battery_hides_the_card(self):
        sample = types.SimpleNamespace(battery=None)
        self.assertNotIn("BatteryCard", self.names(app.left_column(sample)))
        self.assertNotIn("BatteryCard", self.names(app.compact_column(sample)))

    def test_notebook_keeps_the_card(self):
        self.assertIn("BatteryCard", self.names(app.left_column(types.SimpleNamespace(battery={"percent": 50}))))

    def test_scaled_screen_is_positioned_by_its_logical_width(self):
        output = wayland.Output.__new__(wayland.Output)
        output.width, output.scale, output.logical_width = 2560, 2, 2048  # escala de 125%
        self.assertEqual(output.layout_width, 2048)
        output.logical_width = 0
        self.assertEqual(output.layout_width, 1280)


class FormatTest(unittest.TestCase):
    def test_sizes_use_a_decimal_comma(self):
        self.assertEqual(cards.data_size(512), "512 B")
        self.assertEqual(cards.data_size(1536), "1,5 KiB")
        self.assertEqual(cards.data_size(225 * 1024 ** 3), "225 GiB")

    def test_durations(self):
        self.assertEqual(cards.duration(3.5), "3h 30min")
        self.assertEqual(cards.duration(0.5), "30min")

    def test_rounded_rows_stay_inside_the_card(self):
        rows = cards.rounded_rows(0, 0, 320, 100, 16)
        for x, y, width, height in rows:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + width, 320)
            self.assertLessEqual(y + height, 100)
        corner = min(rows, key=lambda row: row[1])
        self.assertGreater(corner[0], 0)  # a primeira linha é recuada pela curva


def tone(frequency, amplitude=8000):
    return [int(amplitude * math.sin(2 * math.pi * frequency * index / media.SAMPLE_RATE))
            for index in range(media.BLOCK_SAMPLES)]


class MediaTest(unittest.TestCase):
    def test_a_playing_player_wins_over_a_paused_one(self):
        output = "Paused\tvlc\tA\tParado\nPlaying\tspotify\tPink Floyd\tTime\n"
        self.assertEqual(media.parse_players(output)["title"], "Time")

    def test_paused_player_is_still_shown(self):
        track = media.parse_players("Paused\tvlc\t\tFilme\n")
        self.assertEqual(track["title"], "Filme")
        self.assertFalse(track["playing"])

    def test_youtube_topic_suffix_is_removed(self):
        track = media.parse_players("Playing\tfirefox\tDaft Punk - Topic\tSomething About Us\n")
        self.assertEqual(track["artist"], "Daft Punk")

    def test_a_band_called_topic_is_kept(self):
        self.assertEqual(media.clean_artist("Topic"), "Topic")

    def test_nothing_playing(self):
        self.assertIsNone(media.parse_players(""))
        self.assertIsNone(media.parse_players("Stopped\tfirefox\t\t\n"))

    SINKS = "10\thdmi\tPipeWire\ts16le\tRUNNING\n11\tfones\tPipeWire\ts16le\tIDLE\n"

    def stream(self, sink, corked="no"):
        return f"Sink Input #5\n\tDriver: PipeWire\n\tSink: {sink}\n\tCorked: {corked}\n\tMute: no\n"

    def test_bars_follow_the_sink_that_is_playing(self):
        self.assertEqual(media.pick_sink(self.stream(11), self.SINKS, "hdmi"), "fones")

    def test_paused_stream_does_not_count(self):
        self.assertEqual(media.pick_sink(self.stream(11, "yes"), self.SINKS, "hdmi"), "hdmi")

    def test_default_sink_wins_when_it_is_also_playing(self):
        self.assertEqual(media.pick_sink(self.stream(11) + self.stream(10), self.SINKS, "hdmi"), "hdmi")

    def test_no_streams_uses_the_default(self):
        self.assertEqual(media.pick_sink("", self.SINKS, "hdmi"), "hdmi")

    def test_a_tone_lights_up_its_own_band(self):
        frequencies = media.band_frequencies()
        spectrum = media.Spectrum()
        spectrum.feed(tone(frequencies[5]))
        self.assertEqual(spectrum.levels.index(max(spectrum.levels)), 5)

    def test_silence_keeps_the_bars_down(self):
        spectrum = media.Spectrum()
        spectrum.feed([0] * media.BLOCK_SAMPLES)
        self.assertEqual(max(spectrum.levels), 0)

    def test_quiet_music_still_moves_the_bars(self):
        spectrum = media.Spectrum()
        spectrum.feed(tone(media.band_frequencies()[3], amplitude=400))
        self.assertGreater(max(spectrum.levels), 0.5)

    def test_broadband_sound_moves_several_bars(self):
        spectrum = media.Spectrum()
        noise = random.Random(1)
        spectrum.feed([noise.randint(-8000, 8000) for _ in range(media.BLOCK_SAMPLES)])
        self.assertGreaterEqual(sum(level > 0.5 for level in spectrum.levels), 5)

    def test_every_band_has_its_own_bins(self):
        for low, high in media.band_edges():
            self.assertLess(low, high)
            self.assertLessEqual(high, media.BLOCK_SAMPLES // 2)

    def test_bars_fall_gradually(self):
        spectrum = media.Spectrum()
        spectrum.feed(tone(media.band_frequencies()[2]))
        before = max(spectrum.levels)
        spectrum.feed([0] * media.BLOCK_SAMPLES)
        self.assertAlmostEqual(max(spectrum.levels), before - media.FALL_PER_FRAME)


class PanelOrderTest(unittest.TestCase):
    def test_a_new_panel_requires_recreating_the_strips(self):
        self.assertTrue(app.panel_restarted(100, 200))
        self.assertTrue(app.panel_restarted(None, 200))

    def test_same_or_missing_panel_changes_nothing(self):
        self.assertFalse(app.panel_restarted(100, 100))
        self.assertFalse(app.panel_restarted(100, None))


class WaylandTest(unittest.TestCase):
    def connection(self):
        connection = wayland.Connection.__new__(wayland.Connection)
        connection.socket, peer = socket.socketpair()
        connection.incoming = bytearray()
        connection.objects = {}
        self.addCleanup(connection.socket.close)
        self.addCleanup(peer.close)
        return connection, peer

    def test_strings_are_terminated_and_padded(self):
        connection, peer = self.connection()
        proxy = wayland.Proxy(connection, 7, "wl_test")
        proxy.request(3, "s", "abc")
        message = peer.recv(64)
        self.assertEqual(struct.unpack_from("=II", message), (7, (16 << 16) | 3))
        self.assertEqual(message[8:], struct.pack("=I", 4) + b"abc\0")

    def test_events_reach_the_handler(self):
        connection, peer = self.connection()
        received = []
        proxy = wayland.Proxy(connection, 9, "wl_callback").on("done", received.append)
        connection.objects[9] = proxy
        peer.send(struct.pack("=III", 9, 12 << 16, 42))
        connection.read()
        self.assertEqual(received, [42])

    def test_partial_messages_wait_for_the_rest(self):
        connection, peer = self.connection()
        received = []
        connection.objects[9] = wayland.Proxy(connection, 9, "wl_callback").on("done", received.append)
        message = struct.pack("=III", 9, 12 << 16, 42)
        peer.send(message[:6])
        connection.read()
        self.assertEqual(received, [])
        peer.send(message[6:])
        connection.read()
        self.assertEqual(received, [42])


if __name__ == "__main__":
    unittest.main()

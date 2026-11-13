"""Testes dos widgets. Execute com: python3 -m unittest discover tests"""

import io
import json
import math
import os
import random
import socket
import struct
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from thinkpad_widgets import app, cards, gpu, media, metrics, popup, theme, volume, wayland  # noqa: E402

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


class GpuTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def intel(self, sleep=1000):
        write(self.root, "class/drm/card1/gt/gt0/rc6_residency_ms", str(sleep))
        write(self.root, "class/drm/card1/gt/gt0/rps_act_freq_mhz", "0")
        write(self.root, "class/drm/card1/gt/gt0/rps_cur_freq_mhz", "1050")
        write(self.root, "class/drm/card1/gt/gt0/rps_max_freq_mhz", "1300")

    def test_intel_use_is_the_time_awake(self):
        self.intel(1000)
        reader = gpu.detect_gpu(self.root)
        self.assertIsNone(reader.read(2)[0])  # a primeira leitura só marca o ponto de partida
        self.intel(2400)  # dormiu 1,4 s de 2 s
        self.assertAlmostEqual(reader.read(2)[0], 30)

    def test_intel_asleep_keeps_the_requested_frequency(self):
        self.intel()
        _percent, current, maximum = gpu.detect_gpu(self.root).read(2)
        self.assertEqual((current, maximum), (1.05, 1.3))

    def test_intel_use_stays_between_0_and_100(self):
        self.intel(1000)
        reader = gpu.detect_gpu(self.root)
        reader.read(2)
        self.intel(9000)
        self.assertEqual(reader.read(2)[0], 0)
        self.intel(9000)
        self.assertEqual(reader.read(2)[0], 100)

    def test_amd(self):
        write(self.root, "class/drm/card0/device/gpu_busy_percent", "42")
        write(self.root, "class/drm/card0/device/pp_dpm_sclk", "0: 500Mhz\n1: 1200Mhz *\n2: 2400Mhz\n")
        self.assertEqual(gpu.detect_gpu(self.root).read(2), (42, 1.2, 2.4))

    def test_dedicated_amd_wins_over_integrated_intel(self):
        self.intel()
        write(self.root, "class/drm/card2/device/gpu_busy_percent", "42")
        self.assertIsInstance(gpu.detect_gpu(self.root), gpu.AmdGpu)

    def test_nvidia_output(self):
        self.assertEqual(gpu.NvidiaGpu.parse("37, 1500, 2100\n"), (37, 1.5, 2.1))
        self.assertEqual(gpu.NvidiaGpu.parse("[N/A], 1500, 2100\n"), (None, None, None))
        self.assertEqual(gpu.NvidiaGpu.parse(""), (None, None, None))

    def test_machine_without_a_known_gpu_has_no_reader(self):
        self.assertIsNone(gpu.detect_gpu(self.root))

    def test_names_from_lspci(self):
        cases = {
            "00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics] (rev 01)": "Iris Xe",
            "01:00.0 VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060] (rev a1)": "GeForce RTX 3060",
            "03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600/6600 XT]": "Radeon RX 6600",
        }
        for line, name in cases.items():
            self.assertEqual(gpu.parse_gpu_name("00:1f.3 Audio device: Intel\n" + line), name)

    def test_history_only_keeps_real_readings(self):
        self.intel()
        sample = metrics.Metrics(self.root)
        sample._sample_gpu(2)
        self.assertEqual(len(sample.gpu_history), 0)
        self.intel(2000)
        sample._sample_gpu(2)
        self.assertEqual(list(sample.gpu_history), [50])


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

    def test_gpu_sits_between_cpu_and_temperature(self):
        sample = types.SimpleNamespace(disks=[("Sistema", 1, 2, 50)], gpu=object())
        names = self.names(app.right_column(sample))
        self.assertEqual(names[:3], ["CpuCard", "GpuCard", "TemperatureCard"])

    def test_no_gpu_reading_hides_the_card(self):
        sample = types.SimpleNamespace(disks=[("Sistema", 1, 2, 50)], gpu=None)
        self.assertNotIn("GpuCard", self.names(app.right_column(sample)))

    def test_right_column_fits_a_1080p_screen(self):
        sample = types.SimpleNamespace(disks=[("Sistema", 1, 2, 50)], gpu=object())
        column = app.right_column(sample)
        total = sum(card.height for card in column) + cards.GAP * (len(column) - 1)
        self.assertLess(app.TOP_MARGIN + 36 + total, 1080)

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

    def test_album_art_link_is_read(self):
        track = media.parse_players("Playing\tspotify\tA\tB\thttps://i.scdn.co/image/1\n")
        self.assertEqual(track["art"], "https://i.scdn.co/image/1")

    def test_track_without_art(self):
        self.assertEqual(media.parse_players("Playing\tvlc\tA\tB\t\n")["art"], "")

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


def png(width, height, painter):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    painter(cairo.Context(surface))
    output = io.BytesIO()
    surface.write_to_png(output)
    return output.getvalue()


def pixel(surface, x, y):
    data = surface.get_data()
    offset = y * surface.get_stride() + x * 4
    blue, green, red, alpha = data[offset:offset + 4]
    return red, green, blue, alpha


@unittest.skipIf(media.GdkPixbuf is None, "GdkPixbuf ausente")
class AlbumArtTest(unittest.TestCase):
    def test_colors_keep_their_channels(self):
        def red(context):
            context.set_source_rgb(1, 0, 0)
            context.paint()
        surface = media.art_surface(png(64, 64, red))
        self.assertEqual((surface.get_width(), surface.get_height()), (media.ART_PIXELS, media.ART_PIXELS))
        self.assertEqual(pixel(surface, 10, 10), (255, 0, 0, 255))

    def test_video_thumbnail_is_cropped_from_the_center(self):
        def sides_and_center(context):
            context.set_source_rgb(0, 0, 1)
            context.paint()
            context.set_source_rgb(0, 1, 0)
            context.rectangle(40, 0, 80, 80)
            context.fill()
        surface = media.art_surface(png(160, 80, sides_and_center))
        self.assertEqual(pixel(surface, 1, 24), (0, 255, 0, 255))
        self.assertEqual(pixel(surface, 46, 24), (0, 255, 0, 255))

    def test_broken_image_leaves_the_strip_without_art(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "capa.jpg", "isto não é uma imagem")
            art = media.AlbumArt()
            art.url = path.as_uri()
            art._load(art.url)
        self.assertIsNone(art.surface)

    def test_local_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "minha capa.png"
            path.write_bytes(png(8, 8, lambda context: context.paint()))
            art = media.AlbumArt()
            art.url = path.as_uri()
            art._load(art.url)
        self.assertIsNotNone(art.surface)

    def test_late_download_of_the_previous_song_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "antiga.png"
            path.write_bytes(png(8, 8, lambda context: context.paint()))
            art = media.AlbumArt()
            art.url = "https://exemplo.invalid/nova"
            art._load(path.as_uri())
        self.assertIsNone(art.surface)

    def test_other_schemes_are_refused(self):
        for url in ("ftp://exemplo.invalid/a.png", "data:image/png;base64,AAAA", "/etc/passwd"):
            with self.assertRaises(ValueError):
                media.read_art(url)


def track(**changes):
    return {"playing": True, "paused": False, "player": "spotify", "artist": "A", "title": "B",
            "art": "", "album": "C", "length": 200.0, "position": 50.0, "shuffle": False,
            "loop": "None", **changes}


class PlayerStateTest(unittest.TestCase):
    def test_full_metadata_is_parsed(self):
        line = "Playing\tspotify\tFrank Sinatra\tMy Way\thttps://a\tSinatra/Basie\t147000000\t78536020\tfalse\n"
        parsed = media.parse_players(line)
        self.assertEqual(parsed["album"], "Sinatra/Basie")
        self.assertEqual(parsed["length"], 147)
        self.assertAlmostEqual(parsed["position"], 78.53602)
        self.assertIs(parsed["shuffle"], False)

    def test_player_without_shuffle_or_length(self):
        parsed = media.parse_players("Playing\tfirefox\tA\tB\t\t\t\t\t\n")
        self.assertIsNone(parsed["shuffle"])
        self.assertEqual(parsed["length"], 0)

    def test_position_advances_only_while_playing(self):
        player = media.Media()
        player.current = track()
        player.sampled_at = media.time.monotonic() - 10
        self.assertAlmostEqual(player.position, 60, delta=0.5)
        player.current = track(playing=False)
        self.assertEqual(player.position, 50)

    def test_position_never_passes_the_end(self):
        player = media.Media()
        player.current = track(position=199.0)
        player.sampled_at = media.time.monotonic() - 60
        self.assertEqual(player.position, 200)

    def test_pause_shows_at_once_and_keeps_the_position(self):
        player = media.Media()
        player.current = track()
        player.sampled_at = media.time.monotonic() - 10
        with unittest.mock.patch.object(media.threading, "Thread"):
            player.control("play-pause")
        self.assertFalse(player.current["playing"])
        self.assertAlmostEqual(player.position, 60, delta=0.5)

    def test_commands_go_to_the_right_player(self):
        player = media.Media()
        player.current = track()
        with unittest.mock.patch.object(media.threading, "Thread") as thread:
            player.control("shuffle")
        self.assertEqual(thread.call_args.kwargs["args"][0],
                         ["playerctl", "--player=spotify", "shuffle", "Toggle"])
        self.assertTrue(player.current["shuffle"])

    def test_repeat_starts_with_the_current_song(self):
        player = media.Media()
        player.current = track()
        with unittest.mock.patch.object(media.threading, "Thread") as thread:
            player.control("repeat")
        self.assertEqual(player.current["loop"], "Track")
        self.assertEqual(thread.call_args.kwargs["args"][0], ["playerctl", "--player=spotify", "loop", "Track"])

    def test_repeat_cycles_back_to_off(self):
        player = media.Media()
        player.current = track()
        with unittest.mock.patch.object(media.threading, "Thread"):
            for _click in range(3):
                player.control("repeat")
        self.assertEqual(player.current["loop"], "None")

    def test_player_without_repeat_ignores_the_click(self):
        player = media.Media()
        player.current = track(loop=None)
        with unittest.mock.patch.object(media.threading, "Thread") as thread:
            player.control("repeat")
        thread.assert_not_called()

    def test_loop_field_is_read(self):
        line = "Playing\tspotify\tA\tB\t\t\t1000000\t0\tfalse\tTrack\n"
        self.assertEqual(media.parse_players(line)["loop"], "Track")
        self.assertIsNone(media.parse_players("Playing\tfirefox\tA\tB\n")["loop"])

    def test_seek_moves_at_once_and_uses_a_dot_decimal(self):
        player = media.Media()
        player.current = track()
        with unittest.mock.patch.object(media.threading, "Thread") as thread:
            player.seek(120.5)
        self.assertAlmostEqual(player.position, 120.5, delta=0.5)
        self.assertEqual(thread.call_args.kwargs["args"][0],
                         ["playerctl", "--player=spotify", "position", "120.50"])

    def test_unknown_action_is_ignored(self):
        player = media.Media()
        player.current = track()
        with unittest.mock.patch.object(media.threading, "Thread") as thread:
            player.control("rm -rf")
        thread.assert_not_called()


class HoverTest(unittest.TestCase):
    def test_opens_only_after_the_delay(self):
        hover = popup.Hover()
        hover.enter("strip", 0)
        self.assertFalse(hover.tick(0.1))
        self.assertTrue(hover.tick(popup.OPEN_DELAY_SECONDS))
        self.assertTrue(hover.open)

    def test_passing_by_quickly_does_not_open(self):
        hover = popup.Hover()
        hover.enter("strip", 0)
        hover.leave("strip", 0.1)
        self.assertFalse(hover.tick(1))
        self.assertFalse(hover.open)

    def test_moving_from_the_strip_to_the_popup_keeps_it_open(self):
        hover = popup.Hover()
        hover.enter("strip", 0)
        hover.tick(1)
        hover.leave("strip", 2)
        hover.enter("popup", 2.1)
        self.assertFalse(hover.tick(5))
        self.assertTrue(hover.open)

    def test_closes_after_leaving(self):
        hover = popup.Hover()
        hover.enter("popup", 0)
        hover.tick(1)
        hover.leave("popup", 2)
        self.assertFalse(hover.tick(2.1))
        self.assertTrue(hover.tick(2 + popup.CLOSE_DELAY_SECONDS))
        self.assertFalse(hover.open)


class FakeProcess:
    def __init__(self, command, log):
        self.command = command
        self.finished = False
        log.append(command)

    def poll(self):
        return 0 if self.finished else None


def stream_json(index, percent, muted=False, **properties):
    return {"index": index, "mute": muted, "properties": properties,
            "volume": {"front-left": {"value_percent": f"{percent}%"}, "front-right": {"value_percent": f"{percent}%"}}}


SPOTIFY = stream_json(7, 50, **{"application.name": "Spotify", "node.name": "spotify"})
OTHER_APP = stream_json(3, 100, **{"application.name": "FreeRDP", "application.process.id": "2"})


def volume_control(level=0.5, muted=False, player="spotify"):
    commands = []
    streams = [] if level is None else volume.parse_streams(json.dumps([
        OTHER_APP, {**SPOTIFY, "mute": muted, "volume": {"mono": {"value_percent": f"{round(level * 100)}%"}}}]))
    control = volume.PlayerVolume(run=lambda command: FakeProcess(command, commands), read=lambda: streams,
                                  find_pid=lambda _player: None)
    control.update(player)
    return control, commands


def finish(control):
    control.process.finished = True
    control.flush()


class StreamMatchTest(unittest.TestCase):
    def test_parses_level_mute_and_names(self):
        stream = volume.parse_streams(json.dumps([stream_json(4, 72, True, **{"application.name": "Firefox",
                                                                                 "application.process.id": "88"})]))[0]
        self.assertEqual((stream["index"], stream["level"], stream["muted"], stream["pid"]), (4, 0.72, True, 88))
        self.assertIn("firefox", stream["names"])

    def test_bad_output_gives_no_streams(self):
        self.assertEqual(volume.parse_streams("não é json"), [])
        self.assertEqual(volume.parse_streams(""), [])

    def test_flatpak_player_is_found_by_name(self):
        streams = volume.parse_streams(json.dumps([OTHER_APP, SPOTIFY]))
        self.assertEqual([s["index"] for s in volume.player_streams(streams, "spotify")], [7])

    def test_browser_instance_name_is_reduced(self):
        streams = volume.parse_streams(json.dumps([OTHER_APP, stream_json(9, 80, **{"application.name": "Firefox"})]))
        self.assertEqual([s["index"] for s in volume.player_streams(streams, "firefox.instance_1_83")], [9])

    def test_chrome_child_process_is_found_by_ancestry(self):
        streams = volume.parse_streams(json.dumps([
            stream_json(5, 80, **{"application.name": "Google Chrome", "application.process.id": "300"}),
            stream_json(6, 80, **{"application.name": "Google Chrome", "application.process.id": "900"}),
        ]))
        parents = {300: 200, 200: 100, 900: 1}
        found = volume.player_streams(streams, "chromium.instance100", 100, parents.get)
        self.assertEqual([s["index"] for s in found], [5])  # a outra janela de Chrome fica de fora

    def test_unrelated_apps_are_never_touched(self):
        streams = volume.parse_streams(json.dumps([OTHER_APP]))
        self.assertEqual(volume.player_streams(streams, "spotify"), [])

    def test_parent_of_this_process(self):
        self.assertEqual(volume.parent_pid(os.getpid()), os.getppid())


class PlayerVolumeTest(unittest.TestCase):
    def test_reads_the_player_level_not_the_system(self):
        control, _commands = volume_control(level=0.4)
        self.assertEqual(control.level, 0.4)

    def test_sets_only_the_player_stream(self):
        control, commands = volume_control()
        control.set(0.3)
        self.assertEqual(control.level, 0.3)
        self.assertEqual(commands, [["pactl", "set-sink-input-volume", "7", "30%"]])

    def test_limits_to_the_slider_range(self):
        control, _commands = volume_control()
        control.set(1.7)
        self.assertEqual(control.level, 1.0)
        control.set(-0.2)
        self.assertEqual(control.level, 0.0)

    def test_dragging_sends_only_the_latest_level(self):
        control, commands = volume_control()
        for level in (0.1, 0.2, 0.3, 0.4):
            control.set(level)
        self.assertEqual(len(commands), 1)  # o primeiro ainda roda
        finish(control)
        self.assertEqual(commands[-1][-1], "40%")
        self.assertEqual(len(commands), 2)
        finish(control)
        self.assertFalse(control.busy)

    def test_changing_the_level_unmutes(self):
        control, commands = volume_control(muted=True)
        self.assertTrue(control.muted)
        control.set(0.6)
        finish(control)
        self.assertFalse(control.muted)
        self.assertIn(["pactl", "set-sink-input-mute", "7", "0"], commands)

    def test_toggle_mute(self):
        control, commands = volume_control()
        control.toggle_mute()
        self.assertTrue(control.muted)
        self.assertEqual(commands, [["pactl", "set-sink-input-mute", "7", "1"]])

    def test_scrolling_down_lowers(self):
        control, _commands = volume_control()
        control.scroll(volume.SCROLL_UNIT)
        self.assertAlmostEqual(control.level, 0.5 - volume.SCROLL_STEP)
        control.scroll(-2 * volume.SCROLL_UNIT)
        self.assertAlmostEqual(control.level, 0.5 + volume.SCROLL_STEP)

    def test_a_reading_does_not_undo_a_pending_change(self):
        control, _commands = volume_control()
        control.set(0.8)
        control.update("spotify")  # leitura antiga, de antes do comando terminar
        self.assertEqual(control.level, 0.8)
        finish(control)
        control.update("spotify")  # agora vale o que o sistema diz
        self.assertEqual(control.level, 0.5)

    def test_without_a_stream_nothing_is_sent(self):
        control, commands = volume_control(level=None)
        self.assertFalse(control.available)
        control.set(0.5)
        control.toggle_mute()
        self.assertEqual(commands, [])

    def test_player_change_looks_up_the_new_process(self):
        lookups = []
        control = volume.PlayerVolume(read=lambda: [], find_pid=lambda player: lookups.append(player))
        control.update("spotify")
        control.update("spotify")
        control.update("firefox")
        control.update(None)
        self.assertEqual(lookups, ["spotify", "firefox"])

    def test_missing_pactl_is_ignored(self):
        def missing(_command):
            raise FileNotFoundError
        control = volume.PlayerVolume(run=missing, read=lambda: volume.parse_streams(json.dumps([SPOTIFY])),
                                      find_pid=lambda _player: None)
        control.update("spotify")
        control.set(0.2)
        self.assertFalse(control.busy)


class PopupTest(unittest.TestCase):
    def view(self, level=0.5, muted=False, **changes):
        player = media.Media()
        player.current = track(**changes)
        return popup.MediaPopup(player, media.AudioCapture(), volume_control(level, muted, player.current["player"])[0])

    def test_volume_slider_follows_the_click(self):
        view = self.view()
        view.move(popup.VOLUME_LEFT + popup.VOLUME_WIDTH * 0.25, popup.VOLUME_Y)
        self.assertEqual(view.hovered, "volume")
        self.assertFalse(view.press())
        self.assertAlmostEqual(view.volume.level, 0.25)

    def test_volume_drag_continues_outside_the_slider(self):
        view = self.view()
        view.move(popup.VOLUME_LEFT, popup.VOLUME_Y)
        view.press()
        view.move(popup.VOLUME_LEFT + popup.VOLUME_WIDTH * 0.7, popup.VOLUME_Y - 60)
        self.assertAlmostEqual(view.volume.level, 0.7)
        view.move(popup.WIDTH + 80, 0)
        self.assertEqual(view.volume.level, 1.0)
        view.release()
        view.move(popup.VOLUME_LEFT, popup.VOLUME_Y)
        self.assertEqual(view.volume.level, 1.0)  # solto: só passar por cima não muda

    def test_speaker_icon_mutes(self):
        view = self.view()
        view.move(popup.VOLUME_ICON_X, popup.VOLUME_Y)
        self.assertEqual(view.hovered, "mute")
        view.press()
        self.assertTrue(view.volume.muted)

    def test_scroll_changes_volume(self):
        view = self.view()
        view.scroll(volume.SCROLL_UNIT)
        self.assertLess(view.volume.level, 0.5)

    def test_volume_row_fits_and_clears_the_buttons(self):
        self.assertLessEqual(popup.VOLUME_Y + popup.VOLUME_HIT_MARGIN, popup.HEIGHT)
        tallest = max(radius for _action, _x, radius in popup.BUTTONS)
        self.assertGreater(popup.VOLUME_Y - popup.VOLUME_HIT_MARGIN, popup.BUTTON_Y + tallest)

    def test_volume_is_disabled_without_sound_server(self):
        view = self.view(level=None)
        self.assertIsNone(view.hit(popup.VOLUME_LEFT + 50, popup.VOLUME_Y))

    def test_each_button_answers_at_its_center(self):
        view = self.view()
        for action, x, _radius in popup.BUTTONS:
            self.assertEqual(view.hit(x, popup.BUTTON_Y), action)

    def test_progress_bar_is_clickable_with_margin(self):
        view = self.view()
        self.assertEqual(view.hit(100, popup.PROGRESS_Y + 2), "seek")
        self.assertEqual(view.hit(100, popup.PROGRESS_Y - 4), "seek")
        self.assertIsNone(view.hit(100, popup.PROGRESS_Y - 15))

    def test_click_on_the_bar_goes_to_that_point(self):
        view = self.view()
        view.move(popup.PADDING + popup.PROGRESS_WIDTH / 2, popup.PROGRESS_Y)
        self.assertEqual(view.seek_target(), 100)
        with unittest.mock.patch.object(view.media, "seek") as seek:
            self.assertTrue(view.click())
        seek.assert_called_once_with(100)

    def test_bar_ends_map_to_start_and_end(self):
        view = self.view()
        view.move(popup.PADDING, popup.PROGRESS_Y)
        self.assertEqual(view.seek_target(), 0)
        view.move(popup.PADDING + popup.PROGRESS_WIDTH, popup.PROGRESS_Y)
        self.assertEqual(view.seek_target(), 200)

    def test_live_stream_without_length_cannot_seek(self):
        self.assertIsNone(self.view(length=0).hit(100, popup.PROGRESS_Y))

    def test_repeat_is_disabled_when_the_player_has_none(self):
        self.assertIsNone(self.view(loop=None).hit(popup.BUTTONS[-1][1], popup.BUTTON_Y))

    def test_click_outside_does_nothing(self):
        view = self.view()
        view.move(300, 40)
        self.assertFalse(view.click())

    def test_button_click_reaches_the_player(self):
        view = self.view()
        view.move(popup.BUTTONS[-1][1], popup.BUTTON_Y)
        with unittest.mock.patch.object(view.media, "control") as control:
            view.click()
        control.assert_called_once_with("repeat")

    def test_clicking_elsewhere_does_nothing(self):
        self.assertIsNone(self.view().hit(300, 40))

    def test_shuffle_is_disabled_when_the_player_has_none(self):
        x = popup.BUTTONS[0][1]
        self.assertIsNone(self.view(shuffle=None).hit(x, popup.BUTTON_Y))

    def test_buttons_do_not_overlap(self):
        for (_a, first, first_radius), (_b, second, second_radius) in zip(popup.BUTTONS, popup.BUTTONS[1:]):
            self.assertGreater(second - first, first_radius + second_radius)

    def test_paints_every_state_without_errors(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, popup.WIDTH, popup.HEIGHT)
        colors = types.SimpleNamespace(primary=(1, 0, 1), secondary=(1, 0, 0), glass=(0, 0, 0))
        for changes in ({}, {"playing": False}, {"shuffle": True}, {"loop": "Track"}, {"loop": "Playlist"},
                        {"shuffle": None, "loop": None, "length": 0, "album": ""},
                        {"muted": True}, {"level": 0}, {"level": None}):
            view = self.view(**changes)
            view.paint(cairo.Context(surface), colors)
            view.move(100, popup.PROGRESS_Y)
            view.paint(cairo.Context(surface), colors)

    def test_columns_keep_the_ends_of_the_spectrum(self):
        result = popup.columns([0.2, 0.4, 0.6, 0.8, 1.0, 0.8, 0.6, 0.1], 40)
        self.assertEqual(len(result), 40)
        self.assertAlmostEqual(result[0], 0.2)
        self.assertAlmostEqual(result[-1], 0.1)

    def test_columns_stay_between_the_bands(self):
        result = popup.columns([0.0, 1.0] * 4, 40)
        self.assertGreaterEqual(min(result), 0)
        self.assertLessEqual(max(result), 1)

    def test_silence_draws_flat_columns(self):
        self.assertEqual(set(popup.columns([0.0] * 8, 40)), {0.0})

    def test_times(self):
        self.assertEqual(popup.time_text(78.5), "1:18")
        self.assertEqual(popup.time_text(3725), "1:02:05")


class FakeSurface:
    def __init__(self, ready):
        self.ready = ready
        self.drawn = 0

    def can_draw(self):
        return self.ready

    def draw(self, painter):
        self.drawn += 1


class RedrawTest(unittest.TestCase):
    def application(self, *surfaces):
        application = app.Application.__new__(app.Application)
        application.painted = [(surface, None) for surface in surfaces]
        application.invalidate()
        return application

    def test_a_covered_surface_does_not_force_the_others_to_redraw(self):
        free, covered = FakeSurface(True), FakeSurface(False)
        application = self.application(free, covered)
        for _frame in range(25):
            application.draw()
        self.assertEqual(free.drawn, 1)
        self.assertEqual(covered.drawn, 0)

    def test_a_surface_that_was_waiting_draws_when_it_is_released(self):
        covered = FakeSurface(False)
        application = self.application(covered)
        application.draw()
        covered.ready = True
        application.draw()
        application.draw()
        self.assertEqual(covered.drawn, 1)

    def test_new_sample_redraws_everything_once(self):
        free = FakeSurface(True)
        application = self.application(free)
        application.draw()
        application.invalidate()
        application.draw()
        self.assertEqual(free.drawn, 2)


class PanelOrderTest(unittest.TestCase):
    def test_a_new_panel_requires_recreating_the_strips(self):
        self.assertTrue(app.panel_restarted(100, 200))
        self.assertTrue(app.panel_restarted(None, 200))

    def test_same_or_missing_panel_changes_nothing(self):
        self.assertFalse(app.panel_restarted(100, 100))
        self.assertFalse(app.panel_restarted(100, None))


class PointerEventTest(unittest.TestCase):
    def test_fixed_point_coordinates(self):
        connection = wayland.Connection.__new__(wayland.Connection)
        values = connection._decode("uoff", struct.pack("=IIii", 7, 12, 100 * 256 + 128, 3 * 256))
        self.assertEqual(values, [7, 12, 100.5, 3.0])

    def pointer(self):
        pointer = wayland.Pointer.__new__(wayland.Pointer)
        pointer.focus = 9
        pointer.on_press = pointer.on_release = pointer.on_scroll = None
        return pointer

    def test_only_the_left_button_counts(self):
        pointer = self.pointer()
        events = []
        pointer.on_press = lambda surface: events.append(("press", surface))
        pointer.on_release = lambda surface: events.append(("release", surface))
        pointer._on_button(1, 1, 0x111, wayland.BUTTON_PRESSED)
        pointer._on_button(1, 1, wayland.BUTTON_LEFT, wayland.BUTTON_PRESSED)
        pointer._on_button(1, 1, wayland.BUTTON_LEFT, 0)
        self.assertEqual(events, [("press", 9), ("release", 9)])

    def test_only_vertical_scroll_counts(self):
        pointer = self.pointer()
        scrolls = []
        pointer.on_scroll = lambda surface, amount: scrolls.append((surface, amount))
        pointer._on_axis(1, 1, 15.0)
        pointer._on_axis(1, wayland.AXIS_VERTICAL, -15.0)
        self.assertEqual(scrolls, [(9, -15.0)])


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

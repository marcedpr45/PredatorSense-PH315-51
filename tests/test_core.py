import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from predatorsense import config as cfgmod
from predatorsense import curve, daemon
from predatorsense.client import ServiceError, request
from predatorsense.hw import HardwareError
from predatorsense.hw import ec as ecmod
from predatorsense.hw import wmi


class WmiEncodingTest(unittest.TestCase):
    """Las constantes salen de CommonFunction.cs de la versión Windows."""

    def test_all_fans_behavior_matches_windows(self):
        both = lambda m: wmi.fan_behavior_arg({"cpu": m, "gpu": m})  # noqa: E731
        self.assertEqual(both("auto"), 9 | 4259840)
        self.assertEqual(both("max"), 9 | 8519680)
        self.assertEqual(both("custom"), 9 | 12779520)

    def test_single_fan_behavior_matches_windows(self):
        self.assertEqual(wmi.fan_behavior_arg({"cpu": "custom"}), 1 | (3 << 16))
        self.assertEqual(wmi.fan_behavior_arg({"gpu": "auto"}), 8 | (1 << 22))

    def test_fan_speed(self):
        self.assertEqual(wmi.fan_speed_arg("cpu", 70), 1 | (70 << 8))
        self.assertEqual(wmi.fan_speed_arg("gpu", 150), 4 | (100 << 8))

    def test_profile_and_led(self):
        self.assertEqual(wmi.win_key_arg(True), (1 << 24) | 2)
        self.assertEqual(wmi.win_key_arg(False), 2)
        self.assertEqual(wmi.lcd_overdrive_arg(True), 16 | 281474976710656)
        self.assertEqual(wmi.sysinfo_arg(wmi.SENSOR_GPU_TEMP), 1 | (10 << 8))

    def test_call_format_and_parse(self):
        self.assertEqual(wmi.build_call(wmi.DEFAULT_METHOD_PATH, 14, wmi.encode_u64(0x410009)),
                         r"\_SB.PCI0.WMID.WMBH 0 14 b0900410000000000")
        self.assertEqual(wmi.parse_result("{0x00, 0x2d, 0x00, 0x00}"), 0x2d00)
        self.assertEqual(wmi.parse_result("0x0\x00"), 0)
        with self.assertRaises(HardwareError):
            wmi.parse_result("Error: AE_NOT_FOUND")

    def test_backend_roundtrip_with_fake_proc(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            path = f.name
        try:
            b = wmi.WmiBackend(call_path=path)
            # Simula acpi_call: lo escrito se sobrescribe con una respuesta de éxito
            real_open = open

            def fake_open(p, mode="r", *a, **k):
                if p == path and mode == "r":
                    return real_open(p.replace(p, path + ".resp"), mode)
                return real_open(p, mode, *a, **k)
            with real_open(path + ".resp", "w") as r:
                r.write("{0x00, 0x34, 0x0c, 0x00}")
            with mock.patch("builtins.open", fake_open):
                self.assertEqual(b.sensor(wmi.SENSOR_CPU_FAN), 0x0C34)
            with real_open(path) as f:
                self.assertEqual(f.read(), r"\_SB.PCI0.WMID.WMBH 0 5 b01020000")
            self.assertTrue(b.capabilities()["win_key"])
            self.assertFalse(b.capabilities()["coolboost"])
            with self.assertRaises(HardwareError):
                b.set_coolboost(True)
        finally:
            for p in (path, path + ".resp"):
                os.unlink(p)


class BmofTest(unittest.TestCase):
    def test_mrci_literals_and_match(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from bmof_decompress import mrci_decompress
        # "FOMB" como en la cabecera real de la BIOS (4 literales)
        self.assertEqual(mrci_decompress(bytes.fromhex("445300011a7dda5428"), 4), b"FOMB")


class CurveTest(unittest.TestCase):
    PTS = [[40, 20], [60, 40], [80, 80]]

    def test_evaluate(self):
        self.assertEqual(curve.evaluate(self.PTS, 30), 20)
        self.assertEqual(curve.evaluate(self.PTS, 50), 30)
        self.assertEqual(curve.evaluate(self.PTS, 50, interpolate=False), 20)
        self.assertEqual(curve.evaluate(self.PTS, 95), 80)

    def test_hysteresis(self):
        c = curve.CurveController(hysteresis=3)
        self.assertEqual(c.target(self.PTS, 70), 60)
        self.assertIsNone(c.target(self.PTS, 68))       # dentro de la histéresis
        self.assertEqual(c.target(self.PTS, 74), 68)    # sube enseguida
        self.assertEqual(c.target(self.PTS, 60), 46)    # bajó > 3°: evalúa 63°

    def test_spike_filter(self):
        c = curve.CurveController()
        c.target(self.PTS, 50)
        self.assertIsNone(c.target(self.PTS, 99))       # pico aislado ignorado
        self.assertEqual(c.target(self.PTS, 98), 80)    # confirmado
        self.assertIsNone(c.target(self.PTS, 0))        # lectura imposible


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")

    def test_persists_across_reload(self):
        c = cfgmod.Config(self.path)
        c.update_active({"fans": {"cpu": {"mode": "custom", "percent": 65}}, "coolboost": True})
        c.save_profile_as("Juego")
        c.select_profile("Juego")
        c.save()
        c2 = cfgmod.Config(self.path)
        self.assertEqual(c2.active_name, "Juego")
        self.assertEqual(c2.active["fans"]["cpu"], {"mode": "custom", "percent": 65})
        self.assertTrue(c2.active["coolboost"])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o644")
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".config-")], [])

    def test_corrupt_file_recovers(self):
        with open(self.path, "w") as f:
            f.write("{roto")
        c = cfgmod.Config(self.path)
        self.assertEqual(c.active_name, "Predeterminado")
        self.assertTrue(os.path.exists(self.path + ".corrupt"))

    def test_invalid_profiles_dropped(self):
        with open(self.path, "w") as f:
            json.dump({"active_profile": "malo", "profiles": {
                "malo": {"fans": {"cpu": {"mode": "volar"}}},
                "bueno": {"coolboost": True}}}, f)
        c = cfgmod.Config(self.path)
        self.assertEqual(list(c.data["profiles"]), ["bueno"])
        self.assertEqual(c.active_name, "bueno")
        self.assertTrue(c.active["coolboost"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            cfgmod.validate_curve([[50, 10], [50, 20]])
        with self.assertRaises(ValueError):
            cfgmod.validate_name("../x")
        c = cfgmod.Config(self.path)
        with self.assertRaises(ValueError):
            c.delete_profile("Predeterminado")
        c.save_profile_as("B")
        c.rename_profile("Predeterminado", "A")
        self.assertEqual(c.active_name, "A")


class EcTest(unittest.TestCase):
    def test_read_write(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(bytes(256))
        try:
            b = ecmod.EcBackend(f.name)
            b.set_fan("cpu", "custom", 70)
            b.set_fan("gpu", "max")
            b.set_coolboost(True)
            fans = b.get_fans()
            self.assertEqual(fans["cpu"]["mode"], "custom")
            self.assertEqual(fans["cpu"]["percent"], 70)
            self.assertEqual(fans["gpu"]["mode"], "max")
            self.assertTrue(b.get_coolboost())
            with open(f.name, "rb") as r:
                data = r.read()
            self.assertEqual((data[0x22], data[0x37], data[0x21], data[0x10]), (0x5C, 70, 0x60, 1))
        finally:
            os.unlink(f.name)


class FakeFan:
    name = "fake"

    def __init__(self):
        self.calls = []
        self.coolboost = None

    def capabilities(self):
        return {"fan_modes": True, "fan_custom": True, "coolboost": True, "fan_rpm": True}

    def set_fan(self, fan, mode, percent=None):
        self.calls.append(("mode", fan, mode, percent))

    def set_fan_speed(self, fan, percent):
        self.calls.append(("speed", fan, percent))

    def get_fans(self):
        return {}

    def set_coolboost(self, on):
        self.coolboost = on

    def read_rpm(self):
        return {"cpu": 3000, "gpu": 2800}


class FakeHardware(daemon.Hardware):
    def detect(self):
        self.fan = FakeFan()
        self.temp = {"cpu": 55.0, "gpu": None}

    def temps(self):
        return dict(self.temp), {"state": "suspended"}


class FakeNvml:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def set_offsets(self, core, mem):
        FakeNvml.calls.append((core, mem))

    def read(self):
        return {"core": 0, "mem": 0}


class CpuFreqTest(unittest.TestCase):
    def setUp(self):
        from predatorsense.hw import cpufreq
        self.cf, self.root = cpufreq, tempfile.mkdtemp()
        os.makedirs(self.root + "/intel_pstate")
        for p in ("policy0", "policy6"):
            os.makedirs(f"{self.root}/cpufreq/{p}")
            for name, value in (("cpuinfo_min_freq", 800000), ("base_frequency", 2200000), ("scaling_max_freq", 2200000)):
                with open(f"{self.root}/cpufreq/{p}/{name}", "w") as f:
                    f.write(str(value))
        for name, value in (("no_turbo", 1), ("num_pstates", 34)):
            with open(f"{self.root}/intel_pstate/{name}", "w") as f:
                f.write(str(value))
        patcher = mock.patch.object(cpufreq, "CPU_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, rel):
        with open(f"{self.root}/{rel}") as f:
            return f.read()

    def test_limits_ignore_turbo_state(self):
        self.assertEqual(self.cf.status(), {"min_mhz": 800, "max_mhz": 4100, "base_mhz": 2200, "turbo": False})

    def test_set_turbo_inverts_no_turbo(self):
        self.cf.set_turbo(True)
        self.assertEqual(self.read("intel_pstate/no_turbo"), "0")
        self.assertTrue(self.cf.turbo_enabled())

    def test_limit_is_clamped_and_applies_to_all_policies(self):
        self.cf.set_max_mhz(3000)
        self.assertEqual(self.read("cpufreq/policy6/scaling_max_freq"), "3000000")
        self.cf.set_max_mhz(100)
        self.assertEqual(self.read("cpufreq/policy0/scaling_max_freq"), "800000")
        self.cf.set_max_mhz(None)
        self.assertEqual(self.read("cpufreq/policy0/scaling_max_freq"), "4100000")

    def test_profile_defaults_leave_system_untouched(self):
        self.assertEqual(cfgmod.validate_profile({})["cpu"], {"turbo": None, "max_mhz": None, "undervolt_mv": 0})


class UndervoltTest(unittest.TestCase):
    def test_encoding_matches_known_values(self):
        from predatorsense.hw import undervolt as uv
        self.assertEqual(uv.write_request(0, -125), 0x80000011F0000000)
        self.assertEqual(uv.write_request(2, -125), 0x80000211F0000000)
        self.assertEqual(uv.read_request(2), 0x8000021000000000)
        self.assertEqual(uv.decode_offset(uv.encode_offset(-125)), -125)
        self.assertEqual(uv.decode_offset(0), 0)

    def test_clamp(self):
        from predatorsense.hw import undervolt as uv
        self.assertEqual(uv.clamp(-200, unlocked=False), -125)
        self.assertEqual(uv.clamp(-200, unlocked=True), -200)
        self.assertEqual(uv.clamp(-999, unlocked=True), -250)
        self.assertEqual(uv.clamp(50, unlocked=True), 0)


class WinKeySettingTest(unittest.TestCase):
    def test_keymap_entry(self):
        from predatorsense.hw import keyboard
        e = keyboard.keymap_entry(0xA2, 164)
        self.assertEqual(len(e), 40)
        self.assertEqual(e[:12], bytes([0, 4, 0, 0, 164, 0, 0, 0, 0xA2, 0, 0, 0]))

    def test_migrates_from_active_profile(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w") as f:
                json.dump({"active_profile": "A", "profiles": {"A": {"win_key": False, "lcd_overdrive": True}}}, f)
            c = cfgmod.Config(path)
            self.assertFalse(c.settings["win_key"])
            self.assertNotIn("win_key", c.active)
            self.assertNotIn("lcd_overdrive", c.active)


class UsbChargeTest(unittest.TestCase):
    def test_encoding_matches_quick_access(self):
        self.assertEqual(wmi.usb_charge_arg(True, 30), 4 | (15 << 8) | (30 << 16))
        self.assertEqual(wmi.usb_charge_arg(False, 10), 4 | (31 << 8) | (10 << 16))
        with self.assertRaises(ValueError):
            wmi.usb_charge_arg(True, 50)

    def test_parse(self):
        self.assertEqual(wmi.parse_usb_charge((20 << 16) | (15 << 8)), {"enabled": True, "threshold": 20})
        self.assertEqual(wmi.parse_usb_charge((10 << 16) | (31 << 8)), {"enabled": False, "threshold": 10})
        with self.assertRaises(wmi.HardwareError):
            wmi.parse_usb_charge(1)

    def test_call_uses_apge_method(self):
        self.assertEqual(wmi.build_call(wmi.APGE_METHOD_PATH, wmi.APGE_GET_FUNCTION, wmi.encode_u32(4)),
                         r"\_SB.PCI0.WMID.WMAA 0 2 b04000000")


class GpuOverclockTest(unittest.TestCase):
    def setUp(self):
        FakeNvml.calls = []
        self.oc = daemon.GpuOverclock(session=FakeNvml, available=True)

    def test_levels_ac_battery_and_suspend(self):
        prof = {"level": "turbo", "core": 0, "mem": 0}
        self.oc.update("suspended", True, prof)
        self.assertEqual(FakeNvml.calls, [])               # nunca despierta la GPU
        self.oc.update("active", True, prof)
        self.oc.update("active", True, prof)               # sin cambios: no repite
        self.assertEqual(FakeNvml.calls, [(50, 200)])
        self.oc.update("active", False, prof)              # en batería -> 0
        self.assertEqual(FakeNvml.calls[-1], (0, 0))
        self.oc.update("suspended", True, prof)
        self.oc.update("active", True, prof)               # al despertar se reaplica
        self.assertEqual(FakeNvml.calls[-1], (50, 200))
        self.oc.reset("active")
        self.assertEqual(FakeNvml.calls[-1], (0, 0))

    def test_custom_is_clamped(self):
        from predatorsense.hw import nvgpu
        self.assertEqual(nvgpu.offsets_for("custom", 9999, -9999), (250, -500))
        with self.assertRaises(ValueError):
            nvgpu.offsets_for("ultra")
        c = cfgmod.Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        p = c.update_active({"gpu_oc": {"level": "custom", "core": 900, "mem": 100}})
        self.assertEqual(p["gpu_oc"], {"level": "custom", "core": 250, "mem": 100})


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        self.sock = os.path.join(self.dir, "sock")
        with mock.patch.object(daemon, "Hardware", FakeHardware):
            self.d = daemon.Daemon(self.cfg, self.sock)
        self.d.gpu_oc = daemon.GpuOverclock(session=FakeNvml, available=True)

    def test_commands_persist_and_apply(self):
        self.d.handle({"cmd": "set_fan", "fan": "cpu", "mode": "custom", "percent": 40})
        self.assertIn(("mode", "cpu", "custom", 40), self.d.hw.fan.calls)
        self.d.handle({"cmd": "set_coolboost", "on": True})
        self.assertTrue(self.d.hw.fan.coolboost)
        with open(self.cfg) as f:
            saved = json.load(f)
        self.assertEqual(saved["profiles"]["Predeterminado"]["fans"]["cpu"]["percent"], 40)
        self.assertTrue(saved["profiles"]["Predeterminado"]["coolboost"])
        with self.assertRaises(HardwareError):
            self.d.handle({"cmd": "set_win_key", "enabled": False})

    def test_undervolt_is_capped_and_guarded(self):
        with mock.patch.object(daemon.undervolt, "available", return_value=True), \
                mock.patch.object(daemon.undervolt, "apply") as apply:
            self.d.handle({"cmd": "set_undervolt", "mv": -200})
            apply.assert_called_once_with(-125)
            self.assertTrue(os.path.exists(self.d.uv_guard))
            self.d.handle({"cmd": "set_undervolt_unlocked", "enabled": True})
            self.d.handle({"cmd": "set_undervolt", "mv": -200})
            self.assertEqual(apply.call_args.args, (-200,))
            self.d.handle({"cmd": "set_undervolt_unlocked", "enabled": False})
            self.assertEqual(apply.call_args.args, (-125,))
            # Un cuelgue dejó la marca: al arrancar no se reaplica
            d2 = daemon.Daemon(self.d.config.path, self.d.socket_path)
            self.assertTrue(d2.uv_skipped)
            apply.reset_mock()
            d2.apply_undervolt()
            apply.assert_not_called()
            self.assertFalse(os.path.exists(d2.uv_guard))

    def test_curve_runs_and_falls_back_to_auto(self):
        self.d.handle({"cmd": "set_curve", "fan": "gpu", "points": [[40, 20], [80, 100]], "enabled": True})
        self.d.sample()
        # GPU suspendida -> usa la temperatura de CPU (55° -> 50%)
        self.assertIn(("speed", "gpu", 50), self.d.hw.fan.calls)
        self.d.hw.temp["cpu"] = None
        for _ in range(5):
            self.d.sample()
        self.assertEqual(self.d.hw.fan.calls[-1], ("mode", "gpu", "auto", None))

    def test_profile_switch_reapplies(self):
        self.d.handle({"cmd": "set_fan", "fan": "cpu", "mode": "max"})
        self.d.handle({"cmd": "profile_save_as", "name": "Silencio"})
        self.d.handle({"cmd": "set_fan", "fan": "cpu", "mode": "auto"})
        self.d.hw.fan.calls.clear()
        self.d.handle({"cmd": "profile_select", "name": "Predeterminado"})
        self.assertIn(("mode", "cpu", "max", 50), self.d.hw.fan.calls)

    def test_socket_and_restore_on_stop(self):
        t = threading.Thread(target=self.d.run, daemon=True)
        with mock.patch("signal.signal"):
            t.start()
            for _ in range(50):     # el socket existe un instante antes de aceptar conexiones
                try:
                    st = request("status", socket_path=self.sock)
                    break
                except ServiceError:
                    time.sleep(0.05)
            self.assertEqual(st["capabilities"]["fan_backend"], "fake")
            with self.assertRaises(ServiceError):
                request("nope", socket_path=self.sock)
            self.d.hw.fan.calls.clear()
            self.d.running = False
            t.join(3)
        self.assertEqual(self.d.hw.fan.calls[-2:], [("mode", "cpu", "auto", None), ("mode", "gpu", "auto", None)])
        self.assertFalse(os.path.exists(self.sock))


if __name__ == "__main__":
    unittest.main()

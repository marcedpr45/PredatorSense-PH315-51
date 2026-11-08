"""Servicio simulado para desarrollar la interfaz sin root ni hardware.

    python3 tools/dev_fake_daemon.py /tmp/ps.sock &
    PREDATORSENSE_SOCKET=/tmp/ps.sock python3 -m predatorsense.gui.app
"""
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from predatorsense import daemon  # noqa: E402


class FakeFan:
    name = "simulado"
    def __init__(self): self.state = {"cpu": ["auto", 50], "gpu": ["auto", 50]}
    def capabilities(self): return {"fan_modes": True, "fan_custom": True, "coolboost": True, "fan_rpm": True}
    def set_fan(self, fan, mode, percent=None): self.state[fan] = [mode, percent or self.state[fan][1]]
    def set_fan_speed(self, fan, percent): self.state[fan][1] = percent
    def get_fans(self): return {}
    def set_coolboost(self, on): pass
    def read_rpm(self):
        def rpm(f):
            m, p = self.state[f]
            return 5400 if m == "max" else int(p * 50) if m == "custom" else 2600
        return {"cpu": rpm("cpu"), "gpu": rpm("gpu")}


class FakeWmi:
    def capabilities(self): return {"win_key": True, "lcd_overdrive": True, "fan_rpm": True}
    def set_win_key(self, e): pass
    def set_lcd_overdrive(self, e): pass
    usb = {"enabled": True, "threshold": 30}
    def get_usb_charge(self): return dict(self.usb)
    def set_usb_charge(self, e, t):
        self.usb = {"enabled": e, "threshold": t}
        return dict(self.usb)
    def read_rpm(self): return {}


class FakeHardware(daemon.Hardware):
    def detect(self):
        self.fan, self.wmi, self.ec, self.hwmon = FakeFan(), FakeWmi(), None, None
    def temps(self):
        t = time.time()
        return ({"cpu": 55 + 12 * math.sin(t / 20), "gpu": 48 + 8 * math.sin(t / 30)},
                {"state": "active", "temp": 48 + 8 * math.sin(t / 30), "usage": 30 + 25 * math.sin(t / 9), "clock": 1400})


if __name__ == "__main__":
    sock = sys.argv[1] if len(sys.argv) > 1 else "/tmp/predatorsense-dev.sock"
    daemon.Hardware = FakeHardware
    d = daemon.Daemon(os.path.join(tempfile.mkdtemp(), "config.json"), sock)
    d.config.settings["curve_interval"] = 1

    class FakeNvml:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def set_offsets(self, core, mem): pass
        def read(self): return {"core": 0, "mem": 0, "core_range": (-200, 1200), "mem_range": (-2000, 2000),
                                "max_core_clock": 1911, "max_mem_clock": 4004}
    d.gpu_oc = daemon.GpuOverclock(session=FakeNvml, available=True)
    print("Servicio simulado en", sock, flush=True)
    d.run()

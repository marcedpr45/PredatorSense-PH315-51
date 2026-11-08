"""Backend hwmon del driver acer-wmi del kernel.

Con `acer_wmi predator_v4=1` el kernel registra un hwmon llamado "acer" con
fanN_input (RPM), tempN_input y, en kernels recientes, pwmN/pwmN_enable para
controlar los ventiladores con las mismas llamadas WMI que usa Windows.
Todo se detecta en tiempo de ejecución: si un archivo no existe, la función no
se ofrece.
"""

import glob
import os

from . import HardwareError, clamp_percent, read_text, write_text

PWM_MANUAL = 1
PWM_AUTO = 2
# Canal de cada ventilador en el hwmon de acer-wmi (1 = CPU, 2 = GPU)
CHANNEL = {"cpu": 1, "gpu": 2}


def find_hwmon(name="acer", root="/sys/class/hwmon"):
    for path in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        if read_text(os.path.join(path, "name")) == name:
            return path
    return None


class HwmonBackend:
    name = "hwmon"

    def __init__(self, path=None):
        self.path = path or find_hwmon()
        if not self.path:
            raise HardwareError("No hay hwmon de acer-wmi")

    @staticmethod
    def available():
        return find_hwmon() is not None

    def _attr(self, attr):
        return os.path.join(self.path, attr)

    def _has(self, attr):
        return os.path.exists(self._attr(attr))

    def capabilities(self):
        pwm = all(self._has(f"pwm{c}") and self._has(f"pwm{c}_enable") for c in CHANNEL.values())
        return {
            "fan_modes": pwm,
            "fan_custom": pwm,
            "coolboost": False,
            "fan_rpm": any(self._has(f"fan{c}_input") for c in CHANNEL.values()),
            "temps": any(self._has(f"temp{i}_input") for i in range(1, 4)),
        }

    def set_fan(self, fan, mode, percent=None):
        ch = CHANNEL[fan]
        if mode == "auto":
            write_text(self._attr(f"pwm{ch}_enable"), PWM_AUTO)
            return
        write_text(self._attr(f"pwm{ch}_enable"), PWM_MANUAL)
        self.set_fan_speed(fan, 100 if mode == "max" else (percent if percent is not None else 50))

    def set_fan_speed(self, fan, percent):
        pwm = round(clamp_percent(percent) * 255 / 100)
        write_text(self._attr(f"pwm{CHANNEL[fan]}"), pwm)

    def get_fans(self):
        out = {}
        for fan, ch in CHANNEL.items():
            en = read_text(self._attr(f"pwm{ch}_enable"))
            pwm = read_text(self._attr(f"pwm{ch}"))
            if en is None:
                continue
            percent = round(int(pwm) * 100 / 255) if pwm and pwm.isdigit() else None
            mode = "auto" if en == str(PWM_AUTO) else ("max" if percent == 100 else "custom")
            out[fan] = {"mode": mode, "percent": percent}
        return out

    def set_coolboost(self, on):
        raise HardwareError("CoolBoost no disponible vía hwmon")

    def get_coolboost(self):
        return None

    def read_rpm(self):
        out = {}
        for fan, ch in CHANNEL.items():
            v = read_text(self._attr(f"fan{ch}_input"))
            if v and v.lstrip("-").isdigit():
                out[fan] = int(v)
        return out

    def read_temps(self):
        """Devuelve {"cpu": °C, "gpu": °C} según las etiquetas del hwmon."""
        out = {}
        for i in range(1, 6):
            v = read_text(self._attr(f"temp{i}_input"))
            if not v or not v.lstrip("-").isdigit():
                continue
            label = (read_text(self._attr(f"temp{i}_label")) or "").lower()
            key = "gpu" if "gpu" in label else "cpu" if ("cpu" in label or i == 1) else f"temp{i}"
            out.setdefault(key, int(v) / 1000)
        return out

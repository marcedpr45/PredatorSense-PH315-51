"""Sensores genéricos de Linux: coretemp, /proc/stat, NVIDIA, batería y platform_profile."""

import glob
import os
import shutil
import subprocess

from . import read_text, write_text
from .hwmon import find_hwmon

PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"


def cpu_temp():
    path = find_hwmon("coretemp")
    if not path:
        return None
    for label_file in sorted(glob.glob(os.path.join(path, "temp*_label"))):
        if read_text(label_file, "").startswith("Package"):
            v = read_text(label_file.replace("_label", "_input"))
            return int(v) / 1000 if v else None
    v = read_text(os.path.join(path, "temp1_input"))
    return int(v) / 1000 if v else None


class CpuUsage:
    def __init__(self):
        self._last = None

    def sample(self):
        try:
            with open("/proc/stat") as f:
                parts = [int(x) for x in f.readline().split()[1:]]
        except OSError:
            return None
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        last, self._last = self._last, (idle, total)
        if not last or total == last[1]:
            return None
        return round(100 * (1 - (idle - last[0]) / (total - last[1])), 1)


def nvidia_pci_device():
    for dev in glob.glob("/sys/bus/pci/devices/*"):
        if read_text(os.path.join(dev, "vendor")) == "0x10de" and \
                (read_text(os.path.join(dev, "class")) or "").startswith("0x03"):
            return dev
    return None


def gpu_info(dev=None):
    """Temperatura y uso de la NVIDIA, sin despertarla si está suspendida."""
    dev = dev or nvidia_pci_device()
    if not dev:
        return {"state": "absent"}
    status = read_text(os.path.join(dev, "power", "runtime_status"), "active")
    if status != "active":
        return {"state": "suspended"}
    if not shutil.which("nvidia-smi"):
        return {"state": "active"}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,clocks.gr,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        temp, util, clock, power = (x.strip() for x in out.split(",")[:4])
        def num(x):
            try:
                return float(x)
            except ValueError:
                return None
        return {"state": "active", "temp": num(temp), "usage": num(util),
                "clock": num(clock), "power": num(power)}
    except (subprocess.SubprocessError, ValueError, OSError):
        return {"state": "active"}


def on_ac():
    for supply in glob.glob("/sys/class/power_supply/*"):
        if read_text(os.path.join(supply, "type")) == "Mains":
            return read_text(os.path.join(supply, "online")) == "1"
    return None


def battery_percent():
    for supply in glob.glob("/sys/class/power_supply/BAT*"):
        v = read_text(os.path.join(supply, "capacity"))
        if v and v.isdigit():
            return int(v)
    return None


def platform_profile():
    choices = read_text(PLATFORM_PROFILE_CHOICES)
    current = read_text(PLATFORM_PROFILE)
    # En el PH315-51, acer-wmi publica opciones pero leer el perfil da EIO: usa los métodos
    # WMI 22/23 y esta BIOS solo tiene del 1 al 19. Si no se puede leer, no está soportado.
    if current is None:
        return {"current": None, "choices": []}
    return {"current": current, "choices": choices.split() if choices else []}


def set_platform_profile(name):
    if name not in platform_profile()["choices"]:
        raise ValueError(f"Perfil no soportado: {name}")
    write_text(PLATFORM_PROFILE, name)

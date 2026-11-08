"""Acceso al hardware: EC, hwmon del kernel (acer-wmi), WMI vía acpi_call y sensores."""

FANS = ("cpu", "gpu")
MODES = ("auto", "max", "custom")


class HardwareError(Exception):
    pass


def read_text(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def write_text(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def clamp_percent(value):
    return max(0, min(100, int(round(value))))

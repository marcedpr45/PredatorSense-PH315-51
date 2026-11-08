"""Backend EC (módulo ec_sys) con las direcciones probadas en el PH315-51.

Las direcciones vienen de la versión Linux anterior (mohsunb/PredatorSense), que
funcionaba en este modelo. Son específicas del modelo: en otro equipo podrían
escribir en registros que controlan otra cosa, por eso este backend solo se
activa si el modelo coincide (o si se fuerza en la configuración).
"""

import os
import subprocess

from . import HardwareError, clamp_percent, read_text

EC_IO = "/sys/kernel/debug/ec/ec0/io"
SUPPORTED_MODELS = ("PH315-51",)

# Direcciones y valores del PH315-51
REG = {
    "cpu": {"mode": 0x22, "speed": 0x37, "auto": 0x54, "max": 0x58, "custom": 0x5C,
            "known": {0x00: "auto", 0x54: "auto", 0x58: "max", 0x5C: "custom", 0x5D: "custom"}},
    "gpu": {"mode": 0x21, "speed": 0x3A, "auto": 0x50, "max": 0x60, "custom": 0x70,
            "known": {0x00: "auto", 0x50: "auto", 0x60: "max", 0x70: "custom"}},
}
COOLBOOST = 0x10


def model_supported():
    product = read_text("/sys/class/dmi/id/product_name", "")
    return any(m in product for m in SUPPORTED_MODELS)


class EcBackend:
    name = "ec"

    def __init__(self, path=EC_IO):
        self.path = path

    # --- bajo nivel -----------------------------------------------------
    @staticmethod
    def ensure_module():
        if not os.path.exists(EC_IO):
            subprocess.run(["modprobe", "ec_sys", "write_support=1"], check=False)
        return os.path.exists(EC_IO)

    def read(self, addr):
        with open(self.path, "rb") as f:
            f.seek(addr)
            data = f.read(1)
        if not data:
            raise HardwareError(f"EC: no se pudo leer 0x{addr:02X}")
        return data[0]

    def write(self, addr, value):
        if not 0 <= value <= 0xFF:
            raise HardwareError(f"EC: valor fuera de rango {value}")
        with open(self.path, "rb+") as f:
            f.seek(addr)
            if f.read(1) == bytes([value]):
                return
            f.seek(addr)
            f.write(bytes([value]))

    # --- interfaz común -------------------------------------------------
    @classmethod
    def available(cls, force=False):
        if not (force or model_supported()):
            return False
        if not cls.ensure_module():
            return False
        try:
            with open(EC_IO, "rb+"):
                return True
        except OSError:
            return False

    def capabilities(self):
        return {"fan_modes": True, "fan_custom": True, "coolboost": True, "fan_rpm": False}

    def set_fan(self, fan, mode, percent=None):
        r = REG[fan]
        if mode == "custom":
            self.write(r["speed"], clamp_percent(percent if percent is not None else 50))
        self.write(r["mode"], r[mode])

    def set_fan_speed(self, fan, percent):
        self.write(REG[fan]["speed"], clamp_percent(percent))

    def get_fans(self):
        out = {}
        for fan, r in REG.items():
            raw = self.read(r["mode"])
            out[fan] = {"mode": r["known"].get(raw, "auto"), "percent": self.read(r["speed"]),
                        "raw": raw}
        return out

    def set_coolboost(self, on):
        self.write(COOLBOOST, 1 if on else 0)

    def get_coolboost(self):
        return self.read(COOLBOOST) == 1

    def read_rpm(self):
        return {}

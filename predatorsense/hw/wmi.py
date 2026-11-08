"""Llamadas WMI "AcerGamingFunction" (GUID 7A4DDFE7-...) vía el módulo acpi_call.

Son las mismas llamadas que hace el servicio PSSvc.exe de la versión Windows.
En esta BIOS el objeto WMI es "BH", o sea el método ACPI \\_SB.PCI0.WMID.WMBH,
que recibe (instancia, id_de_método, buffer).

Los IDs salen del MOF binario de la BIOS del PH315-51 (V1.26), extraído con
tools/probe.sh y descomprimido con tools/bmof_decompress.py. El MOF declara 19
métodos (WmiMethodId 1..19); coinciden con los del driver acer-wmi del kernel.
Un método en None no se ofrece en la interfaz.
"""

import re

from . import HardwareError, clamp_percent

ACPI_CALL = "/proc/acpi/call"
DEFAULT_METHOD_PATH = r"\_SB.PCI0.WMID.WMBH"

# Interfaz APGeAction (GUID 61EF69EA-..., objeto "AA"): la usa Acer Quick Access.
APGE_METHOD_PATH = r"\_SB.PCI0.WMID.WMAA"
APGE_SET_FUNCTION = 1           # uiInput uint64 -> uiOutput uint32
APGE_GET_FUNCTION = 2           # uiInput uint32 -> uiOutput uint64
APGE_USB_CHARGE = 4
USB_CHARGE_ON, USB_CHARGE_OFF = 15, 31
USB_CHARGE_THRESHOLDS = (10, 20, 30)

# WmiMethodId según el MOF de la BIOS (AcerGamingFunction)
DEFAULT_METHOD_IDS = {
    "SetGamingProfile": 1,          # tecla Windows, LCD overdrive
    "GetGamingProfile": 3,
    "GetGamingSysInfo": 5,
    "SetGamingLEDColor": 12,        # zonas RGB (el PH315-51 no tiene teclado RGB: no se usa)
    "GetGamingLEDColor": 13,
    "SetGamingFanBehavior": 14,
    "SetGamingFanSpeed": 16,
    # CoolBoost: Windows usa otra llamada (probablemente SetGamingProfileSetting = 8)
    # que no está verificada; se sigue usando el EC, que sí está probado.
    "SetGamingMiscSetting": None,
}

# Índices de GetGamingSysInfo (System_Health_Information_Index en Windows)
SENSOR_CPU_TEMP = 1
SENSOR_CPU_FAN = 2
SENSOR_GPU_FAN = 6
SENSOR_GPU_TEMP = 10

FAN_BITMAP = {"cpu": 0x1, "gpu": 0x8}
FAN_SPEED_ID = {"cpu": 0x1, "gpu": 0x4}
FAN_MODE_SHIFT = {"cpu": 16, "gpu": 22}
FAN_MODE_VALUE = {"auto": 1, "max": 2, "custom": 3}


# --- codificación (pura, testeable) --------------------------------------
def encode_u64(value):
    return int(value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


def encode_u32(value):
    return int(value & 0xFFFFFFFF).to_bytes(4, "little")


def build_call(method_path, method_id, payload):
    return f"{method_path} 0 {method_id} b{payload.hex()}"


def parse_result(text):
    """Convierte la respuesta de acpi_call ("{0x00, 0x01}" o "0x1") en un entero LE."""
    text = text.strip().rstrip("\x00")
    if text.startswith("Error"):
        raise HardwareError(f"acpi_call: {text}")
    if text.startswith("{"):
        nums = re.findall(r"0x([0-9a-fA-F]+)", text)
        return int.from_bytes(bytes(int(n, 16) & 0xFF for n in nums), "little")
    if text.startswith("0x"):
        return int(text, 16)
    raise HardwareError(f"acpi_call: respuesta inesperada {text!r}")


def fan_behavior_arg(fans_modes):
    """fans_modes: {"cpu": "auto", "gpu": "max"} -> argumento de SetGamingFanBehavior."""
    arg = 0
    for fan, mode in fans_modes.items():
        arg |= FAN_BITMAP[fan] | (FAN_MODE_VALUE[mode] << FAN_MODE_SHIFT[fan])
    return arg


def fan_speed_arg(fan, percent):
    return FAN_SPEED_ID[fan] | (clamp_percent(percent) << 8)


def win_key_arg(enabled):
    return ((1 if enabled else 0) << 24) | 2


def lcd_overdrive_arg(enabled):
    return 16 | ((1 << 48) if enabled else 0)


def sysinfo_arg(sensor):
    return 1 | (sensor << 8)


def usb_charge_arg(enabled, threshold):
    """Argumento de SetFunction, igual que el plugin UsbCharge de Quick Access."""
    if threshold not in USB_CHARGE_THRESHOLDS:
        raise ValueError(f"Umbral de batería inválido: {threshold}")
    return APGE_USB_CHARGE | ((USB_CHARGE_ON if enabled else USB_CHARGE_OFF) << 8) | (threshold << 16)


def parse_usb_charge(result):
    if result & 0xFF:
        raise HardwareError(f"GetFunction devolvió error 0x{result & 0xFF:02X}")
    threshold = (result >> 16) & 0xFF
    return {"enabled": (result >> 8) & 0xFF == USB_CHARGE_ON,
            "threshold": threshold if threshold in USB_CHARGE_THRESHOLDS else 30}


# --- backend ------------------------------------------------------------
class WmiBackend:
    name = "wmi"

    def __init__(self, method_ids=None, method_path=DEFAULT_METHOD_PATH, call_path=ACPI_CALL):
        self.ids = dict(DEFAULT_METHOD_IDS)
        self.ids.update({k: v for k, v in (method_ids or {}).items() if k in self.ids})
        self.method_path = method_path
        self.call_path = call_path

    @staticmethod
    def available(call_path=ACPI_CALL):
        import os
        return os.path.exists(call_path)

    def has(self, method):
        return self.ids.get(method) is not None

    def call(self, method, payload):
        mid = self.ids.get(method)
        if mid is None:
            raise HardwareError(f"Método WMI {method} sin ID confirmado")
        with open(self.call_path, "w") as f:
            f.write(build_call(self.method_path, mid, payload))
        with open(self.call_path) as f:
            return parse_result(f.read())

    def set_u64(self, method, value):
        status = self.call(method, encode_u64(value))
        if status & 0xFF:
            raise HardwareError(f"{method} devolvió error 0x{status & 0xFF:02X}")
        return status

    def get_u32(self, method, value):
        result = self.call(method, encode_u32(value))
        if result & 0xFF:
            raise HardwareError(f"{method} devolvió error 0x{result & 0xFF:02X}")
        return result

    # --- ventiladores ---------------------------------------------------
    def capabilities(self):
        return {
            "fan_modes": self.has("SetGamingFanBehavior"),
            "fan_custom": self.has("SetGamingFanSpeed"),
            "coolboost": self.has("SetGamingMiscSetting"),
            "fan_rpm": self.has("GetGamingSysInfo"),
            "win_key": self.has("SetGamingProfile"),
            "lcd_overdrive": self.has("SetGamingProfile"),
        }

    def set_fan(self, fan, mode, percent=None):
        self.set_u64("SetGamingFanBehavior", fan_behavior_arg({fan: mode}))
        if mode == "custom":
            self.set_fan_speed(fan, percent if percent is not None else 50)

    def set_fan_speed(self, fan, percent):
        self.set_u64("SetGamingFanSpeed", fan_speed_arg(fan, percent))

    def get_fans(self):
        return {}  # Esta BIOS no expone lectura del modo; se usa el estado guardado.

    def set_coolboost(self, on):
        self.set_u64("SetGamingMiscSetting", 7 | ((1 if on else 0) << 16))

    def get_coolboost(self):
        return None

    def sensor(self, sensor):
        result = self.get_u32("GetGamingSysInfo", sysinfo_arg(sensor))
        return (result >> 8) & 0xFFFF

    def read_rpm(self):
        if not self.has("GetGamingSysInfo"):
            return {}
        out = {}
        for fan, sid in (("cpu", SENSOR_CPU_FAN), ("gpu", SENSOR_GPU_FAN)):
            try:
                out[fan] = self.sensor(sid)
            except (HardwareError, OSError):
                pass
        return out

    # --- carga USB con el equipo apagado ---------------------------------
    def _apge(self, method_id, payload):
        with open(self.call_path, "w") as f:
            f.write(build_call(APGE_METHOD_PATH, method_id, payload))
        with open(self.call_path) as f:
            return parse_result(f.read())

    def get_usb_charge(self):
        return parse_usb_charge(self._apge(APGE_GET_FUNCTION, encode_u32(APGE_USB_CHARGE)))

    def set_usb_charge(self, enabled, threshold):
        status = self._apge(APGE_SET_FUNCTION, encode_u64(usb_charge_arg(enabled, threshold)))
        if status & 0xFF:
            raise HardwareError(f"SetFunction devolvió error 0x{status & 0xFF:02X}")
        return self.get_usb_charge()

    # --- teclado / pantalla ---------------------------------------------
    def set_win_key(self, enabled):
        self.set_u64("SetGamingProfile", win_key_arg(enabled))

    def set_lcd_overdrive(self, enabled):
        self.set_u64("SetGamingProfile", lcd_overdrive_arg(enabled))

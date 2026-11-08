"""Undervolt del i7-8750H mediante el MSR 0x150, el mismo que usa ThrottleStop.

Núcleo y caché se aplican juntos porque en esta CPU comparten voltaje. La BIOS puede
bloquear el MSR: por eso cada escritura se comprueba leyendo el valor de vuelta.

Topes en mV. -125 es el punto de partida habitual entre usuarios del 8750H, y muchos
pierden estabilidad en la caché por debajo de ese valor. Se han reportado cuelgues
desde -165 en caché, y casi ningún chip es estable a -180.
"""

import os
import struct

from . import HardwareError, read_text

MSR_PATH = "/dev/cpu/0/msr"
MSR_VOLTAGE = 0x150
PLANE_CORE, PLANE_CACHE = 0, 2
SAFE_MIN_MV = -125
UNLOCKED_MIN_MV = -250
SUPPORTED_CPU = "i7-8750H"


def encode_offset(mv):
    return (int(round(mv * 1.024)) << 21) & 0xFFE00000


def write_request(plane, mv):
    return 0x8000001100000000 | (plane << 40) | encode_offset(mv)


def read_request(plane):
    return 0x8000001000000000 | (plane << 40)


def decode_offset(value):
    raw = (value >> 21) & 0x7FF
    return round((raw - 2048 if raw >= 1024 else raw) / 1.024)


def clamp(mv, unlocked):
    return max(UNLOCKED_MIN_MV if unlocked else SAFE_MIN_MV, min(0, int(mv)))


def available():
    if not os.path.exists(MSR_PATH):
        return False
    return SUPPORTED_CPU in (read_text("/proc/cpuinfo") or "")


def _rw(fd, value):
    os.pwrite(fd, struct.pack("<Q", value), MSR_VOLTAGE)
    return struct.unpack("<Q", os.pread(fd, 8, MSR_VOLTAGE))[0]


def apply(mv):
    try:
        fd = os.open(MSR_PATH, os.O_RDWR)
    except OSError as e:
        raise HardwareError(f"No se pudo abrir el MSR: {e}") from e
    try:
        for plane in (PLANE_CORE, PLANE_CACHE):
            _rw(fd, write_request(plane, mv))
            got = decode_offset(_rw(fd, read_request(plane)))
            if abs(got - mv) > 1:
                raise HardwareError(f"La BIOS no aceptó el undervolt (pedido {mv} mV, leído {got} mV)")
    except OSError as e:
        raise HardwareError(f"La BIOS bloquea el undervolt: {e}") from e
    finally:
        os.close(fd)

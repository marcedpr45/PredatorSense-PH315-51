"""Turbo Boost y límite de frecuencia de la CPU mediante intel_pstate.

El i7-8750H tiene el multiplicador bloqueado y el PredatorSense oficial no define
overclock de CPU para este equipo, así que solo se puede bajar la frecuencia máxima.
"""

import os

from . import HardwareError, read_text, write_text

CPU_ROOT = "/sys/devices/system/cpu"


def _pstate(name):
    return read_text(f"{CPU_ROOT}/intel_pstate/{name}")


def available():
    return _pstate("no_turbo") is not None


def turbo_enabled():
    return _pstate("no_turbo") == "0"


def set_turbo(enabled):
    try:
        write_text(f"{CPU_ROOT}/intel_pstate/no_turbo", 0 if enabled else 1)
    except OSError as e:
        raise HardwareError(f"No se pudo cambiar Turbo Boost: {e}") from e


def _policies():
    base = f"{CPU_ROOT}/cpufreq"
    return [f"{base}/{p}" for p in sorted(os.listdir(base)) if p.startswith("policy")]


def limits():
    """Rango en MHz. cpuinfo_max_freq baja al desactivar el turbo: se calcula con num_pstates."""
    first = _policies()[0]
    low = int(read_text(f"{first}/cpuinfo_min_freq")) // 1000
    return {"min_mhz": low,
            "max_mhz": low + (int(_pstate("num_pstates")) - 1) * 100,
            "base_mhz": int(read_text(f"{first}/base_frequency")) // 1000}


def set_max_mhz(mhz):
    """mhz=None quita el límite."""
    lim = limits()
    target = lim["max_mhz"] if mhz is None else max(lim["min_mhz"], min(lim["max_mhz"], int(mhz)))
    try:
        for policy in _policies():
            write_text(f"{policy}/scaling_max_freq", target * 1000)
    except OSError as e:
        raise HardwareError(f"No se pudo limitar la frecuencia: {e}") from e


def status():
    return {**limits(), "turbo": turbo_enabled()}

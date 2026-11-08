"""Configuración persistente y perfiles.

Arreglo del bug de la versión Windows: allí los valores se guardaban en el
registro, pero nadie los volvía a aplicar al arrancar ni al volver de la
suspensión, y la curva solo funcionaba con la ventana abierta. Aquí el
servicio guarda cada cambio de forma atómica (archivo temporal + fsync +
rename) y lo reaplica al iniciar y después de cada suspensión.
"""

import copy
import json
import os
import tempfile

from . import CONFIG_PATH
from .hw import FANS, MODES

DEFAULT_CURVE = [[40, 20], [50, 30], [60, 45], [70, 60], [80, 80], [90, 100]]

DEFAULT_PROFILE = {
    "fans": {fan: {"mode": "auto", "percent": 50} for fan in FANS},
    "curve_enabled": {fan: False for fan in FANS},
    "curves": {fan: copy.deepcopy(DEFAULT_CURVE) for fan in FANS},
    "coolboost": False,
    "platform_profile": None,
    "gpu_oc": {"level": "normal", "core": 0, "mem": 0},
    "cpu": {"turbo": None, "max_mhz": None,   # None: no tocar lo que ya tenga el sistema
            "undervolt_mv": 0},
}

DEFAULT_CONFIG = {
    "version": 1,
    "active_profile": "Predeterminado",
    "profiles": {"Predeterminado": copy.deepcopy(DEFAULT_PROFILE)},
    "settings": {
        "apply_on_boot": True,
        "win_key": True,            # global, como WinAndMenuKey en la versión Windows
        "undervolt_unlocked": False,
        "backend": "auto",          # auto | hwmon | wmi | ec
        "force_ec": False,
        "temp_unit": "C",
        "curve_hysteresis": 3,
        "curve_interval": 2,
        "curve_interpolate": True,
        "gpu_curve_source": "gpu",  # gpu | cpu (si la GPU está suspendida se usa la CPU)
        "wmi_method_ids": {},
        "socket_group": "wheel",
    },
}

MAX_PROFILES = 32
MAX_NAME = 40


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("profiles", "wmi_method_ids"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate_curve(points):
    if not isinstance(points, list) or not 2 <= len(points) <= 12:
        raise ValueError("La curva necesita entre 2 y 12 puntos")
    clean = []
    for p in points:
        t, s = int(p[0]), int(p[1])
        if not (0 <= t <= 110 and 0 <= s <= 100):
            raise ValueError(f"Punto fuera de rango: {p}")
        clean.append([t, s])
    clean.sort()
    temps = [p[0] for p in clean]
    if len(set(temps)) != len(temps):
        raise ValueError("Dos puntos tienen la misma temperatura")
    return clean


def validate_profile(profile):
    # Las claves desconocidas (p. ej. "kb_zones" de versiones anteriores) se descartan
    p = _merge(DEFAULT_PROFILE, {k: v for k, v in (profile or {}).items() if k in DEFAULT_PROFILE})
    for fan in FANS:
        f = p["fans"][fan]
        if f["mode"] not in MODES:
            raise ValueError(f"Modo inválido para {fan}: {f['mode']}")
        f["percent"] = max(0, min(100, int(f["percent"])))
        p["curve_enabled"][fan] = bool(p["curve_enabled"][fan])
        p["curves"][fan] = validate_curve(p["curves"][fan])
    from .hw import nvgpu, undervolt
    oc = p["gpu_oc"]
    if oc["level"] not in (*nvgpu.LEVELS, "custom"):
        raise ValueError(f"Nivel de overclock inválido: {oc['level']}")
    oc["core"], oc["mem"] = nvgpu.offsets_for("custom", oc["core"], oc["mem"])
    cpu = p["cpu"]
    if cpu["turbo"] is not None:
        cpu["turbo"] = bool(cpu["turbo"])
    if cpu["max_mhz"] is not None:
        cpu["max_mhz"] = max(400, min(6000, int(cpu["max_mhz"])))
    cpu["undervolt_mv"] = undervolt.clamp(cpu["undervolt_mv"], unlocked=True)
    p["coolboost"] = bool(p["coolboost"])
    return p


def validate_name(name):
    name = str(name).strip()
    if not name or len(name) > MAX_NAME or any(c in name for c in "/\\\0"):
        raise ValueError("Nombre de perfil inválido")
    return name


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULT_CONFIG)
        except (OSError, json.JSONDecodeError):
            # Archivo corrupto: se conserva una copia y se arranca con valores por defecto.
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            return copy.deepcopy(DEFAULT_CONFIG)
        data = _merge(DEFAULT_CONFIG, raw)
        if "win_key" not in (raw.get("settings") or {}):
            # Antes era un ajuste por perfil: se conserva el valor del perfil activo.
            old = (raw.get("profiles") or {}).get(raw.get("active_profile"), {}).get("win_key", True)
            data["settings"]["win_key"] = bool(old)
        profiles = {}
        for name, prof in (raw.get("profiles") or {}).items():
            try:
                profiles[validate_name(name)] = validate_profile(prof)
            except (ValueError, TypeError, KeyError, IndexError):
                continue
        data["profiles"] = profiles or copy.deepcopy(DEFAULT_CONFIG["profiles"])
        if data["active_profile"] not in data["profiles"]:
            data["active_profile"] = next(iter(data["profiles"]))
        return data

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
            dfd = os.open(directory, os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --- accesos ----------------------------------------------------------
    @property
    def settings(self):
        return self.data["settings"]

    @property
    def active_name(self):
        return self.data["active_profile"]

    @property
    def active(self):
        return self.data["profiles"][self.active_name]

    def update_active(self, changes):
        self.data["profiles"][self.active_name] = validate_profile(_merge(self.active, changes))
        return self.active

    def update_settings(self, changes):
        allowed = set(DEFAULT_CONFIG["settings"])
        for k, v in changes.items():
            if k not in allowed:
                raise ValueError(f"Ajuste desconocido: {k}")
            self.settings[k] = v

    def save_profile_as(self, name, profile=None):
        name = validate_name(name)
        if name not in self.data["profiles"] and len(self.data["profiles"]) >= MAX_PROFILES:
            raise ValueError("Demasiados perfiles")
        self.data["profiles"][name] = validate_profile(profile if profile is not None else self.active)
        return name

    def select_profile(self, name):
        if name not in self.data["profiles"]:
            raise ValueError(f"No existe el perfil {name}")
        self.data["active_profile"] = name

    def rename_profile(self, old, new):
        new = validate_name(new)
        if old not in self.data["profiles"]:
            raise ValueError(f"No existe el perfil {old}")
        if new in self.data["profiles"]:
            raise ValueError(f"Ya existe un perfil llamado {new}")
        self.data["profiles"] = {(new if k == old else k): v for k, v in self.data["profiles"].items()}
        if self.data["active_profile"] == old:
            self.data["active_profile"] = new

    def delete_profile(self, name):
        if name not in self.data["profiles"]:
            raise ValueError(f"No existe el perfil {name}")
        if len(self.data["profiles"]) == 1:
            raise ValueError("No se puede borrar el único perfil")
        del self.data["profiles"][name]
        if self.data["active_profile"] == name:
            self.data["active_profile"] = next(iter(self.data["profiles"]))

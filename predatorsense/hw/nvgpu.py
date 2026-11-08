"""Overclock de la GPU NVIDIA mediante NVML (libnvidia-ml), vía ctypes.

La versión Windows usaba NvAPI_GPU_SetPstates20 con tres niveles (Normal,
Más rápido, Turbo). En Linux lo equivalente sin X11 (funciona en Wayland) son
los offsets de la curva V/F de NVML: nvmlDeviceSetGpcClkVfOffset (núcleo) y
nvmlDeviceSetMemClkVfOffset (memoria). Requiere root.

Nota: en el PH315-51 el PredatorSense oficial marcaba GPU_OC_Support = 0 en el
registro, así que estos niveles NO son los de Acer: son valores propios y
conservadores. Los offsets se pierden al apagar la GPU o reiniciar; el
servicio los vuelve a aplicar cuando la GPU se despierta.
"""

import ctypes

NVML_SUCCESS = 0
NVML_ERRORS = {1: "no inicializado", 2: "argumento inválido", 3: "no soportado", 4: "sin permiso",
               6: "no encontrado", 9: "driver no cargado", 12: "librería no encontrada",
               15: "GPU perdida", 999: "error desconocido"}
CLOCK_GRAPHICS, CLOCK_MEM = 0, 2

# Niveles: (núcleo MHz, memoria MHz). Valores propios, conservadores.
LEVELS = {"normal": (0, 0), "faster": (75, 200), "turbo": (150, 400)}
CUSTOM_LIMITS = {"core": (-200, 250), "mem": (-500, 800)}


class NvmlError(Exception):
    pass


class Nvml:
    """Sesión corta de NVML: se abre, se usa y se cierra para que la GPU pueda dormir."""

    def __init__(self, lib="libnvidia-ml.so.1"):
        self.lib_name = lib
        self.lib = None
        self.handle = None

    def __enter__(self):
        try:
            self.lib = ctypes.CDLL(self.lib_name)
        except OSError as e:
            raise NvmlError(f"No se pudo cargar {self.lib_name}: {e}") from e
        self._check(self.lib.nvmlInit_v2(), "nvmlInit")
        h = ctypes.c_void_p()
        self._check(self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)), "GetHandle")
        self.handle = h
        return self

    def __exit__(self, *exc):
        if self.lib:
            self.lib.nvmlShutdown()

    @staticmethod
    def _check(rc, what):
        if rc != NVML_SUCCESS:
            raise NvmlError(f"{what}: {NVML_ERRORS.get(rc, rc)}")

    def _int(self, fn):
        v = ctypes.c_int()
        self._check(getattr(self.lib, fn)(self.handle, ctypes.byref(v)), fn)
        return v.value

    def _range(self, fn):
        lo, hi = ctypes.c_int(), ctypes.c_int()
        self._check(getattr(self.lib, fn)(self.handle, ctypes.byref(lo), ctypes.byref(hi)), fn)
        return lo.value, hi.value

    def _clock(self, fn, clk):
        v = ctypes.c_uint()
        rc = getattr(self.lib, fn)(self.handle, clk, ctypes.byref(v))
        return v.value if rc == NVML_SUCCESS else None

    def read(self):
        return {
            "core": self._int("nvmlDeviceGetGpcClkVfOffset"),
            "mem": self._int("nvmlDeviceGetMemClkVfOffset"),
            "core_range": self._range("nvmlDeviceGetGpcClkMinMaxVfOffset"),
            "mem_range": self._range("nvmlDeviceGetMemClkMinMaxVfOffset"),
            "max_core_clock": self._clock("nvmlDeviceGetMaxClockInfo", CLOCK_GRAPHICS),
            "max_mem_clock": self._clock("nvmlDeviceGetMaxClockInfo", CLOCK_MEM),
        }

    def set_offsets(self, core, mem):
        self._check(self.lib.nvmlDeviceSetGpcClkVfOffset(self.handle, ctypes.c_int(int(core))),
                    "SetGpcClkVfOffset")
        self._check(self.lib.nvmlDeviceSetMemClkVfOffset(self.handle, ctypes.c_int(int(mem))),
                    "SetMemClkVfOffset")


def available():
    try:
        ctypes.CDLL("libnvidia-ml.so.1")
        return True
    except OSError:
        return False


def offsets_for(level, core=0, mem=0):
    """Offsets objetivo para un nivel; los personalizados se recortan a límites seguros."""
    if level in LEVELS:
        return LEVELS[level]
    if level != "custom":
        raise ValueError(f"Nivel de overclock inválido: {level}")
    lo, hi = CUSTOM_LIMITS["core"]
    mlo, mhi = CUSTOM_LIMITS["mem"]
    return max(lo, min(hi, int(core))), max(mlo, min(mhi, int(mem)))

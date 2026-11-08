"""Servicio root: único proceso que toca el hardware.

- Aplica el perfil activo al arrancar y al volver de la suspensión (SIGHUP).
- Ejecuta las curvas de ventilador aunque la ventana esté cerrada.
- Guarda cada cambio de inmediato en /etc/predatorsense-linux/config.json.
- Al detenerse deja los ventiladores en automático: nunca se quedan a baja
  velocidad fija sin nadie vigilando.
"""

import collections
import grp
import json
import logging
import os
import selectors
import signal
import socket
import sys
import time

from . import SOCKET_PATH, __version__
from .config import Config, validate_curve, validate_profile
from .curve import CurveController
from .hw import FANS, MODES, HardwareError
from .hw import sensors
from .hw.ec import EcBackend
from .hw import nvgpu
from .hw.hwmon import HwmonBackend
from .hw.wmi import WmiBackend

log = logging.getLogger("predatorsense")

HISTORY = 180
MAX_REQUEST = 64 * 1024


class Hardware:
    def __init__(self, settings):
        self.settings = settings
        self.fan = None
        self.wmi = None
        self.ec = None
        self.hwmon = None
        self.detect()

    def detect(self):
        s = self.settings
        if HwmonBackend.available():
            try:
                self.hwmon = HwmonBackend()
            except HardwareError:
                pass
        if WmiBackend.available():
            self.wmi = WmiBackend(s.get("wmi_method_ids"))
        if EcBackend.available(force=s.get("force_ec", False)):
            self.ec = EcBackend()

        order = {"auto": ("hwmon", "wmi", "ec")}.get(s["backend"], (s["backend"],))
        for name in order:
            b = getattr(self, name, None)
            if b and b.capabilities().get("fan_modes"):
                self.fan = b
                break
        log.info("Backend de ventiladores: %s | hwmon=%s wmi=%s ec=%s",
                 self.fan.name if self.fan else "ninguno",
                 bool(self.hwmon), bool(self.wmi), bool(self.ec))

    def capabilities(self):
        fan_caps = self.fan.capabilities() if self.fan else {}
        wmi_caps = self.wmi.capabilities() if self.wmi else {}
        pp = sensors.platform_profile()
        return {
            "fan_backend": self.fan.name if self.fan else None,
            "fan_modes": bool(fan_caps.get("fan_modes")),
            "fan_custom": bool(fan_caps.get("fan_custom")),
            "coolboost": bool(fan_caps.get("coolboost") or wmi_caps.get("coolboost") or self.ec),
            "fan_rpm": bool(self.hwmon and self.hwmon.capabilities().get("fan_rpm")) or bool(wmi_caps.get("fan_rpm")),
            "win_key": bool(wmi_caps.get("win_key")),
            "lcd_overdrive": bool(wmi_caps.get("lcd_overdrive")),
            "platform_profile": pp["choices"],
            "gpu_oc": nvgpu.available() and sensors.nvidia_pci_device() is not None,
            "acpi_call": bool(self.wmi),
            "ec": bool(self.ec),
            "hwmon": bool(self.hwmon),
        }

    def coolboost_backend(self):
        for b in (self.fan, self.wmi, self.ec):
            if b and b.capabilities().get("coolboost"):
                return b
        return None

    def rpm(self):
        for b in (self.hwmon, self.wmi, self.fan):
            if b:
                try:
                    r = b.read_rpm()
                except (HardwareError, OSError):
                    r = {}
                if r:
                    return r
        return {}

    def temps(self):
        out = {"cpu": sensors.cpu_temp()}
        gpu = sensors.gpu_info()
        out["gpu"] = gpu.get("temp")
        if out["cpu"] is None and self.hwmon:
            out["cpu"] = self.hwmon.read_temps().get("cpu")
        return out, gpu


class GpuOverclock:
    """Aplica los offsets de NVML solo con cargador y solo cuando la GPU ya está despierta
    (abrir NVML con la GPU suspendida la despertaría). Tras suspenderse, se reaplican."""

    def __init__(self, session=nvgpu.Nvml, available=None):
        self.session = session
        self.available = nvgpu.available() if available is None else available
        self.applied = None
        self.info = None
        self.error = None

    def target(self, prof_oc, on_ac):
        if on_ac is False:          # en batería: sin overclock, como en la versión Windows
            return (0, 0)
        return nvgpu.offsets_for(prof_oc["level"], prof_oc["core"], prof_oc["mem"])

    def update(self, gpu_state, on_ac, prof_oc, force=False):
        if not self.available:
            return
        if gpu_state != "active":
            self.applied = None     # al apagarse la GPU se pierden los offsets
            return
        target = self.target(prof_oc, on_ac)
        if not force and self.applied == target:
            return
        try:
            with self.session() as n:
                n.set_offsets(*target)
                self.info = n.read()
            self.applied = target
            self.error = None
            log.info("Overclock GPU aplicado: núcleo %+d MHz, memoria %+d MHz", *target)
        except nvgpu.NvmlError as e:
            self.error = str(e)
            self.applied = target   # no reintentar en cada ciclo; se reintenta al cambiar algo
            raise HardwareError(f"Overclock GPU: {e}") from e

    def reset(self, gpu_state):
        if self.available and gpu_state == "active" and self.applied not in (None, (0, 0)):
            with self.session() as n:
                n.set_offsets(0, 0)
            self.applied = (0, 0)

    def status(self, prof_oc, on_ac):
        return {"available": self.available, "applied": self.applied,
                "target": self.target(prof_oc, on_ac) if self.available else None,
                "info": self.info, "error": self.error, "levels": nvgpu.LEVELS,
                "limits": nvgpu.CUSTOM_LIMITS}


class Daemon:
    def __init__(self, config_path=None, socket_path=SOCKET_PATH):
        self.config = Config(config_path) if config_path else Config()
        self.socket_path = socket_path
        self.hw = Hardware(self.config.settings)
        self.gpu_oc = GpuOverclock()
        self.curves = {fan: self._new_controller() for fan in FANS}
        self.curve_failures = {fan: 0 for fan in FANS}
        self.cpu_usage = sensors.CpuUsage()
        self.history = collections.deque(maxlen=HISTORY)
        self.last_sample = {}
        self.errors = collections.deque(maxlen=20)
        self.running = True
        self.reapply_requested = False
        self.sel = selectors.DefaultSelector()

    def _new_controller(self):
        s = self.config.settings
        return CurveController(int(s["curve_hysteresis"]), bool(s["curve_interpolate"]))

    def _error(self, msg):
        log.warning(msg)
        self.errors.append({"time": time.time(), "message": msg})

    # --- aplicar estado -------------------------------------------------
    def apply_fan(self, fan, prof):
        if not self.hw.fan:
            return
        if prof["curve_enabled"][fan]:
            # La curva arranca desde cero para escribir la velocidad en el próximo ciclo.
            self.curves[fan] = self._new_controller()
            self.hw.fan.set_fan(fan, "custom", prof["fans"][fan]["percent"])
            return
        f = prof["fans"][fan]
        self.hw.fan.set_fan(fan, f["mode"], f["percent"])

    def apply_profile(self):
        prof = self.config.active
        caps = self.hw.capabilities()
        steps = []
        for fan in FANS:
            steps.append((f"ventilador {fan}", lambda fan=fan: self.apply_fan(fan, prof)))
        cb = self.hw.coolboost_backend()
        if cb:
            steps.append(("CoolBoost", lambda: cb.set_coolboost(prof["coolboost"])))
        if prof["platform_profile"] and prof["platform_profile"] in caps["platform_profile"]:
            steps.append(("perfil de energía", lambda: sensors.set_platform_profile(prof["platform_profile"])))
        if caps["win_key"]:
            steps.append(("tecla Windows", lambda: self.hw.wmi.set_win_key(prof["win_key"])))
        if caps["lcd_overdrive"] and prof["lcd_overdrive"] is not None:
            steps.append(("LCD overdrive", lambda: self.hw.wmi.set_lcd_overdrive(prof["lcd_overdrive"])))
        for name, fn in steps:
            try:
                fn()
            except (HardwareError, OSError, ValueError) as e:
                self._error(f"No se pudo aplicar {name}: {e}")
        log.info("Perfil '%s' aplicado", self.config.active_name)

    def restore_auto(self):
        if not self.hw.fan:
            return
        for fan in FANS:
            try:
                self.hw.fan.set_fan(fan, "auto")
            except (HardwareError, OSError) as e:
                log.warning("No se pudo volver a automático (%s): %s", fan, e)

    # --- ciclo de sensores y curvas ---------------------------------------
    def sample(self):
        temps, gpu = self.hw.temps()
        rpm = self.hw.rpm()
        sample = {
            "time": time.time(),
            "cpu_temp": temps["cpu"], "gpu_temp": temps["gpu"],
            "cpu_usage": self.cpu_usage.sample(), "gpu_usage": gpu.get("usage"),
            "gpu_state": gpu.get("state"), "gpu_clock": gpu.get("clock"), "gpu_power": gpu.get("power"),
            "cpu_rpm": rpm.get("cpu"), "gpu_rpm": rpm.get("gpu"),
            "on_ac": sensors.on_ac(), "battery": sensors.battery_percent(),
        }
        self.last_sample = sample
        self.history.append(sample)
        self.run_curves(sample)
        try:
            self.gpu_oc.update(sample["gpu_state"], sample["on_ac"], self.config.active["gpu_oc"])
        except HardwareError as e:
            self._error(str(e))

    def run_curves(self, sample):
        prof = self.config.active
        if not self.hw.fan:
            return
        for fan in FANS:
            if not prof["curve_enabled"][fan]:
                continue
            temp = sample["cpu_temp"]
            if fan == "gpu" and self.config.settings["gpu_curve_source"] == "gpu" and sample["gpu_temp"] is not None:
                temp = sample["gpu_temp"]
            if temp is None:
                self.curve_failures[fan] += 1
                if self.curve_failures[fan] == 5:
                    self._error(f"Sin lectura de temperatura para la curva {fan}: ventilador en automático")
                    try:
                        self.hw.fan.set_fan(fan, "auto")
                    except (HardwareError, OSError):
                        pass
                    self.curves[fan].reset()
                continue
            if self.curve_failures[fan] >= 5:
                self.hw.fan.set_fan(fan, "custom", prof["fans"][fan]["percent"])
            self.curve_failures[fan] = 0
            speed = self.curves[fan].target(prof["curves"][fan], temp)
            if speed is not None:
                try:
                    self.hw.fan.set_fan_speed(fan, speed)
                except (HardwareError, OSError) as e:
                    self._error(f"Curva {fan}: {e}")

    # --- comandos ---------------------------------------------------------
    def status(self):
        prof = self.config.active
        live_fans = {}
        if self.hw.fan:
            try:
                live_fans = self.hw.fan.get_fans()
            except (HardwareError, OSError):
                pass
        return {
            "version": __version__,
            "capabilities": self.hw.capabilities(),
            "active_profile": self.config.active_name,
            "profile": prof,
            "profiles": list(self.config.data["profiles"]),
            "settings": self.config.settings,
            "live_fans": live_fans,
            "curve_speed": {fan: self.curves[fan].applied for fan in FANS},
            "platform_profile": sensors.platform_profile(),
            "gpu_oc": self.gpu_oc.status(prof["gpu_oc"], (self.last_sample or {}).get("on_ac")),
            "sample": self.last_sample,
            "errors": list(self.errors),
            "model": sensors.read_text("/sys/class/dmi/id/product_name"),
        }

    def handle(self, req):
        cmd = req.get("cmd")
        cfg = self.config
        if cmd == "status":
            return self.status()
        if cmd == "history":
            return {"history": list(self.history)}
        if cmd == "set_fan":
            fan, mode = req["fan"], req["mode"]
            if fan not in FANS or mode not in MODES:
                raise ValueError("Ventilador o modo inválido")
            changes = {"fans": {fan: {"mode": mode}}, "curve_enabled": {fan: False}}
            if "percent" in req:
                changes["fans"][fan]["percent"] = int(req["percent"])
            prof = cfg.update_active(changes)
            self.apply_fan(fan, prof)
        elif cmd == "set_curve":
            fan = req["fan"]
            if fan not in FANS:
                raise ValueError("Ventilador inválido")
            changes = {"curve_enabled": {fan: bool(req.get("enabled", True))}}
            if "points" in req:
                changes["curves"] = {fan: validate_curve(req["points"])}
            prof = cfg.update_active(changes)
            self.apply_fan(fan, prof)
            if self.last_sample:
                self.run_curves(self.last_sample)
        elif cmd == "set_coolboost":
            cb = self.hw.coolboost_backend()
            if not cb:
                raise HardwareError("CoolBoost no disponible")
            cfg.update_active({"coolboost": bool(req["on"])})
            cb.set_coolboost(bool(req["on"]))
        elif cmd == "set_platform_profile":
            sensors.set_platform_profile(req["name"])
            cfg.update_active({"platform_profile": req["name"]})
        elif cmd == "set_gpu_oc":
            level = req["level"]
            changes = {"gpu_oc": {"level": level}}
            if level == "custom":
                changes["gpu_oc"].update(core=int(req.get("core", 0)), mem=int(req.get("mem", 0)))
            prof = cfg.update_active(changes)
            cfg.save()
            smp = self.last_sample or {}
            state = smp.get("gpu_state")
            if state is None:
                state = sensors.gpu_info().get("state")
            self.gpu_oc.update(state, smp.get("on_ac", sensors.on_ac()), prof["gpu_oc"], force=True)
        elif cmd == "set_win_key":
            self._need("win_key")
            self.hw.wmi.set_win_key(bool(req["enabled"]))
            cfg.update_active({"win_key": bool(req["enabled"])})
        elif cmd == "set_lcd_overdrive":
            self._need("lcd_overdrive")
            self.hw.wmi.set_lcd_overdrive(bool(req["enabled"]))
            cfg.update_active({"lcd_overdrive": bool(req["enabled"])})
        elif cmd == "profile_save_as":
            cfg.save_profile_as(req["name"])
            cfg.select_profile(req["name"].strip())
        elif cmd == "profile_select":
            cfg.select_profile(req["name"])
            self.apply_profile()
        elif cmd == "profile_rename":
            cfg.rename_profile(req["old"], req["new"])
        elif cmd == "profile_delete":
            was_active = req["name"] == cfg.active_name
            cfg.delete_profile(req["name"])
            if was_active:
                self.apply_profile()
        elif cmd == "profile_import":
            name = cfg.save_profile_as(req["name"], validate_profile(req["profile"]))
            if name == cfg.active_name:
                self.apply_profile()
        elif cmd == "profile_export":
            name = req.get("name", cfg.active_name)
            if name not in cfg.data["profiles"]:
                raise ValueError(f"No existe el perfil {name}")
            return {"name": name, "profile": cfg.data["profiles"][name]}
        elif cmd == "settings_update":
            changes = dict(req["settings"])
            changes.pop("socket_group", None)  # solo editable a mano en el archivo
            changes.pop("wmi_method_ids", None)
            cfg.update_settings(changes)
            for fan in FANS:
                self.curves[fan] = self._new_controller()
        elif cmd == "reapply":
            self.apply_profile()
        else:
            raise ValueError(f"Comando desconocido: {cmd}")
        cfg.save()
        return self.status()

    def _need(self, cap):
        if not self.hw.capabilities().get(cap):
            raise HardwareError(f"Función no disponible en este equipo: {cap}")

    # --- servidor ---------------------------------------------------------
    def open_socket(self):
        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.socket_path)
        group = self.config.settings.get("socket_group")
        mode = 0o600
        if group and os.geteuid() == 0:
            try:
                os.chown(self.socket_path, 0, grp.getgrnam(group).gr_gid)
                mode = 0o660
            except KeyError:
                log.warning("El grupo %s no existe; el socket queda solo para root", group)
        os.chmod(self.socket_path, mode)
        srv.listen(8)
        srv.setblocking(False)
        self.sel.register(srv, selectors.EVENT_READ, self._accept)
        return srv

    def _accept(self, srv):
        conn, _ = srv.accept()
        conn.settimeout(2)
        try:
            buf = b""
            while b"\n" not in buf and len(buf) < MAX_REQUEST:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            try:
                req = json.loads(buf.split(b"\n", 1)[0] or b"{}")
                resp = {"ok": True, "data": self.handle(req)}
            except (HardwareError, ValueError, KeyError, TypeError, OSError) as e:
                self._error(f"{type(e).__name__}: {e}")
                resp = {"ok": False, "error": str(e)}
            conn.sendall(json.dumps(resp, ensure_ascii=False).encode() + b"\n")
        except (OSError, socket.timeout):
            pass
        finally:
            conn.close()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, "reapply_requested", True))
        srv = self.open_socket()
        if self.config.settings["apply_on_boot"]:
            self.apply_profile()
        interval = max(1, int(self.config.settings["curve_interval"]))
        next_sample = 0
        try:
            while self.running:
                if self.reapply_requested:
                    self.reapply_requested = False
                    log.info("Reaplicando perfil (vuelta de suspensión o petición)")
                    for fan in FANS:
                        self.curves[fan] = self._new_controller()
                    self.apply_profile()
                now = time.monotonic()
                if now >= next_sample:
                    try:
                        self.sample()
                    except Exception as e:  # noqa: BLE001 - el servicio no debe caerse por un sensor
                        self._error(f"Error leyendo sensores: {e}")
                    next_sample = now + interval
                for key, _ in self.sel.select(timeout=max(0.05, next_sample - time.monotonic())):
                    key.data(key.fileobj)
        finally:
            log.info("Deteniendo: ventiladores a automático y overclock de GPU a cero")
            self.restore_auto()
            try:
                self.gpu_oc.reset(sensors.gpu_info().get("state"))
            except (nvgpu.NvmlError, OSError) as e:
                log.warning("No se pudo quitar el overclock: %s", e)
            srv.close()
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    if os.geteuid() != 0:
        print("predatorsensed debe ejecutarse como root (lo inicia systemd)", file=sys.stderr)
        sys.exit(1)
    # Ruta fija: la variable PREDATORSENSE_SOCKET es solo para el servicio simulado de desarrollo.
    Daemon(socket_path="/run/predatorsense/socket").run()


if __name__ == "__main__":
    main()

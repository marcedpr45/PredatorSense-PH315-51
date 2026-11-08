"""predatorsense-ctl: control desde la terminal."""

import argparse
import json
import sys

from .client import ServiceError, request


def fmt(v, unit=""):
    return "n/d" if v is None else f"{v:g}{unit}" if isinstance(v, (int, float)) else str(v)


def main(argv=None):
    p = argparse.ArgumentParser(prog="predatorsense-ctl", description="Control de PredatorSense Linux")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Estado actual")
    sub.add_parser("reapply", help="Volver a aplicar el perfil activo")
    f = sub.add_parser("fan", help="Modo de ventilador")
    f.add_argument("fan", choices=["cpu", "gpu", "all"])
    f.add_argument("mode", choices=["auto", "max", "custom"])
    f.add_argument("percent", nargs="?", type=int)
    c = sub.add_parser("coolboost")
    c.add_argument("state", choices=["on", "off"])
    w = sub.add_parser("winkey", help="Activar/desactivar la tecla Windows")
    w.add_argument("state", choices=["on", "off"])
    pr = sub.add_parser("profile", help="Seleccionar perfil")
    pr.add_argument("name")
    sub.add_parser("profiles", help="Listar perfiles")
    ex = sub.add_parser("export", help="Exportar un perfil a JSON")
    ex.add_argument("name", nargs="?")
    a = p.parse_args(argv)

    try:
        if a.cmd == "status":
            s = request("status")
            smp = s.get("sample") or {}
            caps = s["capabilities"]
            print(f"Modelo: {s.get('model')}   Backend ventiladores: {caps['fan_backend']}")
            print(f"Perfil activo: {s['active_profile']}")
            for fan in ("cpu", "gpu"):
                cfg = s["profile"]["fans"][fan]
                curve = s["profile"]["curve_enabled"][fan]
                mode = f"curva ({fmt(s['curve_speed'][fan], '%')})" if curve else \
                    cfg["mode"] + (f" {cfg['percent']}%" if cfg["mode"] == "custom" else "")
                print(f"  {fan.upper()}: {mode:<18} temp {fmt(smp.get(fan + '_temp'), '°C'):>7}"
                      f"  rpm {fmt(smp.get(fan + '_rpm'))}")
            print(f"CoolBoost: {'sí' if s['profile']['coolboost'] else 'no'}")
            for e in s.get("errors", [])[-3:]:
                print("! " + e["message"])
        elif a.cmd == "reapply":
            request("reapply")
            print("Perfil reaplicado.")
        elif a.cmd == "fan":
            for fan in (("cpu", "gpu") if a.fan == "all" else (a.fan,)):
                args = {"fan": fan, "mode": a.mode}
                if a.percent is not None:
                    args["percent"] = a.percent
                request("set_fan", **args)
                print(f"Ventilador {fan.upper()}: {a.mode}" + (f" {a.percent}%" if a.percent is not None else ""))
        elif a.cmd == "coolboost":
            request("set_coolboost", on=a.state == "on")
            print("CoolBoost " + ("activado." if a.state == "on" else "desactivado."))
        elif a.cmd == "winkey":
            request("set_win_key", enabled=a.state == "on")
            print("Tecla Windows " + ("activada." if a.state == "on" else
                                      "desactivada (Super enviará Play/Pausa). Se mantiene tras reiniciar."))
        elif a.cmd == "profile":
            request("profile_select", name=a.name)
            print(f"Perfil «{a.name}» aplicado.")
        elif a.cmd == "profiles":
            s = request("status")
            for n in s["profiles"]:
                print(("* " if n == s["active_profile"] else "  ") + n)
        elif a.cmd == "export":
            args = {"name": a.name} if a.name else {}
            print(json.dumps(request("profile_export", **args), indent=2, ensure_ascii=False))
    except ServiceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

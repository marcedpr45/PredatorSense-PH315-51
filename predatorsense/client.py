"""Cliente del socket del servicio (lo usan la interfaz y la línea de comandos)."""

import json
import socket

from . import SOCKET_PATH


class ServiceError(Exception):
    pass


class ServiceUnavailable(ServiceError):
    pass


def request(cmd, socket_path=SOCKET_PATH, timeout=5, **args):
    payload = dict(args, cmd=cmd)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            s.sendall(json.dumps(payload).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except FileNotFoundError as e:
        raise ServiceUnavailable("El servicio predatorsensed no está en ejecución") from e
    except PermissionError as e:
        raise ServiceUnavailable("Sin permiso para hablar con el servicio (¿estás en el grupo configurado?)") from e
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        raise ServiceUnavailable(f"No se pudo conectar con el servicio: {e}") from e
    try:
        resp = json.loads(buf)
    except json.JSONDecodeError as e:
        raise ServiceError("Respuesta inválida del servicio") from e
    if not resp.get("ok"):
        raise ServiceError(resp.get("error", "Error desconocido"))
    return resp["data"]

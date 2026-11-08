"""PredatorSense para Linux: control de ventiladores, sensores y ajustes de Acer Predator."""

__version__ = "0.1.0"

APP_NAME = "PredatorSense Linux"
import os as _os

# PREDATORSENSE_SOCKET solo para desarrollo (servicio simulado sin root)
SOCKET_PATH = _os.environ.get("PREDATORSENSE_SOCKET", "/run/predatorsense/socket")
CONFIG_DIR = "/etc/predatorsense-linux"
CONFIG_PATH = CONFIG_DIR + "/config.json"

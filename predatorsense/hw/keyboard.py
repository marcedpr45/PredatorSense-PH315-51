"""Mapa de teclas del teclado interno (AT, PS/2) mediante ioctl de evdev.

Con la tecla Windows desactivada, la BIOS hace que Super envíe el código 0xa2, el
mismo que Fn+Play/Pausa, así que no se pueden separar. Para que Super no haga nada se
anula ese código solo en el teclado interno; los teclados externos no se ven afectados.
"""

import fcntl
import os
import struct

from . import HardwareError, read_text

AT_KEYBOARD = "AT Translated Set 2 keyboard"
PLAY_PAUSE_SCANCODE = 0xA2
KEY_RESERVED = 0
KEY_PLAYPAUSE = 164
EVIOCSKEYCODE_V2 = 0x40284504   # _IOW('E', 0x04, struct input_keymap_entry)


def keymap_entry(scancode, keycode):
    """struct input_keymap_entry con el código de 32 bits en little endian."""
    return struct.pack("<BBHI32s", 0, 4, 0, keycode, struct.pack("<I", scancode))


def find_at_keyboard(sys_root="/sys/class/input"):
    for name in sorted(os.listdir(sys_root)):
        if name.startswith("event") and read_text(f"{sys_root}/{name}/device/name") == AT_KEYBOARD:
            return f"/dev/input/{name}"
    return None


def set_play_pause_blocked(blocked):
    device = find_at_keyboard()
    if device is None:
        raise HardwareError("No se encontró el teclado interno")
    fd = os.open(device, os.O_RDWR)
    try:
        code = KEY_RESERVED if blocked else KEY_PLAYPAUSE
        fcntl.ioctl(fd, EVIOCSKEYCODE_V2, keymap_entry(PLAY_PAUSE_SCANCODE, code))
    finally:
        os.close(fd)

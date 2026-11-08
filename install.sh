#!/usr/bin/env bash
# Instalador de PredatorSense Linux. Uso: sudo ./install.sh [--no-predator-v4]
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este instalador necesita root: sudo ./install.sh" >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/predatorsense-linux
PREDATOR_V4=1
[[ "${1:-}" == "--no-predator-v4" ]] && PREDATOR_V4=0

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

model=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo "?")
say "Modelo detectado: $model"
[[ "$model" == *PH315-51* ]] || warn "Las direcciones EC de respaldo son del PH315-51; en otro modelo el EC queda desactivado."

python3 -c 'import PyQt6' 2>/dev/null || { say "Instalando python-pyqt6"; pacman -S --needed --noconfirm python-pyqt6; }

say "Copiando programa a $DEST"
rm -rf "$DEST"
install -d -m 755 "$DEST"
cp -r "$SRC/predatorsense" "$DEST/"
find "$DEST" -name '__pycache__' -prune -exec rm -rf {} +
chown -R root:root "$DEST"
chmod -R u=rwX,go=rX "$DEST"

install -m 755 "$SRC/bin/predatorsense" "$SRC/bin/predatorsense-ctl" "$SRC/bin/predatorsensed" /usr/local/bin/
install -m 644 "$SRC/data/predatorsensed.service" /etc/systemd/system/predatorsensed.service
install -D -m 755 "$SRC/data/predatorsense-sleep" /usr/lib/systemd/system-sleep/predatorsense
install -D -m 644 "$SRC/data/predatorsense-linux.desktop" /usr/share/applications/predatorsense-linux.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/predatorsense-linux.svg   # icono de versiones anteriores
for s in 16 32 48 64 128 256; do
    install -D -m 644 "$SRC/data/icons/predatorsense-linux-$s.png" "/usr/share/icons/hicolor/${s}x${s}/apps/predatorsense-linux.png"
done
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
install -d -m 755 /etc/predatorsense-linux

say "Configurando módulos del kernel"
{
    echo "# PredatorSense Linux"
    echo "options ec_sys write_support=1"
    [[ $PREDATOR_V4 == 1 ]] && echo "options acer_wmi predator_v4=1"
} > /etc/modprobe.d/predatorsense-linux.conf
{
    echo "ec_sys"
    modinfo acpi_call &>/dev/null && echo "acpi_call"
} > /etc/modules-load.d/predatorsense-linux.conf

modprobe -r ec_sys 2>/dev/null || true
modprobe ec_sys write_support=1 || warn "No se pudo cargar ec_sys"
if modinfo acpi_call &>/dev/null; then
    modprobe acpi_call || warn "No se pudo cargar acpi_call"
else
    warn "acpi_call no está instalado: tecla Windows y overdrive quedarán desactivados."
    warn "Instálalo con: sudo pacman -S acpi_call-dkms   (y vuelve a correr este instalador)"
fi
if [[ $PREDATOR_V4 == 1 ]] && [[ "$(cat /sys/module/acer_wmi/parameters/predator_v4 2>/dev/null)" != "Y" ]]; then
    say "Recargando acer_wmi con predator_v4=1"
    systemctl stop predatorsensed 2>/dev/null || true
    if modprobe -r acer_wmi && modprobe acer_wmi predator_v4=1; then
        :
    else
        warn "No se pudo recargar acer_wmi ahora; se aplicará al reiniciar."
    fi
fi

say "Activando el servicio"
systemctl daemon-reload
systemctl enable predatorsensed.service >/dev/null
systemctl restart predatorsensed.service
sleep 1
systemctl --no-pager --lines=8 status predatorsensed.service || true

if [[ -x /usr/bin/predator-sense-no-launch ]]; then
    warn "Tienes instalada la versión vieja (/usr/bin/predator-sense). No la abras a la vez que esta:"
    warn "las dos escriben en el mismo controlador. Puedes quitarla con: sudo ./uninstall.sh --old"
fi
say "Listo. Abre «PredatorSense Linux» desde el menú o ejecuta: predatorsense"

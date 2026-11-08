#!/usr/bin/env bash
# Desinstala PredatorSense Linux.
#   sudo ./uninstall.sh          quita esta versión (conserva /etc/predatorsense-linux)
#   sudo ./uninstall.sh --purge  quita esta versión y su configuración
#   sudo ./uninstall.sh --old    quita SOLO la versión vieja (mohsunb/PredatorSense)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Necesita root: sudo ./uninstall.sh" >&2; exit 1; }

if [[ " $* " == *" --old "* ]]; then
    removed=0
    for f in /usr/bin/predator-sense /usr/bin/predator-sense-no-launch \
             /usr/share/applications/predator-sense.desktop /usr/share/icons/predator-sense; do
        [[ -e "$f" ]] && { rm -f "$f"; echo "eliminado $f"; removed=1; }
    done
    # Fuentes TT Squares que instaló la versión vieja
    if [[ -d /usr/local/share/fonts/s ]] && ls /usr/local/share/fonts/s/Squares* &>/dev/null; then
        rm -f /usr/local/share/fonts/s/Squares*
        rmdir --ignore-fail-on-non-empty /usr/local/share/fonts/s
        fc-cache -f >/dev/null 2>&1 || true
        echo "eliminadas las fuentes TT Squares"
        removed=1
    fi
    [[ $removed == 1 ]] && echo "Versión vieja eliminada." || echo "La versión vieja no estaba instalada."
    exit 0
fi

systemctl disable --now predatorsensed.service 2>/dev/null || true
rm -f /etc/systemd/system/predatorsensed.service /usr/lib/systemd/system-sleep/predatorsense \
      /usr/local/bin/predatorsense /usr/local/bin/predatorsense-ctl /usr/local/bin/predatorsensed \
      /usr/share/applications/predatorsense-linux.desktop \
      /usr/share/icons/hicolor/scalable/apps/predatorsense-linux.svg \
      /etc/modprobe.d/predatorsense-linux.conf /etc/modules-load.d/predatorsense-linux.conf
for s in 16 32 48 64 128 256; do
    rm -f "/usr/share/icons/hicolor/${s}x${s}/apps/predatorsense-linux.png"
done
rm -rf /opt/predatorsense-linux
systemctl daemon-reload
[[ " $* " == *" --purge "* ]] && rm -rf /etc/predatorsense-linux
echo "PredatorSense Linux desinstalado."

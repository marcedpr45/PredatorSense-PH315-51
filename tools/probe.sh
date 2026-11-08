#!/usr/bin/env bash
# Recolecta información de solo lectura para confirmar los métodos WMI de tu BIOS.
# Uso: sudo tools/probe.sh   -> crea ./hw-dump/ (tuyo, no de root)
# No escribe nada en el hardware: solo copia tablas ACPI y hace lecturas.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Necesita root: sudo tools/probe.sh" >&2; exit 1; }

OUT="$(cd "$(dirname "$0")/.." && pwd)/hw-dump"
OWNER="${SUDO_USER:-root}"
mkdir -p "$OUT"
echo "Guardando en $OUT"

{
    echo "fecha: $(date -Is)"
    echo "kernel: $(uname -r)"
    for f in sys_vendor product_name product_version bios_version bios_date board_name; do
        echo "$f: $(cat /sys/class/dmi/id/$f 2>/dev/null)"
    done
    echo "acer_wmi predator_v4: $(cat /sys/module/acer_wmi/parameters/predator_v4 2>/dev/null)"
    echo "platform_profile: $(cat /sys/firmware/acpi/platform_profile 2>/dev/null) [$(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null)]"
    echo "acpi_call: $([[ -e /proc/acpi/call ]] && echo cargado || echo no)"
    echo "--- hwmon"
    for h in /sys/class/hwmon/hwmon*; do echo "$h $(cat $h/name): $(ls $h | grep -E '^(fan|pwm|temp)' | tr '\n' ' ')"; done
    echo "--- WMI"
    for d in /sys/bus/wmi/devices/*; do echo "$(basename $d) object_id=$(cat $d/object_id 2>/dev/null) driver=$(basename "$(readlink $d/driver 2>/dev/null)")"; done
} > "$OUT/info.txt" 2>&1

cp /sys/firmware/acpi/tables/DSDT "$OUT/dsdt.dat"
i=0
for t in /sys/firmware/acpi/tables/SSDT*; do cp "$t" "$OUT/ssdt$((i++)).dat"; done
for d in /sys/bus/wmi/devices/05901221-D566-11D1-B2F0-00A0C9062910-*; do
    cp "$d/bmof" "$OUT/bmof-$(basename "$d").bin" 2>/dev/null
done

if command -v iasl >/dev/null; then
    (cd "$OUT" && for t in dsdt.dat ssdt*.dat; do iasl -d "$t" >/dev/null 2>&1; done)
    # WMBH puede estar en el DSDT o en una SSDT (en el PH315-51 está en una SSDT)
    f=$(grep -l 'Method (WMBH' "$OUT"/*.dsl 2>/dev/null | head -1)
    [[ -n "$f" ]] && awk '/Method \(WMBH/{p=1} p{print; o+=gsub(/\{/,"{"); c+=gsub(/\}/,"}"); if(o>0 && o==c) exit}' "$f" > "$OUT/wmbh.dsl"
else
    echo "iasl no está instalado (paquete acpica): no se desensambló el DSDT." | tee -a "$OUT/info.txt"
fi

# Lectura del EC (solo lectura) como referencia
if modprobe ec_sys 2>/dev/null && [[ -r /sys/kernel/debug/ec/ec0/io ]]; then
    od -An -tx1 -v -w16 /sys/kernel/debug/ec/ec0/io > "$OUT/ec.txt"
fi

# Lectura WMI de prueba: temperatura de CPU (GetGamingSysInfo, solo lectura)
if [[ -e /proc/acpi/call ]]; then
    echo '\_SB.PCI0.WMID.WMBH 0 5 b01010000' > /proc/acpi/call
    echo "GetGamingSysInfo(CPU temp): $(tr -d '\0' < /proc/acpi/call)" >> "$OUT/info.txt"
    echo '\_SB.PCI0.WMID.WMBH 0 5 b01020000' > /proc/acpi/call
    echo "GetGamingSysInfo(CPU fan): $(tr -d '\0' < /proc/acpi/call)" >> "$OUT/info.txt"
fi

# Descomprimir el MOF y listar los métodos de AcerGamingFunction con su ID
for b in "$OUT"/bmof-*.bin; do
    python3 -I "$(dirname "$0")/bmof_decompress.py" "$b" > "${b%.bin}.dec" 2>/dev/null
done
python3 -I - "$OUT" > "$OUT/wmi-methods.txt" <<'PY'
import glob, re, struct, sys
for f in glob.glob(sys.argv[1] + "/bmof-*.dec"):
    d = open(f, "rb").read()
    i = d.find("AcerGamingFunction".encode("utf-16le"))
    if i < 0:
        continue
    strs = [(m.start(), m.end(), m.group().decode("utf-16le"))
            for m in re.finditer(rb"(?:[\x20-\x7e]\x00){3,}", d[i:])]
    names = [(s, n) for s, e, n in strs if re.fullmatch(r"[GS]etGaming\w+", n)]
    for s, e, n in strs:
        if n == "WmiMethodId":
            prev = [nm for p, nm in names if p < s]
            if not prev:
                continue
            print(struct.unpack_from("<I", d, i + e + 2)[0], prev[-1])
        if n == "LongRunFunction":
            break
PY
cat "$OUT/wmi-methods.txt"

chown -R "$OWNER": "$OUT"
echo "Listo. Archivos en $OUT:"
ls -1 "$OUT"

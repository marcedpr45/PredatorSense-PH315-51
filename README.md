<p align="center">
  <img src="data/icons/predatorsense-linux-128.png" width="96" alt="icono">
</p>

<h1 align="center">PredatorSense para Linux · PH315-51</h1>

<p align="center">
  Control nativo de ventiladores, sensores, tecla Windows, overclock de GPU y perfiles
  para el <b>Acer Predator Helios 300 PH315-51</b> (2018, i7-8750H + GTX 1060).
</p>

<p align="center">
  <img src="docs/screenshots/ventiladores.png" width="800" alt="Ventiladores">
</p>

Reúne las funciones de la versión Windows de PredatorSense y las de la versión Linux anterior
([mohsunb/PredatorSense], que controlaba el EC). Usa **las mismas llamadas WMI de la BIOS que la
versión Windows**, sacadas del MOF binario de la propia BIOS. Además corrige el problema principal de
Windows: **la configuración ahora se guarda y se vuelve a aplicar**.

## Funciones

| Función | Estado en el PH315-51 |
|---|---|
| **Ventiladores** CPU y GPU por separado: Auto / Máx / Personalizado | ✅ Probado (WMI, hasta 6122 RPM) |
| **Curva de ventilador** por temperatura, una por ventilador, con histéresis | ✅ |
| **CoolBoost™** | ✅ (EC) |
| **Supervisión**: temperaturas, uso de CPU/GPU, RPM reales, gráficas | ✅ |
| **Tecla Windows** activar/desactivar | ✅ Probado |
| **LCD Overdrive** | ✅ |
| **Overclock de GPU** Normal / Más rápido / Turbo / Personalizado (experimental) | ✅ NVML, solo con cargador |
| **Perfiles**: crear, renombrar, borrar, importar/exportar | ✅ |
| **Reaplicar al arrancar y al volver de suspensión** | ✅ |
| **Abrir al iniciar sesión** (minimizado en la bandeja), activable desde Ajustes | ✅ |
| Icono en la bandeja y `predatorsense-ctl` para la terminal | ✅ |
| Perfil de energía (`platform_profile`) | ❌ La BIOS no tiene los métodos WMI 22/23 que usa el kernel; se oculta |
| Iluminación RGB | ❌ El PH315-51 no tiene teclado RGB |

### Qué mejora respecto a la versión Windows

- **Guarda y reaplica la configuración.** Windows la guardaba en el registro, pero no la volvía a
  aplicar. Aquí un servicio la guarda en `/etc/predatorsense-linux/config.json` (escritura atómica) y la
  reaplica al iniciar y después de cada suspensión.
- **La curva funciona con la ventana cerrada.** En Windows solo corría con el editor abierto.
- **Una curva por ventilador.** Windows usaba solo la temperatura de la CPU para los dos.
- **Modo seguro.** Si el servicio se detiene o se pierde la lectura de temperatura, los ventiladores
  vuelven a automático y el overclock vuelve a cero.
- **No despierta la GPU NVIDIA** solo para leer sus sensores.

## Capturas

| Supervisión | Overclock de GPU |
|---|---|
| ![Supervisión](docs/screenshots/supervision.png) | ![Overclock](docs/screenshots/overclock.png) |
| **Teclado y pantalla** | **Ajustes** |
| ![Teclado](docs/screenshots/teclado.png) | ![Ajustes](docs/screenshots/ajustes.png) |

## Instalación (Arch / CachyOS)

```bash
sudo pacman -S --needed python-pyqt6 acpi_call-dkms acpica
git clone https://github.com/marcedpr45/PredatorSense-PH315-51.git
cd PredatorSense-PH315-51
sudo ./install.sh
```

El instalador:
- copia el programa a `/opt/predatorsense-linux`;
- instala el servicio `predatorsensed` (systemd) y el hook de suspensión;
- configura los módulos `ec_sys write_support=1` y `acer_wmi predator_v4=1`.

Después abre **PredatorSense Linux** desde el menú.

Desinstalar:
```bash
sudo ./uninstall.sh            # conserva la configuración
sudo ./uninstall.sh --purge    # también borra la configuración
sudo ./uninstall.sh --old      # quita SOLO la versión vieja (mohsunb/PredatorSense)
```

### Terminal

```bash
predatorsense-ctl status
predatorsense-ctl fan all max          # auto | max | custom <0-100>
predatorsense-ctl fan cpu custom 60
predatorsense-ctl coolboost on
predatorsense-ctl winkey on            # off = Super envía Play/Pausa
predatorsense-ctl profiles
predatorsense-ctl profile "Juego"
```

## Cómo funciona

```
predatorsense (interfaz, tu usuario) ─┐
predatorsense-ctl (terminal) ─────────┴─ socket ─> predatorsensed (root)
                                                     ├─ WMI AcerGamingFunction vía acpi_call
                                                     ├─ hwmon acer-wmi (RPM y temperaturas)
                                                     ├─ EC (CoolBoost, respaldo de ventiladores)
                                                     └─ NVML (overclock de GPU)
```

Solo el servicio toca el hardware. La interfaz corre como usuario normal, sin `pkexec`, y el socket
solo lo pueden usar los usuarios del grupo `wheel`.

### Métodos WMI de la BIOS (V1.26)

El método ACPI es `\_SB.PCI0.WMID.WMBH`. Los IDs salen del MOF binario de la BIOS, que viene
comprimido con MRCI y se lee con `tools/bmof_decompress.py`:

| ID | Método | Uso |
|---|---|---|
| 1 | SetGamingProfile | tecla Windows, LCD Overdrive |
| 5 | GetGamingSysInfo | temperaturas y RPM |
| 14 | SetGamingFanBehavior | modo de ventilador |
| 16 | SetGamingFanSpeed | velocidad de ventilador |

Para revisarlos en otra versión de BIOS: `sudo tools/probe.sh` (solo lectura).

### Overclock de GPU

Windows usaba NvAPI (`SetPstates20`). Aquí se usan los offsets V/F de NVML, que funcionan en Wayland.
El PredatorSense oficial marcaba `GPU_OC_Support = 0` en este modelo, así que los niveles son
propios y conservadores:

| Nivel | Núcleo | Memoria |
|---|---|---|
| Normal | +0 MHz | +0 MHz |
| Más rápido | +75 MHz | +200 MHz |
| Turbo | +150 MHz | +400 MHz |
| Personalizado | −200…+250 MHz | −500…+800 MHz |

Solo se aplica con el cargador conectado y se reaplica cuando la GPU sale de reposo.

## Desarrollo

```bash
python3 -m unittest discover -s tests              # tests (sin root ni hardware)
python3 tools/dev_fake_daemon.py /tmp/ps.sock &    # servicio simulado
PREDATORSENSE_SOCKET=/tmp/ps.sock python3 -m predatorsense.gui.app
```

## Créditos y aviso

- Direcciones EC del PH315-51: [mohsunb/PredatorSense] (fork de PredatorNonSense de kphanipavan).
- Codificación de las llamadas WMI: deducida de [boazvdwansem/PredatorSenseDecomp], del driver
  `acer-wmi` del kernel y del MOF de la BIOS. No incluye código de Acer.
- Predator y su logo son marcas de Acer Inc. Este proyecto no está afiliado a Acer.
- Cambiar ventiladores, llamar métodos ACPI o hacer overclock es bajo tu responsabilidad.

[mohsunb/PredatorSense]: https://github.com/mohsunb/PredatorSense
[boazvdwansem/PredatorSenseDecomp]: https://github.com/boazvdwansem/PredatorSenseDecomp

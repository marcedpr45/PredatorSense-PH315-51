"""Ventana principal de PredatorSense Linux (PyQt6). Corre como usuario normal y
habla con el servicio root por su socket."""

import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QRadioButton, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QMenu, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..client import ServiceError, ServiceUnavailable, request
from ..hw import FANS
from .widgets import ACCENT, ACCENT2, CurveEditor, LineChart

FAN_NAMES = {"cpu": "CPU", "gpu": "GPU"}
MODE_LABELS = [("auto", "Auto"), ("max", "Máx"), ("custom", "Personalizado"), ("curve", "Curva")]
PP_LABELS = {"quiet": "Silencioso", "balanced": "Equilibrado", "balanced-performance": "Rendimiento",
             "performance": "Turbo", "low-power": "Ahorro", "cool": "Fresco"}

STYLE = """
* { font-size: 10.5pt; }
QMainWindow, QWidget#root { background: #0d1117; }
QWidget { color: #e6edf3; }
QListWidget#nav { background: #0a0d12; border: none; padding-top: 12px; outline: 0; }
QListWidget#nav::item { padding: 12px 18px; border-left: 3px solid transparent; color: #9aa4b2; }
QListWidget#nav::item:selected { background: #151b23; border-left: 3px solid #19c3ff; color: #ffffff; }
QFrame.card { background: #151b23; border: 1px solid #222a35; border-radius: 10px; }
QLabel.h1 { font-size: 17pt; font-weight: 600; }
QLabel.h2 { font-size: 12pt; font-weight: 600; }
QLabel.muted { color: #8b949e; }
QLabel.big { font-size: 24pt; font-weight: 600; color: #19c3ff; }
QLabel.warn { color: #f0b429; }
QPushButton { background: #222a35; border: 1px solid #2f3a48; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { border-color: #19c3ff; }
QPushButton:checked, QPushButton.primary { background: #0e5d7a; border-color: #19c3ff; }
QPushButton:disabled { color: #59636e; border-color: #222a35; }
QRadioButton::indicator, QCheckBox::indicator { width: 16px; height: 16px; }
QSlider::groove:horizontal { height: 6px; background: #2f3a48; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #19c3ff; border-radius: 3px; }
QSlider::handle:horizontal { background: #e6edf3; width: 16px; margin: -6px 0; border-radius: 8px; }
QComboBox, QSpinBox { background: #222a35; border: 1px solid #2f3a48; border-radius: 6px; padding: 5px 8px; }
QListWidget { background: #0d1117; border: 1px solid #222a35; border-radius: 6px; }
QListWidget::item { padding: 8px; }
QListWidget::item:selected { background: #0e5d7a; }
QStatusBar { color: #8b949e; }
"""


def card():
    f = QFrame()
    f.setProperty("class", "card")
    return f


def label(text, cls=None):
    lbl = QLabel(text)
    if cls:
        lbl.setProperty("class", cls)
    lbl.setWordWrap(True)
    return lbl


def fmt_temp(c, unit):
    if c is None:
        return "—"
    return f"{c * 9 / 5 + 32:.0f}°F" if unit == "F" else f"{c:.0f}°C"


class Page(QWidget):
    def __init__(self, win, title, subtitle=None):
        super().__init__()
        self.win = win
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(28, 24, 28, 24)
        self.layout_.setSpacing(16)
        self.layout_.addWidget(label(title, "h1"))
        if subtitle:
            self.layout_.addWidget(label(subtitle, "muted"))

    def call(self, cmd, **args):
        return self.win.call(cmd, **args)

    def refresh(self, status):
        pass


# --- Monitoreo -------------------------------------------------------------
class MonitorPage(Page):
    def __init__(self, win):
        super().__init__(win, "Supervisión")
        tiles = QHBoxLayout()
        self.tiles = {}
        for key, title in (("cpu_temp", "CPU"), ("gpu_temp", "GPU"), ("cpu_rpm", "Ventilador CPU"),
                           ("gpu_rpm", "Ventilador GPU")):
            c = card()
            v = QVBoxLayout(c)
            v.addWidget(label(title, "muted"))
            big = label("—", "big")
            v.addWidget(big)
            sub = label("", "muted")
            v.addWidget(sub)
            self.tiles[key] = (big, sub)
            tiles.addWidget(c)
        self.layout_.addLayout(tiles)
        self.temp_chart = LineChart("Temperatura (°C)", "°", 100)
        self.usage_chart = LineChart("Uso (%)", "%", 100)
        self.rpm_chart = LineChart("Ventiladores (RPM)", "", 6500)
        for ch in (self.temp_chart, self.usage_chart, self.rpm_chart):
            c = card()
            QVBoxLayout(c).addWidget(ch)
            self.layout_.addWidget(c, 1)
        self.info = label("", "muted")
        self.layout_.addWidget(self.info)

    def refresh(self, status):
        unit = status["settings"]["temp_unit"]
        s = status.get("sample") or {}
        hist = self.win.history
        self.tiles["cpu_temp"][0].setText(fmt_temp(s.get("cpu_temp"), unit))
        self.tiles["cpu_temp"][1].setText(f"Uso {s.get('cpu_usage') or 0:.0f}%")
        gstate = s.get("gpu_state")
        self.tiles["gpu_temp"][0].setText(fmt_temp(s.get("gpu_temp"), unit) if gstate == "active" else "Inactiva")
        self.tiles["gpu_temp"][1].setText(
            f"Uso {s.get('gpu_usage') or 0:.0f}% · {s.get('gpu_clock') or 0:.0f} MHz" if gstate == "active"
            else "GPU dedicada en reposo (no se despierta para leerla)" if gstate == "suspended" else "")
        rpm_ok = status["capabilities"]["fan_rpm"]
        for fan in FANS:
            v = s.get(f"{fan}_rpm")
            self.tiles[f"{fan}_rpm"][0].setText(f"{v}" if v is not None else "—")
            self.tiles[f"{fan}_rpm"][1].setText("RPM" if rpm_ok else "Sin lectura de RPM en este backend")
        self.temp_chart.set_series("CPU", ACCENT, [h.get("cpu_temp") for h in hist])
        self.temp_chart.set_series("GPU", ACCENT2, [h.get("gpu_temp") for h in hist])
        self.usage_chart.set_series("CPU", ACCENT, [h.get("cpu_usage") for h in hist])
        self.usage_chart.set_series("GPU", ACCENT2, [h.get("gpu_usage") for h in hist])
        self.rpm_chart.set_series("CPU", ACCENT, [h.get("cpu_rpm") for h in hist])
        self.rpm_chart.set_series("GPU", ACCENT2, [h.get("gpu_rpm") for h in hist])
        bat = s.get("battery")
        self.info.setText(("Con cargador" if s.get("on_ac") else "Con batería") +
                          (f" · batería {bat}%" if bat is not None else ""))


# --- Ventiladores ------------------------------------------------------------
class FanCard(QFrame):
    def __init__(self, page, fan):
        super().__init__()
        self.setProperty("class", "card")
        self.page, self.fan = page, fan
        v = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(label(f"Ventilador {FAN_NAMES[fan]}", "h2"))
        head.addStretch()
        self.live = label("", "muted")
        head.addWidget(self.live)
        v.addLayout(head)
        row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.buttons = {}
        for mode, text in MODE_LABELS:
            b = QPushButton(text)
            b.setCheckable(True)
            self.group.addButton(b)
            self.buttons[mode] = b
            b.clicked.connect(lambda _, m=mode: self.on_mode(m))
            row.addWidget(b)
        v.addLayout(row)
        srow = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.value = QLabel("50%")
        self.value.setMinimumWidth(44)
        # Se guarda 400 ms después del último cambio (arrastre, clic en la barra o teclado)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(400)
        self.debounce.timeout.connect(self.on_slider)
        self.slider.valueChanged.connect(lambda x: (self.value.setText(f"{x}%"), self.debounce.start()))
        srow.addWidget(self.slider, 1)
        srow.addWidget(self.value)
        v.addLayout(srow)

    def on_mode(self, mode):
        if mode == "curve":
            self.page.call("set_curve", fan=self.fan, enabled=True)
        else:
            args = {"fan": self.fan, "mode": mode}
            if mode == "custom":
                args["percent"] = self.slider.value()
            self.page.call("set_fan", **args)

    def on_slider(self):
        self.page.call("set_fan", fan=self.fan, mode="custom", percent=self.slider.value())

    def refresh(self, status):
        prof = status["profile"]
        mode = "curve" if prof["curve_enabled"][self.fan] else prof["fans"][self.fan]["mode"]
        self.buttons[mode].setChecked(True)
        caps = status["capabilities"]
        for m, b in self.buttons.items():
            b.setEnabled(caps["fan_modes"] and (m in ("auto", "max") or caps["fan_custom"]))
        self.slider.setEnabled(mode == "custom" and caps["fan_custom"])
        if not self.slider.isSliderDown() and not self.debounce.isActive():
            self.slider.blockSignals(True)
            pct = status["curve_speed"][self.fan] if mode == "curve" else prof["fans"][self.fan]["percent"]
            self.slider.setValue(pct if pct is not None else prof["fans"][self.fan]["percent"])
            self.value.setText(f"{self.slider.value()}%")
            self.slider.blockSignals(False)
        s = status.get("sample") or {}
        rpm = s.get(f"{self.fan}_rpm")
        temp = fmt_temp(s.get(f"{self.fan}_temp"), status["settings"]["temp_unit"])
        self.live.setText(f"{temp}" + (f" · {rpm} RPM" if rpm is not None else ""))


class FanPage(Page):
    def __init__(self, win):
        super().__init__(win, "Control de ventiladores")
        self.backend = label("", "muted")
        self.layout_.addWidget(self.backend)
        self.cards = {fan: FanCard(self, fan) for fan in FANS}
        row = QHBoxLayout()
        for c in self.cards.values():
            row.addWidget(c)
        self.layout_.addLayout(row)

        extra = card()
        g = QGridLayout(extra)
        self.coolboost = QCheckBox("CoolBoost™ (sube la velocidad máxima de los ventiladores)")
        self.coolboost.clicked.connect(lambda on: self.call("set_coolboost", on=on))
        g.addWidget(self.coolboost, 0, 0, 1, 2)
        self.pp_label = QLabel("Perfil de energía (platform_profile):")
        g.addWidget(self.pp_label, 1, 0)
        self.pp = QComboBox()
        self.pp.activated.connect(lambda _: self.call("set_platform_profile", name=self.pp.currentData()))
        g.addWidget(self.pp, 1, 1)
        self.pp_note = label("", "muted")
        g.addWidget(self.pp_note, 2, 0, 1, 2)
        self.layout_.addWidget(extra)

        cc = card()
        v = QVBoxLayout(cc)
        head = QHBoxLayout()
        head.addWidget(label("Curva de ventilador", "h2"))
        head.addStretch()
        self.curve_fan = QComboBox()
        for fan in FANS:
            self.curve_fan.addItem(FAN_NAMES[fan], fan)
        self.curve_fan.currentIndexChanged.connect(lambda _: self.win.refresh_now())
        head.addWidget(self.curve_fan)
        self.copy_btn = QPushButton("Copiar a la otra")
        self.copy_btn.clicked.connect(self.copy_curve)
        head.addWidget(self.copy_btn)
        self.reset_btn = QPushButton("Restablecer")
        self.reset_btn.clicked.connect(self.reset_curve)
        head.addWidget(self.reset_btn)
        v.addLayout(head)
        v.addWidget(label("Arrastra los puntos · doble clic para añadir · clic derecho para borrar. "
                          "Se guarda al soltar y la aplica el servicio aunque cierres esta ventana.", "muted"))
        self.editor = CurveEditor()
        self.editor.changed.connect(self.save_curve)
        v.addWidget(self.editor, 1)
        self.layout_.addWidget(cc, 1)

    @property
    def fan(self):
        return self.curve_fan.currentData()

    def save_curve(self, points):
        prof = self.win.status["profile"]
        self.call("set_curve", fan=self.fan, points=points, enabled=prof["curve_enabled"][self.fan])

    def copy_curve(self):
        other = "gpu" if self.fan == "cpu" else "cpu"
        prof = self.win.status["profile"]
        self.call("set_curve", fan=other, points=self.editor.points, enabled=prof["curve_enabled"][other])

    def reset_curve(self):
        from ..config import DEFAULT_CURVE
        self.save_curve(DEFAULT_CURVE)

    def refresh(self, status):
        caps = status["capabilities"]
        prof = status["profile"]
        names = {"hwmon": "driver acer-wmi del kernel", "wmi": "WMI (acpi_call)", "ec": "EC directo (ec_sys)"}
        fb = caps["fan_backend"]
        self.backend.setText("Método de control: " + (names.get(fb, fb) if fb else "ninguno disponible"))
        for c in self.cards.values():
            c.refresh(status)
        self.coolboost.setEnabled(caps["coolboost"])
        self.coolboost.setChecked(prof["coolboost"])
        choices = caps["platform_profile"]
        self.pp.blockSignals(True)
        self.pp.clear()
        for ch in choices:
            self.pp.addItem(PP_LABELS.get(ch, ch), ch)
        cur = status["platform_profile"]["current"]
        if cur in choices:
            self.pp.setCurrentIndex(choices.index(cur))
        self.pp.blockSignals(False)
        # Sin soporte (como en el PH315-51) se oculta en vez de mostrar un selector vacío
        for w in (self.pp_label, self.pp):
            w.setVisible(bool(choices))
        self.pp_note.setVisible(False)
        self.editor.interpolate = status["settings"]["curve_interpolate"]
        self.editor.set_points(prof["curves"][self.fan])
        s = status.get("sample") or {}
        t = s.get("gpu_temp") if self.fan == "gpu" and s.get("gpu_temp") is not None else s.get("cpu_temp")
        self.editor.set_current_temp(t)


# --- Overclock -----------------------------------------------------------------
OC_LABELS = [("normal", "Normal"), ("faster", "Más rápido"), ("turbo", "Turbo"), ("custom", "Personalizado")]


class OverclockPage(Page):
    def __init__(self, win):
        super().__init__(win, "Overclock")
        c = card()
        v = QVBoxLayout(c)
        head = QHBoxLayout()
        head.addWidget(label("GPU", "h2"))
        head.addStretch()
        self.state = label("", "muted")
        self.state.setWordWrap(False)
        head.addWidget(self.state)
        v.addLayout(head)
        row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.buttons = {}
        for lvl, text in OC_LABELS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setMinimumHeight(44)
            self.group.addButton(b)
            self.buttons[lvl] = b
            b.clicked.connect(lambda _, l=lvl: self.set_level(l))
            row.addWidget(b)
        v.addLayout(row)
        self.preset = label("", "muted")
        self.preset.setWordWrap(False)
        v.addWidget(self.preset)
        v.addWidget(label("Solo se aplica con el cargador conectado; en batería se vuelve a Normal "
                          "automáticamente.", "warn"))
        self.layout_.addWidget(c)

        cc = card()
        g = QGridLayout(cc)
        g.addWidget(label("GPU personalizado", "h2"), 0, 0, 1, 3)
        self.sliders = {}
        for r, (key, text) in enumerate((("core", "Núcleo"), ("mem", "Memoria")), start=1):
            g.addWidget(QLabel(text), r, 0)
            s = QSlider(Qt.Orientation.Horizontal)
            s.setSingleStep(5)
            s.setPageStep(25)
            val = QLabel("+0 MHz")
            val.setMinimumWidth(80)
            s.valueChanged.connect(lambda x, val=val: val.setText(f"{x:+d} MHz"))
            g.addWidget(s, r, 1)
            g.addWidget(val, r, 2)
            self.sliders[key] = s
        self.apply_btn = QPushButton("Aplicar personalizado")
        self.apply_btn.setProperty("class", "primary")
        self.apply_btn.clicked.connect(lambda: self.set_level("custom"))
        g.addWidget(self.apply_btn, 3, 2)
        self.layout_.addWidget(cc)
        self.info = label("", "muted")
        ic = card()
        QVBoxLayout(ic).addWidget(self.info)
        self.layout_.addWidget(ic)
        self.cpu_card = card()
        cg = QGridLayout(self.cpu_card)
        cg.addWidget(label("Procesador", "h2"), 0, 0, 1, 3)
        self.turbo = QCheckBox("Intel Turbo Boost")
        self.turbo.clicked.connect(lambda on: self.call("set_cpu", turbo=on))
        cg.addWidget(self.turbo, 1, 0, 1, 3)
        cg.addWidget(QLabel("Frecuencia máxima"), 2, 0)
        self.cpu_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_slider.setPageStep(2)
        self.cpu_value = QLabel("")
        self.cpu_value.setMinimumWidth(100)
        self.cpu_debounce = QTimer(self)
        self.cpu_debounce.setSingleShot(True)
        self.cpu_debounce.setInterval(400)
        self.cpu_debounce.timeout.connect(self.send_cpu_limit)
        self.cpu_slider.valueChanged.connect(self.on_cpu_slider)
        cg.addWidget(self.cpu_slider, 2, 1)
        cg.addWidget(self.cpu_value, 2, 2)
        self.cpu_note = label("", "muted")
        cg.addWidget(self.cpu_note, 3, 0, 1, 3)
        self.uv_label = QLabel("Undervolt (núcleo y caché)")
        cg.addWidget(self.uv_label, 4, 0)
        self.uv_slider = QSlider(Qt.Orientation.Horizontal)
        self.uv_slider.setSingleStep(5)
        self.uv_slider.setPageStep(25)
        self.uv_value = QLabel("0 mV")
        self.uv_slider.valueChanged.connect(lambda x: self.uv_value.setText(f"{self.uv_snapped(x)} mV"))
        self.uv_slider.actionTriggered.connect(lambda _: setattr(self, "_uv_dirty", True))
        cg.addWidget(self.uv_slider, 4, 1)
        cg.addWidget(self.uv_value, 4, 2)
        self.uv_apply = QPushButton("Aplicar undervolt")
        self.uv_apply.setProperty("class", "primary")
        self.uv_apply.clicked.connect(self.send_undervolt)
        cg.addWidget(self.uv_apply, 5, 2)
        self.uv_state = label("", "muted")
        cg.addWidget(self.uv_state, 5, 0, 1, 2)
        self._uv_dirty = False
        self.layout_.addWidget(self.cpu_card)
        self.layout_.addStretch()
        self._loaded = False
        self._cpu_max = 41

    def show_cpu_value(self, value):
        self.cpu_value.setText("Sin límite" if value >= self._cpu_max else f"{value * 100} MHz")

    def on_cpu_slider(self, value):
        self.show_cpu_value(value)
        self.cpu_debounce.start()

    def send_cpu_limit(self):
        value = self.cpu_slider.value()
        self.call("set_cpu", max_mhz=None if value >= self._cpu_max else value * 100)

    @staticmethod
    def uv_snapped(value):
        return -(abs(value) // 5 * 5)

    def send_undervolt(self):
        self._uv_dirty = False
        self.call("set_undervolt", mv=self.uv_snapped(self.uv_slider.value()))

    def refresh_undervolt(self, status):
        uv = status.get("undervolt")
        for w in (self.uv_label, self.uv_slider, self.uv_value, self.uv_apply, self.uv_state):
            w.setVisible(uv is not None)
        if not uv:
            return
        self.uv_slider.setRange(uv["min_mv"], 0)
        if not self._uv_dirty and not self.uv_slider.isSliderDown():
            self.uv_slider.setValue(uv["target"])
        if uv["error"]:
            text = uv["error"]
        elif uv["skipped"]:
            text = "El equipo se detuvo justo después de aplicarlo: no se aplicó al arrancar. Prueba un valor menor."
        else:
            text = f"Aplicado: {uv['applied']} mV" + ("   ·   Tope liberado en Ajustes" if uv["unlocked"] else
                                                      f"   ·   Tope seguro: {uv['min_mv']} mV")
        self.uv_state.setText(text)

    def refresh_cpu(self, status):
        self.refresh_undervolt(status)
        cpu = status.get("cpu")
        self.cpu_card.setVisible(cpu is not None)
        if not cpu:
            return
        self.turbo.setChecked(cpu["turbo"])
        self._cpu_max = cpu["max_mhz"] // 100
        if not self.cpu_slider.isSliderDown() and not self.cpu_debounce.isActive():
            limit = status["profile"]["cpu"]["max_mhz"]
            self.cpu_slider.blockSignals(True)
            self.cpu_slider.setRange(cpu["min_mhz"] // 100, self._cpu_max)
            self.cpu_slider.setValue(self._cpu_max if limit is None else limit // 100)
            self.cpu_slider.blockSignals(False)
            self.show_cpu_value(self.cpu_slider.value())
        self.cpu_note.setText(
            (f"Con Turbo Boost desactivado, la CPU no pasa de {cpu['base_mhz']} MHz. " if not cpu["turbo"] else "")
            + "Este procesador tiene el multiplicador bloqueado: solo se puede bajar su frecuencia.")

    def set_level(self, level):
        args = {"level": level}
        if level == "custom":
            args.update(core=self.sliders["core"].value(), mem=self.sliders["mem"].value())
        self.call("set_gpu_oc", **args)

    def refresh(self, status):
        self.refresh_cpu(status)
        caps = status["capabilities"]
        oc = status.get("gpu_oc") or {}
        prof = status["profile"]["gpu_oc"]
        enabled = bool(caps.get("gpu_oc"))
        for b in self.buttons.values():
            b.setEnabled(enabled)
        self.buttons[prof["level"]].setChecked(True)
        limits = oc.get("limits") or {"core": (-200, 250), "mem": (-500, 800)}
        if not self._loaded:
            for key, s in self.sliders.items():
                s.setRange(*limits[key])
                s.setValue(prof[key])
            self._loaded = True
        for s in self.sliders.values():
            s.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        levels = oc.get("levels") or {}
        self.preset.setText("Núcleo / memoria en MHz   ·   " + "   ·   ".join(
            f"{t}: {levels[l][0]:+d} / {levels[l][1]:+d}" for l, t in OC_LABELS if l in levels and l != "normal"))
        smp = status.get("sample") or {}
        applied = oc.get("applied")
        if not enabled:
            self.state.setText("No disponible (sin driver NVIDIA/NVML)")
        elif smp.get("gpu_state") != "active":
            self.state.setText("GPU en reposo: se aplica al despertar")
        elif smp.get("on_ac") is False:
            self.state.setText("En batería: overclock desactivado")
        elif applied:
            self.state.setText(f"Aplicado: núcleo {applied[0]:+d} / memoria {applied[1]:+d} MHz")
        else:
            self.state.setText("Pendiente")
        info = oc.get("info") or {}
        lines = []
        if smp.get("gpu_clock"):
            lines.append(f"Reloj actual: {smp['gpu_clock']:.0f} MHz · temperatura {fmt_temp(smp.get('gpu_temp'), status['settings']['temp_unit'])}")
        if info.get("max_core_clock"):
            lines.append(f"Máximos del driver: núcleo {info['max_core_clock']} MHz, memoria {info['max_mem_clock']} MHz")
        if info.get("core_range"):
            lines.append(f"Rango que acepta el driver: núcleo {info['core_range'][0]}…{info['core_range'][1]}, "
                         f"memoria {info['mem_range'][0]}…{info['mem_range'][1]} MHz")
        if oc.get("error"):
            lines.append("Error: " + oc["error"])
        self.info.setText("\n".join(lines) or "Los datos del driver aparecen cuando la GPU está activa.")


# --- Perfiles ----------------------------------------------------------------
class ProfilesPage(Page):
    def __init__(self, win):
        super().__init__(win, "Perfiles",
                         "Cada perfil guarda ventiladores, curvas, CoolBoost, energía, teclado y pantalla. "
                         "El activo se aplica al arrancar y al volver de suspensión.")
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: self.select(it.data(Qt.ItemDataRole.UserRole)))
        row.addWidget(self.list, 1)
        col = QVBoxLayout()
        for text, fn in (("Aplicar", lambda: self.select(self.current())), ("Guardar actual como…", self.save_as),
                         ("Renombrar…", self.rename), ("Borrar", self.delete), (None, None),
                         ("Importar…", self.import_), ("Exportar…", self.export)):
            if text is None:
                col.addSpacing(16)
                continue
            b = QPushButton(text)
            b.clicked.connect(fn)
            col.addWidget(b)
        col.addStretch()
        row.addLayout(col)
        self.layout_.addLayout(row, 1)

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def select(self, name):
        if name:
            self.call("profile_select", name=name)

    def save_as(self):
        name, ok = QInputDialog.getText(self, "Nuevo perfil", "Nombre:")
        if ok and name.strip():
            self.call("profile_save_as", name=name.strip())

    def rename(self):
        old = self.current()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Renombrar perfil", "Nuevo nombre:", text=old)
        if ok and new.strip() and new.strip() != old:
            self.call("profile_rename", old=old, new=new.strip())

    def delete(self):
        name = self.current()
        if name and QMessageBox.question(self, "Borrar perfil", f"¿Borrar el perfil «{name}»?") == \
                QMessageBox.StandardButton.Yes:
            self.call("profile_delete", name=name)

    def import_(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar perfil", "", "Perfil (*.json)")
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Importar", f"No se pudo leer el archivo: {e}")
            return
        name = data.get("name") or path.rsplit("/", 1)[-1].removesuffix(".json")
        if name in self.win.status["profiles"]:
            name, ok = QInputDialog.getText(self, "Importar", "Ya existe un perfil con ese nombre. Nuevo nombre:",
                                            text=name + " (importado)")
            if not ok:
                return
        self.call("profile_import", name=name, profile=data.get("profile", data))

    def export(self):
        name = self.current() or self.win.status["active_profile"]
        data = self.call("profile_export", name=name)
        if not data:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar perfil", f"{name}.json", "Perfil (*.json)")
        if path:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def refresh(self, status):
        names = status["profiles"]
        current = self.current()
        if [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())] != names or \
                getattr(self, "_active", None) != status["active_profile"]:
            self._active = status["active_profile"]
            self.list.clear()
            for n in names:
                it = QListWidgetItem(("●  " if n == self._active else "     ") + n)
                it.setData(Qt.ItemDataRole.UserRole, n)
                self.list.addItem(it)
                if n == (current or self._active):
                    self.list.setCurrentItem(it)


# --- Ajustes -----------------------------------------------------------------
class SettingsPage(Page):
    def __init__(self, win):
        super().__init__(win, "Ajustes")
        c = card()
        g = QGridLayout(c)
        r = 0
        self.autostart = QCheckBox("Abrir PredatorSense al iniciar sesión (minimizado en la bandeja)")
        self.autostart.setChecked(autostart_enabled())
        self.autostart.clicked.connect(self.toggle_autostart)
        g.addWidget(self.autostart, r, 0, 1, 2); r += 1
        self.boot = QCheckBox("Aplicar el perfil activo al iniciar el sistema (lo hace el servicio, sin abrir la ventana)")
        self.boot.clicked.connect(lambda on: self.set(apply_on_boot=on))
        g.addWidget(self.boot, r, 0, 1, 2); r += 1
        self.win_key = QCheckBox("Tecla Windows activada (al desactivarla, Fn+Play/Pausa del teclado interno tampoco funciona)")
        self.win_key.clicked.connect(lambda on: self.call("set_win_key", enabled=on))
        g.addWidget(self.win_key, r, 0, 1, 2); r += 1
        g.addWidget(QLabel("Unidad de temperatura"), r, 0)
        self.unit = QComboBox()
        self.unit.addItem("Celsius (°C)", "C")
        self.unit.addItem("Fahrenheit (°F)", "F")
        self.unit.activated.connect(lambda _: self.set(temp_unit=self.unit.currentData()))
        g.addWidget(self.unit, r, 1); r += 1
        g.addWidget(QLabel("Histéresis de la curva (°C)"), r, 0)
        self.hyst = QSpinBox()
        self.hyst.setRange(0, 15)
        self.hyst.editingFinished.connect(lambda: self.set(curve_hysteresis=self.hyst.value()))
        g.addWidget(self.hyst, r, 1); r += 1
        g.addWidget(QLabel("Intervalo de la curva (s)"), r, 0)
        self.interval = QSpinBox()
        self.interval.setRange(1, 30)
        self.interval.editingFinished.connect(lambda: self.set(curve_interval=self.interval.value()))
        g.addWidget(self.interval, r, 1); r += 1
        self.interp = QCheckBox("Interpolar entre puntos (si no, escalones como en Windows)")
        self.interp.clicked.connect(lambda on: self.set(curve_interpolate=on))
        g.addWidget(self.interp, r, 0, 1, 2); r += 1
        g.addWidget(QLabel("La curva de la GPU usa la temperatura de"), r, 0)
        self.src = QComboBox()
        self.src.addItem("GPU (CPU si está en reposo)", "gpu")
        self.src.addItem("CPU", "cpu")
        self.src.activated.connect(lambda _: self.set(gpu_curve_source=self.src.currentData()))
        g.addWidget(self.src, r, 1); r += 1
        self.layout_.addWidget(c)
        self.uv_unlock = QCheckBox("Liberar el tope de undervolt (bajo tu propio riesgo)")
        self.uv_unlock.clicked.connect(self.toggle_uv_unlock)
        g.addWidget(self.uv_unlock, r, 0, 1, 2); r += 1
        self.usb_card = card()
        u = QGridLayout(self.usb_card)
        self.usb = QCheckBox("USB con carga en estado apagado")
        self.usb.setToolTip("Carga dispositivos por el puerto USB designado aunque la laptop "
                            "esté apagada o en hibernación, también con batería.")
        self.usb.clicked.connect(lambda _: self.set_usb())
        u.addWidget(self.usb, 0, 0, 1, 2)
        self.usb_label = QLabel("Con alimentación de batería, detener la carga cuando la batería alcance:")
        self.usb_label.setWordWrap(True)
        u.addWidget(self.usb_label, 1, 0)
        self.usb_level = QComboBox()
        for pct in (10, 20, 30):
            self.usb_level.addItem(f"{pct}%", pct)
        self.usb_level.activated.connect(lambda _: self.set_usb())
        u.addWidget(self.usb_level, 1, 1)
        self.layout_.addWidget(self.usb_card)
        self.caps = label("", "muted")
        cc = card()
        QVBoxLayout(cc).addWidget(self.caps)
        self.layout_.addWidget(cc)
        self.layout_.addStretch()

    def set(self, **kw):
        self.call("settings_update", settings=kw)

    def toggle_uv_unlock(self, on):
        if on and QMessageBox.warning(
                self, "Liberar el tope de undervolt",
                "Sin el tope de -125 mV podrás bajar hasta -250 mV. Valores así suelen congelar o reiniciar "
                "el equipo, y pueden provocar pérdida de datos si ocurre mientras se guarda algo.\n\n"
                "Lo activas bajo tu propio riesgo. ¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            self.uv_unlock.setChecked(False)
            return
        self.call("set_undervolt_unlocked", enabled=on)

    def set_usb(self):
        self.call("set_usb_charge", enabled=self.usb.isChecked(), threshold=self.usb_level.currentData())

    def toggle_autostart(self, on):
        try:
            set_autostart(on)
            self.win.statusBar().showMessage("Inicio automático " + ("activado" if on else "desactivado"), 3000)
        except OSError as e:
            self.autostart.setChecked(autostart_enabled())
            QMessageBox.warning(self, "Inicio automático", f"No se pudo cambiar: {e}")

    def refresh(self, status):
        s = status["settings"]
        for w in (self.boot, self.unit, self.hyst, self.interval, self.interp, self.src):
            w.blockSignals(True)
        self.boot.setChecked(s["apply_on_boot"])
        self.unit.setCurrentIndex(0 if s["temp_unit"] == "C" else 1)
        if not self.hyst.hasFocus():
            self.hyst.setValue(int(s["curve_hysteresis"]))
        if not self.interval.hasFocus():
            self.interval.setValue(int(s["curve_interval"]))
        self.interp.setChecked(s["curve_interpolate"])
        self.src.setCurrentIndex(0 if s["gpu_curve_source"] == "gpu" else 1)
        for w in (self.boot, self.unit, self.hyst, self.interval, self.interp, self.src):
            w.blockSignals(False)
        uv = status.get("undervolt")
        self.uv_unlock.setVisible(uv is not None)
        if uv:
            self.uv_unlock.setChecked(uv["unlocked"])
        self.win_key.setEnabled(status["capabilities"]["win_key"])
        self.win_key.setChecked(s["win_key"])
        usb = status.get("usb_charge")
        self.usb_card.setVisible(usb is not None)
        if usb:
            self.usb.setChecked(usb["enabled"])
            self.usb_level.setCurrentIndex(self.usb_level.findData(usb["threshold"]))
            for w in (self.usb_label, self.usb_level):
                w.setVisible(usb["enabled"])
        caps = status["capabilities"]
        yn = lambda b: "sí" if b else "no"  # noqa: E731
        self.caps.setText(
            f"Modelo: {status.get('model')} · servicio {status['version']}\n"
            f"Backend de ventiladores: {caps['fan_backend']}\n"
            f"hwmon acer-wmi: {yn(caps['hwmon'])} · acpi_call: {yn(caps['acpi_call'])} · EC: {yn(caps['ec'])}\n"
            f"RPM: {yn(caps['fan_rpm'])} · CoolBoost: {yn(caps['coolboost'])} · Tecla Windows: {yn(caps['win_key'])}"
            "\n"
            f"Configuración: /etc/predatorsense-linux/config.json")


# --- Ventana -----------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, icon):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(1080, 760)
        self.status = None
        self.tray = None
        self.history = []
        root = QWidget()
        root.setObjectName("root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        self.stack = QStackedWidget()
        self.offline = QWidget()
        ov = QVBoxLayout(self.offline)
        ov.addStretch()
        self.offline_msg = label("", "h2")
        self.offline_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov.addWidget(self.offline_msg)
        hint = label("Instálalo con  sudo ./install.sh  o arráncalo con  "
                     "sudo systemctl enable --now predatorsensed", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov.addWidget(hint)
        ov.addStretch()
        self.pages = [MonitorPage(self), FanPage(self), OverclockPage(self), ProfilesPage(self),
                      SettingsPage(self)]
        for name, page in zip(("Supervisión", "Ventiladores", "Overclock", "Perfiles", "Ajustes"), self.pages):
            self.nav.addItem(name)
            self.stack.addWidget(page)
        self.stack.addWidget(self.offline)
        self.nav.currentRowChanged.connect(self.on_nav)
        h.addWidget(self.nav)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(2000)
        self.load_history()
        self.poll()

    def on_nav(self, row):
        if self.status:
            self.stack.setCurrentIndex(row)
            self.pages[row].refresh(self.status)

    def load_history(self):
        try:
            self.history = request("history")["history"]
        except ServiceError:
            self.history = []

    def call(self, cmd, **args):
        try:
            data = request(cmd, **args)
        except ServiceError as e:
            self.statusBar().showMessage(f"Error: {e}", 8000)
            self.refresh_now()
            return None
        if isinstance(data, dict) and "capabilities" in data:
            self.apply_status(data)
        self.statusBar().showMessage("Guardado", 2000)
        return data

    def refresh_now(self):
        if self.status:
            self.pages[self.nav.currentRow()].refresh(self.status)

    def poll(self):
        try:
            st = request("status")
        except ServiceUnavailable as e:
            self.status = None
            self.offline_msg.setText(str(e))
            self.stack.setCurrentWidget(self.offline)
            return
        except ServiceError as e:
            self.statusBar().showMessage(f"Error: {e}", 5000)
            return
        smp = st.get("sample")
        if smp and (not self.history or self.history[-1].get("time") != smp.get("time")):
            self.history.append(smp)
            self.history = self.history[-180:]
        was_offline = self.status is None
        self.apply_status(st)
        if was_offline:
            self.stack.setCurrentIndex(self.nav.currentRow())

    def update_tray_tooltip(self, st):
        if not self.tray:
            return
        s = st.get("sample") or {}
        unit = st["settings"]["temp_unit"]
        def line(name, key):
            rpm = s.get(f"{key}_rpm")
            speed = f"{rpm} RPM" if rpm is not None else "— RPM"
            if key == "gpu" and s.get("gpu_state") != "active":
                return f"{name}: en reposo · {speed}"
            usage = s.get(f"{key}_usage")
            pct = f"{usage:.0f}%" if usage is not None else "—"
            return f"{name}: {fmt_temp(s.get(f'{key}_temp'), unit)} · uso {pct} · {speed}"
        self.tray.setToolTip(line("CPU", "cpu") + "\n" + line("GPU", "gpu"))

    def apply_status(self, st):
        self.status = st
        self.update_tray_tooltip(st)
        if self.stack.currentWidget() is self.offline:
            self.stack.setCurrentIndex(self.nav.currentRow())
        self.pages[self.nav.currentRow()].refresh(st)
        errs = st.get("errors") or []
        if errs:
            self.statusBar().showMessage("Aviso del servicio: " + errs[-1]["message"], 6000)


AUTOSTART_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                              "autostart", "predatorsense-linux.desktop")
AUTOSTART_ENTRY = """[Desktop Entry]
Type=Application
Name=PredatorSense Linux
Comment=Ventiladores y ajustes de Acer Predator (en la bandeja)
Exec=predatorsense --hidden
Icon=predatorsense-linux
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
"""


def autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


def set_autostart(on):
    if on:
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        with open(AUTOSTART_FILE, "w") as f:
            f.write(AUTOSTART_ENTRY)
    elif os.path.exists(AUTOSTART_FILE):
        os.remove(AUTOSTART_FILE)


def make_icon():
    from pathlib import Path
    icon = QIcon()
    local = Path(__file__).resolve().parent.parent.parent / "data" / "icons"
    for size in (16, 32, 48, 64, 128, 256):
        for p in (local / f"predatorsense-linux-{size}.png",
                  Path(f"/usr/share/icons/hicolor/{size}x{size}/apps/predatorsense-linux.png")):
            if p.exists():
                icon.addFile(str(p))
                break
    return icon if not icon.isNull() else QIcon.fromTheme("preferences-system")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("predatorsense-linux")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon = make_icon()
    win = MainWindow(icon)
    if QSystemTrayIcon.isSystemTrayAvailable() and "--no-tray" not in sys.argv:
        tray = QSystemTrayIcon(icon, app)
        menu = QMenu()
        menu.addAction(QAction("Abrir", menu, triggered=lambda: (win.show(), win.raise_())))
        for mode, text in (("auto", "Ventiladores: Auto"), ("max", "Ventiladores: Máx")):
            menu.addAction(QAction(text, menu, triggered=lambda _=False, m=mode: [
                win.call("set_fan", fan=f, mode=m) for f in FANS]))
        menu.addSeparator()
        menu.addAction(QAction("Salir", menu, triggered=app.quit))
        tray.setContextMenu(menu)
        tray.setToolTip(APP_NAME)
        tray.activated.connect(lambda r: (win.show(), win.raise_()) if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        app.tray = win.tray = tray
        if win.status:
            win.update_tray_tooltip(win.status)
        app.setQuitOnLastWindowClosed(False)  # cerrar la ventana la deja en la bandeja
    if "--hidden" not in sys.argv or not getattr(app, "tray", None):
        win.show()   # sin bandeja disponible, --hidden dejaría la app invisible
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

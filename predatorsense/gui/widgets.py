"""Widgets dibujados a mano: gráfica de monitoreo y editor de curva."""

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from ..curve import evaluate

ACCENT = QColor("#19c3ff")
ACCENT2 = QColor("#ff4d5e")
GRID = QColor(255, 255, 255, 28)
TEXT = QColor("#9aa4b2")


class LineChart(QWidget):
    """Gráfica de líneas simple con varias series y escala fija."""

    def __init__(self, title, unit, y_max=100, parent=None):
        super().__init__(parent)
        self.title, self.unit, self.y_max = title, unit, y_max
        self.series = {}   # nombre -> (color, [valores])
        self.setMinimumHeight(150)

    def set_series(self, name, color, values):
        self.series[name] = (QColor(color), list(values))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(38, 26, -10, -18)
        p.setPen(TEXT)
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        p.drawText(QRectF(0, 2, self.width(), 20), Qt.AlignmentFlag.AlignLeft, "  " + self.title)
        # Leyenda
        x = self.width() - 10
        for name, (color, values) in reversed(list(self.series.items())):
            last = next((v for v in reversed(values) if v is not None), None)
            label = f"{name} {'—' if last is None else f'{last:.0f}{self.unit}'}"
            w = p.fontMetrics().horizontalAdvance(label) + 18
            x -= w
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(x + 5, 12), 4, 4)
            p.setPen(TEXT)
            p.drawText(QPointF(x + 12, 16), label)
        # Rejilla
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(TEXT)
            p.drawText(QRectF(0, y - 8, 34, 16), Qt.AlignmentFlag.AlignRight, f"{self.y_max * i / 4:.0f}")
        # Series
        for color, values in self.series.values():
            n = len(values)
            if n < 2:
                continue
            path, started = QPainterPath(), False
            for i, v in enumerate(values):
                if v is None:
                    started = False
                    continue
                pt = QPointF(r.left() + r.width() * i / (n - 1),
                             r.bottom() - r.height() * min(v, self.y_max) / self.y_max)
                if started:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    started = True
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)


class CurveEditor(QWidget):
    """Editor de curva: arrastrar puntos, doble clic para añadir, clic derecho para borrar."""

    changed = pyqtSignal(list)
    T_MIN, T_MAX = 20, 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = [[40, 20], [90, 100]]
        self.current_temp = None
        self.drag = None
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)
        self.interpolate = True
        self.setToolTip("Arrastra los puntos · doble clic: añadir · clic derecho: borrar")

    def set_points(self, points):
        if self.drag is None:
            self.points = [list(p) for p in sorted(points)]
            self.update()

    def set_current_temp(self, t):
        self.current_temp = t
        self.update()

    def _area(self):
        return QRectF(self.rect()).adjusted(44, 14, -16, -30)

    def _to_px(self, t, s):
        r = self._area()
        return QPointF(r.left() + r.width() * (t - self.T_MIN) / (self.T_MAX - self.T_MIN),
                       r.bottom() - r.height() * s / 100)

    def _from_px(self, pos):
        r = self._area()
        t = self.T_MIN + (pos.x() - r.left()) / r.width() * (self.T_MAX - self.T_MIN)
        s = (r.bottom() - pos.y()) / r.height() * 100
        return round(max(self.T_MIN, min(self.T_MAX, t))), round(max(0, min(100, s)))

    def _hit(self, pos):
        for i, (t, s) in enumerate(self.points):
            if (self._to_px(t, s) - pos).manhattanLength() < 14:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if e.button() == Qt.MouseButton.RightButton and i is not None and len(self.points) > 2:
            del self.points[i]
            self.update()
            self.changed.emit(self.points)
        elif e.button() == Qt.MouseButton.LeftButton:
            self.drag = i

    def mouseDoubleClickEvent(self, e):
        if self._hit(e.position()) is None and len(self.points) < 12:
            t, s = self._from_px(e.position())
            if all(p[0] != t for p in self.points):
                self.points.append([t, s])
                self.points.sort()
                self.update()
                self.changed.emit(self.points)

    def mouseMoveEvent(self, e):
        if self.drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hit(e.position()) is not None
                           else Qt.CursorShape.ArrowCursor)
            return
        t, s = self._from_px(e.position())
        lo = self.points[self.drag - 1][0] + 1 if self.drag > 0 else self.T_MIN
        hi = self.points[self.drag + 1][0] - 1 if self.drag < len(self.points) - 1 else self.T_MAX
        self.points[self.drag] = [max(lo, min(hi, t)), s]
        self.update()

    def mouseReleaseEvent(self, _):
        if self.drag is not None:
            self.drag = None
            self.changed.emit(self.points)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._area()
        p.setPen(TEXT)
        for s in range(0, 101, 20):
            y = self._to_px(self.T_MIN, s).y()
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(TEXT)
            p.drawText(QRectF(0, y - 8, 38, 16), Qt.AlignmentFlag.AlignRight, f"{s}%")
        for t in range(self.T_MIN, self.T_MAX + 1, 10):
            x = self._to_px(t, 0).x()
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(TEXT)
            p.drawText(QRectF(x - 20, r.bottom() + 6, 40, 16), Qt.AlignmentFlag.AlignCenter, f"{t}°")
        # Curva evaluada
        path = QPainterPath()
        if self.interpolate:
            xs = sorted({self.T_MIN, self.T_MAX, *(min(self.T_MAX, max(self.T_MIN, t)) for t, _ in self.points)})
        else:  # escalones: dos vértices por punto
            xs = [self.T_MIN]
            for t, _ in self.points:
                if self.T_MIN < t <= self.T_MAX:
                    xs += [t - 0.001, t]
            xs.append(self.T_MAX)
        for i, t in enumerate(xs):
            pt = self._to_px(t, evaluate(self.points, t, self.interpolate, exact=True))
            path.lineTo(pt) if i else path.moveTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(self._to_px(self.T_MAX, 0))
        fill.lineTo(self._to_px(self.T_MIN, 0))
        c = QColor(ACCENT)
        c.setAlpha(40)
        p.fillPath(fill, c)
        p.setPen(QPen(ACCENT, 2.5))
        p.drawPath(path)
        # Temperatura actual
        if self.current_temp is not None:
            t = max(self.T_MIN, min(self.T_MAX, self.current_temp))
            x = self._to_px(t, 0).x()
            p.setPen(QPen(ACCENT2, 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            sp = evaluate(self.points, t, self.interpolate)
            p.setPen(ACCENT2)
            p.drawText(QPointF(min(x + 6, r.right() - 70), r.top() + 14), f"{self.current_temp:.0f}° → {sp}%")
        # Puntos
        for t, s in self.points:
            pt = self._to_px(t, s)
            p.setPen(QPen(QColor("#0d1117"), 2))
            p.setBrush(ACCENT)
            p.drawEllipse(pt, 6, 6)

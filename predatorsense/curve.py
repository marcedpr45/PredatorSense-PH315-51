"""Curva de ventilador temperatura -> porcentaje.

Mejoras respecto a la versión Windows: allí la curva era escalonada, solo
miraba la temperatura de la CPU y aplicaba el mismo % a la GPU. Aquí cada
ventilador tiene su curva y su sensor, se interpola linealmente y hay
histéresis para que el ventilador no suba y baje sin parar.
"""


def evaluate(points, temp, interpolate=True, exact=False):
    pts = sorted(points)
    if not interpolate:
        # Escalones como en Windows: la velocidad del último punto alcanzado
        return next((s for t, s in reversed(pts) if temp >= t), pts[0][1])
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if not interpolate:
                return s0
            value = s0 + (s1 - s0) * (temp - t0) / (t1 - t0)
            return value if exact else round(value)
    return pts[-1][1]


class CurveController:
    """Aplica la curva con histéresis y filtrado de lecturas erróneas."""

    MAX_JUMP = 20       # °C: un salto mayor necesita confirmarse con una segunda lectura
    MIN_STEP = 2        # %: no se reescribe la velocidad por cambios menores

    def __init__(self, hysteresis=3, interpolate=True):
        self.hysteresis = hysteresis
        self.interpolate = interpolate
        self.last_temp = None
        self.pending = None
        self.applied = None
        self.peak_temp = None   # temperatura que justificó la velocidad actual

    def reset(self):
        self.last_temp = self.pending = self.applied = self.peak_temp = None

    def stable(self, temp):
        if temp is None or not 1 <= temp <= 110:
            return None
        if self.last_temp is not None and abs(temp - self.last_temp) > self.MAX_JUMP:
            if self.pending is not None and abs(self.pending - temp) <= 5:
                self.pending = None
                self.last_temp = temp
                return temp
            self.pending = temp
            return None
        self.pending = None
        self.last_temp = temp
        return temp

    def target(self, points, temp):
        """Devuelve el % a aplicar, o None si no hay que cambiar nada."""
        temp = self.stable(temp)
        if temp is None:
            return None
        if self.applied is None:
            speed = evaluate(points, temp, self.interpolate)
            self.peak_temp = temp
        elif temp >= self.peak_temp:
            speed = evaluate(points, temp, self.interpolate)
            self.peak_temp = temp
        elif temp <= self.peak_temp - self.hysteresis:
            # Bajó lo suficiente: se evalúa con la temperatura + histéresis (más conservador)
            speed = evaluate(points, temp + self.hysteresis, self.interpolate)
            self.peak_temp = temp + self.hysteresis
            if speed > self.applied:
                speed = self.applied
        else:
            return None
        if self.applied is not None and abs(speed - self.applied) < self.MIN_STEP and speed not in (0, 100):
            return None
        if speed == self.applied:
            return None
        self.applied = speed
        return speed

"""
sensor_plot_widget.py — PyQtGraph chart widget for the EN 14055 Cistern Analytics app.

Provides SensorPlotWidget(pg.PlotWidget) with:
  • 5 plot series  : sensor data, MWL, Meniscus, Water Discharge, CWL
  • 4 horizontal InfiniteLines for limit levels (draggable in manual mode)
  • Hover crosshair + snapped value tooltip
  • Plot modes   : Height (mm) | Volume (L) | Flow Rate (L/s)
  • Smoothing    : None | SMA-3/5/10 | EMA-Fast | EMA-Slow
  • Auto-scroll  : 10 s / 30 s / 60 s / 5 min windows
  • Pause / resume
  • Manual MWL/CWL click selection (emits Qt signals)

Shared by sensor_app_qt.py; has no dependency on sensor_app.py or DearPyGui.
"""
from __future__ import annotations

import bisect
import collections
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QColor, QFont

from sensor_core import smooth, SensorApp, DEFAULT_LINE_COLORS


# ── Colour helpers ───────────────────────────────────────────────────────────

def _rgba(rgba_list: list[int]) -> pg.mkColor:
    """Convert [R,G,B,A] list to a QColor accepted by pyqtgraph."""
    r, g, b, a = rgba_list
    return QColor(r, g, b, a)


# ── Plot widget ───────────────────────────────────────────────────────────────

class SensorPlotWidget(pg.PlotWidget):
    """Self-contained pyqtgraph chart for cistern water level / volume / flow.

    Signals
    -------
    mwl_clicked(float)   — emitted when user clicks chart in MWL selection mode
                           (value = height mm at click moment, averaged ±0.5 s)
    cwl_clicked(float)   — emitted when user clicks chart in CWL selection mode
    """

    mwl_clicked = Signal(float)
    cwl_clicked  = Signal(float)

    # Time-window options shown in the combo box
    WINDOW_OPTIONS: dict[str, Optional[int]] = {
        "10 s":  10,
        "30 s":  30,
        "60 s":  60,
        "5 min": 300,
        "All":   None,
    }

    def __init__(self, app: SensorApp, parent=None):
        super().__init__(parent=parent, background=None)
        self._app = app
        self._paused = False
        self._mode = "Height (mm)"          # current plot mode
        self._smooth_alg = "SMA-5"          # current smoothing algorithm
        self._auto_scroll = True
        self._window_key = "30 s"           # key into WINDOW_OPTIONS
        self._manual_select: Optional[str] = None  # "MWL" | "CWL" | None
        self._last_t: list[float] = []
        self._last_y: list[float] = []

        self._setup_plot()
        self._setup_curves()
        self._setup_limit_lines()
        self._setup_crosshair()

    # ── Setup ────────────────────────────────────────────────────────────────

    def _setup_plot(self):
        pi = self.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.18)
        pi.setMenuEnabled(False)
        self.setMouseTracking(True)
        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.apply_plot_theme("Dark")

    def apply_plot_theme(self, mode: str):
        """Switch the pyqtgraph canvas colours to match the current UI theme."""
        is_light = (mode == "Light")
        if is_light:
            bg       = QColor("#f0f2f8")
            grid_pen = pg.mkPen(QColor("#c4cadc"))
            text_col = "#6070a0"
        else:
            bg       = QColor("#0d0d1f")
            grid_pen = pg.mkPen(QColor("#1e1e40"))
            text_col = "#4a5568"

        self.setBackground(bg)
        pi = self.getPlotItem()
        for axis_name in ("left", "bottom", "right", "top"):
            ax = pi.getAxis(axis_name)
            ax.setPen(grid_pen)
            ax.setTextPen(QColor(text_col))
        pi.setLabel("left",   "Height", units="mm", color=text_col)
        pi.setLabel("bottom", "Time",   units="s",  color=text_col)

    def _setup_curves(self):
        lc = self._app.app_settings.get("line_colors", {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()})
        self._curve_sensor = self.plot(
            pen=pg.mkPen(_rgba(lc.get("sensor", DEFAULT_LINE_COLORS["sensor"])), width=2),
            name="Sensor",
        )
        # Limit-level series (horizontal lines drawn as ordinary PlotDataItems)
        self._curve_mwl   = self.plot(pen=pg.mkPen(_rgba(lc.get("mwl",   DEFAULT_LINE_COLORS["mwl"])),   width=1, style=Qt.DashLine), name="MWL")
        self._curve_menis = self.plot(pen=pg.mkPen(_rgba(lc.get("menis", DEFAULT_LINE_COLORS["menis"])), width=1, style=Qt.DashLine), name="Meniscus")
        self._curve_wd    = self.plot(pen=pg.mkPen(_rgba(lc.get("wd",    DEFAULT_LINE_COLORS["wd"])),    width=1, style=Qt.DotLine),  name="WD")
        self._curve_cwl   = self.plot(pen=pg.mkPen(_rgba(lc.get("cwl",   DEFAULT_LINE_COLORS["cwl"])),   width=1, style=Qt.DashLine), name="CWL")

    def _setup_limit_lines(self):
        """Draggable InfiniteLines for each limit level (shown in Height mode only)."""
        lc = self._app.app_settings.get("line_colors", {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()})

        def _make_line(color_key, label_text, movable=False):
            col = _rgba(lc.get(color_key, DEFAULT_LINE_COLORS[color_key]))
            line = pg.InfiniteLine(
                angle=0,
                movable=movable,
                pen=pg.mkPen(col, width=1, style=Qt.DashLine),
                label=label_text,
                labelOpts={"color": col, "position": 0.05, "anchors": [(0, 1), (0, 1)]},
            )
            line.setVisible(False)
            self.addItem(line)
            return line

        self._line_mwl   = _make_line("mwl",   "NWL")
        self._line_menis = _make_line("menis",  "Meniscus")
        self._line_wd    = _make_line("wd",     "WD")
        self._line_cwl   = _make_line("cwl",    "CWL")

    def _setup_crosshair(self):
        """Vertical crosshair + floating text label for hover tooltip."""
        self._crosshair_v = pg.InfiniteLine(angle=90, movable=False,
                                             pen=pg.mkPen("w", width=1, style=Qt.DotLine))
        self._crosshair_v.setVisible(False)
        self.addItem(self._crosshair_v, ignoreBounds=True)

        self._hover_label = pg.TextItem(anchor=(0, 1), color="w")
        self._hover_label.setVisible(False)
        self.addItem(self._hover_label, ignoreBounds=True)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        """Set plot mode: 'Height (mm)', 'Volume (L)', or 'Flow Rate (L/s)'."""
        self._mode = mode
        pi = self.getPlotItem()
        if "Volume" in mode:
            pi.setLabel("left", "Volume", units="L")
        elif "Flow" in mode:
            pi.setLabel("left", "Flow Rate", units="L/s")
        else:
            pi.setLabel("left", "Height", units="mm")
        self.refresh()

    def set_smooth(self, alg: str):
        self._smooth_alg = alg
        self.refresh()

    def set_auto_scroll(self, enabled: bool):
        self._auto_scroll = enabled
        if not enabled:
            self.getPlotItem().enableAutoRange()

    def set_window(self, key: str):
        self._window_key = key

    def set_paused(self, paused: bool):
        self._paused = paused
        if not paused:
            self.refresh()

    def is_paused(self) -> bool:
        return self._paused

    def enter_manual_select(self, mode: str):
        """Enter manual MWL or CWL click-selection mode. mode='MWL' or 'CWL'."""
        self._manual_select = mode
        self.setCursor(Qt.CrossCursor)

    def exit_manual_select(self):
        self._manual_select = None
        self.setCursor(Qt.ArrowCursor)

    def apply_line_colors(self):
        """Re-apply line colors from app_settings (called after color picker dialog)."""
        lc = self._app.app_settings.get("line_colors", {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()})
        self._curve_sensor.setPen(pg.mkPen(_rgba(lc.get("sensor", DEFAULT_LINE_COLORS["sensor"])), width=2))
        self._curve_mwl.setPen(  pg.mkPen(_rgba(lc.get("mwl",    DEFAULT_LINE_COLORS["mwl"])),   width=1, style=Qt.DashLine))
        self._curve_menis.setPen(pg.mkPen(_rgba(lc.get("menis",  DEFAULT_LINE_COLORS["menis"])), width=1, style=Qt.DashLine))
        self._curve_wd.setPen(   pg.mkPen(_rgba(lc.get("wd",     DEFAULT_LINE_COLORS["wd"])),    width=1, style=Qt.DotLine))
        self._curve_cwl.setPen(  pg.mkPen(_rgba(lc.get("cwl",    DEFAULT_LINE_COLORS["cwl"])),   width=1, style=Qt.DashLine))
        # Re-colour InfiniteLines
        for curve_key, line_item in [
            ("mwl",   self._line_mwl),
            ("menis", self._line_menis),
            ("wd",    self._line_wd),
            ("cwl",   self._line_cwl),
        ]:
            col = _rgba(lc.get(curve_key, DEFAULT_LINE_COLORS[curve_key]))
            line_item.setPen(pg.mkPen(col, width=1, style=Qt.DashLine))
            line_item.label.setColor(col)

    # ── Chart refresh ────────────────────────────────────────────────────────

    def refresh(self):
        """Pull latest data from SensorApp buffers and redraw. Thread-safe."""
        app = self._app
        with app.data_lock:
            t_snap = list(app.t_buf)
            if "Volume" in self._mode:
                y_snap = list(app.v_buf)
            elif "Flow" in self._mode:
                y_snap = list(app.f_buf)
            else:
                y_snap = list(app.h_buf)

        if not t_snap or not y_snap:
            return

        y_s = smooth(y_snap, self._smooth_alg)
        self._last_t = t_snap
        self._last_y = y_s

        t_arr = np.asarray(t_snap, dtype=np.float64)
        y_arr = np.asarray(y_s,    dtype=np.float64)
        self._curve_sensor.setData(t_arr, y_arr)

        self._update_scroll(t_snap, y_s)
        self._update_limit_lines(t_snap)

    def _update_scroll(self, t_data: list, y_s: list):
        if not self._auto_scroll or self._paused:
            return
        secs = self.WINDOW_OPTIONS.get(self._window_key)
        if secs is None:
            self.getPlotItem().enableAutoRange()
            return
        x_max = t_data[-1]
        x_min = max(t_data[0], x_max - secs)
        if x_max - x_min < 1:
            x_max = x_min + 1
        self.setXRange(x_min, x_max, padding=0)
        # Y range for visible window only
        start_i = bisect.bisect_left(t_data, x_min)
        y_view = y_s[start_i:]
        if y_view:
            y_lo, y_hi = min(y_view), max(y_view)
            margin = max((y_hi - y_lo) * 0.1, 0.5)
            self.setYRange(y_lo - margin, y_hi + margin, padding=0)

    def _update_limit_lines(self, t_data: list):
        """Show/hide horizontal limit lines and PlotDataItem line series."""
        p = self._app.profile
        in_height = "Height" in self._mode
        x0 = t_data[0] if t_data else 0.0
        x1 = t_data[-1] if t_data else 1.0
        if x1 - x0 < 1:
            x1 = x0 + 1

        menis_abs = (p.overflow + p.meniscus) if p.overflow > 0 else 0.0

        level_map = [
            (self._curve_mwl,   self._line_mwl,   p.mwl),
            (self._curve_menis, self._line_menis,  menis_abs),
            (self._curve_wd,    self._line_wd,     p.water_discharge),
            (self._curve_cwl,   self._line_cwl,    p.cwl),
        ]

        for curve, inf_line, val in level_map:
            visible = in_height and val > 0
            curve.setVisible(visible)
            inf_line.setVisible(visible)
            if visible:
                curve.setData([x0, x1], [val, val])
                inf_line.setValue(val)

    # ── Mouse / crosshair ────────────────────────────────────────────────────

    def _on_mouse_moved(self, pos):
        """Update crosshair and hover tooltip on mouse move."""
        vb = self.getPlotItem().vb
        if not self.sceneBoundingRect().contains(pos):
            self._crosshair_v.setVisible(False)
            self._hover_label.setVisible(False)
            return
        mp = vb.mapSceneToView(pos)
        t = mp.x()
        # Snap to nearest data point
        if not self._last_t:
            return
        idx = bisect.bisect_left(self._last_t, t)
        idx = max(0, min(idx, len(self._last_t) - 1))
        snap_t = self._last_t[idx]
        snap_y = self._last_y[idx]

        self._crosshair_v.setPos(snap_t)
        self._crosshair_v.setVisible(True)

        if "Volume" in self._mode:
            unit = "L"
        elif "Flow" in self._mode:
            unit = "L/s"
        else:
            unit = "mm"
        self._hover_label.setText(f"t={snap_t:.1f}s  y={snap_y:.2f} {unit}")
        self._hover_label.setPos(snap_t, snap_y)
        self._hover_label.setVisible(True)

    def mousePressEvent(self, ev):
        """Handle chart click for manual MWL/CWL selection."""
        if self._manual_select and ev.button() == Qt.LeftButton:
            vb = self.getPlotItem().vb
            mp = vb.mapSceneToView(ev.pos())
            t_click = mp.x()
            # Average ±0.5 s window around click for a stable value
            h_val = self._avg_around(t_click, 0.5)
            if self._manual_select == "MWL":
                self.mwl_clicked.emit(h_val)
            else:
                self.cwl_clicked.emit(h_val)
            self.exit_manual_select()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def _avg_around(self, t_center: float, half_window: float) -> float:
        """Return average of _last_y values within [t_center - half_window, t_center + half_window]."""
        if not self._last_t or not self._last_y:
            return 0.0
        t_lo = t_center - half_window
        t_hi = t_center + half_window
        i_lo = bisect.bisect_left(self._last_t,  t_lo)
        i_hi = bisect.bisect_right(self._last_t, t_hi)
        vals = self._last_y[i_lo:i_hi]
        return sum(vals) / len(vals) if vals else (self._last_y[-1] if self._last_y else 0.0)

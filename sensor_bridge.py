"""
sensor_bridge.py — Python ↔ QML bridge for the EN 14055 Cistern Analytics app.

SensorBridge is a QObject subclass that exposes SensorApp state as Q_PROPERTYs
and @Slot methods callable from QML.  It also manages two timers (UI + chart)
and drives SensorApp tick methods every cycle.

QML reads sensor values through properties (height, volume, pressure, …).
QML calls actions through slots (toggleConnect, setNwl, toggleLog, …).
QML receives async notifications through signals (dataChanged, toastMessage, …).

Chart data flow:
  Python timer → chartDataReady(list[list[float]]) signal
  QML ChartView JS handler calls lineSeries.replace(points)
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QObject, QTimer, Property, Signal, Slot,
)
from PySide6.QtWidgets import QFileDialog, QApplication

from sensor_core import (
    SensorApp, CisternProfile,
    PRESSURE_UNITS, EXPORT_DIR, CONFIG_DIR,
    DEFAULT_APP, DEFAULT_LINE_COLORS,
    p_format, save_settings, _rebuild_interp_cache,
    smooth, run_compliance_checks,
    EN14055_MAX_CAL_FILE_BYTES,
)
from dialogs_qt import (
    ConnectionDialog, CalibrationDialog,
    ProgramSettingsDialog, LineColorsDialog, ComplianceDialog,
)

# per-session air gap confirmation flag (not saved to disk)
_air_gap_confirmed: list[bool] = [False]


class SensorBridge(QObject):
    """Bridges Python SensorApp state into QML via properties, signals, slots."""

    # ── Signals ───────────────────────────────────────────────────────
    dataChanged     = Signal()               # live readouts changed
    limitsChanged   = Signal()               # profile / limit values changed
    flushChanged    = Signal()               # flush results list changed
    toastMessage    = Signal(str)            # short status message for QML overlay
    # Chart: list of [t, y] pairs for the current plot mode
    chartDataReady  = Signal("QVariantList") # emitted every chart tick
    # Connection state
    connectionChanged = Signal(str, str, bool)  # connText, icon_class, isConnected
    # Status labels
    rwlStateChanged   = Signal(str, str)    # text, css-class
    cwlAutoStateChanged = Signal(str, str)  # text, css-class
    # Flush table rows as list of formatted strings
    flushRowsChanged  = Signal("QVariantList")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._app = SensorApp()
        self._plot_mode = "Height (mm)"
        self._smooth_alg = "None"
        self._manual_select: str | None = None  # "MWL" or "CWL"
        self._toast_clear_at: float = 0.0
        self._toast_msg: str = ""

        self._ui_timer    = QTimer(self)
        self._chart_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._tick_ui)
        self._chart_timer.timeout.connect(self._tick_chart)
        self._start_timers()

    # ── Timer management ──────────────────────────────────────────────

    def _start_timers(self):
        self._ui_timer.start(self._app.app_settings.get("ui_refresh_ms", 50))
        self._chart_timer.start(self._app.app_settings.get("chart_refresh_ms", 100))

    def _restart_timers(self):
        self._ui_timer.stop(); self._chart_timer.stop()
        self._start_timers()

    # ── Q_PROPERTY: live readouts ─────────────────────────────────────

    @Property(float, notify=dataChanged)
    def height(self) -> float:
        return self._app.current_height

    @Property(float, notify=dataChanged)
    def volume(self) -> float:
        return self._app.current_volume

    @Property(str, notify=dataChanged)
    def pressureStr(self) -> str:
        unit = self._app.app_settings.get("pressure_unit", "bar")
        return p_format(self._app.current_pressure, unit=unit)

    @Property(float, notify=dataChanged)
    def flow(self) -> float:
        return self._app.current_flow

    @Property(str, notify=dataChanged)
    def temperatureStr(self) -> str:
        t = self._app.current_temperature
        return f"{t:.1f} °C" if t is not None else "-- °C"

    @Property(str, notify=dataChanged)
    def headroom(self) -> str:
        of = self._app.profile.overflow
        if of > 0:
            return f"{of - self._app.current_height:.1f} mm"
        return "— mm"

    @Property(str, notify=dataChanged)
    def headroomClass(self) -> str:
        of = self._app.profile.overflow
        if of > 0:
            sm = of - self._app.current_height
            return "green" if sm >= 20 else ("orange" if sm >= 5 else "red")
        return "muted"

    # ── Q_PROPERTY: profile limits ────────────────────────────────────

    @Property(str, notify=limitsChanged)
    def profileName(self) -> str:
        return self._app.profile.name

    @Property(str, notify=limitsChanged)
    def nwlStr(self) -> str:
        return self._mm_rel(self._app.profile.mwl)

    @Property(str, notify=limitsChanged)
    def mwlFaultStr(self) -> str:
        v = self._app.profile.mwl_fault
        return self._mm_rel(v) if v > 0 else "—"

    @Property(str, notify=limitsChanged)
    def cwlStr(self) -> str:
        v = self._app.profile.cwl
        return self._mm_rel(v) if v > 0 else "—"

    @Property(str, notify=limitsChanged)
    def meniscusStr(self) -> str:
        m = self._app.profile.meniscus
        return f"{m:+.1f} mm" if m != 0 else "0.0 mm"

    @Property(str, notify=limitsChanged)
    def overflowStr(self) -> str:
        return f"{self._app.profile.overflow:.1f} mm"

    @Property(str, notify=limitsChanged)
    def residualStr(self) -> str:
        return f"{self._app.profile.residual_wl:.1f} mm"

    @Property(str, notify=limitsChanged)
    def safetyMarginStr(self) -> str:
        p = self._app.profile
        if p.overflow > 0 and p.mwl > 0:
            return f"{p.overflow - p.mwl:.1f} mm"
        return "—"

    @Property(str, notify=limitsChanged)
    def safetyMarginClass(self) -> str:
        p = self._app.profile
        if p.overflow > 0 and p.mwl > 0:
            return "green" if (p.overflow - p.mwl) >= 20 else "red"
        return "muted"

    @Property(str, notify=limitsChanged)
    def cwlStatusStr(self) -> str:
        p = self._app.profile
        if p.overflow > 0 and p.cwl > 0:
            diff = p.cwl - p.overflow
            mark = "✓ (≤10)" if diff <= 10 else "✗ (>10)"
            return f"CWL: {diff:+.1f} mm OF  {mark}"
        return "CWL: — (capture during fault test)"

    @Property(str, notify=limitsChanged)
    def cwlStatusClass(self) -> str:
        p = self._app.profile
        if p.overflow > 0 and p.cwl > 0:
            return "green" if (p.cwl - p.overflow) <= 10 else "red"
        return "muted"

    @Property(str, notify=limitsChanged)
    def avgWindowLabel(self) -> str:
        return str(self._app.app_settings.get("avg_window", 0.5))

    # ── Q_PROPERTY: connection / app state ────────────────────────────

    @Property(bool, notify=connectionChanged)
    def isConnected(self) -> bool:
        return self._app.is_connected

    @Property(bool, notify=dataChanged)
    def isLogging(self) -> bool:
        return self._app.is_logging

    @Property(bool, notify=flushChanged)
    def isFlushMeasuring(self) -> bool:
        return self._app.flush_measuring

    @Property(str, notify=dataChanged)
    def plotMode(self) -> str:
        return self._plot_mode

    @Property(str, notify=dataChanged)
    def smoothAlg(self) -> str:
        return self._smooth_alg

    @Property(bool, notify=limitsChanged)
    def showManualRwlBtn(self) -> bool:
        return (self._app.app_settings.get("cwl_mode") == "Manual"
                and self._app.cwl_state == "ARMED")

    # ── Slots: connection ─────────────────────────────────────────────

    @Slot()
    def toggleConnect(self):
        if self._app.is_connected:
            self._app.disconnect()
            self.connectionChanged.emit("Disconnected", "muted", False)
        else:
            self._app.last_error = ""
            self._app.connect()
            if self._app.is_connected:
                cp = self._app.conn_params
                self.connectionChanged.emit(
                    f"{cp['port']}  {cp['baud']}bd", "green", True)
            elif self._app.last_error:
                self.connectionChanged.emit(self._app.last_error, "red", False)

    # ── Slots: EN14055 limit capture ──────────────────────────────────

    @Slot()
    def setNwl(self):
        val = self._app.get_avg_height()
        self._app.profile.mwl = val
        self._app.cwl_state = "ARMED"
        self._app.cwl_peak = val
        self.limitsChanged.emit()
        self._toast(f"NWL set: {val:.1f} mm")

    @Slot()
    def setMeniscus(self):
        if self._app.profile.overflow <= 0:
            self._toast("⚠ Set Overflow in Calibration first!")
            return
        measured = self._app.get_avg_height()
        self._app.profile.meniscus = measured - self._app.profile.overflow
        self.limitsChanged.emit()
        self._toast(f"Meniscus set: {self._app.profile.meniscus:+.1f} mm above OF")

    @Slot()
    def armCwlAuto(self):
        if self._app.profile.overflow <= 0:
            self._toast("⚠ Set Overflow in Calibration first!")
            return
        self._app.cwl_auto_state = "ARMED"
        self._app.cwl_auto_peak  = self._app.get_avg_height()
        self._app.cwl_auto_timer = 0.0
        self.limitsChanged.emit()
        self._toast("CWL armed — cut supply when water is at MWL.")

    @Slot()
    def manualRwl(self):
        self._app.cwl_state = "WAITING"
        self._app.cwl_timer = time.time()

    @Slot(float, float)
    def applyManualMwlCwl(self, mwl_val: float, cwl_val: float):
        self._app.profile.mwl_fault = mwl_val
        self._app.profile.cwl = cwl_val
        self._app.manual_mwl_cwl_pending = False
        self.limitsChanged.emit()
        self._toast(f"MWL = {mwl_val:.1f} mm  |  CWL = {cwl_val:.1f} mm")

    # ── Slots: chart controls ─────────────────────────────────────────

    @Slot(str)
    def setPlotMode(self, mode: str):
        self._plot_mode = mode
        self.dataChanged.emit()

    @Slot(str)
    def setSmoothAlg(self, alg: str):
        self._smooth_alg = alg

    # ── Slots: flush ──────────────────────────────────────────────────

    @Slot(str)
    def startFlush(self, flush_type: str):
        with self._app.data_lock:
            v_now = self._app.current_volume
            h_now = self._app.current_height
        with self._app._flush_lock:
            self._app.flush_measuring   = True
            self._app.flush_start_vol   = v_now
            self._app.flush_start_h     = h_now
            self._app.flush_start_time  = time.time()
            self._app.flush_vol_history = []
            self._app.flush_min_h       = float("inf")
            self._app.flush_rising      = False
            self._app.flush_rising_timer = 0.0
        self.flushChanged.emit()

    @Slot(str)
    def stopFlush(self, flush_type: str):
        with self._app._flush_lock:
            self._app.flush_measuring = False
            elapsed = time.time() - self._app.flush_start_time
            with self._app.data_lock:
                v_now = self._app.current_volume
            vol = abs(self._app.flush_start_vol - v_now)
            en_rate = None
            hist = self._app.flush_vol_history
            if hist and vol > 3.0:
                sv = self._app.flush_start_vol
                trim = [(t, v, h) for t, v, h in hist
                        if abs(sv - v) >= 1.0 and abs(sv - v) <= vol - 2.0]
                if len(trim) >= 2:
                    dt = trim[-1][0] - trim[0][0]
                    dv = abs(trim[0][1] - trim[-1][1])
                    if dt > 0:
                        en_rate = dv / dt
            self._app.flush_results.append({
                "type": flush_type, "vol": vol, "time": elapsed,
                "en14055_rate": en_rate,
                "temp_c": self._app.current_temperature,
            })
        self.flushChanged.emit()
        self._emit_flush_rows()

    @Slot()
    def clearFlush(self):
        self._app.flush_results.clear()
        self.flushChanged.emit()
        self.flushRowsChanged.emit([])

    @Slot(result="QVariantList")
    def getFlushRows(self) -> list:
        rows = []
        for i, r in enumerate(self._app.flush_results):
            rate_s = f"{r['en14055_rate']:.3f}" if r.get("en14055_rate") else "—"
            temp_s = f"{r['temp_c']:.1f}°C" if r.get("temp_c") is not None else "—"
            rows.append(
                f"#{i+1} {r['type'][:4]}  "
                f"{r['vol']:.2f} L  {r['time']:.1f}s  EN:{rate_s} L/s  {temp_s}")
        return rows

    # ── Slots: data log ───────────────────────────────────────────────

    @Slot()
    def toggleLog(self):
        if not self._app.is_logging:
            EXPORT_DIR.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r'[^\w\-]', '_', self._app.profile.name)[:80]
            fn = str(EXPORT_DIR / f"{safe}_{ts}.csv")
            try:
                self._app.csv_file   = open(fn, "w", newline="", encoding="utf-8")
                self._app.csv_writer = csv.writer(self._app.csv_file)
                pu = PRESSURE_UNITS.get(
                    self._app.app_settings.get("pressure_unit", "bar"), (1.0, "bar"))[1]
                self._app.csv_writer.writerow(
                    ["Timestamp", f"P({pu})", "H(mm)", "V(L)", "F(L/s)", "T(C)"])
                self._app.is_logging = True
                self.dataChanged.emit()
                self._toast(f"Logging: {Path(fn).name}")
            except Exception as e:
                logging.warning(f"CSV open failed: {e}")
                self._toast(f"Log failed: {e}")
        else:
            self._app.is_logging = False
            with self._app._csv_lock:
                if self._app.csv_file:
                    try:
                        self._app.csv_file.flush()
                        self._app.csv_file.close()
                    except OSError as e:
                        logging.warning(f"CSV close: {e}")
                    finally:
                        self._app.csv_file = None
                        self._app.csv_writer = None
                        self._app._csv_row_count = 0
            self.dataChanged.emit()
            self._toast("Data log stopped.")

    # ── Slots: profile I/O (open Qt dialogs) ──────────────────────────

    @Slot()
    def loadProfile(self):
        fp, _ = QFileDialog.getOpenFileName(
            None, "Load Profile", str(CONFIG_DIR), "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, encoding="utf-8") as f:
                self._app.profile = CisternProfile.from_dict(json.load(f))
            _rebuild_interp_cache(self._app.profile.points)
            self._app.recalc_from_pressure()
            self.limitsChanged.emit()
            self._toast(f"Profile loaded: {self._app.profile.name}")
        except Exception as e:
            self._toast(f"Load failed: {e}")

    @Slot()
    def saveProfile(self):
        fp, _ = QFileDialog.getSaveFileName(
            None, "Save Profile As",
            str(CONFIG_DIR / f"{self._app.profile.name}.json"), "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(self._app.profile.to_dict(), f, indent=4)
            self._toast(f"Saved: {Path(fp).name}")
        except OSError as e:
            self._toast(f"Save failed: {e}")

    @Slot()
    def saveAsDefault(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        fp = CONFIG_DIR / "default_profile.json"
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(self._app.profile.to_dict(), f, indent=4)
            self._toast(f"Default profile set: {self._app.profile.name}")
        except OSError as e:
            logging.error(f"Default profile save: {e}")

    @Slot()
    def clearDefault(self):
        fp = CONFIG_DIR / "default_profile.json"
        if fp.exists():
            fp.unlink()
        self._toast("Default profile cleared.")

    # ── Slots: settings dialogs (use Qt dialogs from dialogs_qt.py) ───

    @Slot()
    def openConnectionDlg(self):
        dlg = ConnectionDialog(self._app)
        dlg.exec()

    @Slot()
    def openCalibrationDlg(self):
        dlg = CalibrationDialog(self._app)
        if dlg.exec():
            self.limitsChanged.emit()

    @Slot()
    def openProgramDlg(self):
        old_theme = self._app.app_settings.get("ui_theme", "Dark")
        dlg = ProgramSettingsDialog(self._app)
        if dlg.exec():
            self._restart_timers()
            self.limitsChanged.emit()
            new_theme = self._app.app_settings.get("ui_theme", "Dark")
            if new_theme != old_theme:
                self.toastMessage.emit(f"Theme changed to {new_theme} — restart to apply")

    @Slot()
    def openColorsDlg(self):
        dlg = LineColorsDialog(self._app)
        dlg.exec()

    @Slot()
    def checkCompliance(self):
        dlg = ComplianceDialog(self._app, _air_gap_confirmed)
        dlg.exec()

    @Slot()
    def exportScreenshot(self):
        EXPORT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = str(EXPORT_DIR / f"screenshot_{ts}.png")
        try:
            screen = QApplication.primaryScreen()
            # Grab the active window
            win = QApplication.activeWindow()
            if win:
                pix = win.grab()
            else:
                pix = screen.grabWindow(0)
            pix.save(fp, "PNG")
            self._toast(f"Screenshot: {Path(fp).name}")
        except Exception as e:
            self._toast(f"Screenshot failed: {e}")

    # ── Slots: chart data ─────────────────────────────────────────────

    @Slot(result="QVariantList")
    def getChartPoints(self) -> list:
        """Return list of [t, y] pairs for QML ChartView."""
        with self._app.data_lock:
            t_snap = list(self._app.t_buf)
            if "Volume" in self._plot_mode:
                y_snap = list(self._app.v_buf)
            elif "Flow" in self._plot_mode:
                y_snap = list(self._app.f_buf)
            else:
                y_snap = list(self._app.h_buf)
        if not t_snap:
            return []
        y_s = smooth(y_snap, self._smooth_alg)
        return [[t, y] for t, y in zip(t_snap, y_s)]

    @Slot(result="QVariantList")
    def getLimitLines(self) -> list:
        """Return list of {label, value, color} for horizontal limit lines."""
        p = self._app.profile
        if "Height" not in self._plot_mode:
            return []
        lc = self._app.app_settings.get(
            "line_colors", {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()})
        def _col(key):
            c = lc.get(key, DEFAULT_LINE_COLORS[key])
            return f"rgba({c[0]},{c[1]},{c[2]},{c[3]/255:.2f})"
        lines = []
        menis_abs = (p.overflow + p.meniscus) if p.overflow > 0 else 0.0
        for label, val, key in [
            ("NWL",      p.mwl,              "mwl"),
            ("Meniscus", menis_abs,           "menis"),
            ("WD",       p.water_discharge,   "wd"),
            ("CWL",      p.cwl,               "cwl"),
        ]:
            if val > 0:
                lines.append({"label": label, "value": val, "color": _col(key)})
        return lines

    # ── Timer ticks ───────────────────────────────────────────────────

    def _tick_ui(self):
        now = time.time()
        with self._app.data_lock:
            h = self._app.current_height
            h_history = list(self._app.h_buf)[-150:] or [h]
            t_history = list(self._app.t_buf)[-150:]

        if self._app.tick_rwl(h, h_history):
            self.limitsChanged.emit()
        if self._app.tick_cwl_auto(h, h_history, t_history):
            self.limitsChanged.emit()
        self._tick_flush_auto_stop(h, now)
        self.dataChanged.emit()

        # RWL state signal
        cs = self._app.cwl_state
        thresh = self._app.app_settings.get("cwl_drop_thresh", 1.5)
        if cs == "ARMED":
            if self._app.app_settings.get("cwl_mode") == "Automatic":
                self.rwlStateChanged.emit(f"RWL: ARMED (drop ≥ {thresh}mm)", "blue")
            else:
                self.rwlStateChanged.emit("RWL: ARMED (Manual)", "blue")
        elif cs == "WAITING":
            rem = max(0.0, 2.0 - (now - self._app.cwl_timer))
            self.rwlStateChanged.emit(f"RWL: TIMER {rem:.1f}s", "orange")
        elif cs == "DONE":
            self.rwlStateChanged.emit(
                f"RWL: {self._app.profile.residual_wl:.1f}mm captured", "green")
        else:
            self.rwlStateChanged.emit("RWL: IDLE (set NWL to arm)", "muted")

        # CWL auto state signal
        cas = self._app.cwl_auto_state
        if self._app.manual_mwl_cwl_pending:
            self.cwlAutoStateChanged.emit("MWL/CWL: click chart MWL moment", "orange")
        elif cas == "ARMED":
            self.cwlAutoStateChanged.emit("CWL: ARMED — watching for drop ≥1.5mm", "blue")
        elif cas == "WAITING":
            rem = max(0.0, 2.0 - (now - self._app.cwl_auto_timer))
            self.cwlAutoStateChanged.emit(f"CWL: 2s WINDOW — {rem:.1f}s left", "orange")
        elif cas == "DONE":
            p = self._app.profile
            diff = p.cwl - p.overflow if p.overflow > 0 else 0
            self.cwlAutoStateChanged.emit(
                f"CWL: {p.cwl:.1f}mm  ({diff:+.1f}mm OF)", "green")
        else:
            self.cwlAutoStateChanged.emit("MWL/CWL: IDLE", "muted")

        # Toast dismiss
        if self._toast_clear_at > 0 and now >= self._toast_clear_at:
            self._toast_clear_at = 0.0
            self._toast_msg = ""

    def _tick_chart(self):
        pts = self.getChartPoints()
        self.chartDataReady.emit(pts)

    def _tick_flush_auto_stop(self, h: float, now: float):
        if not self._app.flush_measuring:
            return
        elapsed = now - self._app.flush_start_time
        if elapsed < 3.0:
            return
        if h < self._app.flush_min_h:
            self._app.flush_min_h = h
            self._app.flush_rising = False
        if not self._app.flush_rising and h > self._app.flush_min_h + 5.0:
            self._app.flush_rising = True
            self._app.flush_rising_timer = now
        if self._app.flush_rising and (now - self._app.flush_rising_timer >= 2.0):
            # Get current flush type from settings or default
            self.stopFlush("Full Flush")

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self):
        self._ui_timer.stop()
        self._chart_timer.stop()
        self._app.cleanup()

    # ── Private helpers ───────────────────────────────────────────────

    def _toast(self, msg: str, duration: float = 3.0):
        self._toast_msg = msg
        self._toast_clear_at = time.time() + duration
        self.toastMessage.emit(msg)

    def _mm_rel(self, v: float) -> str:
        of = self._app.profile.overflow
        if of > 0 and v > 0:
            return f"{v:.1f} mm  ({v - of:+.1f} mm OF)"
        return f"{v:.1f} mm"

    def _emit_flush_rows(self):
        self.flushRowsChanged.emit(self.getFlushRows())

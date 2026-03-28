"""
dialogs_qt.py — Qt dialog windows for the EN 14055 Cistern Analytics app.

Five QDialog subclasses:
  ConnectionDialog       — COM port, baud rate, IO port, polling interval
  CalibrationDialog      — profile name, overflow, water discharge, cal points table
  ProgramSettingsDialog  — theme, pressure unit, averaging, CWL, refresh rates
  LineColorsDialog       — per-series color picker
  ComplianceDialog       — EN 14055 compliance check results

No dependency on DearPyGui.  Shared state via SensorApp instance.
"""
from __future__ import annotations

import csv
import json
import math
import re
import logging
from pathlib import Path

import serial.tools.list_ports
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QColorDialog, QScrollArea, QWidget,
    QCheckBox, QMessageBox, QFileDialog, QSizePolicy, QFrame,
)

from sensor_core import (
    SensorApp, CalibrationPoint, CisternProfile,
    PRESSURE_UNITS, DEFAULT_LINE_COLORS,
    p_format, p_parse_to_bar,
    run_compliance_checks,
    save_settings, _rebuild_interp_cache,
    EXPORT_DIR, EN14055_MAX_CAL_FILE_BYTES,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

_LINE_COLOR_LABELS: dict[str, str] = {
    "sensor": "Sensor line",
    "mwl":    "NWL (fill level)",
    "menis":  "Meniscus",
    "wd":     "Water Discharge",
    "cwl":    "CWL (fault)",
}


def _to_qcolor(rgba_list: list[int]) -> QColor:
    r, g, b, a = (rgba_list + [255])[:4]
    return QColor(r, g, b, a)


def _from_qcolor(c: QColor) -> list[int]:
    return [c.red(), c.green(), c.blue(), c.alpha()]


# ── ConnectionDialog ──────────────────────────────────────────────────────────

class ConnectionDialog(QDialog):
    """Edit COM port, baud rate, AL1060 IO port and polling interval."""

    def __init__(self, app: SensorApp, parent=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Hardware Connection")
        self.setFixedSize(360, 280)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        ports = [p.device for p in serial.tools.list_ports.comports()] or ["COM1"]

        self._port = QComboBox()
        self._port.addItems(ports)
        current_port = self._app.conn_params.get("port", "COM8")
        if current_port in ports:
            self._port.setCurrentText(current_port)
        else:
            self._port.insertItem(0, current_port)
            self._port.setCurrentIndex(0)
        form.addRow("COM Port:", self._port)

        self._baud = QComboBox()
        self._baud.addItems(["115200", "38400", "9600"])
        self._baud.setCurrentText(str(self._app.conn_params.get("baud", 115200)))
        form.addRow("Baud Rate:", self._baud)

        self._io_port = QComboBox()
        self._io_port.addItems(["Port 1", "Port 2", "Port 3", "Port 4"])
        self._io_port.setCurrentText(self._app.conn_params.get("io_port", "Port 1"))
        form.addRow("AL1060 Port:", self._io_port)

        self._poll = QComboBox()
        self._poll.addItems(["5", "20", "50", "100", "500", "1000"])
        self._poll.setCurrentText(str(self._app.conn_params.get("poll_ms", 50)))
        form.addRow("Polling (ms):", self._poll)

        layout.addStretch()
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save(self):
        cp = self._app.conn_params
        cp["port"]     = self._port.currentText()
        cp["baud"]     = int(self._baud.currentText())
        cp["io_port"]  = self._io_port.currentText()
        cp["poll_ms"]  = int(self._poll.currentText())
        save_settings(cp, self._app.app_settings)
        self.accept()


# ── CalibrationDialog ─────────────────────────────────────────────────────────

class CalibrationDialog(QDialog):
    """Edit calibration profile: name, overflow, water discharge, and cal points."""

    def __init__(self, app: SensorApp, parent=None):
        super().__init__(parent)
        self._app = app
        self._clone = app.profile.clone()
        self._edit_idx: int | None = None  # None = add new, int = editing existing
        self.setWindowTitle("Calibration Profile")
        self.setMinimumSize(520, 580)
        self._build()
        self._refresh_table()

    def _build(self):
        layout = QVBoxLayout(self)

        # Profile name
        form = QFormLayout()
        self._name_edit = QLineEdit(self._clone.name)
        form.addRow("Profile Name:", self._name_edit)

        row2 = QHBoxLayout()
        self._over_edit = QLineEdit(str(self._clone.overflow))
        self._wd_edit   = QLineEdit(str(self._clone.water_discharge))
        row2.addWidget(QLabel("Overflow (mm):"))
        row2.addWidget(self._over_edit)
        row2.addSpacing(16)
        row2.addWidget(QLabel("Water Discharge (mm):"))
        row2.addWidget(self._wd_edit)

        layout.addLayout(form)
        layout.addLayout(row2)
        layout.addWidget(_separator())

        # Points table
        layout.addWidget(QLabel("Calibration Points:"))
        self._table = QTableWidget(0, 4)
        unit = self._app.app_settings.get("pressure_unit", "bar")
        _, unit_label = PRESSURE_UNITS.get(unit, (1.0, "bar"))
        self._table.setHorizontalHeaderLabels([f"P ({unit_label})", "H (mm)", "Vol (L)", "Actions"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setMaximumHeight(200)
        layout.addWidget(self._table)
        layout.addWidget(_separator())

        # Add/edit inputs
        layout.addWidget(QLabel("Add / Edit Point:"))
        input_row = QHBoxLayout()
        self._p_edit = QLineEdit()
        self._p_edit.setPlaceholderText(f"P ({unit_label})")
        self._h_edit = QLineEdit()
        self._h_edit.setPlaceholderText("H (mm)")
        self._v_edit = QLineEdit()
        self._v_edit.setPlaceholderText("Vol (L)")
        input_row.addWidget(self._p_edit)
        input_row.addWidget(self._h_edit)
        input_row.addWidget(self._v_edit)
        layout.addLayout(input_row)

        btn_row = QHBoxLayout()
        self._btn_read = QPushButton("Read Sensor")
        self._btn_read.clicked.connect(self._read_sensor)
        self._btn_add = QPushButton("Add Point")
        self._btn_add.clicked.connect(self._add_or_update)
        self._btn_cancel_edit = QPushButton("Cancel Edit")
        self._btn_cancel_edit.clicked.connect(self._cancel_edit)
        btn_row.addWidget(self._btn_read)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_cancel_edit)
        layout.addLayout(btn_row)
        layout.addWidget(_separator())

        # Export / import
        io_row = QHBoxLayout()
        btn_exp = QPushButton("Export Points (.json)")
        btn_exp.clicked.connect(self._export_json)
        btn_imp = QPushButton("Import...")
        btn_imp.clicked.connect(self._import)
        io_row.addWidget(btn_exp)
        io_row.addWidget(btn_imp)
        layout.addLayout(io_row)
        layout.addWidget(_separator())

        # Save / cancel
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _refresh_table(self):
        self._clone.points.sort(key=lambda x: x.p)
        self._table.setRowCount(0)
        unit = self._app.app_settings.get("pressure_unit", "bar")
        for idx, pt in enumerate(self._clone.points):
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(p_format(pt.p, unit=unit).split()[0]))
            self._table.setItem(row, 1, QTableWidgetItem(f"{pt.h:.1f}"))
            self._table.setItem(row, 2, QTableWidgetItem(f"{pt.v:.2f}"))
            # Action buttons in column 3
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(2, 2, 2, 2)
            btn_edit = QPushButton("Edit")
            btn_del  = QPushButton("Del")
            btn_edit.setFixedWidth(42)
            btn_del.setFixedWidth(36)
            btn_edit.clicked.connect(lambda _, i=idx: self._edit_point(i))
            btn_del.clicked.connect( lambda _, i=idx: self._delete_point(i))
            cell_layout.addWidget(btn_edit)
            cell_layout.addWidget(btn_del)
            self._table.setCellWidget(row, 3, cell)

    def _edit_point(self, idx: int):
        if 0 <= idx < len(self._clone.points):
            pt = self._clone.points[idx]
            unit = self._app.app_settings.get("pressure_unit", "bar")
            self._p_edit.setText(p_format(pt.p, unit=unit).split()[0])
            self._h_edit.setText(f"{pt.h:.1f}")
            self._v_edit.setText(f"{pt.v:.2f}")
            self._edit_idx = idx
            self._btn_add.setText("Update Point")

    def _delete_point(self, idx: int):
        if 0 <= idx < len(self._clone.points):
            self._clone.points.pop(idx)
            self._edit_idx = None
            self._btn_add.setText("Add Point")
            self._refresh_table()

    def _add_or_update(self):
        try:
            unit = self._app.app_settings.get("pressure_unit", "bar")
            p = p_parse_to_bar(self._p_edit.text(), unit=unit)
            h = float(self._h_edit.text().replace(",", "."))
            v = float(self._v_edit.text().replace(",", "."))
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numeric values.")
            return
        if p < 0 or h < 0 or v < 0:
            QMessageBox.warning(self, "Invalid Input", "Values must be non-negative.")
            return
        # Reject duplicate pressure
        for i, pt in enumerate(self._clone.points):
            if i != self._edit_idx and abs(pt.p - p) < 1e-9:
                QMessageBox.warning(self, "Duplicate", "Duplicate pressure value — adjust and retry.")
                return
        if self._edit_idx is not None and 0 <= self._edit_idx < len(self._clone.points):
            self._clone.points[self._edit_idx] = CalibrationPoint(p=p, h=h, v=v)
        else:
            self._clone.points.append(CalibrationPoint(p=p, h=h, v=v))
        self._cancel_edit()
        self._refresh_table()

    def _cancel_edit(self):
        self._edit_idx = None
        self._btn_add.setText("Add Point")
        self._p_edit.clear()
        self._h_edit.clear()
        self._v_edit.clear()

    def _read_sensor(self):
        unit = self._app.app_settings.get("pressure_unit", "bar")
        self._p_edit.setText(p_format(self._app.current_pressure, unit=unit).split()[0])

    def _export_json(self):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe = re.sub(r'[^\w\-]', '_', self._clone.name)[:80]
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = EXPORT_DIR / f"{safe}_cal_points_{ts}.json"
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(
                    {"name": self._clone.name,
                     "points": [{"p": pt.p, "h": pt.h, "v": pt.v} for pt in self._clone.points]},
                    f, indent=4,
                )
            QMessageBox.information(self, "Exported", f"Saved: {fp.name}")
        except OSError as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _import(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Import Calibration Points", str(EXPORT_DIR),
            "JSON / CSV (*.json *.csv)")
        if not fp:
            return
        fp = Path(fp)
        try:
            if fp.stat().st_size > EN14055_MAX_CAL_FILE_BYTES:
                QMessageBox.warning(self, "File Too Large",
                                    f"File exceeds {EN14055_MAX_CAL_FILE_BYTES // (1024*1024)} MB limit.")
                return
            new_pts: list[CalibrationPoint] = []
            skipped = 0
            if fp.suffix.lower() == ".csv":
                with open(fp, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        p = float(row.get("P_bar", row.get("p", 0)))
                        h = float(row.get("H_mm",  row.get("h", 0)))
                        v = float(row.get("Vol_L",  row.get("v", 0)))
                        if not all(math.isfinite(x) for x in (p, h, v)):
                            skipped += 1
                            continue
                        new_pts.append(CalibrationPoint(p=p, h=h, v=v))
            else:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                raw = data if isinstance(data, list) else data.get("points", [])
                for row in raw:
                    p = float(row.get("p", 0))
                    h = float(row.get("h", 0))
                    v = float(row.get("v", 0))
                    if not all(math.isfinite(x) for x in (p, h, v)):
                        skipped += 1
                        continue
                    new_pts.append(CalibrationPoint(p=p, h=h, v=v))
            self._clone.points = new_pts
            self._refresh_table()
            msg = f"Imported {len(new_pts)} point(s) from {fp.name}"
            if skipped:
                msg += f" ({skipped} invalid row(s) skipped)"
            QMessageBox.information(self, "Import Complete", msg)
        except Exception as e:
            logging.error(f"Cal import failed: {e}")
            QMessageBox.critical(self, "Import Failed", str(e))

    def _save(self):
        try:
            self._clone.name = self._name_edit.text().strip() or "Untitled Profile"
            self._clone.overflow         = float(self._over_edit.text().replace(",", "."))
            self._clone.water_discharge  = float(self._wd_edit.text().replace(",", "."))
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return
        self._app.profile = self._clone
        _rebuild_interp_cache(self._app.profile.points)
        self._app.recalc_from_pressure()
        self.accept()


# ── ProgramSettingsDialog ─────────────────────────────────────────────────────

class ProgramSettingsDialog(QDialog):
    """Edit application-level settings (theme, units, CWL, refresh rates)."""

    def __init__(self, app: SensorApp, parent=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Program Settings")
        self.setFixedSize(400, 490)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        s = self._app.app_settings

        self._theme = QComboBox(); self._theme.addItems(["Dark", "Light"])
        self._theme.setCurrentText(s.get("ui_theme", "Dark"))
        form.addRow("Interface Theme:", self._theme)

        self._unit = QComboBox(); self._unit.addItems(["bar", "mbar", "kPa"])
        self._unit.setCurrentText(s.get("pressure_unit", "bar"))
        form.addRow("Pressure Display Unit:", self._unit)

        self._avg = QComboBox(); self._avg.addItems(["0.1", "0.5", "1.0", "2.0"])
        self._avg.setCurrentText(str(s.get("avg_window", 0.5)))
        form.addRow("Averaging Window (s):", self._avg)

        self._cwl_mode = QComboBox(); self._cwl_mode.addItems(["Automatic", "Manual"])
        self._cwl_mode.setCurrentText(s.get("cwl_mode", "Automatic"))
        form.addRow("CWL Mode:", self._cwl_mode)

        self._drop_thresh = QComboBox()
        self._drop_thresh.addItems(["0.5", "1.0", "1.5", "2.0", "5.0"])
        self._drop_thresh.setCurrentText(str(s.get("cwl_drop_thresh", 1.5)))
        form.addRow("Auto CWL Drop (mm):", self._drop_thresh)

        self._cwl_smooth = QComboBox()
        self._cwl_smooth.addItems(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"])
        self._cwl_smooth.setCurrentText(s.get("cwl_smooth", "SMA-5"))
        form.addRow("CWL Smooth:", self._cwl_smooth)

        self._ui_ref = QComboBox(); self._ui_ref.addItems(["20", "50", "100"])
        self._ui_ref.setCurrentText(str(s.get("ui_refresh_ms", 50)))
        form.addRow("UI Refresh (ms):", self._ui_ref)

        self._ch_ref = QComboBox(); self._ch_ref.addItems(["30", "50", "100", "200"])
        self._ch_ref.setCurrentText(str(s.get("chart_refresh_ms", 100)))
        form.addRow("Chart Refresh (ms):", self._ch_ref)

        layout.addLayout(form)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_reset = QPushButton("Reset to Defaults")
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_reset)
        layout.addLayout(btn_row)

    def _save(self):
        s = self._app.app_settings
        s["ui_theme"]       = self._theme.currentText()
        s["pressure_unit"]  = self._unit.currentText()
        s["cwl_mode"]       = self._cwl_mode.currentText()
        s["cwl_smooth"]     = self._cwl_smooth.currentText()
        try:
            s["avg_window"]      = float(self._avg.currentText())
            s["cwl_drop_thresh"] = float(self._drop_thresh.currentText())
            s["ui_refresh_ms"]   = int(self._ui_ref.currentText())
            s["chart_refresh_ms"]= int(self._ch_ref.currentText())
        except ValueError:
            pass
        save_settings(self._app.conn_params, s)
        self.accept()

    def _reset(self):
        from sensor_core import DEFAULT_APP
        self._app.app_settings.update(DEFAULT_APP)
        save_settings(self._app.conn_params, self._app.app_settings)
        self.reject()  # close; caller should reopen if desired


# ── LineColorsDialog ──────────────────────────────────────────────────────────

class LineColorsDialog(QDialog):
    """Pick RGBA colors for each chart line series."""

    def __init__(self, app: SensorApp, parent=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Chart Line Colors")
        self.setFixedSize(380, 300)
        self._buttons: dict[str, QPushButton] = {}
        self._colors:  dict[str, list[int]]  = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        lc = self._app.app_settings.get(
            "line_colors", {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()})

        for key, label in _LINE_COLOR_LABELS.items():
            col = lc.get(key, list(DEFAULT_LINE_COLORS[key]))
            self._colors[key] = list(col)
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label}:"))
            btn = QPushButton()
            btn.setFixedSize(80, 24)
            self._set_btn_color(btn, col)
            btn.clicked.connect(lambda _, k=key: self._pick_color(k))
            self._buttons[key] = btn
            row.addStretch()
            row.addWidget(btn)
            layout.addLayout(row)

        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply && Save")
        btn_apply.clicked.connect(self._apply)
        btn_reset = QPushButton("Reset Defaults")
        btn_reset.clicked.connect(self._reset)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _set_btn_color(self, btn: QPushButton, rgba: list[int]):
        r, g, b = rgba[:3]
        btn.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #888;"
        )

    def _pick_color(self, key: str):
        initial = _to_qcolor(self._colors[key])
        col = QColorDialog.getColor(initial, self, f"Choose color for {key}",
                                    QColorDialog.ShowAlphaChannel)
        if col.isValid():
            self._colors[key] = _from_qcolor(col)
            self._set_btn_color(self._buttons[key], self._colors[key])

    def _apply(self):
        lc = self._app.app_settings.setdefault("line_colors", {})
        for key, col in self._colors.items():
            lc[key] = col
        save_settings(self._app.conn_params, self._app.app_settings)
        self.accept()

    def _reset(self):
        for key, col in DEFAULT_LINE_COLORS.items():
            self._colors[key] = list(col)
            self._set_btn_color(self._buttons[key], list(col))


# ── ComplianceDialog ──────────────────────────────────────────────────────────

class ComplianceDialog(QDialog):
    """Display EN 14055 compliance check results.

    Calls run_compliance_checks() from sensor_core (pure logic, no GUI).
    Handles EN-06: manual air gap confirmation when WD/CWL not set.
    """

    def __init__(self, app: SensorApp, air_gap_confirmed: list[bool], parent=None):
        super().__init__(parent)
        self._app = app
        self._air_gap_confirmed = air_gap_confirmed  # [False] mutable flag from caller
        self.setWindowTitle("EN 14055 Compliance Check")
        self.setMinimumSize(560, 460)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # Scrollable results area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setSpacing(4)
        scroll.setWidget(results_widget)

        results, air_gap_auto = run_compliance_checks(
            self._app.profile, self._app.flush_results)

        for line in results:
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            if "[PASS]" in line:
                lbl.setStyleSheet("color: #a6e3a1;")
            elif "[FAIL]" in line:
                lbl.setStyleSheet("color: #f38ba8;")
            elif "[WARN]" in line:
                lbl.setStyleSheet("color: #fab45a;")
            else:
                lbl.setStyleSheet("color: #888;")
            results_layout.addWidget(lbl)

        # EN-06: manual air gap confirmation when not auto-computable
        if air_gap_auto is None:
            sep = QFrame(); sep.setFrameShape(QFrame.HLine)
            results_layout.addWidget(sep)
            results_layout.addWidget(
                QLabel("Air gap a (§5.2.7): set Water Discharge height to auto-compute,\n"
                       "or confirm physical ruler measurement ≥ 20 mm:"))
            self._ag_lbl = QLabel(
                "[PASS] Air gap manually confirmed ≥ 20 mm"
                if self._air_gap_confirmed[0]
                else "[----] Air gap: not yet confirmed (tick to acknowledge)")
            self._ag_lbl.setStyleSheet(
                "color: #a6e3a1;" if self._air_gap_confirmed[0] else "color: #888;")
            results_layout.addWidget(self._ag_lbl)

            self._ag_check = QCheckBox("I confirm air gap ≥ 20 mm (measured with ruler)")
            self._ag_check.setChecked(self._air_gap_confirmed[0])
            self._ag_check.toggled.connect(self._on_ag_toggle)
            results_layout.addWidget(self._ag_check)

        results_layout.addStretch()
        layout.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)

    def _on_ag_toggle(self, checked: bool):
        self._air_gap_confirmed[0] = checked
        if checked:
            self._ag_lbl.setText("[PASS] Air gap manually confirmed ≥ 20 mm")
            self._ag_lbl.setStyleSheet("color: #a6e3a1;")
        else:
            self._ag_lbl.setText("[----] Air gap: not yet confirmed (tick to acknowledge)")
            self._ag_lbl.setStyleSheet("color: #888;")


# ── Shared widget helpers ─────────────────────────────────────────────────────

def _separator() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f

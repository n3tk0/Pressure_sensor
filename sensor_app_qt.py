"""
sensor_app_qt.py — EN 14055 Cistern Analytics — PySide6 / PyQtGraph edition.

Replaces sensor_app.py (DearPyGui) while sharing all business logic via
sensor_core.py.  Single-file PyInstaller exe: see sensor_app_qt.spec.

Entry point:  python sensor_app_qt.py
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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QFrame,
    QGroupBox, QScrollArea, QFileDialog, QMessageBox,
    QStatusBar, QMenuBar,
)

from sensor_core import (
    SensorApp, CisternProfile,
    PRESSURE_UNITS, EXPORT_DIR, CONFIG_DIR,
    DEFAULT_APP, DEFAULT_LINE_COLORS,
    p_format, save_settings, _rebuild_interp_cache,
    FONT_PATH_REGULAR, FONT_PATH_BOLD,
    smooth,
)
from sensor_plot_widget import SensorPlotWidget
from dialogs_qt import (
    ConnectionDialog, CalibrationDialog,
    ProgramSettingsDialog, LineColorsDialog, ComplianceDialog,
)

# ── per-session UI state (not saved) ────────────────────────────────────────
_air_gap_confirmed: list[bool] = [False]


# ── QSS themes ───────────────────────────────────────────────────────────────

_QSS_DARK = """
QMainWindow, QWidget#central { background: #1e1e2e; color: #cdd6f4; }
QWidget { background: #1e1e2e; color: #cdd6f4; font-size: 13px; }
QMenuBar { background: #232336; color: #cdd6f4; }
QMenuBar::item:selected { background: #313347; }
QMenu { background: #2a2a3d; color: #cdd6f4; border: 1px solid #45475a; }
QMenu::item:selected { background: #313347; }
QGroupBox {
    border: 1px solid #45475a; border-radius: 6px;
    margin-top: 8px; padding-top: 4px; color: #89b4fa;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QPushButton {
    background: #313347; color: #cdd6f4; border: none;
    border-radius: 5px; padding: 4px 10px;
}
QPushButton:hover { background: #3f3f5a; }
QPushButton:pressed { background: #4a4a6a; }
QPushButton#action { background: #285a5a; color: #f0f0f0; }
QPushButton#action:hover { background: #357373; }
QPushButton#danger { background: #642832; color: #f0f0f0; }
QPushButton#danger:hover { background: #823741; }
QPushButton#success { background: #235a37; color: #f0f0f0; }
QPushButton#success:hover { background: #2e7346; }
QComboBox {
    background: #313347; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 4px; padding: 2px 8px;
}
QComboBox QAbstractItemView { background: #2a2a3d; color: #cdd6f4; }
QLabel#lbl_h  { color: #89b4fa; font-size: 20px; font-weight: bold; }
QLabel#lbl_v  { color: #a6e3a1; font-size: 18px; }
QLabel#lbl_p  { color: #888; }
QLabel#lbl_f  { color: #fab45a; }
QLabel#lbl_temp { color: #888; }
QLabel.section_hdr { color: #89b4fa; font-weight: bold; }
QLabel.green  { color: #a6e3a1; }
QLabel.red    { color: #f38ba8; }
QLabel.orange { color: #fab45a; }
QLabel.blue   { color: #89b4fa; }
QLabel.gray   { color: #888; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #45475a; }
QScrollBar:vertical { background: #232336; width: 10px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 4px; }
QStatusBar { background: #232336; color: #888; }
"""

_QSS_LIGHT = """
QMainWindow, QWidget#central { background: #eff1f5; color: #4c4f69; }
QWidget { background: #eff1f5; color: #4c4f69; font-size: 13px; }
QMenuBar { background: #d2d7e4; color: #4c4f69; }
QMenuBar::item:selected { background: #bec4d6; }
QMenu { background: #e6e9f0; color: #4c4f69; border: 1px solid #b4b8cc; }
QMenu::item:selected { background: #bec4d6; }
QGroupBox {
    border: 1px solid #b4b8cc; border-radius: 6px;
    margin-top: 8px; padding-top: 4px; color: #1c5fd2;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QPushButton {
    background: #b4b9cd; color: #4c4f69; border: none;
    border-radius: 5px; padding: 4px 10px;
}
QPushButton:hover { background: #a2a8bf; }
QPushButton:pressed { background: #9097b2; }
QPushButton#action { background: #286060; color: #fff; }
QPushButton#action:hover { background: #347878; }
QPushButton#danger { background: #be2644; color: #fff; }
QPushButton#danger:hover { background: #d43050; }
QPushButton#success { background: #198237; color: #fff; }
QPushButton#success:hover { background: #20a346; }
QComboBox {
    background: #dce0ec; color: #4c4f69; border: 1px solid #b4b8cc;
    border-radius: 4px; padding: 2px 8px;
}
QComboBox QAbstractItemView { background: #e6e9f0; color: #4c4f69; }
QLabel#lbl_h  { color: #1c5fd2; font-size: 20px; font-weight: bold; }
QLabel#lbl_v  { color: #198237; font-size: 18px; }
QLabel#lbl_p  { color: #5a5a73; }
QLabel#lbl_f  { color: #af5800; }
QLabel#lbl_temp { color: #5a5a73; }
QLabel.section_hdr { color: #1c5fd2; font-weight: bold; }
QLabel.green  { color: #198237; }
QLabel.red    { color: #be2644; }
QLabel.orange { color: #af5800; }
QLabel.blue   { color: #1c5fd2; }
QLabel.gray   { color: #5a5a73; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #b4b8cc; }
QScrollBar:vertical { background: #d2d7e4; width: 10px; }
QScrollBar::handle:vertical { background: #9097b2; border-radius: 4px; }
QStatusBar { background: #d2d7e4; color: #5a5a73; }
"""


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


def _btn(label: str, style: str = "") -> QPushButton:
    b = QPushButton(label)
    if style:
        b.setObjectName(style)
    return b


def _lbl(text: str, css_class: str = "") -> QLabel:
    lb = QLabel(text)
    if css_class:
        lb.setProperty("class", css_class)
    return lb


# ── Main window ───────────────────────────────────────────────────────────────

class SensorMainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._app = SensorApp()
        self.setWindowTitle("EN 14055 Cistern Analytics — ifm PI1789")
        self.resize(1280, 860)

        self._toast_clear_at: float = 0.0
        self._toast_msg: str = ""
        self._left_visible: bool = True

        self._build_menus()
        self._build_central()
        self._build_status_bar()
        self._apply_font()
        self._apply_theme(self._app.app_settings.get("ui_theme", "Dark"))

        # Two QTimers replacing DPG frame_callback
        self._ui_timer    = QTimer(self)
        self._chart_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._tick_ui)
        self._chart_timer.timeout.connect(self._tick_chart)
        self._start_timers()

    # ── Timers ────────────────────────────────────────────────────────

    def _start_timers(self):
        ui_ms    = self._app.app_settings.get("ui_refresh_ms",    50)
        chart_ms = self._app.app_settings.get("chart_refresh_ms", 100)
        self._ui_timer.start(ui_ms)
        self._chart_timer.start(chart_ms)

    def _restart_timers(self):
        self._ui_timer.stop()
        self._chart_timer.stop()
        self._start_timers()

    # ── Menu bar ──────────────────────────────────────────────────────

    def _build_menus(self):
        mb = self.menuBar()

        # File menu
        m_file = mb.addMenu("&File")
        a = QAction("Load Profile…", self); a.triggered.connect(self._load_profile); m_file.addAction(a)
        a = QAction("Save Profile As…", self); a.triggered.connect(self._save_profile); m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("Set as Default Profile", self); a.triggered.connect(self._save_as_default); m_file.addAction(a)
        a = QAction("Clear Default Profile", self);  a.triggered.connect(self._clear_default);   m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("Export Screenshot…", self); a.triggered.connect(self._export_screenshot); m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("E&xit", self); a.triggered.connect(self.close); m_file.addAction(a)

        # Settings menu
        m_set = mb.addMenu("&Settings")
        a = QAction("Hardware Connection…",       self); a.triggered.connect(self._open_conn_dlg);    m_set.addAction(a)
        a = QAction("Edit Calibration Profile…",  self); a.triggered.connect(self._open_cal_dlg);     m_set.addAction(a)
        a = QAction("Program Settings…",          self); a.triggered.connect(self._open_prog_dlg);    m_set.addAction(a)
        a = QAction("Chart Line Colors…",         self); a.triggered.connect(self._open_colors_dlg);  m_set.addAction(a)

        # Test menu
        m_test = mb.addMenu("&Test")
        a = QAction("EN 14055 Compliance Check", self); a.triggered.connect(self._check_compliance); m_test.addAction(a)

    # ── Central widget ────────────────────────────────────────────────

    def _build_central(self):
        central = QWidget(); central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Top bar: collapse + profile label + connection status + connect button
        top = QHBoxLayout()
        self._btn_collapse = QPushButton("◀")
        self._btn_collapse.setFixedWidth(28)
        self._btn_collapse.clicked.connect(self._toggle_left_panel)
        self._lbl_profile = QLabel("Active Profile: Untitled Profile")

        self._lbl_conn_icon = QLabel("●"); self._lbl_conn_icon.setProperty("class", "gray")
        self._lbl_conn      = QLabel("Disconnected");  self._lbl_conn.setProperty("class", "gray")
        self._btn_connect   = _btn("Connect Sensor")
        self._btn_connect.clicked.connect(self._toggle_connect)

        top.addWidget(self._btn_collapse)
        top.addWidget(self._lbl_profile, 1)
        conn_row = QHBoxLayout(); conn_row.setSpacing(4)
        conn_row.addWidget(self._lbl_conn_icon)
        conn_row.addWidget(self._lbl_conn)
        top.addLayout(conn_row)
        top.addWidget(self._btn_connect)
        root.addLayout(top)
        root.addWidget(_sep())

        # Splitter: left panel | right chart panel
        splitter = QSplitter(Qt.Horizontal)
        self._left_panel  = self._build_left_panel()
        self._right_panel = self._build_right_panel()
        splitter.addWidget(self._left_panel)
        splitter.addWidget(self._right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 900])
        root.addWidget(splitter, 1)

    # ── Left panel ────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget(); panel.setFixedWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        # Live data
        g_live = QGroupBox("  LIVE DATA")
        g_live.setProperty("class", "section_hdr")
        live_layout = QVBoxLayout(g_live)
        row = QHBoxLayout()
        left_col = QVBoxLayout()
        self._lbl_h    = QLabel("0.0 mm");     self._lbl_h.setObjectName("lbl_h")
        self._lbl_v    = QLabel("0.00 L");     self._lbl_v.setObjectName("lbl_v")
        self._lbl_p    = QLabel("0.0000 bar"); self._lbl_p.setObjectName("lbl_p")
        left_col.addWidget(self._lbl_h)
        left_col.addWidget(self._lbl_v)
        left_col.addWidget(self._lbl_p)
        right_col = QVBoxLayout()
        self._lbl_temp = QLabel("-- °C");      self._lbl_temp.setObjectName("lbl_temp")
        self._lbl_f    = QLabel("0.000 L/s");  self._lbl_f.setObjectName("lbl_f")
        right_col.addWidget(self._lbl_temp)
        right_col.addWidget(self._lbl_f)
        row.addLayout(left_col)
        row.addSpacing(12)
        row.addLayout(right_col)
        row.addStretch()
        live_layout.addLayout(row)
        layout.addWidget(g_live)

        # EN 14055 Limits
        g_lim = QGroupBox("  EN 14055 LIMITS")
        lim_layout = QVBoxLayout(g_lim)

        avg = self._app.app_settings.get("avg_window", 0.5)
        btn_row1 = QHBoxLayout()
        self._btn_nwl    = _btn(f"Set NWL ({avg}s)", "action")
        self._btn_menis  = _btn(f"Set Meniscus ({avg}s)", "action")
        self._btn_nwl.clicked.connect(self._set_nwl)
        self._btn_menis.clicked.connect(self._set_meniscus)
        btn_row1.addWidget(self._btn_nwl)
        btn_row1.addWidget(self._btn_menis)
        lim_layout.addLayout(btn_row1)

        self._btn_auto_cwl = _btn("Auto-detect MWL/CWL", "action")
        self._btn_auto_cwl.clicked.connect(self._arm_cwl_auto)
        lim_layout.addWidget(self._btn_auto_cwl)

        self._btn_manual_mwlcwl = _btn("Manual MWL/CWL", "action")
        self._btn_manual_mwlcwl.clicked.connect(self._manual_mwl_cwl)
        lim_layout.addWidget(self._btn_manual_mwlcwl)

        self._btn_manual_rwl = _btn("Start RWL 2s Timer", "action")
        self._btn_manual_rwl.clicked.connect(self._manual_rwl)
        self._btn_manual_rwl.setVisible(False)
        lim_layout.addWidget(self._btn_manual_rwl)

        # Two-column limits grid
        grid = QGridLayout(); grid.setHorizontalSpacing(12)
        def _g(text, css=""): l = QLabel(text); l.setProperty("class", css or "gray"); return l
        grid.addWidget(_g("NWL (fill):"),       0, 0)
        self._lbl_mwl           = QLabel("—"); grid.addWidget(self._lbl_mwl, 1, 0)
        grid.addWidget(_g("MWL (fault):"),      2, 0)
        self._lbl_mwl_fault     = QLabel("—"); grid.addWidget(self._lbl_mwl_fault, 3, 0)
        grid.addWidget(_g("CWL (2s):"),         4, 0)
        self._lbl_cwl_val       = QLabel("—"); grid.addWidget(self._lbl_cwl_val, 5, 0)
        grid.addWidget(_g("Residual WL:"),      6, 0)
        self._lbl_residual      = QLabel("0.0 mm"); grid.addWidget(self._lbl_residual, 7, 0)

        grid.addWidget(_g("Meniscus:"),         0, 1)
        self._lbl_menis_val     = QLabel("0.0 mm"); grid.addWidget(self._lbl_menis_val, 1, 1)
        grid.addWidget(_g("Overflow:"),         2, 1)
        self._lbl_overflow      = QLabel("0.0 mm"); grid.addWidget(self._lbl_overflow, 3, 1)
        grid.addWidget(_g("Safety margin c:"),  4, 1)
        self._lbl_sm_static     = QLabel("—");  grid.addWidget(self._lbl_sm_static, 5, 1)
        grid.addWidget(_g("Live headroom:"),    6, 1)
        self._lbl_headroom      = QLabel("— mm"); grid.addWidget(self._lbl_headroom, 7, 1)
        lim_layout.addLayout(grid)

        self._lbl_cwl_status    = QLabel("CWL: — (capture during fault test)")
        self._lbl_cwl_auto_st   = QLabel("CWL: IDLE — arm while at MWL")
        self._lbl_rwl_st        = QLabel("RWL: IDLE (set NWL to arm)")
        for lb in (self._lbl_cwl_status, self._lbl_cwl_auto_st, self._lbl_rwl_st):
            lb.setProperty("class", "gray")
            lim_layout.addWidget(lb)
        layout.addWidget(g_lim)

        # Flush test
        g_flush = QGroupBox("  FLUSH TEST  (EN 14055)")
        flush_layout = QVBoxLayout(g_flush)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type:"))
        self._combo_flush_type = QComboBox()
        self._combo_flush_type.addItems(["Full Flush", "Part Flush"])
        type_row.addWidget(self._combo_flush_type, 1)
        flush_layout.addLayout(type_row)

        self._btn_flush = _btn("Start Flush Measurement", "success")
        self._btn_flush.clicked.connect(self._toggle_flush_measure)
        flush_layout.addWidget(self._btn_flush)
        flush_layout.addWidget(QLabel("* EN col = rate ignoring first 1L and last 2L"))

        # Flush results table (scroll area)
        self._flush_scroll = QScrollArea()
        self._flush_scroll.setWidgetResizable(True)
        self._flush_scroll.setFixedHeight(148)
        self._flush_inner  = QWidget()
        self._flush_vbox   = QVBoxLayout(self._flush_inner)
        self._flush_vbox.setSpacing(2)
        self._flush_no_data = QLabel("No measurements yet.")
        self._flush_no_data.setProperty("class", "gray")
        self._flush_vbox.addWidget(self._flush_no_data)
        self._flush_vbox.addStretch()
        self._flush_scroll.setWidget(self._flush_inner)
        flush_layout.addWidget(self._flush_scroll)

        flush_btns = QHBoxLayout()
        btn_clr = _btn("Clear All", "danger")
        btn_clr.clicked.connect(self._clear_flush)
        btn_comply = _btn("Compliance Check")
        btn_comply.clicked.connect(self._check_compliance)
        flush_btns.addWidget(btn_clr)
        flush_btns.addWidget(btn_comply, 1)
        flush_layout.addLayout(flush_btns)
        layout.addWidget(g_flush)

        # Data log
        g_log = QGroupBox("  DATA LOG")
        log_layout = QVBoxLayout(g_log)
        self._btn_log = _btn("Start Data Log (CSV)", "success")
        self._btn_log.clicked.connect(self._toggle_log)
        log_layout.addWidget(self._btn_log)
        layout.addWidget(g_log)

        layout.addStretch()
        return panel

    # ── Right panel (chart) ───────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toolbar
        tb = QHBoxLayout()
        tb.setSpacing(6)

        tb.addWidget(QLabel("Axis:"))
        self._combo_plot = QComboBox()
        self._combo_plot.addItems(["Height (mm)", "Volume (L)", "Flow Rate (L/s)"])
        self._combo_plot.currentTextChanged.connect(self._on_plot_mode_changed)
        tb.addWidget(self._combo_plot)

        tb.addWidget(QLabel("Window:"))
        self._combo_win = QComboBox()
        self._combo_win.addItems(list(SensorPlotWidget.WINDOW_OPTIONS.keys()))
        self._combo_win.setCurrentText("30 s")
        self._combo_win.currentTextChanged.connect(self._on_window_changed)
        tb.addWidget(self._combo_win)

        tb.addWidget(QLabel("Smooth:"))
        self._combo_smooth = QComboBox()
        self._combo_smooth.addItems(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"])
        self._combo_smooth.currentTextChanged.connect(self._on_smooth_changed)
        tb.addWidget(self._combo_smooth)

        self._chk_autoscroll = QCheckBox("Auto-scroll")
        self._chk_autoscroll.setChecked(True)
        self._chk_autoscroll.toggled.connect(self._on_autoscroll_changed)
        tb.addWidget(self._chk_autoscroll)

        self._btn_pause = _btn("Pause")
        self._btn_pause.clicked.connect(self._toggle_pause)
        tb.addWidget(self._btn_pause)

        btn_shot = _btn("Screenshot")
        btn_shot.clicked.connect(self._export_screenshot)
        tb.addWidget(btn_shot)

        btn_colors = _btn("Colors")
        btn_colors.clicked.connect(self._open_colors_dlg)
        tb.addWidget(btn_colors)

        tb.addSpacing(8)
        tb.addWidget(QLabel("Delta:"))
        self._lbl_delta = QLabel("---")
        self._lbl_delta.setProperty("class", "blue")
        tb.addWidget(self._lbl_delta)
        btn_clr_delta = _btn("Clear")
        btn_clr_delta.setFixedWidth(52)
        btn_clr_delta.clicked.connect(self._clear_delta)
        tb.addWidget(btn_clr_delta)
        tb.addStretch()

        layout.addLayout(tb)

        # Plot widget
        self._plot = SensorPlotWidget(self._app)
        self._plot.mwl_clicked.connect(self._on_mwl_clicked)
        self._plot.cwl_clicked.connect(self._on_cwl_clicked)
        layout.addWidget(self._plot, 1)
        return panel

    # ── Status bar ────────────────────────────────────────────────────

    def _build_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_label = QLabel("Ready")
        sb.addWidget(self._sb_label)

    # ── Font ──────────────────────────────────────────────────────────

    def _apply_font(self):
        font_path = FONT_PATH_REGULAR or FONT_PATH_BOLD
        if font_path:
            fid = QFontDatabase.addApplicationFont(font_path)
            if fid >= 0:
                families = QFontDatabase.applicationFontFamilies(fid)
                if families:
                    f = QFont(families[0], 10)
                    QApplication.setFont(f)

    # ── Theme ─────────────────────────────────────────────────────────

    def _apply_theme(self, mode: str):
        self._app.app_settings["ui_theme"] = mode
        QApplication.instance().setStyleSheet(
            _QSS_DARK if mode != "Light" else _QSS_LIGHT)

    # ── Toggle left panel ─────────────────────────────────────────────

    def _toggle_left_panel(self):
        self._left_visible = not self._left_visible
        self._left_panel.setVisible(self._left_visible)
        self._btn_collapse.setText("▶" if not self._left_visible else "◀")

    # ── Connection ────────────────────────────────────────────────────

    def _toggle_connect(self):
        if self._app.is_connected:
            self._app.disconnect()
            self._btn_connect.setText("Connect Sensor")
            self._btn_connect.setObjectName("")
            self._btn_connect.style().unpolish(self._btn_connect)
            self._btn_connect.style().polish(self._btn_connect)
            self._lbl_conn.setText("Disconnected")
            self._lbl_conn.setProperty("class", "gray")
            self._lbl_conn_icon.setProperty("class", "gray")
            self._refresh_label_style(self._lbl_conn)
            self._refresh_label_style(self._lbl_conn_icon)
        else:
            self._app.last_error = ""
            self._app.connect()
            if self._app.is_connected:
                self._btn_connect.setText("Disconnect")
                self._btn_connect.setObjectName("danger")
                self._btn_connect.style().unpolish(self._btn_connect)
                self._btn_connect.style().polish(self._btn_connect)
                cp = self._app.conn_params
                self._lbl_conn.setText(f"{cp['port']}  {cp['baud']}bd")
                self._lbl_conn.setProperty("class", "green")
                self._lbl_conn_icon.setProperty("class", "green")
            elif self._app.last_error:
                self._lbl_conn.setText(self._app.last_error)
                self._lbl_conn.setProperty("class", "red")
                self._lbl_conn_icon.setProperty("class", "red")
            self._refresh_label_style(self._lbl_conn)
            self._refresh_label_style(self._lbl_conn_icon)

    @staticmethod
    def _refresh_label_style(lbl: QLabel):
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)

    # ── EN 14055 limit capture buttons ───────────────────────────────

    def _set_nwl(self):
        val = self._app.get_avg_height()
        self._app.profile.mwl = val
        self._app.cwl_state = "ARMED"
        self._app.cwl_peak = val
        self._refresh_limits()
        self._show_toast(f"NWL set: {val:.1f} mm")

    def _set_meniscus(self):
        if self._app.profile.overflow <= 0:
            self._show_toast("⚠ Set Overflow level in Calibration first!")
            return
        measured = self._app.get_avg_height()
        self._app.profile.meniscus = measured - self._app.profile.overflow
        self._refresh_limits()
        self._show_toast(f"Meniscus set: {self._app.profile.meniscus:+.1f} mm above OF")

    def _arm_cwl_auto(self):
        if self._app.profile.overflow <= 0:
            self._show_toast("⚠ Set Overflow level in Calibration first!")
            return
        self._app.cwl_auto_state = "ARMED"
        self._app.cwl_auto_peak  = self._app.get_avg_height()
        self._app.cwl_auto_timer = 0.0
        self._refresh_limits()
        self._show_toast("CWL armed — water should be at MWL. Cut supply when ready.")

    def _manual_mwl_cwl(self):
        if self._app.manual_mwl_cwl_pending:
            self._app.manual_mwl_cwl_pending = False
            self._btn_manual_mwlcwl.setText("Manual MWL/CWL")
            self._btn_manual_mwlcwl.setObjectName("action")
            self._btn_manual_mwlcwl.style().unpolish(self._btn_manual_mwlcwl)
            self._btn_manual_mwlcwl.style().polish(self._btn_manual_mwlcwl)
            self._plot.exit_manual_select()
            if self._plot.is_paused():
                self._toggle_pause()
            return
        if self._app.profile.overflow <= 0:
            self._show_toast("⚠ Set Overflow level in Calibration first!")
            return
        if not self._plot.is_paused():
            self._toggle_pause()
        self._app.manual_mwl_cwl_pending = True
        self._btn_manual_mwlcwl.setText("Cancel Manual MWL/CWL")
        self._btn_manual_mwlcwl.setObjectName("danger")
        self._btn_manual_mwlcwl.style().unpolish(self._btn_manual_mwlcwl)
        self._btn_manual_mwlcwl.style().polish(self._btn_manual_mwlcwl)
        self._plot.enter_manual_select("MWL")
        self._show_toast("Chart paused — click the MWL moment on the graph")

    def _on_mwl_clicked(self, mwl_val: float):
        """Slot: SensorPlotWidget emitted mwl_clicked after user clicked chart."""
        ts = self._plot._last_t
        ys = self._plot._last_y
        t_click_idx = len(ts) - 1  # approx: mwl_val already averaged in widget
        # Compute CWL = smoothed value at t_click + 2s
        cwl = None
        if ts and ys:
            # Find t corresponding to mwl_val: use nearest by value
            best_i = min(range(len(ys)), key=lambda i: abs(ys[i] - mwl_val))
            t_click = ts[best_i]
            target_t = t_click + 2.0
            for t, y in zip(ts, ys):
                if t >= target_t:
                    cwl = y
                    break
            if cwl is None:
                cwl = ys[-1] if ys else mwl_val
        else:
            cwl = mwl_val
        self._app.profile.mwl_fault = mwl_val
        self._app.profile.cwl = cwl
        self._app.manual_mwl_cwl_pending = False
        self._btn_manual_mwlcwl.setText("Manual MWL/CWL")
        self._btn_manual_mwlcwl.setObjectName("action")
        self._btn_manual_mwlcwl.style().unpolish(self._btn_manual_mwlcwl)
        self._btn_manual_mwlcwl.style().polish(self._btn_manual_mwlcwl)
        self._refresh_limits()
        self._show_toast(f"MWL = {mwl_val:.1f} mm  |  CWL = {cwl:.1f} mm")
        if self._plot.is_paused():
            self._toggle_pause()

    def _on_cwl_clicked(self, cwl_val: float):
        self._app.profile.cwl = cwl_val
        self._app.manual_mwl_cwl_pending = False
        self._refresh_limits()
        self._show_toast(f"CWL set: {cwl_val:.1f} mm")

    def _manual_rwl(self):
        """Manual RWL: start 2-second timer, then capture residual level."""
        self._app.cwl_state = "WAITING"
        self._app.cwl_timer = time.time()

    # ── Chart controls ────────────────────────────────────────────────

    def _toggle_pause(self):
        paused = not self._plot.is_paused()
        self._plot.set_paused(paused)
        if paused:
            self._btn_pause.setText("Resume")
            self._btn_pause.setObjectName("success")
        else:
            self._btn_pause.setText("Pause")
            self._btn_pause.setObjectName("")
        self._btn_pause.style().unpolish(self._btn_pause)
        self._btn_pause.style().polish(self._btn_pause)

    def _on_plot_mode_changed(self, text: str):
        self._plot.set_mode(text)

    def _on_window_changed(self, key: str):
        self._plot.set_window(key)

    def _on_smooth_changed(self, alg: str):
        self._plot.set_smooth(alg)

    def _on_autoscroll_changed(self, checked: bool):
        self._plot.set_auto_scroll(checked)

    def _clear_delta(self):
        self._lbl_delta.setText("---")

    # ── Flush measurement ─────────────────────────────────────────────

    def _toggle_flush_measure(self):
        import copy
        if not self._app.flush_measuring:
            # Start
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
            self._btn_flush.setText("Stop Flush Measurement")
            self._btn_flush.setObjectName("danger")
            self._btn_flush.style().unpolish(self._btn_flush)
            self._btn_flush.style().polish(self._btn_flush)
        else:
            # Stop
            with self._app._flush_lock:
                self._app.flush_measuring = False
                elapsed = time.time() - self._app.flush_start_time
                with self._app.data_lock:
                    v_now = self._app.current_volume
                    h_now = self._app.current_height
                vol = abs(self._app.flush_start_vol - v_now)
                ftype = self._combo_flush_type.currentText()
                # EN14055 flow rate: ignore first 1 L and last 2 L
                en_rate = None
                hist = self._app.flush_vol_history
                if hist and vol > 3.0:
                    start_v = self._app.flush_start_vol
                    trim_pts = [(t, v, h) for t, v, h in hist
                                if abs(start_v - v) >= 1.0 and abs(start_v - v) <= vol - 2.0]
                    if len(trim_pts) >= 2:
                        dt = trim_pts[-1][0] - trim_pts[0][0]
                        dv = abs(trim_pts[0][1] - trim_pts[-1][1])
                        if dt > 0:
                            en_rate = dv / dt
                temp_c = self._app.current_temperature
                self._app.flush_results.append({
                    "type":         ftype,
                    "vol":          vol,
                    "time":         elapsed,
                    "en14055_rate": en_rate,
                    "temp_c":       temp_c,
                })
            self._btn_flush.setText("Start Flush Measurement")
            self._btn_flush.setObjectName("success")
            self._btn_flush.style().unpolish(self._btn_flush)
            self._btn_flush.style().polish(self._btn_flush)
            self._refresh_flush_table()

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
            self._toggle_flush_measure()

    def _clear_flush(self):
        self._app.flush_results.clear()
        self._refresh_flush_table()

    def _refresh_flush_table(self):
        # Remove old rows (keep stretch at end)
        while self._flush_vbox.count() > 1:
            item = self._flush_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._app.flush_results:
            self._flush_no_data.setVisible(True)
            self._flush_vbox.insertWidget(0, self._flush_no_data)
            return
        self._flush_no_data.setVisible(False)
        for i, r in enumerate(self._app.flush_results):
            rate_s = f"{r['en14055_rate']:.3f}" if r.get('en14055_rate') is not None else "—"
            temp_s = f"{r['temp_c']:.1f}°C" if r.get('temp_c') is not None else "—"
            lbl = QLabel(
                f"#{i+1} {r['type'][:4]}  "
                f"{r['vol']:.2f}L  {r['time']:.1f}s  EN:{rate_s}L/s  {temp_s}"
            )
            lbl.setProperty("class", "gray")
            self._flush_vbox.insertWidget(i, lbl)

    # ── CSV data log ──────────────────────────────────────────────────

    def _toggle_log(self):
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
                self._btn_log.setText("Stop Data Log")
                self._btn_log.setObjectName("danger")
                self._btn_log.style().unpolish(self._btn_log)
                self._btn_log.style().polish(self._btn_log)
                self._show_toast(f"Logging to: {Path(fn).name}")
            except Exception as e:
                logging.warning(f"Failed to open CSV log: {e}")
                self._show_toast(f"Log failed: {e}")
        else:
            self._app.is_logging = False
            self._btn_log.setText("Start Data Log (CSV)")
            self._btn_log.setObjectName("success")
            self._btn_log.style().unpolish(self._btn_log)
            self._btn_log.style().polish(self._btn_log)
            with self._app._csv_lock:
                if self._app.csv_file:
                    try:
                        self._app.csv_file.flush()
                        self._app.csv_file.close()
                    except OSError as e:
                        logging.warning(f"CSV close error: {e}")
                    finally:
                        self._app.csv_file   = None
                        self._app.csv_writer = None
                        self._app._csv_row_count = 0
            self._show_toast("Data log stopped.")

    # ── File I/O ──────────────────────────────────────────────────────

    def _load_profile(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Load Profile", str(CONFIG_DIR), "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, encoding="utf-8") as f:
                self._app.profile = CisternProfile.from_dict(json.load(f))
            _rebuild_interp_cache(self._app.profile.points)
            self._app.recalc_from_pressure()
            self._refresh_limits()
            self._show_toast(f"Profile loaded: {self._app.profile.name}")
        except Exception as e:
            QMessageBox.critical(self, "Load Failed", str(e))

    def _save_profile(self):
        fp, _ = QFileDialog.getSaveFileName(
            self, "Save Profile As", str(CONFIG_DIR / f"{self._app.profile.name}.json"),
            "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(self._app.profile.to_dict(), f, indent=4)
            self._show_toast(f"Saved: {Path(fp).name}")
        except OSError as e:
            QMessageBox.critical(self, "Save Failed", str(e))

    def _save_as_default(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        fp = CONFIG_DIR / "default_profile.json"
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(self._app.profile.to_dict(), f, indent=4)
            self._show_toast(f"Default profile set: {self._app.profile.name}")
        except OSError as e:
            logging.error(f"Failed to save default profile: {e}")

    def _clear_default(self):
        fp = CONFIG_DIR / "default_profile.json"
        if fp.exists():
            fp.unlink()
        self._show_toast("Default profile cleared.")

    def _export_screenshot(self):
        EXPORT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = str(EXPORT_DIR / f"screenshot_{ts}.png")
        try:
            screen = QApplication.primaryScreen()
            pix = screen.grabWindow(int(self.winId()))
            pix.save(fp, "PNG")
            self._show_toast(f"Screenshot: {Path(fp).name}")
        except Exception as e:
            QMessageBox.warning(self, "Screenshot Failed", str(e))

    # ── Dialog openers ────────────────────────────────────────────────

    def _open_conn_dlg(self):
        dlg = ConnectionDialog(self._app, self)
        dlg.exec()

    def _open_cal_dlg(self):
        dlg = CalibrationDialog(self._app, self)
        if dlg.exec():
            self._refresh_limits()

    def _open_prog_dlg(self):
        old_theme = self._app.app_settings.get("ui_theme", "Dark")
        dlg = ProgramSettingsDialog(self._app, self)
        if dlg.exec():
            new_theme = self._app.app_settings.get("ui_theme", "Dark")
            if new_theme != old_theme:
                self._apply_theme(new_theme)
            self._restart_timers()
            self._refresh_limits()

    def _open_colors_dlg(self):
        dlg = LineColorsDialog(self._app, self)
        if dlg.exec():
            self._plot.apply_line_colors()

    def _check_compliance(self):
        dlg = ComplianceDialog(self._app, _air_gap_confirmed, self)
        dlg.exec()


    # ── Timer tick: UI labels ─────────────────────────────────────────

    def _tick_ui(self):
        now = time.time()
        with self._app.data_lock:
            h = self._app.current_height
            v = self._app.current_volume
            p = self._app.current_pressure
            f = self._app.current_flow
            temp = self._app.current_temperature

        unit = self._app.app_settings.get("pressure_unit", "bar")
        self._lbl_h.setText(f"{h:.1f} mm")
        self._lbl_v.setText(f"{v:.2f} L")
        self._lbl_p.setText(p_format(p, unit=unit))
        self._lbl_f.setText(f"{f:.3f} L/s")
        self._lbl_temp.setText(f"{temp:.1f} °C" if temp is not None else "-- °C")

        # Live headroom to overflow
        of = self._app.profile.overflow
        if of > 0:
            sm = of - h
            css = "green" if sm >= 20 else ("orange" if sm >= 5 else "red")
            self._lbl_headroom.setText(f"{sm:.1f} mm")
            self._set_label_class(self._lbl_headroom, css)
        else:
            self._lbl_headroom.setText("— mm")

        # RWL state
        thresh = self._app.app_settings.get("cwl_drop_thresh", 1.5)
        cs = self._app.cwl_state
        if cs == "ARMED":
            if self._app.app_settings.get("cwl_mode") == "Automatic":
                self._lbl_rwl_st.setText(f"RWL: ARMED (drop ≥ {thresh}mm)")
            else:
                self._lbl_rwl_st.setText("RWL: ARMED (Manual)")
            self._set_label_class(self._lbl_rwl_st, "blue")
        elif cs == "WAITING":
            rem = max(0.0, 2.0 - (now - self._app.cwl_timer))
            self._lbl_rwl_st.setText(f"RWL: TIMER {rem:.1f}s")
            self._set_label_class(self._lbl_rwl_st, "orange")
        elif cs == "DONE":
            self._lbl_rwl_st.setText(f"RWL: {self._app.profile.residual_wl:.1f}mm captured")
            self._set_label_class(self._lbl_rwl_st, "green")
        else:
            self._lbl_rwl_st.setText("RWL: IDLE (set NWL to arm)")
            self._set_label_class(self._lbl_rwl_st, "gray")

        # CWL auto state
        if self._app.manual_mwl_cwl_pending:
            self._lbl_cwl_auto_st.setText("MWL/CWL: click the MWL point on chart")
            self._set_label_class(self._lbl_cwl_auto_st, "orange")
        elif self._app.cwl_auto_state == "ARMED":
            self._lbl_cwl_auto_st.setText("CWL: ARMED — watching for drop ≥1.5mm")
            self._set_label_class(self._lbl_cwl_auto_st, "blue")
        elif self._app.cwl_auto_state == "WAITING":
            rem = max(0.0, 2.0 - (now - self._app.cwl_auto_timer))
            self._lbl_cwl_auto_st.setText(f"CWL: 2s WINDOW — {rem:.1f}s left")
            self._set_label_class(self._lbl_cwl_auto_st, "orange")
        elif self._app.cwl_auto_state == "DONE":
            diff = self._app.profile.cwl - of if of > 0 else 0
            self._lbl_cwl_auto_st.setText(
                f"CWL: {self._app.profile.cwl:.1f}mm  ({diff:+.1f}mm OF)")
            self._set_label_class(self._lbl_cwl_auto_st, "green")
        else:
            self._lbl_cwl_auto_st.setText("MWL/CWL: IDLE — use Auto-detect or Manual")
            self._set_label_class(self._lbl_cwl_auto_st, "gray")

        # Drive SensorApp tick methods
        with self._app.data_lock:
            h_history = list(self._app.h_buf)[-150:] or [h]
            t_history = list(self._app.t_buf)[-150:]
        if self._app.tick_rwl(h, h_history):
            self._refresh_limits()
        if self._app.tick_cwl_auto(h, h_history, t_history):
            self._refresh_limits()
        self._tick_flush_auto_stop(h, now)

        # Toast dismiss
        if self._toast_clear_at > 0 and now >= self._toast_clear_at:
            if self._lbl_delta.text() == self._toast_msg:
                self._lbl_delta.setText("---")
            self._toast_clear_at = 0.0
            self._toast_msg = ""

    # ── Timer tick: chart ─────────────────────────────────────────────

    def _tick_chart(self):
        if not self._plot.is_paused():
            self._plot.refresh()

    # ── Refresh limits display ────────────────────────────────────────

    def _refresh_limits(self):
        p  = self._app.profile
        of = p.overflow

        def _mm_rel(v):
            if of > 0 and v > 0:
                d = v - of
                return f"{v:.1f} mm  ({d:+.1f} mm OF)"
            return f"{v:.1f} mm"

        self._lbl_mwl.setText(_mm_rel(p.mwl) if p.mwl > 0 else "—")
        self._lbl_mwl_fault.setText(_mm_rel(p.mwl_fault) if p.mwl_fault > 0 else "—")
        self._lbl_cwl_val.setText(_mm_rel(p.cwl) if p.cwl > 0 else "—")
        self._lbl_menis_val.setText(f"{p.meniscus:+.1f} mm" if p.meniscus != 0 else "0.0 mm")
        self._lbl_overflow.setText(f"{of:.1f} mm")
        self._lbl_residual.setText(f"{p.residual_wl:.1f} mm")
        self._lbl_profile.setText(f"Active Profile: {p.name}")

        avg = self._app.app_settings.get("avg_window", 0.5)
        self._btn_nwl.setText(f"Set NWL ({avg}s)")
        self._btn_menis.setText(f"Set Meniscus ({avg}s)")

        # Safety margin c = OF − NWL
        if of > 0 and p.mwl > 0:
            sm = of - p.mwl
            self._lbl_sm_static.setText(f"{sm:.1f} mm")
            self._set_label_class(self._lbl_sm_static, "green" if sm >= 20 else "red")
        else:
            self._lbl_sm_static.setText("—")

        # CWL compliance status
        if of > 0 and p.cwl > 0:
            diff = p.cwl - of
            if diff <= 10:
                self._lbl_cwl_status.setText(f"CWL: {diff:+.1f} mm OF  ✓ (≤10)")
                self._set_label_class(self._lbl_cwl_status, "green")
            else:
                self._lbl_cwl_status.setText(f"CWL: +{diff:.1f} mm OF  ✗ (>10)")
                self._set_label_class(self._lbl_cwl_status, "red")
        else:
            self._lbl_cwl_status.setText("CWL: — (capture during fault test)")
            self._set_label_class(self._lbl_cwl_status, "gray")

        # Manual RWL button visibility
        show_rwl = (self._app.app_settings.get("cwl_mode") == "Manual"
                    and self._app.cwl_state == "ARMED")
        self._btn_manual_rwl.setVisible(show_rwl)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _set_label_class(lbl: QLabel, css_class: str):
        lbl.setProperty("class", css_class)
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)

    def _show_toast(self, msg: str, duration: float = 3.0):
        self._toast_msg = msg
        self._toast_clear_at = time.time() + duration
        self._lbl_delta.setText(msg)

    # ── Window close ─────────────────────────────────────────────────

    def closeEvent(self, event):
        self._ui_timer.stop()
        self._chart_timer.stop()
        self._app.cleanup()
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("EN 14055 Cistern Analytics")

    win = SensorMainWindow()
    win.show()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()

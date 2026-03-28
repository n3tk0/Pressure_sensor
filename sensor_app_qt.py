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
# Design: Fluent-inspired deep navy glass cards
#   bg0 #0b0b18  surface #131325  card #1a1a2e  border #262650
#   accent #7c6ef4  green #4ade80  red #f87171  orange #fb923c

_QSS_DARK = """
* { font-family: 'Segoe UI', 'Inter', 'SF Pro Display', sans-serif; font-size: 13px; }

QMainWindow { background: #0b0b18; }
QWidget#central { background: #0b0b18; color: #e2e8f0; }
QWidget { background: #0b0b18; color: #e2e8f0; }

/* ── Menu bar ── */
QMenuBar { background: #0f0f20; color: #94a3b8; border-bottom: 1px solid #1e1e40; padding: 2px 0; }
QMenuBar::item { padding: 4px 14px; border-radius: 4px; }
QMenuBar::item:selected { background: #1e1e3c; color: #e2e8f0; }
QMenu { background: #131325; color: #e2e8f0; border: 1px solid #2a2a50; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 20px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: #1e1e3c; }
QMenu::separator { height: 1px; background: #1e1e40; margin: 4px 8px; }

/* ── Card containers (QGroupBox used as card) ── */
QGroupBox {
    background: #131325;
    border: 1px solid #252545;
    border-radius: 12px;
    margin-top: 16px;
    padding: 12px 10px 10px 10px;
    color: #7c6ef4;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1.5px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px; top: 2px;
    padding: 0 6px;
    background: #131325;
    color: #7c6ef4;
}

/* ── Buttons ── */
QPushButton {
    background: #1e1e3c;
    color: #c4c9e8;
    border: 1px solid #2a2a52;
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 30px;
}
QPushButton:hover   { background: #252550; border-color: #3a3a70; color: #e2e8f0; }
QPushButton:pressed { background: #1a1a44; }

/* Variant: action (teal-violet) */
QPushButton#action  { background: #1e2a5c; border-color: #2d3d80; color: #a5b4fc; }
QPushButton#action:hover  { background: #253472; border-color: #7c6ef4; color: #e2e8f0; }
QPushButton#action:pressed { background: #1a2450; }

/* Variant: danger (deep red) */
QPushButton#danger  { background: #3d1522; border-color: #6b2035; color: #fca5a5; }
QPushButton#danger:hover  { background: #4f1c2d; border-color: #f87171; color: #fecaca; }

/* Variant: success (deep green) */
QPushButton#success { background: #14321e; border-color: #1e5430; color: #86efac; }
QPushButton#success:hover  { background: #1a3f26; border-color: #4ade80; color: #bbf7d0; }

/* Collapse button */
QPushButton#btn_collapse {
    background: transparent; border: 1px solid #252545; border-radius: 6px;
    color: #4a5568; padding: 2px; min-height: 24px;
}
QPushButton#btn_collapse:hover { background: #1a1a30; color: #94a3b8; }

/* Connect button — state-aware via objectName switching */
QPushButton#btn_connect         { background: #1c1850; border-color: #2e2a80; color: #a5b4fc; min-height: 32px; }
QPushButton#btn_connect:hover   { background: #232070; border-color: #7c6ef4; }

/* ── Combo boxes ── */
QComboBox {
    background: #161630; color: #c4c9e8;
    border: 1px solid #252545; border-radius: 7px;
    padding: 4px 28px 4px 10px; min-height: 28px;
}
QComboBox:hover { border-color: #3a3a70; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #7c6ef4; margin-right: 8px; }
QComboBox QAbstractItemView {
    background: #131325; color: #e2e8f0;
    border: 1px solid #252545; border-radius: 8px;
    selection-background-color: #1e1e3c;
    outline: none;
}

/* ── CheckBox ── */
QCheckBox { color: #94a3b8; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #3a3a70; border-radius: 4px; background: #161630; }
QCheckBox::indicator:checked { background: #7c6ef4; border-color: #7c6ef4; }
QCheckBox::indicator:hover { border-color: #7c6ef4; }

/* ── Live readout labels ── */
QLabel#lbl_h    { color: #7c6ef4; font-size: 26px; font-weight: bold; font-family: 'Consolas','JetBrains Mono','monospace'; }
QLabel#lbl_v    { color: #4ade80; font-size: 20px; font-family: 'Consolas','monospace'; }
QLabel#lbl_p    { color: #64748b; font-family: 'Consolas','monospace'; }
QLabel#lbl_f    { color: #fb923c; font-family: 'Consolas','monospace'; }
QLabel#lbl_temp { color: #64748b; font-family: 'Consolas','monospace'; }
QLabel#lbl_profile { color: #64748b; }
QLabel#lbl_conn    { color: #64748b; }
QLabel#lbl_conn_icon { font-size: 10px; }

/* ── Limit value labels ── */
QLabel.limit_val { color: #94a3b8; font-family: 'Consolas','monospace'; font-size: 12px; }
QLabel.limit_key { color: #4a5568; font-size: 11px; }

/* ── Status colour classes ── */
QLabel.green  { color: #4ade80; }
QLabel.red    { color: #f87171; }
QLabel.orange { color: #fb923c; }
QLabel.blue   { color: #7c6ef4; }
QLabel.gray, QLabel.muted { color: #4a5568; }
QLabel.status { font-size: 12px; }

/* ── Scroll bars ── */
QScrollBar:vertical { background: #0f0f20; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #252545; border-radius: 4px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #3a3a70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #0f0f20; height: 8px; }
QScrollBar::handle:horizontal { background: #252545; border-radius: 4px; }

/* ── Separator ── */
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #1e1e40; }

/* ── Status bar ── */
QStatusBar { background: #0f0f20; color: #4a5568; border-top: 1px solid #1e1e40; }
QStatusBar QLabel { color: #4a5568; }

/* ── Scroll area ── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ── Top bar card ── */
QWidget#top_bar {
    background: #0f0f20;
    border: 1px solid #1e1e40;
    border-radius: 10px;
}

/* ── Chart toolbar card ── */
QWidget#chart_toolbar {
    background: #0f0f20;
    border: 1px solid #1e1e40;
    border-radius: 10px;
}
"""

_QSS_LIGHT = """
* { font-family: 'Segoe UI', 'Inter', 'SF Pro Display', sans-serif; font-size: 13px; }

QMainWindow { background: #f0f2f8; }
QWidget#central { background: #f0f2f8; color: #1e2140; }
QWidget { background: #f0f2f8; color: #1e2140; }

QMenuBar { background: #e2e6f2; color: #3d4270; border-bottom: 1px solid #c8cedf; padding: 2px 0; }
QMenuBar::item { padding: 4px 14px; border-radius: 4px; }
QMenuBar::item:selected { background: #d0d6ec; color: #1e2140; }
QMenu { background: #eef0f8; color: #1e2140; border: 1px solid #c4cadc; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 20px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: #dde2f2; }
QMenu::separator { height: 1px; background: #c8cedf; margin: 4px 8px; }

QGroupBox {
    background: #e8ebf5;
    border: 1px solid #c4cadc;
    border-radius: 12px;
    margin-top: 16px;
    padding: 12px 10px 10px 10px;
    color: #4a3fbf;
    font-size: 11px; font-weight: bold; letter-spacing: 1.5px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 14px; top: 2px; padding: 0 6px;
    background: #e8ebf5; color: #4a3fbf;
}

QPushButton { background: #d8dcee; color: #2d3060; border: 1px solid #b8bee0; border-radius: 8px; padding: 6px 14px; min-height: 30px; }
QPushButton:hover   { background: #c8ceea; border-color: #9098cc; }
QPushButton:pressed { background: #b8c0e0; }
QPushButton#action  { background: #2a5898; border-color: #1e4a88; color: #fff; }
QPushButton#action:hover  { background: #3468ae; }
QPushButton#danger  { background: #b82040; border-color: #a01838; color: #fff; }
QPushButton#danger:hover  { background: #cc2850; }
QPushButton#success { background: #1a7838; border-color: #156030; color: #fff; }
QPushButton#success:hover { background: #208844; }
QPushButton#btn_collapse { background: transparent; border: 1px solid #c4cadc; border-radius: 6px; color: #8890b0; padding: 2px; min-height: 24px; }
QPushButton#btn_collapse:hover { background: #dde2f2; }
QPushButton#btn_connect { background: #e8eaf8; border-color: #9098cc; color: #2d3060; min-height: 32px; }
QPushButton#btn_connect:hover { background: #d8dcee; }

QComboBox { background: #dde2f0; color: #1e2140; border: 1px solid #b8bee0; border-radius: 7px; padding: 4px 28px 4px 10px; min-height: 28px; }
QComboBox:hover { border-color: #9098cc; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #4a3fbf; margin-right: 8px; }
QComboBox QAbstractItemView { background: #eef0f8; color: #1e2140; border: 1px solid #c4cadc; border-radius: 8px; selection-background-color: #d0d6ec; outline: none; }

QCheckBox { color: #5a6080; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #9098cc; border-radius: 4px; background: #eef0f8; }
QCheckBox::indicator:checked { background: #4a3fbf; border-color: #4a3fbf; }

QLabel#lbl_h    { color: #4a3fbf; font-size: 26px; font-weight: bold; font-family: 'Consolas','monospace'; }
QLabel#lbl_v    { color: #1a7838; font-size: 20px; font-family: 'Consolas','monospace'; }
QLabel#lbl_p    { color: #6070a0; font-family: 'Consolas','monospace'; }
QLabel#lbl_f    { color: #b05010; font-family: 'Consolas','monospace'; }
QLabel#lbl_temp { color: #6070a0; font-family: 'Consolas','monospace'; }
QLabel#lbl_profile, QLabel#lbl_conn { color: #6070a0; }

QLabel.limit_val { color: #3a4070; font-family: 'Consolas','monospace'; font-size: 12px; }
QLabel.limit_key { color: #8890b0; font-size: 11px; }
QLabel.green  { color: #1a7838; }
QLabel.red    { color: #b82040; }
QLabel.orange { color: #b05010; }
QLabel.blue   { color: #4a3fbf; }
QLabel.gray, QLabel.muted { color: #8890b0; }
QLabel.status { font-size: 12px; }

QScrollBar:vertical { background: #dde2f0; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #b8bee0; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #c4cadc; }
QStatusBar { background: #e2e6f2; color: #8890b0; border-top: 1px solid #c8cedf; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#top_bar { background: #e2e6f2; border: 1px solid #c4cadc; border-radius: 10px; }
QWidget#chart_toolbar { background: #e2e6f2; border: 1px solid #c4cadc; border-radius: 10px; }
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

        # Top bar card
        top_card = QWidget(); top_card.setObjectName("top_bar")
        top_card.setFixedHeight(46)
        top = QHBoxLayout(top_card)
        top.setContentsMargins(10, 0, 10, 0); top.setSpacing(10)

        self._btn_collapse = QPushButton("◀")
        self._btn_collapse.setObjectName("btn_collapse")
        self._btn_collapse.setFixedWidth(30)
        self._btn_collapse.clicked.connect(self._toggle_left_panel)

        self._lbl_profile = QLabel("Active Profile: Untitled Profile")
        self._lbl_profile.setObjectName("lbl_profile")

        self._lbl_conn_icon = QLabel("●")
        self._lbl_conn_icon.setObjectName("lbl_conn_icon")
        self._lbl_conn_icon.setProperty("class", "gray")
        self._lbl_conn = QLabel("Disconnected")
        self._lbl_conn.setObjectName("lbl_conn")
        self._lbl_conn.setProperty("class", "gray")

        self._btn_connect = QPushButton("Connect Sensor")
        self._btn_connect.setObjectName("btn_connect")
        self._btn_connect.setFixedWidth(160)
        self._btn_connect.clicked.connect(self._toggle_connect)

        top.addWidget(self._btn_collapse)
        top.addWidget(self._lbl_profile, 1)
        conn_row = QHBoxLayout(); conn_row.setSpacing(5)
        conn_row.addWidget(self._lbl_conn_icon)
        conn_row.addWidget(self._lbl_conn)
        top.addLayout(conn_row)
        top.addWidget(self._btn_connect)
        root.addWidget(top_card)

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

        # Two-column limits grid — key/value pairs
        grid = QGridLayout()
        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(2)

        def _key(text):
            l = QLabel(text); l.setProperty("class", "limit_key"); return l
        def _val(text="—"):
            l = QLabel(text); l.setProperty("class", "limit_val")
            l.setWordWrap(True); return l

        grid.addWidget(_key("NWL (fill):"),      0, 0)
        self._lbl_mwl        = _val(); grid.addWidget(self._lbl_mwl, 0, 1)
        grid.addWidget(_key("MWL (fault):"),     1, 0)
        self._lbl_mwl_fault  = _val(); grid.addWidget(self._lbl_mwl_fault, 1, 1)
        grid.addWidget(_key("CWL (2s):"),        2, 0)
        self._lbl_cwl_val    = _val(); grid.addWidget(self._lbl_cwl_val, 2, 1)
        grid.addWidget(_key("Residual WL:"),     3, 0)
        self._lbl_residual   = _val("0.0 mm"); grid.addWidget(self._lbl_residual, 3, 1)
        grid.addWidget(_key("Meniscus:"),        4, 0)
        self._lbl_menis_val  = _val("0.0 mm"); grid.addWidget(self._lbl_menis_val, 4, 1)
        grid.addWidget(_key("Overflow:"),        5, 0)
        self._lbl_overflow   = _val("0.0 mm"); grid.addWidget(self._lbl_overflow, 5, 1)
        grid.addWidget(_key("Safety margin c:"), 6, 0)
        self._lbl_sm_static  = _val(); grid.addWidget(self._lbl_sm_static, 6, 1)
        grid.addWidget(_key("Live headroom:"),   7, 0)
        self._lbl_headroom   = _val("— mm"); grid.addWidget(self._lbl_headroom, 7, 1)
        lim_layout.addLayout(grid)

        lim_layout.addWidget(_sep())
        self._lbl_cwl_status  = QLabel("CWL: — (capture during fault test)")
        self._lbl_cwl_auto_st = QLabel("CWL: IDLE — arm while at MWL")
        self._lbl_rwl_st      = QLabel("RWL: IDLE (set NWL to arm)")
        for lb in (self._lbl_cwl_status, self._lbl_cwl_auto_st, self._lbl_rwl_st):
            lb.setProperty("class", "status gray")
            lb.setWordWrap(True)
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

        # Chart toolbar card
        toolbar_card = QWidget(); toolbar_card.setObjectName("chart_toolbar")
        toolbar_card.setFixedHeight(46)
        tb = QHBoxLayout(toolbar_card)
        tb.setContentsMargins(10, 0, 10, 0); tb.setSpacing(8)

        def _tb_lbl(text):
            l = QLabel(text); l.setProperty("class", "gray"); return l

        tb.addWidget(_tb_lbl("Axis:"))
        self._combo_plot = QComboBox()
        self._combo_plot.addItems(["Height (mm)", "Volume (L)", "Flow Rate (L/s)"])
        self._combo_plot.setFixedWidth(140)
        self._combo_plot.currentTextChanged.connect(self._on_plot_mode_changed)
        tb.addWidget(self._combo_plot)

        tb.addWidget(_tb_lbl("Window:"))
        self._combo_win = QComboBox()
        self._combo_win.addItems(list(SensorPlotWidget.WINDOW_OPTIONS.keys()))
        self._combo_win.setCurrentText("30 s")
        self._combo_win.setFixedWidth(76)
        self._combo_win.currentTextChanged.connect(self._on_window_changed)
        tb.addWidget(self._combo_win)

        tb.addWidget(_tb_lbl("Smooth:"))
        self._combo_smooth = QComboBox()
        self._combo_smooth.addItems(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"])
        self._combo_smooth.setFixedWidth(108)
        self._combo_smooth.currentTextChanged.connect(self._on_smooth_changed)
        tb.addWidget(self._combo_smooth)

        self._chk_autoscroll = QCheckBox("Auto-scroll")
        self._chk_autoscroll.setChecked(True)
        self._chk_autoscroll.toggled.connect(self._on_autoscroll_changed)
        tb.addWidget(self._chk_autoscroll)

        self._btn_pause = _btn("Pause")
        self._btn_pause.setFixedWidth(80)
        self._btn_pause.clicked.connect(self._toggle_pause)
        tb.addWidget(self._btn_pause)

        btn_shot = _btn("Screenshot")
        btn_shot.setFixedWidth(100)
        btn_shot.clicked.connect(self._export_screenshot)
        tb.addWidget(btn_shot)

        btn_colors = _btn("Colors")
        btn_colors.setFixedWidth(66)
        btn_colors.clicked.connect(self._open_colors_dlg)
        tb.addWidget(btn_colors)

        tb.addStretch()

        tb.addWidget(_tb_lbl("Delta:"))
        self._lbl_delta = QLabel("---")
        self._lbl_delta.setProperty("class", "blue")
        tb.addWidget(self._lbl_delta)
        btn_clr_delta = _btn("Clear")
        btn_clr_delta.setFixedWidth(52)
        btn_clr_delta.clicked.connect(self._clear_delta)
        tb.addWidget(btn_clr_delta)

        layout.addWidget(toolbar_card)

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

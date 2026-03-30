"""
main.py — Entry point for the EN 14055 Cistern Analytics DearPyGui application.

Combines sensor_core (logic) with dpg_theme (UI theming) into a single
optimised render loop with:
  - 60 FPS throttle (time.time())
  - State caching for all text labels
  - Thread-safe plot data extraction
"""

import sys
import time
import bisect
import collections
import csv
import json
import re
import logging
import math
from pathlib import Path
from datetime import datetime

try:
    import dearpygui.dearpygui as dpg
    import serial
    import serial.tools.list_ports
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install dearpygui pyserial")
    input("Press Enter to exit...")
    sys.exit(1)

import sensor_core as core
from sensor_core import (
    SensorApp, CisternProfile, CalibrationPoint,
    load_settings, save_settings, smooth, interp_hv,
    build_request, p_convert, p_format, p_parse_to_bar,
    _rebuild_interp_cache, PRESSURE_UNITS,
    DEFAULT_CONN, DEFAULT_APP, DEFAULT_LINE_COLORS,
    CONFIG_DIR, EXPORT_DIR, BASE_DIR,
    EN14055_REQUIRED_FLUSH_COUNT, EN14055_FULL_FLUSH_MAX_L,
    EN14055_PART_FLUSH_MAX_L, EN14055_MAX_CAL_FILE_BYTES,
    _TEMP_PLACEHOLDER,
)
from dpg_theme import (
    setup_fonts, create_modern_theme, create_status_themes,
    create_button_themes, create_line_themes,
    COL_BG, COL_CARD, COL_ACCENT, COL_GREEN, COL_RED,
    COL_GRAY, COL_ORANGE, COL_BLUE, COL_WHITE,
    LT_ACCENT, LT_GREEN, LT_RED, LT_ORANGE, LT_GRAY,
)


# ── Performance constants ───────────────────────────────────────────
_MIN_FRAME_TIME = 1.0 / 60.0  # 60 FPS cap


# ── State cache — only call dpg.set_value() when the value changes ──
class _LabelCache:
    """Track the last value written to each DPG label to avoid redundant updates."""
    __slots__ = ("_cache",)

    def __init__(self):
        self._cache: dict[str, str] = {}

    def set(self, tag: str, value: str):
        if self._cache.get(tag) != value:
            self._cache[tag] = value
            dpg.set_value(tag, value)

    def set_if_exists(self, tag: str, value: str):
        if dpg.does_item_exist(tag) and self._cache.get(tag) != value:
            self._cache[tag] = value
            dpg.set_value(tag, value)

    def invalidate(self, tag: str):
        self._cache.pop(tag, None)


# ── Module-level state ──────────────────────────────────────────────
app = SensorApp()
app._item_themes: dict[str, str] = {}  # UI theme tracking (not in sensor_core)
cache = _LabelCache()
_line_color_dpg_ids: dict[str, int] = {}
_font_ui = None
_font_medium = None
_font_large = None

_toast_msg: str = ""
_toast_clear_at: float = 0.0
_left_panel_visible = True
_air_gap_confirmed = {"value": False}

_last_frame_time: float = 0.0


# ── Helpers ─────────────────────────────────────────────────────────
def _show_toast(msg: str, duration: float = 3.0):
    global _toast_msg, _toast_clear_at
    _toast_msg = msg
    _toast_clear_at = time.time() + duration
    dpg.set_value("lbl_delta", msg)
    cache.invalidate("lbl_delta")


def _check_toast_dismiss(now: float):
    global _toast_clear_at, _toast_msg
    if _toast_clear_at > 0 and now >= _toast_clear_at:
        if dpg.get_value("lbl_delta") == _toast_msg:
            cache.set("lbl_delta", "---")
        _toast_clear_at = 0.0
        _toast_msg = ""


def _bind_status(item: str, base_tag: str):
    is_light = app.app_settings.get("ui_theme", "Dark") == "Light"
    tag = base_tag + ("_lt" if is_light else "")
    if not dpg.does_item_exist(tag):
        tag = base_tag
    dpg.bind_item_theme(item, tag)
    app._item_themes[item] = base_tag


def _apply_theme(mode: str):
    is_dark = (mode != "Light")
    dpg.bind_theme("theme_dark" if is_dark else "theme_light")
    acc = COL_ACCENT if is_dark else LT_ACCENT
    grn = COL_GREEN if is_dark else LT_GREEN
    gry = COL_GRAY if is_dark else LT_GRAY
    for tag in ("hdr_live", "hdr_limits", "hdr_flush", "hdr_log"):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, color=acc)
    for tag in ("lbl_h",):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, color=acc)
    for tag in ("lbl_v",):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, color=grn)
    for tag in ("lbl_p", "lbl_conn", "lbl_temp"):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, color=gry)
    for tag in ("lbl_f",):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, color=COL_ORANGE if is_dark else LT_ORANGE)
    app.app_settings["ui_theme"] = mode
    for item, base in app._item_themes.items():
        if dpg.does_item_exist(item):
            _bind_status(item, base)


# ── Connection callbacks ────────────────────────────────────────────
def _toggle_connect():
    if app.is_connected:
        app.disconnect()
        dpg.set_item_label("btn_connect", "Connect Sensor")
        dpg.configure_item("lbl_conn",      default_value="Disconnected")
        dpg.configure_item("lbl_conn_icon", default_value="\u25cf", color=COL_GRAY)
        dpg.bind_item_theme("btn_connect", 0)
    else:
        app.last_error = ""
        app.connect()
        if app.is_connected:
            dpg.set_item_label("btn_connect", "Disconnect")
            dpg.configure_item("lbl_conn",
                               default_value=f"{app.conn_params['port']}  {app.conn_params['baud']}bd")
            dpg.configure_item("lbl_conn_icon", default_value="\u25cf", color=COL_GREEN)
            dpg.bind_item_theme("btn_connect", "theme_btn_danger")
        elif app.last_error:
            dpg.configure_item("lbl_conn",      default_value=app.last_error)
            dpg.configure_item("lbl_conn_icon", default_value="\u25cf", color=COL_RED)
            _bind_status("lbl_conn", "theme_red")


# ── EN 14055 limit callbacks ───────────────────────────────────────
def _set_mwl():
    val = app.get_avg_height()
    app.profile.mwl = val
    app.cwl_state = "ARMED"
    app.cwl_peak = val
    _refresh_limits()


def _arm_cwl_auto():
    if app.profile.overflow <= 0:
        _show_toast("\u26a0 Set Overflow level in Calibration first!")
        return
    app.cwl_auto_state = "ARMED"
    app.cwl_auto_peak = app.get_avg_height()
    app.cwl_auto_timer = 0.0
    _refresh_limits()
    _show_toast("CWL armed \u2014 water should be at MWL. Cut supply when ready.")


def _set_cwl():
    if app.profile.overflow <= 0:
        _show_toast("\u26a0 Set Overflow level in Calibration first!")
        return
    app.cwl_auto_state = "IDLE"
    app.profile.cwl = app.get_avg_height()
    _refresh_limits()


def _manual_mwl_cwl():
    if app.manual_mwl_cwl_pending:
        app.manual_mwl_cwl_pending = False
        dpg.set_item_label("btn_manual_mwlcwl", "Manual MWL/CWL")
        dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_action")
        if app.chart_paused:
            _toggle_pause()
        return
    if app.profile.overflow <= 0:
        _show_toast("\u26a0 Set Overflow level in Calibration first!")
        return
    if not app.chart_paused:
        _toggle_pause()
    app.manual_mwl_cwl_pending = True
    dpg.set_item_label("btn_manual_mwlcwl", "Cancel Manual MWL/CWL")
    dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_danger")
    _show_toast("Chart paused \u2014 click the MWL moment on the graph")


def _set_meniscus():
    if app.profile.overflow <= 0:
        _show_toast("\u26a0 Set Overflow level in Calibration first!")
        return
    measured_level = app.get_avg_height()
    app.profile.meniscus = measured_level - app.profile.overflow
    _refresh_limits()


def _manual_rwl():
    if app.cwl_state == "ARMED":
        app.cwl_state = "WAITING"
        app.cwl_timer = time.time()


def _refresh_limits():
    p = app.profile
    of = p.overflow

    def _mm(v):
        return f"{v:.1f} mm"

    def _mm_rel(v):
        if of > 0 and v > 0:
            d = v - of
            return f"{v:.1f} mm  ({d:+.1f} mm OF)"
        return f"{v:.1f} mm"

    cache.set("lbl_mwl", _mm_rel(p.mwl))
    cache.set_if_exists("lbl_mwl_fault", _mm_rel(p.mwl_fault) if p.mwl_fault > 0 else "\u2014")
    cache.set("lbl_menis", f"{p.meniscus:+.1f} mm" if p.meniscus != 0 else "0.0 mm")
    cache.set("lbl_cwl", _mm_rel(p.cwl) if p.cwl > 0 else "\u2014")
    cache.set("lbl_wd", _mm(p.water_discharge))
    cache.set("lbl_overflow", _mm(of))
    cache.set("lbl_profile", f"Active Profile: {p.name}")
    cache.set_if_exists("lbl_residual", _mm(p.residual_wl))

    w = app.app_settings.get("avg_window", 0.5)
    dpg.set_item_label("btn_mwl",   f"Set NWL  (avg {w}s)")
    dpg.set_item_label("btn_menis", f"Set Meniscus (avg {w}s)")

    show_manual = app.app_settings.get("cwl_mode") == "Manual" and app.cwl_state == "ARMED"
    if dpg.does_item_exist("btn_manual_rwl"):
        dpg.configure_item("btn_manual_rwl", show=show_manual)

    if dpg.does_item_exist("lbl_safety_margin_static"):
        if of > 0 and p.mwl > 0:
            sm = of - p.mwl
            col = COL_GREEN if sm >= 20 else COL_RED
            cache.set("lbl_safety_margin_static", f"{sm:.1f} mm")
            dpg.configure_item("lbl_safety_margin_static", color=col)
        else:
            cache.set("lbl_safety_margin_static", "\u2014")

    if of > 0 and p.cwl > 0:
        diff = p.cwl - of
        if diff <= 10:
            cache.set("lbl_airgap", f"CWL: {diff:+.1f} mm OF  \u2713 (\u226410)")
            _bind_status("lbl_airgap", "theme_green")
        else:
            cache.set("lbl_airgap", f"CWL: +{diff:.1f} mm OF  \u2717 (>10)")
            _bind_status("lbl_airgap", "theme_red")
    else:
        cache.set("lbl_airgap", "CWL: \u2014 (capture during fault test)")
        _bind_status("lbl_airgap", "theme_gray")


# ── Flush callbacks ─────────────────────────────────────────────────
def _refresh_flush_table():
    if not dpg.does_item_exist("flush_table_area"):
        return
    dpg.delete_item("flush_table_area", children_only=True)
    results = app.flush_results
    if not results:
        dpg.add_text("No measurements yet.", parent="flush_table_area", color=COL_GRAY)
        return
    with dpg.table(parent="flush_table_area", header_row=True,
                   borders_innerH=True, borders_outerH=True,
                   borders_innerV=True, borders_outerV=True,
                   row_background=True, resizable=False,
                   scrollY=True, freeze_rows=1, height=-1):
        dpg.add_table_column(label="#",    width_fixed=True, init_width_or_weight=20)
        dpg.add_table_column(label="Type", width_fixed=True, init_width_or_weight=38)
        dpg.add_table_column(label="Vol",  width_fixed=True, init_width_or_weight=46)
        dpg.add_table_column(label="Time", width_fixed=True, init_width_or_weight=42)
        dpg.add_table_column(label="L/s",  width_fixed=True, init_width_or_weight=40)
        dpg.add_table_column(label="EN*",  width_fixed=True, init_width_or_weight=40)
        dpg.add_table_column(label="Del",  width_fixed=True, init_width_or_weight=26)
        for i, r in enumerate(results):
            total_rate = r["vol"] / r["time"] if r["time"] > 0 else 0
            en_rate = r.get("en14055_rate")
            en_note = r.get("en14055_note")
            en_str = f"{en_rate:.2f}" if en_rate is not None else "\u2014"
            type_col = COL_ACCENT if "Full" in r["type"] else COL_ORANGE
            with dpg.table_row():
                dpg.add_text(str(i + 1))
                dpg.add_text(r["type"][:4], color=type_col)
                dpg.add_text(f"{r['vol']:.2f}L")
                dpg.add_text(f"{r['time']:.1f}s")
                dpg.add_text(f"{total_rate:.2f}")
                en_cell_tag = f"en_cell_{i}"
                dpg.add_text(en_str, tag=en_cell_tag,
                             color=COL_ACCENT if en_rate is not None else COL_GRAY)
                if en_note:
                    with dpg.tooltip(en_cell_tag):
                        dpg.add_text(en_note, color=COL_ORANGE)
                dpg.add_button(label="X", width=22,
                               user_data=i, callback=_delete_flush_row)


def _delete_flush_row(sender, app_data, user_data):
    idx = user_data
    if 0 <= idx < len(app.flush_results):
        app.flush_results.pop(idx)
        _refresh_flush_table()


def _toggle_flush_measure():
    if not app.flush_measuring:
        with app._flush_lock:
            app.flush_start_vol = app.current_volume
            app.flush_start_h = app.current_height
            app.flush_start_time = time.time()
            app.flush_vol_history = []
            app.flush_min_h = float("inf")
            app.flush_rising = False
            app.flush_rising_timer = 0.0
            app.flush_measuring = True
        dpg.set_item_label("btn_flush", "Stop Flush Measurement")
        dpg.bind_item_theme("btn_flush", "theme_btn_danger")
    else:
        with app._flush_lock:
            app.flush_measuring = False
            history = list(app.flush_vol_history)
            elapsed = time.time() - app.flush_start_time
            start_vol = app.flush_start_vol
        flush_type = dpg.get_value("combo_flush_type") if dpg.does_item_exist("combo_flush_type") else "Full Flush"
        snap_temp = app.current_temperature
        if history:
            min_vol = min(r[1] for r in history)
            delta_vol = abs(start_vol - min_vol)
        else:
            delta_vol = abs(start_vol - app.current_volume)
        en14055_rate = None
        en14055_note = None
        if history and delta_vol > 3.0:
            v_start = start_vol
            v_skip_start = v_start - 1.0
            v_end = min(r[1] for r in history)
            v_skip_end = v_end + 2.0
            t1 = next((r[0] for r in history if r[1] <= v_skip_start), None)
            t2 = next((r[0] for r in history if r[1] <= v_skip_end), None)
            if t1 and t2 and t2 > t1:
                eff_vol = v_skip_start - v_skip_end
                eff_time = t2 - t1
                if eff_time > 0:
                    en14055_rate = eff_vol / eff_time
                else:
                    en14055_note = "N/A (flush window too short)"
            else:
                en14055_note = "N/A (flush too short for skip-window)"
        app.flush_results.append({
            "type": flush_type, "vol": delta_vol,
            "time": elapsed, "en14055_rate": en14055_rate,
            "en14055_note": en14055_note,
            "temp_c": snap_temp,
        })
        dpg.set_item_label("btn_flush", "Start Flush Measurement")
        dpg.bind_item_theme("btn_flush", "theme_btn_success")
        _refresh_flush_table()


def _clear_flush():
    app.flush_results.clear()
    _refresh_flush_table()


# ── Compliance check ────────────────────────────────────────────────
def _check_compliance():
    results, _air_gap_auto = core.run_compliance_checks(app.profile, app.flush_results)
    if dpg.does_item_exist("dlg_comply"):
        dpg.delete_item("dlg_comply")
    with dpg.window(label="EN 14055 Compliance Check", modal=True, tag="dlg_comply",
                     width=560, height=460, no_resize=True, pos=[320, 180]):
        with dpg.child_window(height=350, border=False):
            for line in results:
                col = (COL_GREEN  if "[PASS]" in line else
                       COL_RED    if "[FAIL]" in line else
                       COL_ORANGE if "[WARN]" in line else COL_GRAY)
                dpg.add_text(line, color=col)
            if _air_gap_auto is None:
                dpg.add_separator()
                dpg.add_text("Air gap a (\u00a75.2.7): set Water Discharge height to auto-compute,", color=COL_GRAY)
                dpg.add_text("or confirm physical ruler measurement \u2265 20 mm:", color=COL_GRAY)
                _ag_lbl_init = ("[PASS] Air gap manually confirmed \u2265 20 mm"
                                if _air_gap_confirmed["value"]
                                else "[----] Air gap: not yet confirmed (tick to acknowledge)")
                _ag_col_init = COL_GREEN if _air_gap_confirmed["value"] else COL_GRAY
                dpg.add_text(_ag_lbl_init, tag="ag_manual_lbl", color=_ag_col_init)

                def _on_ag_check(s, a):
                    _air_gap_confirmed["value"] = a
                    if a:
                        dpg.configure_item("ag_manual_lbl",
                                           default_value="[PASS] Air gap manually confirmed \u2265 20 mm",
                                           color=COL_GREEN)
                    else:
                        dpg.configure_item("ag_manual_lbl",
                                           default_value="[----] Air gap: not yet confirmed (tick to acknowledge)",
                                           color=COL_GRAY)
                dpg.add_checkbox(label="Confirmed \u2265 20 mm",
                                 default_value=_air_gap_confirmed["value"],
                                 callback=_on_ag_check)
        dpg.add_separator()
        dpg.add_button(label="Close", width=120,
                       callback=lambda: dpg.delete_item("dlg_comply"))


# ── Log toggle ──────────────────────────────────────────────────────
def _toggle_log():
    if not app.is_logging:
        EXPORT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)[:80]
        fn = str(EXPORT_DIR / f"{safe_name}_{ts}.csv")
        try:
            app.csv_file = open(fn, "w", newline="", encoding="utf-8")
            app.csv_writer = csv.writer(app.csv_file)
            _pu = PRESSURE_UNITS.get(app.app_settings.get("pressure_unit", "bar"), (1.0, "bar"))[1]
            app.csv_writer.writerow(["Timestamp", f"P({_pu})", "H(mm)", "V(L)", "F(L/s)", "T(C)"])
            app.is_logging = True
            dpg.set_item_label("btn_log", "Stop Data Log")
            dpg.bind_item_theme("btn_log", "theme_btn_danger")
        except Exception as e:
            logging.warning(f"Failed to open CSV log: {e}")
            _show_toast(f"Log failed: {e}")
    else:
        app.is_logging = False
        dpg.set_item_label("btn_log", "Start Data Log (CSV)")
        dpg.bind_item_theme("btn_log", "theme_btn_success")
        with app._csv_lock:
            if app.csv_file:
                try:
                    app.csv_file.flush()
                    app.csv_file.close()
                except OSError as e:
                    logging.warning(f"CSV close error: {e}")
                finally:
                    app.csv_file = None
                    app.csv_writer = None
                    app._csv_row_count = 0


# ── Pause / chart interaction ───────────────────────────────────────
def _toggle_pause():
    app.chart_paused = not app.chart_paused
    if app.chart_paused:
        update_chart()
        dpg.set_item_label("btn_pause", "Resume")
        dpg.bind_item_theme("btn_pause", "theme_btn_success")
        dpg.set_axis_limits_auto("x_axis")
        dpg.set_axis_limits_auto("y_axis")
        if "Height" in dpg.get_value("combo_plot"):
            dpg.configure_item("drag_mwl", show=True,
                               default_value=app.profile.mwl if app.profile.mwl > 0 else 0.0)
            dpg.configure_item("drag_cwl", show=True,
                               default_value=app.profile.cwl if app.profile.cwl > 0 else 0.0)
    else:
        dpg.set_item_label("btn_pause", "Pause")
        dpg.bind_item_theme("btn_pause", 0)
        dpg.configure_item("drag_mwl", show=False)
        dpg.configure_item("drag_cwl", show=False)


def _on_drag_mwl(sender, value):
    app.profile.mwl = float(value)
    _refresh_limits()


def _on_drag_cwl(sender, value):
    app.profile.cwl = float(value)
    _refresh_limits()


def _clear_delta():
    app.click_points.clear()
    dpg.set_value("scatter_click1", [[], []])
    dpg.set_value("scatter_click2", [[], []])
    dpg.configure_item("annot_click1", show=False)
    dpg.configure_item("annot_click2", show=False)
    dpg.configure_item("lbl_delta", default_value="Delta: Click 2 pts")
    cache.invalidate("lbl_delta")


def _snap_to_line(mouse_t):
    if not app.last_t or not app.last_y:
        return None, None
    i = bisect.bisect_left(app.last_t, mouse_t)
    candidates = []
    if i > 0:
        candidates.append(i - 1)
    if i < len(app.last_t):
        candidates.append(i)
    if not candidates:
        return None, None
    best = min(candidates, key=lambda j: abs(app.last_t[j] - mouse_t))
    return app.last_t[best], app.last_y[best]


def _plot_clicked(sender, app_data):
    if not dpg.is_item_hovered("main_plot"):
        return
    pos = dpg.get_plot_mouse_pos()
    st, sy = _snap_to_line(pos[0])
    if st is None:
        return

    # Manual MWL/CWL selection mode
    if app.manual_mwl_cwl_pending:
        t_click = st
        ts = app.last_t
        ys = app.last_y
        mwl_pts = [y for t, y in zip(ts, ys) if t_click - 1.0 <= t <= t_click]
        mwl = sum(mwl_pts) / len(mwl_pts) if mwl_pts else sy
        target_t = t_click + 2.0
        cwl = None
        for t, y in zip(ts, ys):
            if t >= target_t:
                cwl = y
                break
        if cwl is None:
            cwl = ys[-1] if ys else sy
        app.profile.mwl_fault = mwl
        app.profile.cwl = cwl
        app.manual_mwl_cwl_pending = False
        dpg.set_item_label("btn_manual_mwlcwl", "Manual MWL/CWL")
        dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_action")
        _refresh_limits()
        _show_toast(f"MWL = {mwl:.1f} mm  |  CWL = {cwl:.1f} mm")
        if app.chart_paused:
            _toggle_pause()
        return

    if len(app.click_points) >= 2:
        app.click_points.clear()
        dpg.set_value("scatter_click1", [[], []])
        dpg.set_value("scatter_click2", [[], []])
        dpg.configure_item("annot_click1", show=False)
        dpg.configure_item("annot_click2", show=False)

    app.click_points.append((st, sy))
    plot_mode = dpg.get_value("combo_plot")
    unit = "mm" if "Height" in plot_mode else ("L" if "Volume" in plot_mode else "L/s")

    if len(app.click_points) == 1:
        dpg.set_value("scatter_click1", [[st], [sy]])
        dpg.set_value("annot_click1", (st, sy))
        dpg.configure_item("annot_click1",
                            label=f"P1  T={st:.1f}s\n     {sy:.2f} {unit}",
                            show=True)
        dpg.configure_item("lbl_delta", default_value=f"Pt1: T={st:.1f}s  Y={sy:.2f} {unit} \u2014 Click Pt2...")
        cache.invalidate("lbl_delta")
    else:
        dpg.set_value("scatter_click2", [[st], [sy]])
        dpg.set_value("annot_click2", (st, sy))
        dpg.configure_item("annot_click2",
                            label=f"P2  T={st:.1f}s\n     {sy:.2f} {unit}",
                            show=True)
        dt = app.click_points[1][0] - app.click_points[0][0]
        dy = app.click_points[1][1] - app.click_points[0][1]
        dpg.configure_item("lbl_delta",
                            default_value=f"\u0394T: {abs(dt):.1f}s | \u0394Y: {dy:+.2f} {unit}")
        cache.invalidate("lbl_delta")


# ── Screenshot ──────────────────────────────────────────────────────
def _export_screenshot():
    EXPORT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)[:80]
    filename = str(EXPORT_DIR / f"{safe_name}_{ts}.png")
    try:
        dpg.output_frame_buffer(filename)
        dpg.set_value("lbl_delta", f"Saved: {Path(filename).name}")
        cache.invalidate("lbl_delta")
    except Exception as e:
        dpg.set_value("lbl_delta", f"Screenshot failed: {e}")
        cache.invalidate("lbl_delta")


# ── Left panel toggle ──────────────────────────────────────────────
def _toggle_left_panel():
    global _left_panel_visible
    _left_panel_visible = not _left_panel_visible
    dpg.configure_item("left_panel", show=_left_panel_visible)
    dpg.set_item_label("btn_collapse", "\u25b6" if not _left_panel_visible else "\u25c0")


# ── Connection dialog ───────────────────────────────────────────────
def _open_connection_dlg():
    if dpg.does_item_exist("dlg_conn"):
        dpg.delete_item("dlg_conn")
    ports = [p.device for p in serial.tools.list_ports.comports()] or ["COM1"]
    with dpg.window(label="Hardware Connection", modal=True, tag="dlg_conn",
                     width=360, height=280, no_resize=True, pos=[420, 250]):
        dpg.add_text("COM Port:")
        dpg.add_combo(ports, default_value=app.conn_params["port"], tag="dlg_c_port", width=-1)
        dpg.add_text("Baud Rate:")
        dpg.add_combo(["115200", "38400", "9600"],
                       default_value=str(app.conn_params["baud"]), tag="dlg_c_baud", width=-1)
        dpg.add_text("AL1060 Port:")
        dpg.add_combo(["Port 1", "Port 2", "Port 3", "Port 4"],
                       default_value=app.conn_params["io_port"], tag="dlg_c_io", width=-1)
        dpg.add_text("Polling (ms):")
        dpg.add_combo(["5", "20", "50", "100", "500", "1000"],
                       default_value=str(app.conn_params["poll_ms"]), tag="dlg_c_poll", width=-1)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", width=120, callback=_save_conn)
            dpg.add_button(label="Cancel", width=120, callback=lambda: dpg.delete_item("dlg_conn"))


def _save_conn():
    app.conn_params["port"] = dpg.get_value("dlg_c_port")
    app.conn_params["baud"] = int(dpg.get_value("dlg_c_baud"))
    app.conn_params["io_port"] = dpg.get_value("dlg_c_io")
    app.conn_params["poll_ms"] = int(dpg.get_value("dlg_c_poll"))
    save_settings(app.conn_params, app.app_settings)
    dpg.delete_item("dlg_conn")


# ── Calibration dialog ──────────────────────────────────────────────
def _open_calibration_dlg():
    if dpg.does_item_exist("dlg_cal"):
        dpg.delete_item("dlg_cal")

    clone = app.profile.clone()
    _cal_edit_idx = [None]

    def refresh_table():
        if dpg.does_item_exist("cal_table"):
            dpg.delete_item("cal_table")
        unit = app.app_settings.get("pressure_unit", "bar")
        _, unit_label = PRESSURE_UNITS.get(unit, (1.0, "bar"))
        clone.points.sort(key=lambda x: x.p)
        with dpg.table(tag="cal_table", header_row=True, borders_innerH=True,
                        borders_outerH=True, borders_innerV=True, borders_outerV=True,
                        row_background=True, height=200, scrollY=True,
                        parent="cal_table_wrap"):
            dpg.add_table_column(label=f"P ({unit_label})", width_fixed=True, init_width_or_weight=95)
            dpg.add_table_column(label="H (mm)", width_fixed=True, init_width_or_weight=80)
            dpg.add_table_column(label="Vol (L)", width_fixed=True, init_width_or_weight=80)
            dpg.add_table_column(label="Actions", width_fixed=True, init_width_or_weight=100)
            for idx, pt in enumerate(clone.points):
                with dpg.table_row():
                    dpg.add_text(p_format(pt.p, unit=unit).split()[0])
                    dpg.add_text(f"{pt.h:.1f}")
                    dpg.add_text(f"{pt.v:.2f}")
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Edit", width=42, user_data=idx,
                                       callback=lambda s, a, u: edit_point(u))
                        dpg.add_button(label="Del", width=42, user_data=idx,
                                       callback=lambda s, a, u: delete_point(u))

    def edit_point(idx):
        if 0 <= idx < len(clone.points):
            pt = clone.points[idx]
            unit = app.app_settings.get("pressure_unit", "bar")
            dpg.set_value("cal_p", p_format(pt.p, unit=unit).split()[0])
            dpg.set_value("cal_h", f"{pt.h:.1f}")
            dpg.set_value("cal_v", f"{pt.v:.2f}")
            _cal_edit_idx[0] = idx
            dpg.set_item_label("btn_cal_add", "Update Point")

    def delete_point(idx):
        if 0 <= idx < len(clone.points):
            clone.points.pop(idx)
            _cal_edit_idx[0] = None
            dpg.set_item_label("btn_cal_add", "Add Point")
            refresh_table()

    def add_or_update_point():
        try:
            p = p_parse_to_bar(dpg.get_value("cal_p"),
                                unit=app.app_settings.get("pressure_unit", "bar"))
            h = float(dpg.get_value("cal_h").replace(",", "."))
            v = float(dpg.get_value("cal_v").replace(",", "."))
        except ValueError:
            return
        if p < 0 or h < 0 or v < 0:
            _show_toast("\u26a0 Values must be non-negative.")
            return
        edit_idx = _cal_edit_idx[0]
        for i, pt in enumerate(clone.points):
            if i != edit_idx and abs(pt.p - p) < 1e-9:
                _show_toast("\u26a0 Duplicate pressure value \u2014 adjust and retry.")
                return
        if edit_idx is not None and 0 <= edit_idx < len(clone.points):
            clone.points[edit_idx] = CalibrationPoint(p=p, h=h, v=v)
            _cal_edit_idx[0] = None
            dpg.set_item_label("btn_cal_add", "Add Point")
        else:
            clone.points.append(CalibrationPoint(p=p, h=h, v=v))
            _cal_edit_idx[0] = None
        dpg.set_value("cal_p", "")
        dpg.set_value("cal_h", "")
        dpg.set_value("cal_v", "")
        refresh_table()

    def read_sensor():
        unit = app.app_settings.get("pressure_unit", "bar")
        dpg.set_value("cal_p", p_format(app.current_pressure, unit=unit).split()[0])

    def cancel_edit():
        _cal_edit_idx[0] = None
        dpg.set_item_label("btn_cal_add", "Add Point")
        dpg.set_value("cal_p", "")
        dpg.set_value("cal_h", "")
        dpg.set_value("cal_v", "")

    def save_cal():
        try:
            clone.name = dpg.get_value("cal_name")
            clone.overflow = float(dpg.get_value("cal_over").replace(",", "."))
            clone.water_discharge = float(dpg.get_value("cal_wd").replace(",", "."))
        except ValueError as e:
            logging.warning(f"Invalid calibration value: {e}")
        app.profile = clone
        _rebuild_interp_cache(app.profile.points)
        app.recalc_from_pressure()
        _refresh_limits()
        dpg.delete_item("dlg_cal")

    with dpg.window(label="Calibration Profile", modal=True, tag="dlg_cal",
                     width=500, height=580, no_resize=True, pos=[350, 120]):
        dpg.add_text("Profile Name:")
        dpg.add_input_text(default_value=clone.name, tag="cal_name", width=-1)
        with dpg.group(horizontal=True):
            with dpg.group():
                dpg.add_text("Overflow (mm):")
                dpg.add_input_text(default_value=str(clone.overflow), tag="cal_over", width=120)
            dpg.add_spacer(width=20)
            with dpg.group():
                dpg.add_text("Water Discharge (mm):")
                dpg.add_input_text(default_value=str(clone.water_discharge), tag="cal_wd", width=120)
        dpg.add_separator()
        dpg.add_text("Calibration Points:", color=COL_ACCENT)
        with dpg.group(tag="cal_table_wrap"):
            pass
        refresh_table()
        dpg.add_separator()
        _unit_label = PRESSURE_UNITS.get(app.app_settings.get("pressure_unit", "bar"), (1.0, "bar"))[1]
        dpg.add_text("Add / Edit Point:", color=COL_ACCENT)
        with dpg.group(horizontal=True):
            dpg.add_input_text(hint=f"P ({_unit_label})", tag="cal_p", width=100)
            dpg.add_input_text(hint="H (mm)", tag="cal_h", width=100)
            dpg.add_input_text(hint="Vol (L)", tag="cal_v", width=100)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Read Sensor", width=100, callback=read_sensor)
            dpg.add_button(label="Add Point", tag="btn_cal_add", width=100,
                           callback=add_or_update_point)
            dpg.add_button(label="Cancel Edit", width=100, callback=cancel_edit)
        dpg.add_separator()
        dpg.add_text("Export / Import points:", color=COL_GRAY)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Export Points (.json)", width=160,
                           callback=lambda: _cal_export_json(clone))
            dpg.add_button(label="Import...", width=100,
                           callback=lambda: _cal_import(clone, refresh_table))
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save Profile", width=140, callback=save_cal)
            dpg.add_button(label="Cancel", width=140,
                           callback=lambda: dpg.delete_item("dlg_cal"))


# ── Calibration export / import ─────────────────────────────────────
def _cal_export_json(profile_clone):
    EXPORT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r'[^\w\-]', '_', profile_clone.name)[:80]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = EXPORT_DIR / f"{safe}_cal_points_{ts}.json"
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"name": profile_clone.name, "points": profile_clone.points}, f, indent=4)
        _show_toast(f"Exported: {fp.name}")
    except OSError as e:
        logging.error(f"Cal JSON export failed: {e}")


def _cal_import(profile_clone, refresh_fn):
    if dpg.does_item_exist("fd_cal_import"):
        dpg.delete_item("fd_cal_import")
    with dpg.file_dialog(label="Import Calibration Points",
                          callback=lambda s, a: _cal_import_cb(s, a, profile_clone, refresh_fn),
                          tag="fd_cal_import", width=620, height=420):
        dpg.add_file_extension(".json", color=(137, 180, 250))


def _cal_import_cb(sender, app_data, profile_clone, refresh_fn):
    fp = app_data.get("file_path_name", "")
    if not fp:
        return
    try:
        fp = Path(fp)
        if fp.stat().st_size > EN14055_MAX_CAL_FILE_BYTES:
            _show_toast(f"\u26a0 File too large (>{EN14055_MAX_CAL_FILE_BYTES // (1024*1024)} MB) \u2014 import rejected")
            return
        new_pts = []
        skipped = 0
        if fp.suffix.lower() == ".csv":
            with open(fp, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
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
        profile_clone.points = new_pts
        refresh_fn()
        msg = f"Imported {len(new_pts)} points from {fp.name}"
        if skipped:
            msg += f" ({skipped} invalid row(s) skipped)"
        _show_toast(msg)
    except Exception as e:
        logging.error(f"Cal import failed: {e}")
        _show_toast(f"\u26a0 Import failed: {e}")


# ── Program settings dialog ─────────────────────────────────────────
def _open_program_dlg():
    if dpg.does_item_exist("dlg_prog"):
        dpg.delete_item("dlg_prog")
    s = app.app_settings
    with dpg.window(label="Program Settings", modal=True, tag="dlg_prog",
                     width=400, height=490, no_resize=True, pos=[410, 180]):
        dpg.add_text("Interface Theme:")
        dpg.add_combo(["Dark", "Light"],
                       default_value=s.get("ui_theme", "Dark"), tag="dlg_p_theme", width=-1)
        dpg.add_text("Pressure Display Unit:")
        dpg.add_combo(["bar", "mbar", "kPa"],
                       default_value=s.get("pressure_unit", "bar"), tag="dlg_p_unit", width=-1)
        dpg.add_text("Averaging Window (s):")
        dpg.add_combo(["0.1", "0.5", "1.0", "2.0"],
                       default_value=str(s.get("avg_window", 0.5)), tag="dlg_p_avg", width=-1)
        dpg.add_text("CWL Mode:")
        dpg.add_combo(["Automatic", "Manual"],
                       default_value=s.get("cwl_mode", "Automatic"), tag="dlg_p_mode", width=-1)
        dpg.add_text("Auto CWL Drop (mm):")
        dpg.add_combo(["0.5", "1.0", "1.5", "2.0", "5.0"],
                       default_value=str(s.get("cwl_drop_thresh", 1.5)), tag="dlg_p_thresh", width=-1)
        dpg.add_text("CWL Smooth:")
        dpg.add_combo(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"],
                       default_value=s.get("cwl_smooth", "SMA-5"), tag="dlg_p_smth", width=-1)
        dpg.add_text("UI Refresh (ms):")
        dpg.add_combo(["20", "50", "100"],
                       default_value=str(s.get("ui_refresh_ms", 50)), tag="dlg_p_ui_ref", width=-1)
        dpg.add_text("Chart Refresh (ms):")
        dpg.add_combo(["30", "50", "100", "200"],
                       default_value=str(s.get("chart_refresh_ms", 100)), tag="dlg_p_ch_ref", width=-1)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", width=110, callback=_save_prog)
            dpg.add_button(label="Cancel", width=100,
                           callback=lambda: dpg.delete_item("dlg_prog"))
            dpg.add_button(label="Reset to Defaults", width=140,
                           callback=lambda: (_reset_app_settings(),
                                             dpg.delete_item("dlg_prog")))


def _save_prog():
    s = app.app_settings
    s["ui_theme"] = dpg.get_value("dlg_p_theme")
    _apply_theme(s["ui_theme"])
    s["pressure_unit"] = dpg.get_value("dlg_p_unit")
    try:
        s["avg_window"] = float(dpg.get_value("dlg_p_avg"))
    except ValueError:
        pass
    s["cwl_mode"] = dpg.get_value("dlg_p_mode")
    try:
        s["cwl_drop_thresh"] = float(dpg.get_value("dlg_p_thresh"))
    except ValueError:
        pass
    s["cwl_smooth"] = dpg.get_value("dlg_p_smth")
    try:
        s["ui_refresh_ms"] = int(dpg.get_value("dlg_p_ui_ref"))
        s["chart_refresh_ms"] = int(dpg.get_value("dlg_p_ch_ref"))
    except ValueError:
        pass
    save_settings(app.conn_params, app.app_settings)
    _refresh_limits()
    dpg.delete_item("dlg_prog")


# ── Line colors dialog ──────────────────────────────────────────────
_LINE_COLOR_LABELS = {
    "sensor": "Sensor line",
    "mwl":    "NWL (fill level)",
    "menis":  "Meniscus",
    "wd":     "Water Discharge",
    "cwl":    "CWL (fault)",
}


def _open_line_colors_dlg():
    if dpg.does_item_exist("dlg_lc"):
        dpg.delete_item("dlg_lc")
    lc = app.app_settings.get("line_colors", {})
    with dpg.window(label="Chart Line Colors", modal=True, tag="dlg_lc",
                    width=340, height=320, no_resize=True, pos=[430, 200]):
        dpg.add_text("Click a swatch to change color.", color=COL_GRAY)
        dpg.add_spacer(height=6)
        for key, label in _LINE_COLOR_LABELS.items():
            col = lc.get(key, DEFAULT_LINE_COLORS[key])
            with dpg.group(horizontal=True):
                dpg.add_text(f"{label}:", color=COL_GRAY)
                dpg.add_spacer(width=max(0, 115 - len(label) * 7))
                dpg.add_color_edit(default_value=col, tag=f"lc_{key}",
                                   no_inputs=False, alpha_bar=False,
                                   display_type=dpg.mvColorEdit_rgb, width=200)
        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Apply & Save", width=140, callback=_save_line_colors)
            dpg.add_button(label="Reset Defaults", width=110, callback=_reset_line_colors)
            dpg.add_button(label="Cancel", width=60,
                           callback=lambda: dpg.delete_item("dlg_lc"))


def _save_line_colors():
    lc = app.app_settings.setdefault("line_colors", {})
    for key in _LINE_COLOR_LABELS:
        if dpg.does_item_exist(f"lc_{key}"):
            col = list(dpg.get_value(f"lc_{key}"))[:4]
            lc[key] = [int(c) for c in col]
            dpg.set_value(_line_color_dpg_ids[key], lc[key])
    save_settings(app.conn_params, app.app_settings)
    dpg.delete_item("dlg_lc")


def _reset_line_colors():
    for key, col in DEFAULT_LINE_COLORS.items():
        if dpg.does_item_exist(f"lc_{key}"):
            dpg.set_value(f"lc_{key}", col)


# ── File I/O ────────────────────────────────────────────────────────
def _load_profile_cb(sender, app_data):
    fp = app_data.get("file_path_name", "")
    if fp:
        try:
            with open(fp, encoding="utf-8") as f:
                app.profile = CisternProfile.from_dict(json.load(f))
            _rebuild_interp_cache(app.profile.points)
            app.recalc_from_pressure()
            _refresh_limits()
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Failed to load profile: {e}")


def _save_profile_cb(sender, app_data):
    fp = app_data.get("file_path_name", "")
    if fp:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(app.profile.to_dict(), f, indent=4)
        except OSError as e:
            logging.error(f"Failed to save profile: {e}")


def _load_profile():
    if dpg.does_item_exist("fd_load"):
        dpg.delete_item("fd_load")
    with dpg.file_dialog(label="Load Profile", callback=_load_profile_cb,
                          tag="fd_load", width=600, height=400):
        dpg.add_file_extension(".json", color=(0, 255, 0))


def _save_profile():
    if dpg.does_item_exist("fd_save"):
        dpg.delete_item("fd_save")
    with dpg.file_dialog(label="Save Profile", callback=_save_profile_cb,
                          tag="fd_save", width=600, height=400,
                          default_filename=f"{app.profile.name}.json"):
        dpg.add_file_extension(".json", color=(0, 255, 0))


def _save_as_default_profile():
    CONFIG_DIR.mkdir(exist_ok=True)
    fp = CONFIG_DIR / "default_profile.json"
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(app.profile.to_dict(), f, indent=4)
        _show_toast(f"Default profile set: {app.profile.name}")
    except OSError as e:
        logging.error(f"Failed to save default profile: {e}")


def _clear_default_profile():
    fp = CONFIG_DIR / "default_profile.json"
    if fp.exists():
        fp.unlink()
    _show_toast("Default profile cleared.")


def _reset_app_settings():
    for k, v in DEFAULT_APP.items():
        app.app_settings[k] = v if not isinstance(v, dict) else dict(v)
    for key, col in DEFAULT_LINE_COLORS.items():
        if key in _line_color_dpg_ids:
            dpg.set_value(_line_color_dpg_ids[key], col)
    _apply_theme("Dark")
    save_settings(app.conn_params, app.app_settings)
    _refresh_limits()
    _show_toast("Settings reset to defaults.")


# ── Update functions (with state caching) ───────────────────────────
def update_ui():
    """Update text labels — only writes to DPG when values actually change."""
    with app.data_lock:
        h = app.current_height
        v = app.current_volume
        p = app.current_pressure
        f = app.current_flow
        temp = app.current_temperature

    cache.set("lbl_h", f"{h:.1f} mm")
    cache.set("lbl_v", f"{v:.2f} L")
    cache.set("lbl_p", p_format(p, unit=app.app_settings.get("pressure_unit", "bar")))
    cache.set_if_exists("lbl_f", f"{f:.3f} L/s")
    cache.set_if_exists("lbl_temp", f"{temp:.1f} \u00b0C" if temp is not None else _TEMP_PLACEHOLDER)

    of = app.profile.overflow
    if dpg.does_item_exist("lbl_safety_margin"):
        if of > 0:
            sm = of - h
            col = COL_GREEN if sm >= 20 else (COL_ORANGE if sm >= 5 else COL_RED)
            new_val = f"{sm:.1f} mm"
            cache.set("lbl_safety_margin", new_val)
            dpg.configure_item("lbl_safety_margin", color=col)
        else:
            cache.set("lbl_safety_margin", "\u2014 mm")

    # RWL detection state
    thresh = app.app_settings.get("cwl_drop_thresh", 1.5)
    if app.cwl_state == "ARMED":
        if app.app_settings.get("cwl_mode") == "Automatic":
            cache.set("lbl_cwl_st", f"RWL: ARMED (drop \u2265 {thresh}mm)")
        else:
            cache.set("lbl_cwl_st", "RWL: ARMED (Manual)")
        _bind_status("lbl_cwl_st", "theme_blue")
    elif app.cwl_state == "WAITING":
        rem = max(0.0, 2.0 - (time.time() - app.cwl_timer))
        cache.set("lbl_cwl_st", f"RWL: TIMER {rem:.1f}s")
        _bind_status("lbl_cwl_st", "theme_orange")
    elif app.cwl_state == "DONE":
        cache.set("lbl_cwl_st", f"RWL: {app.profile.residual_wl:.1f}mm captured")
        _bind_status("lbl_cwl_st", "theme_green")
    else:
        cache.set("lbl_cwl_st", "RWL: IDLE (set NWL to arm)")
        _bind_status("lbl_cwl_st", "theme_gray")

    # MWL/CWL detection state
    if dpg.does_item_exist("lbl_cwl_auto_st"):
        if app.manual_mwl_cwl_pending:
            cache.set("lbl_cwl_auto_st", "MWL/CWL: click the MWL point on chart")
            _bind_status("lbl_cwl_auto_st", "theme_orange")
        elif app.cwl_auto_state == "ARMED":
            cache.set("lbl_cwl_auto_st", "CWL: ARMED \u2014 watching for drop \u22651.5mm")
            _bind_status("lbl_cwl_auto_st", "theme_blue")
        elif app.cwl_auto_state == "WAITING":
            rem = max(0.0, 2.0 - (time.time() - app.cwl_auto_timer))
            cache.set("lbl_cwl_auto_st", f"CWL: 2s WINDOW \u2014 {rem:.1f}s left")
            _bind_status("lbl_cwl_auto_st", "theme_orange")
        elif app.cwl_auto_state == "DONE":
            diff = app.profile.cwl - app.profile.overflow if app.profile.overflow > 0 else 0
            cache.set("lbl_cwl_auto_st",
                      f"CWL: {app.profile.cwl:.1f}mm  ({diff:+.1f}mm OF)")
            _bind_status("lbl_cwl_auto_st", "theme_green")
        else:
            cache.set("lbl_cwl_auto_st", "MWL/CWL: IDLE \u2014 use Auto-detect or Manual")
            _bind_status("lbl_cwl_auto_st", "theme_gray")


def update_chart():
    """Update chart series — thread-safe snapshot of deque data."""
    plot_idx = dpg.get_value("combo_plot")
    # Thread-safe: copy deques under lock
    with app.data_lock:
        t_snap = list(app.t_buf)
        if plot_idx == "Volume (L)":
            y_snap = list(app.v_buf)
        elif plot_idx == "Flow Rate (L/s)":
            y_snap = list(app.f_buf)
        else:
            y_snap = list(app.h_buf)

    if not t_snap or not y_snap:
        return

    alg = dpg.get_value("combo_smth")
    y_s = smooth(y_snap, alg)

    app.last_t = t_snap
    app.last_y = y_s

    dpg.set_value("line_main", [app.last_t, app.last_y])

    if not app.chart_paused:
        auto_scroll = dpg.get_value("chk_autoscroll")
        ws = dpg.get_value("combo_win")
        secs = {"10s": 10, "30s": 30, "60s": 60, "5min": 300}.get(ws, None)
        if auto_scroll and secs and t_snap:
            x_max = t_snap[-1]
            x_min = max(t_snap[0], x_max - secs)
            if x_max - x_min < 1:
                x_max = x_min + 1
            dpg.set_axis_limits("x_axis", x_min, x_max)
            start_i = bisect.bisect_left(t_snap, x_min)
            y_view = y_s[start_i:] if start_i < len(y_s) else y_s
            if y_view:
                y_lo, y_hi = min(y_view), max(y_view)
                margin = max((y_hi - y_lo) * 0.1, 0.5)
                dpg.set_axis_limits("y_axis", y_lo - margin, y_hi + margin)
        else:
            dpg.set_axis_limits_auto("x_axis")
            dpg.set_axis_limits_auto("y_axis")

    # Limit lines (height mode only)
    plot_mode = dpg.get_value("combo_plot")
    if "Height" in plot_mode and t_snap:
        x0, x1 = t_snap[0], t_snap[-1]
        if x1 - x0 < 1:
            x1 = x0 + 1
        menis_abs = app.profile.overflow + app.profile.meniscus if app.profile.overflow > 0 else 0
        for tag, val in [
            ("line_mwl",   app.profile.mwl),
            ("line_menis", menis_abs),
            ("line_wd",    app.profile.water_discharge),
            ("line_cwl",   app.profile.cwl),
        ]:
            if val > 0:
                dpg.set_value(tag, [[x0, x1], [val, val]])
                dpg.configure_item(tag, show=True)
            else:
                dpg.configure_item(tag, show=False)
    else:
        for tag in ["line_mwl", "line_menis", "line_wd", "line_cwl"]:
            dpg.configure_item(tag, show=False)


def update_hover_tooltip():
    if not dpg.is_item_hovered("main_plot") or not app.last_t or not app.last_y:
        dpg.configure_item("hover_annot", show=False)
        return
    pos = dpg.get_plot_mouse_pos()
    st, sy = _snap_to_line(pos[0])
    if st is None:
        dpg.configure_item("hover_annot", show=False)
        return
    unit = "mm"
    plot_mode = dpg.get_value("combo_plot")
    if "Volume" in plot_mode:
        unit = "L"
    elif "Flow" in plot_mode:
        unit = "L/s"
    dpg.set_value("hover_annot", (st, sy))
    dpg.configure_item("hover_annot", label=f"T: {st:.1f}s\n{sy:.2f} {unit}", show=True)


def _tick_flush_auto_stop(h: float, now: float):
    if not app.flush_measuring:
        return
    elapsed = now - app.flush_start_time
    if elapsed < 3.0:
        return
    if h < app.flush_min_h:
        app.flush_min_h = h
        app.flush_rising = False
    if not app.flush_rising and h > app.flush_min_h + 5.0:
        app.flush_rising = True
        app.flush_rising_timer = now
    if app.flush_rising and (now - app.flush_rising_timer >= 2.0):
        _toggle_flush_measure()


def frame_callback():
    """Per-frame tick — UI updates, chart updates, detection ticks."""
    now = time.time()
    ui_interval = app.app_settings.get("ui_refresh_ms", 50) / 1000.0
    chart_interval = app.app_settings.get("chart_refresh_ms", 100) / 1000.0

    if now - app._last_ui_tick >= ui_interval:
        with app.data_lock:
            h = app.current_height
            h_history = list(app.h_buf)[-150:] if app.h_buf else [h]
            t_history = list(app.t_buf)[-150:] if app.t_buf else []
        if app.tick_rwl(h, h_history):
            _refresh_limits()
        if app.tick_cwl_auto(h, h_history, t_history):
            _refresh_limits()
        _tick_flush_auto_stop(h, now)
        update_ui()
        _check_toast_dismiss(now)
        app._last_ui_tick = now

    if now - app._last_chart_tick >= chart_interval:
        if not app.chart_paused:
            update_chart()
        app._last_chart_tick = now
        update_hover_tooltip()


# ══════════════════════════════════════════════════════════════════════
# GUI CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════
def build_gui():
    """Build the entire DearPyGui interface. Called once from main()."""
    global _line_color_dpg_ids, _font_ui, _font_medium, _font_large

    dpg.create_context()
    dpg.create_viewport(title="EN 14055 Cistern Analytics - ifm PI1789",
                        width=1200, height=850)

    # ── Fonts ───────────────────────────────────────────────────────
    _, _, _font_ui, _font_medium, _font_large = setup_fonts()

    # ── Themes ──────────────────────────────────────────────────────
    create_modern_theme()
    create_status_themes()
    create_button_themes()
    _lc = app.app_settings.get("line_colors", {})
    _line_color_dpg_ids = create_line_themes(_lc, DEFAULT_LINE_COLORS)

    dpg.bind_theme("theme_dark")

    # ── Main window ─────────────────────────────────────────────────
    with dpg.window(tag="main_win"):
        # Menu bar
        with dpg.menu_bar():
            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Load Profile...", callback=_load_profile)
                dpg.add_menu_item(label="Save Profile As...", callback=_save_profile)
                dpg.add_separator()
                dpg.add_menu_item(label="Set as Default Profile", callback=_save_as_default_profile)
                dpg.add_menu_item(label="Clear Default Profile", callback=_clear_default_profile)
                dpg.add_separator()
                dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
            with dpg.menu(label="Settings"):
                dpg.add_menu_item(label="Hardware Connection...", callback=_open_connection_dlg)
                dpg.add_menu_item(label="Edit Calibration Profile...", callback=_open_calibration_dlg)
                dpg.add_menu_item(label="Program Settings...", callback=_open_program_dlg)
                dpg.add_menu_item(label="Chart Line Colors...", callback=_open_line_colors_dlg)
            with dpg.menu(label="Test"):
                dpg.add_menu_item(label="EN 14055 Compliance Check", callback=_check_compliance)

        # Status bar
        with dpg.table(header_row=False, borders_innerV=False, borders_outerH=False,
                       borders_outerV=False, resizable=False):
            dpg.add_table_column(width_fixed=True, init_width_or_weight=28)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(width_fixed=True, init_width_or_weight=180)
            dpg.add_table_column(width_fixed=True, init_width_or_weight=160)
            with dpg.table_row():
                dpg.add_button(label="\u25c0", tag="btn_collapse",
                               callback=_toggle_left_panel, width=24)
                dpg.add_text("Active Profile: Untitled Profile", tag="lbl_profile")
                with dpg.group(horizontal=True):
                    dpg.add_text("", tag="lbl_conn_icon", color=COL_GRAY)
                    dpg.add_text("Disconnected", tag="lbl_conn", color=COL_GRAY)
                dpg.add_button(label="Connect Sensor", tag="btn_connect",
                               callback=_toggle_connect, width=-1)

        dpg.add_separator()

        # Main layout
        with dpg.group(horizontal=True):
            _build_left_panel()
            _build_right_panel()

    # Apply line themes to series
    dpg.bind_item_theme("line_main",  "theme_line_main")
    dpg.bind_item_theme("line_mwl",   "theme_line_mwl")
    dpg.bind_item_theme("line_menis", "theme_line_menis")
    dpg.bind_item_theme("line_wd",    "theme_line_wd")
    dpg.bind_item_theme("line_cwl",   "theme_line_cwl")
    dpg.bind_item_theme("scatter_click1", "theme_click1")
    dpg.bind_item_theme("scatter_click2", "theme_click2")

    dpg.set_primary_window("main_win", True)

    # Bind larger fonts
    if _font_large:
        dpg.bind_item_font("lbl_h", _font_large)
        dpg.bind_item_font("lbl_v", _font_large)
    if _font_medium:
        dpg.bind_item_font("lbl_p", _font_medium)
        dpg.bind_item_font("lbl_f", _font_medium)
        dpg.bind_item_font("lbl_temp", _font_medium)
        for _tag in ("lbl_mwl", "lbl_mwl_fault", "lbl_cwl", "lbl_menis",
                     "lbl_overflow", "lbl_residual", "lbl_safety_margin",
                     "lbl_safety_margin_static"):
            dpg.bind_item_font(_tag, _font_medium)

    # Mouse handler
    with dpg.handler_registry():
        dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=_plot_clicked)


def _build_left_panel():
    """Construct the left sidebar with live data, limits, flush, and log sections."""
    _avg = app.app_settings.get("avg_window", 0.5)

    with dpg.child_window(width=340, border=False, tag="left_panel"):
        # Live Data
        dpg.add_text("  LIVE DATA", color=COL_ACCENT, tag="hdr_live")
        dpg.add_separator()
        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True):
            with dpg.group():
                dpg.add_text("0.0 mm",     tag="lbl_h", color=COL_ACCENT)
                dpg.add_text("0.00 L",     tag="lbl_v", color=COL_GREEN)
                dpg.add_text("0.0000 bar", tag="lbl_p", color=COL_GRAY)
            dpg.add_spacer(width=12)
            with dpg.group():
                dpg.add_text(_TEMP_PLACEHOLDER, tag="lbl_temp", color=COL_GRAY)
                dpg.add_text("0.000 L/s",      tag="lbl_f",    color=COL_ORANGE)
                with dpg.tooltip("lbl_f"):
                    dpg.add_text("Instantaneous flow rate (live).\n"
                                 "EN 14055 effective rate (skip first 1 L,\n"
                                 "last 2 L) is shown in the Flush table.")

        dpg.add_spacer(height=8)

        # EN 14055 Limits
        dpg.add_text("  EN 14055 LIMITS", color=COL_ACCENT, tag="hdr_limits")
        dpg.add_separator()
        dpg.add_spacer(height=2)

        with dpg.table(header_row=False, borders_innerV=False,
                       borders_outerV=False, borders_innerH=False,
                       borders_outerH=False, pad_outerX=False):
            dpg.add_table_column()
            dpg.add_table_column()
            with dpg.table_row():
                dpg.add_button(label=f"Set NWL ({_avg}s)",
                               tag="btn_mwl", callback=_set_mwl, width=-1)
                dpg.bind_item_theme("btn_mwl", "theme_btn_action")
                dpg.add_button(label=f"Set Meniscus ({_avg}s)",
                               tag="btn_menis", callback=_set_meniscus, width=-1)
                dpg.bind_item_theme("btn_menis", "theme_btn_action")

        dpg.add_button(label="Auto-detect MWL/CWL", callback=_arm_cwl_auto, width=-1)
        dpg.bind_item_theme(dpg.last_item(), "theme_btn_action")
        dpg.add_button(label="Manual MWL/CWL", tag="btn_manual_mwlcwl",
                       callback=_manual_mwl_cwl, width=-1)
        dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_action")
        dpg.add_button(label="Start RWL 2s Timer", tag="btn_manual_rwl",
                       callback=_manual_rwl, width=-1, show=False)
        dpg.bind_item_theme("btn_manual_rwl", "theme_btn_action")

        dpg.add_spacer(height=4)

        # Limits grid
        with dpg.group(horizontal=True):
            with dpg.group(width=160):
                dpg.add_text("NWL (fill):", color=COL_GRAY)
                dpg.add_text("\u2014", tag="lbl_mwl")
                dpg.add_spacer(height=3)
                dpg.add_text("MWL (fault):", color=COL_GRAY)
                dpg.add_text("\u2014", tag="lbl_mwl_fault")
                dpg.add_spacer(height=3)
                dpg.add_text("CWL (2s):", color=COL_GRAY)
                dpg.add_text("\u2014", tag="lbl_cwl")
                dpg.add_spacer(height=3)
                dpg.add_text("Residual WL:", color=COL_GRAY)
                dpg.add_text("0.0 mm", tag="lbl_residual")
            with dpg.group():
                dpg.add_text("Meniscus:", color=COL_GRAY)
                dpg.add_text("0.0 mm", tag="lbl_menis")
                dpg.add_spacer(height=3)
                dpg.add_text("Overflow:", color=COL_GRAY)
                dpg.add_text("0.0 mm", tag="lbl_overflow", color=COL_GRAY)
                dpg.add_spacer(height=3)
                dpg.add_text("Safety margin c:", color=COL_GRAY)
                dpg.add_text("\u2014", tag="lbl_safety_margin_static")
                dpg.add_spacer(height=3)
                dpg.add_text("Live headroom:", color=COL_GRAY)
                dpg.add_text("\u2014 mm", tag="lbl_safety_margin")

        dpg.add_spacer(height=6)

        # Status indicators
        dpg.add_text("CWL: \u2014 (capture during fault test)", tag="lbl_airgap")
        _bind_status("lbl_airgap", "theme_gray")
        dpg.add_text("CWL: IDLE \u2014 arm while at MWL", tag="lbl_cwl_auto_st")
        _bind_status("lbl_cwl_auto_st", "theme_gray")
        dpg.add_text("RWL: IDLE (set NWL to arm)", tag="lbl_cwl_st")
        _bind_status("lbl_cwl_st", "theme_gray")

        dpg.add_spacer(height=8)

        # Flush Test
        dpg.add_text("  FLUSH TEST  (EN 14055)", color=COL_ACCENT, tag="hdr_flush")
        dpg.add_separator()
        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True):
            dpg.add_text("Type:", color=COL_GRAY)
            dpg.add_combo(["Full Flush", "Part Flush"],
                          default_value="Full Flush",
                          tag="combo_flush_type", width=-1)
        dpg.add_spacer(height=3)
        dpg.add_button(label="Start Flush Measurement", tag="btn_flush",
                       callback=_toggle_flush_measure, width=-1)
        dpg.bind_item_theme("btn_flush", "theme_btn_success")
        dpg.add_text("* EN col = rate ignoring first 1L and last 2L",
                     color=COL_GRAY)
        dpg.add_spacer(height=3)
        with dpg.child_window(tag="flush_table_area", height=145,
                              border=False, no_scrollbar=True):
            dpg.add_text("No measurements yet.", color=COL_GRAY)
        dpg.add_spacer(height=3)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Clear All", callback=_clear_flush, width=90)
            dpg.bind_item_theme(dpg.last_item(), "theme_btn_danger")
            dpg.add_button(label="Compliance Check", callback=_check_compliance, width=-1)

        dpg.add_spacer(height=8)

        # Data Log
        dpg.add_text("  DATA LOG", color=COL_ACCENT, tag="hdr_log")
        dpg.add_separator()
        dpg.add_spacer(height=2)
        dpg.add_button(label="Start Data Log (CSV)", tag="btn_log",
                       callback=_toggle_log, width=-1)
        dpg.bind_item_theme("btn_log", "theme_btn_success")


def _build_right_panel():
    """Construct the right panel with chart toolbar and plot."""
    with dpg.child_window(border=False):
        # Toolbar
        with dpg.group(horizontal=True):
            dpg.add_text("Axis:")
            dpg.add_combo(["Height (mm)", "Volume (L)", "Flow Rate (L/s)"],
                          default_value="Height (mm)", tag="combo_plot", width=132)
            dpg.add_spacer(width=4)
            dpg.add_text("Window:")
            dpg.add_combo(["10s", "30s", "60s", "5min", "All"],
                          default_value="30s", tag="combo_win", width=72)
            dpg.add_spacer(width=4)
            dpg.add_text("Smooth:")
            dpg.add_combo(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"],
                          default_value="None", tag="combo_smth", width=100)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Auto-scroll", tag="chk_autoscroll", default_value=True)
            dpg.add_spacer(width=4)
            dpg.add_button(label="Pause", tag="btn_pause",
                           callback=_toggle_pause, width=78)
            dpg.add_button(label="Screenshot", callback=_export_screenshot, width=100)
            dpg.add_button(label="Colors", callback=_open_line_colors_dlg, width=58)
            dpg.add_spacer(width=8)
            dpg.add_text("Delta:", color=COL_GRAY)
            dpg.add_text("---", tag="lbl_delta", color=COL_ACCENT)
            dpg.add_button(label="Clear", callback=_clear_delta, width=50)

        dpg.add_spacer(height=2)

        # Plot
        with dpg.plot(tag="main_plot", height=-1, width=-1, anti_aliased=True,
                      crosshairs=True, query=False):
            dpg.add_plot_legend(location=dpg.mvPlot_Location_NorthWest, outside=False)
            dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag="x_axis")
            with dpg.plot_axis(dpg.mvYAxis, label="Height (mm)", tag="y_axis"):
                dpg.add_line_series([], [], tag="line_main",  label="Sensor")
                dpg.add_line_series([], [], tag="line_mwl",   label="NWL",           show=False)
                dpg.add_line_series([], [], tag="line_menis", label="Meniscus",       show=False)
                dpg.add_line_series([], [], tag="line_wd",    label="Water Disch.",  show=False)
                dpg.add_line_series([], [], tag="line_cwl",   label="CWL (fault)",   show=False)
                dpg.add_scatter_series([], [], tag="scatter_click1", label="")
                dpg.add_scatter_series([], [], tag="scatter_click2", label="")

            dpg.add_drag_line(label="NWL [drag]", tag="drag_mwl",
                              color=[100, 160, 255, 200],
                              default_value=0.0, vertical=False, show=False,
                              callback=_on_drag_mwl)
            dpg.add_drag_line(label="CWL [drag]", tag="drag_cwl",
                              color=[250, 179, 90, 200],
                              default_value=0.0, vertical=False, show=False,
                              callback=_on_drag_cwl)
            dpg.add_plot_annotation(label="", default_value=(0, 0), offset=(12, -18),
                                    color=(205, 214, 244, 230),
                                    tag="hover_annot", show=False)
            dpg.add_plot_annotation(label="", default_value=(0, 0), offset=(12, -28),
                                    color=(243, 139, 168, 240),
                                    tag="annot_click1", show=False)
            dpg.add_plot_annotation(label="", default_value=(0, 0), offset=(12, -28),
                                    color=(166, 227, 161, 240),
                                    tag="annot_click2", show=False)


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════
def main():
    global _last_frame_time

    build_gui()

    dpg.setup_dearpygui()
    dpg.show_viewport()

    try:
        # Apply saved theme
        _apply_theme(app.app_settings.get("ui_theme", "Dark"))

        # Auto-connect
        app.connect()
        if app.is_connected:
            dpg.set_item_label("btn_connect", "Disconnect")
            dpg.configure_item("lbl_conn",
                               default_value=f"{app.conn_params['port']}  {app.conn_params['baud']}bd")
            dpg.configure_item("lbl_conn_icon", default_value="\u25cf", color=COL_GREEN)
            dpg.bind_item_theme("btn_connect", "theme_btn_danger")
        elif app.last_error:
            dpg.configure_item("lbl_conn", default_value=app.last_error[:48])
            _bind_status("lbl_conn", "theme_red")

        # ── Main loop with 60 FPS throttle ──────────────────────────
        while dpg.is_dearpygui_running():
            frame_callback()
            dpg.render_dearpygui_frame()

            # 60 FPS throttle
            now = time.time()
            elapsed = now - _last_frame_time
            if elapsed < _MIN_FRAME_TIME:
                time.sleep(_MIN_FRAME_TIME - elapsed)
            _last_frame_time = time.time()

        app.cleanup()
        dpg.destroy_context()

    except Exception as e:
        import traceback
        try:
            crash_log = BASE_DIR / "crash.log"
            with open(crash_log, "a", encoding="utf-8") as _cf:
                _cf.write(f"\n--- {datetime.now().isoformat()} ---\n")
                traceback.print_exc(file=_cf)
        except Exception:
            pass
        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()

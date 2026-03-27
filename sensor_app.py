import sys
try:
    import dearpygui.dearpygui as dpg
    import serial
    import serial.tools.list_ports
except ImportError as e:
    print(f"Липсва зависимост: {e}")
    print("Моля, инсталирайте нужните пакети: pip install dearpygui pyserial")
    input("Натиснете Enter за изход...")
    sys.exit(1)
import time
import threading
import collections
import struct
import json
import csv
import os
import math
from datetime import datetime

# ── Settings persistence ────────────────────────────────────────────
SETTINGS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "CisternAnalytics")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

DEFAULT_CONN = {"port": "COM8", "baud": 115200, "io_port": "Port 1", "poll_ms": 50}
DEFAULT_APP = {
    "avg_window": 0.5, "cwl_mode": "Automatic", "cwl_drop_thresh": 1.5,
    "cwl_smooth": "SMA-5", "ui_refresh_ms": 50, "chart_refresh_ms": 100
}

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                d = json.load(f)
                return d.get("conn", dict(DEFAULT_CONN)), d.get("app", dict(DEFAULT_APP))
    except Exception:
        pass
    return dict(DEFAULT_CONN), dict(DEFAULT_APP)

def save_settings(conn, app):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"conn": conn, "app": app}, f, indent=2)
    except Exception:
        pass

# ── Profile ─────────────────────────────────────────────────────────
class CisternProfile:
    def __init__(self):
        self.name = "Untitled Profile"
        self.points = []
        self.mwl = 0.0
        self.meniscus = 0.0
        self.cwl = 0.0
        self.overflow = 0.0
        self.water_discharge = 0.0

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ["name", "points", "mwl", "meniscus", "cwl", "overflow", "water_discharge"]}

    def from_dict(self, d):
        for k in ["name", "points", "mwl", "meniscus", "cwl", "overflow", "water_discharge"]:
            if k in d:
                setattr(self, k, d[k])

    def clone(self):
        p = CisternProfile()
        p.from_dict(self.to_dict())
        p.points = [dict(pt) for pt in self.points]
        return p

# ── Smoothing ───────────────────────────────────────────────────────
def smooth(data, alg):
    if alg == "None" or len(data) < 2:
        return list(data)
    r = []
    if alg.startswith("SMA"):
        w = int(alg.split("-")[1])
        for i in range(len(data)):
            s = max(0, i - w + 1)
            r.append(sum(data[s:i+1]) / (i - s + 1))
        return r
    if alg.startswith("EMA"):
        a = 0.2 if "Fast" in alg else 0.05
        r.append(data[0])
        for i in range(1, len(data)):
            r.append(a * data[i] + (1 - a) * r[-1])
        return r
    return list(data)

# ── Interpolation ───────────────────────────────────────────────────
def interp_hv(p_bar, pts):
    if not pts:
        return 0.0, 0.0
    pts = sorted(pts, key=lambda x: x["p"])
    if len(pts) == 1:
        return pts[0]["h"], pts[0]["v"]
    if p_bar <= pts[0]["p"]:
        r = (p_bar - pts[0]["p"]) / (pts[1]["p"] - pts[0]["p"]) if pts[1]["p"] != pts[0]["p"] else 0
        return pts[0]["h"] + r * (pts[1]["h"] - pts[0]["h"]), pts[0]["v"] + r * (pts[1]["v"] - pts[0]["v"])
    if p_bar >= pts[-1]["p"]:
        r = (p_bar - pts[-2]["p"]) / (pts[-1]["p"] - pts[-2]["p"]) if pts[-1]["p"] != pts[-2]["p"] else 0
        return pts[-2]["h"] + r * (pts[-1]["h"] - pts[-2]["h"]), pts[-2]["v"] + r * (pts[-1]["v"] - pts[-2]["v"])
    for i in range(len(pts) - 1):
        if pts[i]["p"] <= p_bar <= pts[i+1]["p"]:
            d = pts[i+1]["p"] - pts[i]["p"]
            r = (p_bar - pts[i]["p"]) / d if d else 0
            return pts[i]["h"] + r * (pts[i+1]["h"] - pts[i]["h"]), pts[i]["v"] + r * (pts[i+1]["v"] - pts[i]["v"])
    return 0.0, 0.0

# ── IFM Protocol ────────────────────────────────────────────────────
def build_request(port_idx):
    payload = json.dumps({
        "code": 10, "cid": 1, "adr": "/getdatamulti",
        "data": {"datatosend": [f"/iolinkmaster/port[{port_idx}]/iolinkdevice/pdin"]}
    })
    return b'\x01\x0110' + f"{len(payload):08X}".encode() + payload.encode()

# ── Application ─────────────────────────────────────────────────────
class SensorApp:
    def __init__(self):
        self.conn_params, self.app_settings = load_settings()
        # Merge defaults for any missing keys
        for k, v in DEFAULT_CONN.items():
            self.conn_params.setdefault(k, v)
        for k, v in DEFAULT_APP.items():
            self.app_settings.setdefault(k, v)

        self.profile = CisternProfile()
        self.serial_conn = None
        self.is_connected = False
        self.is_logging = False
        self.csv_file = None
        self.csv_writer = None

        self.stop_event = threading.Event()
        self.read_thread_obj = None
        self.data_lock = threading.Lock()

        self.max_pts = 12000
        self.t_buf = collections.deque(maxlen=self.max_pts)
        self.h_buf = collections.deque(maxlen=self.max_pts)
        self.v_buf = collections.deque(maxlen=self.max_pts)
        self.f_buf = collections.deque(maxlen=self.max_pts)

        self.start_time = time.time()
        self.current_pressure = 0.0
        self.current_height = 0.0
        self.current_volume = 0.0

        self.cwl_state = "IDLE"
        self.cwl_peak = 0.0
        self.cwl_timer = 0.0

        self.click_points = []
        self.last_t = []
        self.last_y = []
        self._last_ui_tick = 0
        self._last_chart_tick = 0

    # ── Sensor thread ───────────────────────────────────────────────
    def read_thread(self):
        rx_buf = bytearray()
        port_idx = int(self.conn_params["io_port"].replace("Port ", ""))
        poll_sleep = self.conn_params["poll_ms"] / 1000.0

        while not self.stop_event.is_set():
            t0 = time.time()
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    req = build_request(port_idx)
                    self.serial_conn.write(req)
                    time.sleep(min(0.02, poll_sleep / 2))

                    avail = self.serial_conn.in_waiting
                    if avail > 0:
                        rx_buf.extend(self.serial_conn.read(avail))

                    while b'\x01\x0110' in rx_buf:
                        idx = rx_buf.find(b'\x01\x0110')
                        if len(rx_buf) < idx + 12:
                            break
                        try:
                            exp_len = int(rx_buf[idx+4:idx+12].decode(errors="ignore"), 16)
                        except ValueError:
                            rx_buf = rx_buf[idx+4:]
                            continue

                        pkt_len = 12 + exp_len
                        if len(rx_buf) < idx + pkt_len:
                            break

                        j_str = rx_buf[idx+12:idx+pkt_len].decode(errors="ignore").strip()
                        rx_buf = rx_buf[idx+pkt_len:]
                        try:
                            js = json.loads(j_str)
                            pth = f"/iolinkmaster/port[{port_idx}]/iolinkdevice/pdin"
                            pld = js.get("data", {}).get(pth, {})
                            if pld.get("code") == 200:
                                hx = pld.get("data", "")
                                if len(hx) >= 8:
                                    raw = struct.unpack(">i", bytes.fromhex(hx[:8]))[0]
                                    p_bar = raw * 0.0001
                                    h, v = interp_hv(p_bar, self.profile.points)
                                    with self.data_lock:
                                        t = time.time() - self.start_time
                                        f_rate = 0.0
                                        if len(self.t_buf) > 5:
                                            dt = t - self.t_buf[-5]
                                            if dt > 0:
                                                f_rate = (self.v_buf[-5] - v) / dt
                                        self.current_pressure = p_bar
                                        self.current_height = h
                                        self.current_volume = v
                                        self.t_buf.append(t)
                                        self.h_buf.append(h)
                                        self.v_buf.append(v)
                                        self.f_buf.append(f_rate)

                                    if self.is_logging and self.csv_writer:
                                        self.csv_writer.writerow([
                                            datetime.now().isoformat(),
                                            f"{p_bar:.4f}", f"{h:.1f}", f"{v:.2f}", f"{f_rate:.3f}"
                                        ])
                        except Exception:
                            pass

                    if len(rx_buf) > 4000:
                        rx_buf = rx_buf[-2000:]
                except Exception:
                    break

            elapsed = time.time() - t0
            slp = poll_sleep - elapsed
            if slp > 0:
                time.sleep(slp)

    # ── Connection ──────────────────────────────────────────────────
    def connect(self):
        if self.is_connected:
            return
        try:
            self.serial_conn = serial.Serial(
                self.conn_params["port"], self.conn_params["baud"], timeout=0.1)
            self.is_connected = True
            self.stop_event.clear()
            self.start_time = time.time()
            with self.data_lock:
                self.t_buf.clear()
                self.h_buf.clear()
                self.v_buf.clear()
                self.f_buf.clear()
            self.read_thread_obj = threading.Thread(target=self.read_thread, daemon=True)
            self.read_thread_obj.start()
        except Exception as e:
            self.is_connected = False

    def disconnect(self):
        if not self.is_connected:
            return
        self.stop_event.set()
        if self.read_thread_obj:
            self.read_thread_obj.join(timeout=1.0)
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        self.is_connected = False
        self.serial_conn = None

    # ── Helpers ─────────────────────────────────────────────────────
    def get_avg_height(self):
        window = self.app_settings.get("avg_window", 0.5)
        with self.data_lock:
            if not self.h_buf:
                return self.current_height
            now = self.t_buf[-1]
            vals = [self.h_buf[i] for i in range(len(self.h_buf)) if now - self.t_buf[i] <= window]
            return sum(vals) / len(vals) if vals else self.current_height

    def cleanup(self):
        save_settings(self.conn_params, self.app_settings)
        self.disconnect()
        if self.csv_file:
            self.csv_file.close()

# ── GUI ─────────────────────────────────────────────────────────────
app = SensorApp()

# Catppuccin-inspired colors
COL_BG = (30, 30, 46)
COL_CARD = (42, 42, 61, 208)
COL_ACCENT = (137, 180, 250)
COL_GREEN = (166, 227, 161)
COL_RED = (243, 139, 168)
COL_GRAY = (128, 128, 128)
COL_ORANGE = (250, 179, 90)
COL_BLUE = (30, 102, 245)
COL_WHITE = (205, 214, 244)

def _toggle_connect():
    if app.is_connected:
        app.disconnect()
        dpg.set_item_label("btn_connect", "Connect Sensor")
        dpg.configure_item("lbl_conn", default_value="Disconnected")
    else:
        app.connect()
        if app.is_connected:
            dpg.set_item_label("btn_connect", "Disconnect")
            dpg.configure_item("lbl_conn", default_value=f"Connected ({app.conn_params['port']})")

def _set_mwl():
    val = app.get_avg_height()
    app.profile.mwl = val
    app.cwl_state = "ARMED"
    app.cwl_peak = val
    _refresh_limits()

def _set_meniscus():
    app.profile.meniscus = app.get_avg_height()
    _refresh_limits()

def _manual_cwl():
    if app.cwl_state == "ARMED":
        app.cwl_state = "WAITING"
        app.cwl_timer = time.time()

def _refresh_limits():
    p = app.profile
    dpg.set_value("lbl_mwl", f"MWL: {p.mwl:.1f} mm")
    dpg.set_value("lbl_menis", f"Meniscus: {p.meniscus:.1f} mm")
    dpg.set_value("lbl_cwl", f"CWL: {p.cwl:.1f} mm")
    dpg.set_value("lbl_wd", f"Water Disch.: {p.water_discharge:.1f} mm")
    dpg.set_value("lbl_profile", f"Active Profile: {p.name}")

    w = app.app_settings.get("avg_window", 0.5)
    dpg.set_item_label("btn_mwl", f"Set MWL (Avg {w}s)")
    dpg.set_item_label("btn_menis", f"Set Meniscus (Avg {w}s)")

    show_manual = app.app_settings.get("cwl_mode") == "Manual" and app.cwl_state == "ARMED"
    dpg.configure_item("btn_manual_cwl", show=show_manual)

    if p.water_discharge > 0 and p.cwl > 0:
        m = p.water_discharge - p.cwl
        if m >= 20:
            dpg.set_value("lbl_margin", f"MARGIN: OK ({m:.1f}mm)")
            dpg.bind_item_theme("lbl_margin", "theme_green")
        else:
            dpg.set_value("lbl_margin", f"MARGIN: FAIL ({m:.1f}mm < 20)")
            dpg.bind_item_theme("lbl_margin", "theme_red")
    else:
        dpg.set_value("lbl_margin", "MARGIN: WAITING")
        dpg.bind_item_theme("lbl_margin", "theme_gray")

def _toggle_log():
    if not app.is_logging:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"{app.profile.name.replace(' ', '_')}_{ts}.csv"
        try:
            app.csv_file = open(fn, "w", newline="")
            app.csv_writer = csv.writer(app.csv_file)
            app.csv_writer.writerow(["Timestamp", "P(bar)", "H(mm)", "V(L)", "F(L/s)"])
            app.is_logging = True
            dpg.set_item_label("btn_log", "Stop Data Log")
        except Exception:
            pass
    else:
        app.is_logging = False
        dpg.set_item_label("btn_log", "Start Data Log (CSV)")
        if app.csv_file:
            app.csv_file.close()
            app.csv_file = None

def _clear_delta():
    app.click_points.clear()
    dpg.set_value("lbl_delta", "Delta: Click 2 pts")

def _plot_clicked(sender, app_data):
    if dpg.is_item_hovered("main_plot"):
        pos = dpg.get_plot_mouse_pos()
        t = pos[0]
        y = pos[1]
        if len(app.click_points) >= 2:
            app.click_points.clear()
        app.click_points.append((t, y))
        
        if len(app.click_points) == 1:
            dpg.configure_item("lbl_delta", default_value=f"Pt1: {y:.2f}. Click Pt2...")
        else:
            dt = app.click_points[1][0] - app.click_points[0][0]
            dy = app.click_points[1][1] - app.click_points[0][1]
            dpg.configure_item("lbl_delta", default_value=f"\u0394T: {abs(dt):.1f}s | \u0394Y: {dy:.2f}")


# ── Dialogs ─────────────────────────────────────────────────────────
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

def _open_calibration_dlg():
    if dpg.does_item_exist("dlg_cal"):
        dpg.delete_item("dlg_cal")

    clone = app.profile.clone()

    def refresh_table():
        if dpg.does_item_exist("cal_table"):
            dpg.delete_item("cal_table", children_only=True)
            for pt in sorted(clone.points, key=lambda x: x["p"]):
                with dpg.table_row(parent="cal_table"):
                    dpg.add_text(f"{pt['p']:.4f}")
                    dpg.add_text(f"{pt['h']:.1f}")
                    dpg.add_text(f"{pt['v']:.2f}")

    def add_point():
        try:
            p = float(dpg.get_value("cal_p").replace(",", "."))
            h = float(dpg.get_value("cal_h").replace(",", "."))
            v = float(dpg.get_value("cal_v").replace(",", "."))
            clone.points.append({"p": p, "h": h, "v": v})
            dpg.set_value("cal_p", "")
            dpg.set_value("cal_h", "")
            dpg.set_value("cal_v", "")
            refresh_table()
        except ValueError:
            pass

    def read_sensor():
        dpg.set_value("cal_p", f"{app.current_pressure:.4f}")

    def remove_last():
        if clone.points:
            clone.points.pop()
            refresh_table()

    def save_cal():
        try:
            clone.name = dpg.get_value("cal_name")
            clone.overflow = float(dpg.get_value("cal_over").replace(",", "."))
            clone.water_discharge = float(dpg.get_value("cal_wd").replace(",", "."))
        except ValueError:
            pass
        app.profile = clone
        _refresh_limits()
        dpg.delete_item("dlg_cal")

    with dpg.window(label="Calibration Profile", modal=True, tag="dlg_cal",
                     width=440, height=520, no_resize=True, pos=[380, 150]):
        dpg.add_text("Profile Name:")
        dpg.add_input_text(default_value=clone.name, tag="cal_name", width=-1)
        dpg.add_text("Overflow (mm):")
        dpg.add_input_text(default_value=str(clone.overflow), tag="cal_over", width=120)
        dpg.add_text("Water Discharge (mm):")
        dpg.add_input_text(default_value=str(clone.water_discharge), tag="cal_wd", width=120)

        dpg.add_separator()
        dpg.add_text("Add Calibration Point:", color=COL_ACCENT)
        with dpg.group(horizontal=True):
            dpg.add_input_text(hint="P (bar)", tag="cal_p", width=95)
            dpg.add_input_text(hint="H (mm)", tag="cal_h", width=95)
            dpg.add_input_text(hint="Vol (L)", tag="cal_v", width=95)
        dpg.add_button(label="Read Sensor Pressure", width=-1, callback=read_sensor)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Add Point", width=140, callback=add_point)
            dpg.add_button(label="Remove Last", width=140, callback=remove_last)

        with dpg.table(tag="cal_table", header_row=True, borders_innerH=True,
                        borders_outerH=True, borders_innerV=True, borders_outerV=True,
                        row_background=True, height=160):
            dpg.add_table_column(label="P (bar)")
            dpg.add_table_column(label="H (mm)")
            dpg.add_table_column(label="Vol (L)")

        refresh_table()
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", width=120, callback=save_cal)
            dpg.add_button(label="Cancel", width=120, callback=lambda: dpg.delete_item("dlg_cal"))

def _open_program_dlg():
    if dpg.does_item_exist("dlg_prog"):
        dpg.delete_item("dlg_prog")

    s = app.app_settings
    with dpg.window(label="Program Settings", modal=True, tag="dlg_prog",
                     width=400, height=440, no_resize=True, pos=[410, 180]):
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
        dpg.add_combo(["None", "SMA-5", "SMA-20", "EMA-Fast"],
                       default_value=s.get("cwl_smooth", "SMA-5"), tag="dlg_p_smth", width=-1)
        dpg.add_text("UI Refresh (ms):")
        dpg.add_combo(["20", "50", "100"],
                       default_value=str(s.get("ui_refresh_ms", 50)), tag="dlg_p_ui_ref", width=-1)
        dpg.add_text("Chart Refresh (ms):")
        dpg.add_combo(["30", "50", "100", "200"],
                       default_value=str(s.get("chart_refresh_ms", 100)), tag="dlg_p_ch_ref", width=-1)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", width=120, callback=_save_prog)
            dpg.add_button(label="Cancel", width=120, callback=lambda: dpg.delete_item("dlg_prog"))

def _save_prog():
    s = app.app_settings
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

# ── File I/O ────────────────────────────────────────────────────────
def _load_profile_cb(sender, app_data):
    fp = app_data.get("file_path_name", "")
    if fp:
        try:
            with open(fp) as f:
                app.profile.from_dict(json.load(f))
            _refresh_limits()
        except Exception:
            pass

def _save_profile_cb(sender, app_data):
    fp = app_data.get("file_path_name", "")
    if fp:
        try:
            with open(fp, "w") as f:
                json.dump(app.profile.to_dict(), f, indent=4)
        except Exception:
            pass

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

# ── Main loop callbacks ────────────────────────────────────────────
def update_ui():
    with app.data_lock:
        h = app.current_height
        v = app.current_volume
        p = app.current_pressure
        h_history = list(app.h_buf)[-150:] if app.h_buf else [h]

    dpg.set_value("lbl_h", f"{h:.1f} mm")
    dpg.set_value("lbl_v", f"{v:.2f} L")
    dpg.set_value("lbl_p", f"{p:.4f} bar")

    conn_text = f"Connected ({app.conn_params['port']})" if app.is_connected else "Disconnected"
    dpg.set_value("lbl_conn", conn_text)

    # CWL logic
    if app.cwl_state == "ARMED":
        if app.app_settings.get("cwl_mode") == "Automatic":
            alg = app.app_settings.get("cwl_smooth", "None")
            sm_h = smooth(h_history, alg)
            val = sm_h[-1] if sm_h else h
            if val > app.cwl_peak:
                app.cwl_peak = val
            thresh = app.app_settings.get("cwl_drop_thresh", 1.5)
            if app.cwl_peak - val >= thresh:
                app.cwl_state = "WAITING"
                app.cwl_timer = time.time()
            dpg.set_value("lbl_cwl_st", f"CWL: ARMED (drop >= {thresh}mm)")
            dpg.bind_item_theme("lbl_cwl_st", "theme_blue")
        else:
            dpg.set_value("lbl_cwl_st", "CWL: ARMED (Manual)")
            dpg.bind_item_theme("lbl_cwl_st", "theme_blue")
    elif app.cwl_state == "WAITING":
        rem = 2.0 - (time.time() - app.cwl_timer)
        if rem <= 0:
            app.profile.cwl = app.get_avg_height()
            app.cwl_state = "DONE"
            _refresh_limits()
            dpg.set_value("lbl_cwl_st", "CWL: CAPTURED")
            dpg.bind_item_theme("lbl_cwl_st", "theme_green")
        else:
            dpg.set_value("lbl_cwl_st", f"CWL: TIMER {rem:.1f}s")
            dpg.bind_item_theme("lbl_cwl_st", "theme_orange")

def update_chart():
    with app.data_lock:
        t_data = list(app.t_buf)
        plot_idx = dpg.get_value("combo_plot")
        if plot_idx == "Volume (L)":
            raw_y = list(app.v_buf)
        elif plot_idx == "Flow Rate (L/s)":
            raw_y = list(app.f_buf)
        else:
            raw_y = list(app.h_buf)

    if not t_data or not raw_y:
        return

    alg = dpg.get_value("combo_smth")
    y_s = smooth(raw_y, alg)
    app.last_t = t_data
    app.last_y = y_s

    # Window
    ws = dpg.get_value("combo_win")
    secs = {"10s": 10, "30s": 30, "60s": 60, "5min": 300}.get(ws, None)
    ct = t_data[-1]

    if secs:
        t_min = max(0, ct - secs)
        # Filter to window
        pairs = [(t, y) for t, y in zip(t_data, y_s) if t >= t_min]
        if pairs:
            t_plot, y_plot = zip(*pairs)
        else:
            t_plot, y_plot = t_data, y_s
    else:
        t_plot, y_plot = t_data, y_s

    t_plot = list(t_plot)
    y_plot = list(y_plot)

    dpg.set_value("line_main", [t_plot, y_plot])

    # Axis fit
    if t_plot:
        x_min = t_plot[0]
        x_max = t_plot[-1]
        if x_max - x_min < 1:
            x_max = x_min + 1
        dpg.set_axis_limits("x_axis", x_min, x_max)

        y_min_v = min(y_plot)
        y_max_v = max(y_plot)
        margin = max((y_max_v - y_min_v) * 0.1, 0.5)
        dpg.set_axis_limits("y_axis", y_min_v - margin, y_max_v + margin)

    # Limit lines (height mode only)
    plot_mode = dpg.get_value("combo_plot")
    if "Height" in plot_mode and t_plot:
        x0, x1 = t_plot[0], t_plot[-1]
        for tag, val in [("line_mwl", app.profile.mwl), ("line_menis", app.profile.meniscus),
                         ("line_wd", app.profile.water_discharge), ("line_cwl", app.profile.cwl)]:
            if val > 0:
                dpg.set_value(tag, [[x0, x1], [val, val]])
                dpg.configure_item(tag, show=True)
            else:
                dpg.configure_item(tag, show=False)
    else:
        for tag in ["line_mwl", "line_menis", "line_wd", "line_cwl"]:
            dpg.configure_item(tag, show=False)

def frame_callback():
    now = time.time()
    ui_interval = app.app_settings.get("ui_refresh_ms", 50) / 1000.0
    chart_interval = app.app_settings.get("chart_refresh_ms", 100) / 1000.0

    if now - app._last_ui_tick >= ui_interval:
        update_ui()
        app._last_ui_tick = now
    if now - app._last_chart_tick >= chart_interval:
        update_chart()
        app._last_chart_tick = now

# ── Build GUI ───────────────────────────────────────────────────────
dpg.create_context()
dpg.create_viewport(title="EN 14055 Cistern Analytics - ifm PI1789", width=1200, height=850)

# Themes
with dpg.theme(tag="theme_green"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_GREEN)

with dpg.theme(tag="theme_red"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_RED)

with dpg.theme(tag="theme_gray"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_GRAY)

with dpg.theme(tag="theme_blue"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_ACCENT)

with dpg.theme(tag="theme_orange"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_ORANGE)

with dpg.theme(tag="theme_accent_text"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_ACCENT)

with dpg.theme(tag="theme_green_text"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_GREEN)

with dpg.theme(tag="theme_global"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, COL_BG)
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (42, 42, 61))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (55, 55, 77))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (65, 65, 90))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 85))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (75, 75, 110))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 90, 130))
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_WHITE)
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (35, 35, 55))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (45, 45, 70))
        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (35, 35, 55))
        dpg.add_theme_color(dpg.mvThemeCol_Header, (55, 55, 85))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (70, 70, 100))
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
    with dpg.theme_component(dpg.mvPlot):
        dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (25, 25, 40))
        dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, (60, 60, 80))

dpg.bind_theme("theme_global")

# Main window
with dpg.window(tag="main_win"):
    # Menu bar
    with dpg.menu_bar():
        with dpg.menu(label="File"):
            dpg.add_menu_item(label="Load Profile...", callback=_load_profile)
            dpg.add_menu_item(label="Save Profile As...", callback=_save_profile)
            dpg.add_separator()
            dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
        with dpg.menu(label="Settings"):
            dpg.add_menu_item(label="Hardware Connection...", callback=_open_connection_dlg)
            dpg.add_menu_item(label="Edit Calibration Profile...", callback=_open_calibration_dlg)
            dpg.add_menu_item(label="Program Settings...", callback=_open_program_dlg)

    # Top bar
    with dpg.group(horizontal=True):
        dpg.add_text("Active Profile: Untitled Profile", tag="lbl_profile")
        dpg.add_spacer(width=-1)
        dpg.add_text("Disconnected", tag="lbl_conn", color=COL_GRAY)
        dpg.add_button(label="Connect Sensor", tag="btn_connect", callback=_toggle_connect, width=160)

    dpg.add_separator()

    # Main area
    with dpg.group(horizontal=True):
        # Left panel
        with dpg.child_window(width=310, border=True):
            # Real-Time Data
            dpg.add_text("Real-Time Data", color=COL_WHITE)
            dpg.add_spacer(height=4)
            dpg.add_text("0.0 mm", tag="lbl_h", color=COL_ACCENT)
            dpg.add_text("0.00 L", tag="lbl_v", color=COL_GREEN)
            dpg.add_text("0.0000 bar", tag="lbl_p", color=COL_GRAY)

            dpg.add_separator()

            # EN14055 Limits
            dpg.add_text("EN 14055 Limits", color=COL_WHITE)
            dpg.add_spacer(height=4)
            dpg.add_button(label=f"Set MWL (Avg {app.app_settings.get('avg_window', 0.5)}s)",
                           tag="btn_mwl", callback=_set_mwl, width=-1)
            dpg.add_button(label="Start CWL 2s Timer", tag="btn_manual_cwl",
                           callback=_manual_cwl, width=-1, show=False)
            dpg.add_button(label=f"Set Meniscus (Avg {app.app_settings.get('avg_window', 0.5)}s)",
                           tag="btn_menis", callback=_set_meniscus, width=-1)

            dpg.add_separator()

            dpg.add_text("MWL: 0.0 mm", tag="lbl_mwl")
            dpg.add_text("Meniscus: 0.0 mm", tag="lbl_menis")
            dpg.add_text("CWL: 0.0 mm", tag="lbl_cwl")
            dpg.add_text("Water Disch.: 0.0 mm", tag="lbl_wd", color=COL_GRAY)

            dpg.add_spacer(height=8)
            dpg.add_text("MARGIN: WAITING", tag="lbl_margin", color=COL_GRAY)
            dpg.add_text("CWL Trigger: IDLE", tag="lbl_cwl_st", color=COL_GRAY)

            dpg.add_spacer(height=16)
            dpg.add_button(label="Start Data Log (CSV)", tag="btn_log",
                           callback=_toggle_log, width=-1)

        # Right panel - chart
        with dpg.child_window(border=True):
            # Toolbar
            with dpg.group(horizontal=True):
                dpg.add_text("Axis:")
                dpg.add_combo(["Height (mm)", "Volume (L)", "Flow Rate (L/s)"],
                              default_value="Height (mm)", tag="combo_plot", width=130)
                dpg.add_text("Window:")
                dpg.add_combo(["10s", "30s", "60s", "5min", "All"],
                              default_value="30s", tag="combo_win", width=70)
                dpg.add_text("Smooth:")
                dpg.add_combo(["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow"],
                              default_value="None", tag="combo_smth", width=100)
                dpg.add_text("Delta: Click 2 pts", tag="lbl_delta", color=COL_ACCENT)
                dpg.add_button(label="Clear Delta", callback=_clear_delta)

            # Plot
            with dpg.plot(tag="main_plot", height=-1, width=-1, anti_aliased=True):
                dpg.add_plot_legend()
                dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag="x_axis")
                with dpg.plot_axis(dpg.mvYAxis, label="Height (mm)", tag="y_axis"):
                    dpg.add_line_series([], [], tag="line_main", label="Sensor")
                    dpg.add_line_series([], [], tag="line_mwl", label="MWL", show=False)
                    dpg.add_line_series([], [], tag="line_menis", label="Meniscus", show=False)
                    dpg.add_line_series([], [], tag="line_wd", label="Water Disch.", show=False)
                    dpg.add_line_series([], [], tag="line_cwl", label="CWL", show=False)

# Style the chart lines
with dpg.theme(tag="theme_line_main"):
    with dpg.theme_component(dpg.mvLineSeries):
        dpg.add_theme_color(dpg.mvPlotCol_Line, COL_ACCENT)
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0)
dpg.bind_item_theme("line_main", "theme_line_main")

with dpg.theme(tag="theme_line_mwl"):
    with dpg.theme_component(dpg.mvLineSeries):
        dpg.add_theme_color(dpg.mvPlotCol_Line, COL_ACCENT)
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_mwl", "theme_line_mwl")

with dpg.theme(tag="theme_line_menis"):
    with dpg.theme_component(dpg.mvLineSeries):
        dpg.add_theme_color(dpg.mvPlotCol_Line, (180, 130, 255))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_menis", "theme_line_menis")

with dpg.theme(tag="theme_line_wd"):
    with dpg.theme_component(dpg.mvLineSeries):
        dpg.add_theme_color(dpg.mvPlotCol_Line, COL_RED)
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_wd", "theme_line_wd")

with dpg.theme(tag="theme_line_cwl"):
    with dpg.theme_component(dpg.mvLineSeries):
        dpg.add_theme_color(dpg.mvPlotCol_Line, COL_ORANGE)
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_cwl", "theme_line_cwl")

dpg.set_primary_window("main_win", True)

with dpg.handler_registry():
    dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=_plot_clicked)

dpg.setup_dearpygui()
dpg.show_viewport()

try:
    # Auto-connect on startup
    app.connect()
    if app.is_connected:
        dpg.set_item_label("btn_connect", "Disconnect")

    while dpg.is_dearpygui_running():
        frame_callback()
        dpg.render_dearpygui_frame()

    app.cleanup()
    dpg.destroy_context()
except Exception as e:
    import traceback
    traceback.print_exc()
    input("Натиснете Enter за изход...")

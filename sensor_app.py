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
import bisect
import logging
import re
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Named constants ──────────────────────────────────────────────────
# IFM PI1789 protocol
_PRESSURE_SCALE_BAR_PER_LSB: float = 0.0001
_PRESSURE_MIN_BAR: float = 0.0
_PRESSURE_MAX_BAR: float = 10.0        # PI1789 rated range (0–10 bar)
_PACKET_HEADER_SIZE: int = 12          # 2 SOF + 8 length hex + 2 reserved
_RX_BUF_HIGH_WATERMARK: int = 4000
_RX_BUF_LOW_WATERMARK: int = 2000

# EN 14055:2015
EN14055_CWL_WAIT_S: float = 2.0
EN14055_SAFETY_MARGIN_MIN_MM: float = 20.0
EN14055_MWL_MAX_ABOVE_OF_MM: float = 20.0
EN14055_CWL_MAX_ABOVE_OF_MM: float = 10.0
EN14055_MENISCUS_MAX_ABOVE_OF_MM: float = 5.0
EN14055_FULL_FLUSH_MAX_L: float = 6.0
EN14055_PART_FLUSH_MAX_L: float = 4.0
EN14055_REQUIRED_FLUSH_COUNT: int = 3
EN14055_CWL_DROP_THRESH_MM: float = 1.5   # default drop to detect supply cutoff
EN14055_FLUSH_MIN_DURATION_S: float = 3.0 # guard before auto-stop is considered
EN14055_FLUSH_RISE_THRESH_MM: float = 5.0 # rise above min to detect refill start
EN14055_FLUSH_RISE_CONFIRM_S: float = 2.0 # seconds rising before auto-stop fires
EN14055_CWL_HISTORY_SAMPLES: int = 150    # sliding window depth for CWL/RWL detector
EN14055_MAX_CAL_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MB import guard

# ── Runtime base directory (works both as .py and as single-file exe) ──
# auto-py-to-exe / PyInstaller sets sys.frozen; sys.executable is the .exe path.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent   # folder that contains the .exe
else:
    BASE_DIR = Path(__file__).parent         # folder that contains sensor_app.py

CONFIG_DIR = BASE_DIR / "config"
EXPORT_DIR = BASE_DIR / "exports"

for _d in (CONFIG_DIR, EXPORT_DIR):
    _d.mkdir(exist_ok=True)

# ── Font discovery ───────────────────────────────────────────────────
_SCRIPT_DIR = BASE_DIR

def _find_font(name_hints: list[str]) -> str | None:
    """
    Search for a TTF/OTF font by name hints.
    Checks: script dir → script/fonts/ → Windows Fonts → user Fonts.
    Returns the first found path, or None.
    """
    win_fonts = Path(r"C:\Windows\Fonts")
    user_fonts = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    search_dirs = [_SCRIPT_DIR, _SCRIPT_DIR / "fonts", win_fonts, user_fonts]

    for hint in name_hints:
        for d in search_dirs:
            for ext in (".ttf", ".otf", ".TTF", ".OTF"):
                p = d / (hint + ext)
                if p.exists():
                    return str(p)
    return None

# Priority: Samsung Sans → Segoe UI Variable → Segoe UI → Inter → Arial
_FONT_PATH_REGULAR = (
    _find_font(["SamsungSans-Regular", "SamsungSans_v2.0", "SamsungSansV2", "SamsungSans"]) or
    _find_font(["SegoeUI-VF", "SegoeUIVariable-Text", "SegoeUIVariable"]) or
    _find_font(["segoeui"]) or
    _find_font(["Inter-Regular", "Inter_Regular", "Inter"]) or
    _find_font(["arial"])
)
_FONT_PATH_BOLD = (
    _find_font(["SamsungSans-Bold", "SamsungSansBold"]) or
    _find_font(["segoeuib"]) or
    _find_font(["Inter-Bold", "Inter_Bold"]) or
    _find_font(["arialbd"]) or
    _FONT_PATH_REGULAR  # fall back to regular if no bold variant
)

logging.info(f"UI font: {_FONT_PATH_REGULAR}")
logging.info(f"Bold font: {_FONT_PATH_BOLD}")

# ── Settings persistence ────────────────────────────────────────────
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONN = {"port": "COM8", "baud": 115200, "io_port": "Port 1", "poll_ms": 50}
DEFAULT_LINE_COLORS = {
    "sensor": [137, 180, 250, 255],   # blue
    "mwl":    [100, 200, 255, 255],   # light blue
    "menis":  [180, 130, 255, 255],   # purple
    "wd":     [243, 139, 168, 255],   # pink/red
    "cwl":    [250, 179,  90, 255],   # orange
}
DEFAULT_APP = {
    "avg_window": 0.5, "cwl_mode": "Automatic", "cwl_drop_thresh": 1.5,
    "cwl_smooth": "SMA-5", "ui_refresh_ms": 50, "chart_refresh_ms": 100,
    "pressure_unit": "bar",
    "ui_theme": "Dark",
    "line_colors": {k: list(v) for k, v in DEFAULT_LINE_COLORS.items()},
}

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                d = json.load(f)
                return d.get("conn", dict(DEFAULT_CONN)), d.get("app", dict(DEFAULT_APP))
    except Exception as e:
        logging.warning(f"Could not load settings, using defaults: {e}")
    return dict(DEFAULT_CONN), dict(DEFAULT_APP)

def save_settings(conn, app_s):
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"conn": conn, "app": app_s}, f, indent=2)
        shutil.move(str(tmp), str(SETTINGS_FILE))
    except Exception as e:
        logging.warning(f"Could not save settings: {e}")

# ── Profile ─────────────────────────────────────────────────────────
class CisternProfile:
    def __init__(self):
        self.name = "Untitled Profile"
        self.points = []
        self.mwl = 0.0           # NWL — Nominal Water Level (normal fill, BELOW overflow)
        self.mwl_fault = 0.0     # MWL — Max Water Level during fault (ABOVE overflow, ≤20mm)
        self.meniscus = 0.0      # Meniscus delta above OF (≤5mm above overflow)
        self.cwl = 0.0           # CWL — Critical WL 2s after fault cutoff (ABOVE overflow, ≤10mm)
        self.overflow = 0.0      # OF — overflow level (absolute height)
        self.water_discharge = 0.0
        self.residual_wl = 0.0   # RWL — Residual Water Level (min after flush)

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ["name", "points", "mwl", "mwl_fault", "meniscus", "cwl", "overflow",
                 "water_discharge", "residual_wl"]}

    def from_dict(self, d):
        for k in ["name", "points", "mwl", "mwl_fault", "meniscus", "cwl", "overflow",
                  "water_discharge", "residual_wl"]:
            if k in d:
                setattr(self, k, d[k])

    def clone(self):
        p = CisternProfile()
        p.from_dict(self.to_dict())
        p.points = [dict(pt) for pt in self.points]
        return p

# ── Pressure unit conversion ────────────────────────────────────────
PRESSURE_UNITS = {"bar": (1.0, "bar"), "mbar": (1000.0, "mbar"), "kPa": (100.0, "kPa")}

def p_convert(bar_value, unit=None):
    """Convert bar to the selected display unit."""
    if unit is None:
        unit = app.app_settings.get("pressure_unit", "bar")
    factor, _ = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    return bar_value * factor

def p_format(bar_value, decimals=None, unit=None):
    """Format a pressure value in the selected display unit."""
    if unit is None:
        unit = app.app_settings.get("pressure_unit", "bar")
    factor, label = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    val = bar_value * factor
    if decimals is None:
        decimals = 4 if unit == "bar" else 2 if unit == "kPa" else 1
    return f"{val:.{decimals}f} {label}"

def p_parse_to_bar(text, unit=None):
    """Parse a user-entered pressure string back to bar."""
    if unit is None:
        unit = app.app_settings.get("pressure_unit", "bar")
    factor, _ = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    return float(text.replace(",", ".")) / factor

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

# ── Interpolation (pre-sorted cache + bisect) ──────────────────────
_sorted_pts_cache = []
_sorted_pts_pressures = []
_interp_cache_version = 0  # bumped on every rebuild
_interp_expected_version = -1  # checked in interp_hv
_interp_lock = threading.Lock()

def _rebuild_interp_cache(pts):
    """Pre-sort calibration points once. Call on profile load/save/edit."""
    global _sorted_pts_cache, _sorted_pts_pressures, _interp_cache_version
    with _interp_lock:
        _sorted_pts_cache = sorted(pts, key=lambda x: x["p"])
        _sorted_pts_pressures = [pt["p"] for pt in _sorted_pts_cache]
        _interp_cache_version += 1

def interp_hv(p_bar, pts):
    global _interp_expected_version
    if not pts:
        return 0.0, 0.0
    # BUG-06 fix: capture reference to the cache lists *inside* the lock so
    # we never dereference a name that has been rebound by _rebuild_interp_cache
    # on another thread (safe in CPython today; required for free-threaded Python).
    with _interp_lock:
        if _interp_expected_version != _interp_cache_version:
            _sorted_pts_cache[:] = sorted(pts, key=lambda x: x["p"])
            _sorted_pts_pressures[:] = [pt["p"] for pt in _sorted_pts_cache]
            _interp_expected_version = _interp_cache_version
        sp = _sorted_pts_cache          # captured inside lock
        pressures = _sorted_pts_pressures
    if len(sp) == 1:
        return sp[0]["h"], sp[0]["v"]
    if p_bar <= sp[0]["p"]:
        d = sp[1]["p"] - sp[0]["p"]
        r = (p_bar - sp[0]["p"]) / d if d else 0
        return sp[0]["h"] + r * (sp[1]["h"] - sp[0]["h"]), sp[0]["v"] + r * (sp[1]["v"] - sp[0]["v"])
    if p_bar >= sp[-1]["p"]:
        d = sp[-1]["p"] - sp[-2]["p"]
        r = (p_bar - sp[-2]["p"]) / d if d else 0
        return sp[-2]["h"] + r * (sp[-1]["h"] - sp[-2]["h"]), sp[-2]["v"] + r * (sp[-1]["v"] - sp[-2]["v"])
    i = bisect.bisect_right(pressures, p_bar) - 1
    i = max(0, min(i, len(sp) - 2))
    d = sp[i+1]["p"] - sp[i]["p"]
    r = (p_bar - sp[i]["p"]) / d if d else 0
    return sp[i]["h"] + r * (sp[i+1]["h"] - sp[i]["h"]), sp[i]["v"] + r * (sp[i+1]["v"] - sp[i]["v"])

# ── IFM Protocol ────────────────────────────────────────────────────
# IFM PI1789 PDIN layout: bytes 0-3 = pressure, bytes 4-7 = temperature
_PDIN_TEMP_OFFSET = 8    # hex-string start index of temperature word
_PDIN_TEMP_END    = 16   # hex-string end index  (4 bytes = 8 hex chars)
_PDIN_TEMP_SCALE  = 0.1  # °C per LSB
_TEMP_PLACEHOLDER = "-- °C"
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
        _default = CONFIG_DIR / "default_profile.json"
        if _default.exists():
            try:
                with open(_default, encoding="utf-8") as _f:
                    self.profile.from_dict(json.load(_f))
            except Exception as e:
                logging.warning(f"Could not load default profile, starting blank: {e}")
        self.serial_conn = None
        self.is_connected = False
        self.is_logging = False
        self.csv_file = None
        self.csv_writer = None
        self._csv_row_count = 0
        self._csv_lock = threading.Lock()

        self.last_error = ""
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
        self.current_flow = 0.0
        self.current_temperature = None   # °C from pdin bytes 4-7; None if sensor doesn't report it

        self.cwl_state = "IDLE"       # RWL detection state
        self.cwl_peak = 0.0
        self.cwl_timer = 0.0

        self.cwl_auto_state = "IDLE"  # CWL auto-detection state (EN14055 §5.3.4)
        self.cwl_auto_peak  = 0.0     # peak level while supply running at overflow
        self.cwl_auto_timer = 0.0     # wall-clock time of supply-cutoff moment

        # EN 14055 flush volume measurement
        # EN 14055 flush — all fields protected by _flush_lock
        self._flush_lock = threading.Lock()
        self.flush_measuring = False
        self.flush_start_vol = 0.0
        self.flush_start_h = 0.0
        self.flush_start_time = 0.0
        self.flush_results = []  # list of {type, vol, time, en14055_rate, temp_c}
        # History for EN14055 effective flow rate (ignore first 1L, last 2L)
        self.flush_vol_history = []  # list of (timestamp, volume, height)
        # Auto-stop state: track minimum height during flush
        self.flush_min_h = float("inf")
        self.flush_rising = False
        self.flush_rising_timer = 0.0

        # UI theme tracking — keyed by DPG item tag
        self._item_themes: dict[str, str] = {}

        self.click_points = []
        self.last_t = []
        self.last_y = []
        self.chart_paused = False
        self.manual_mwl_cwl_pending = False  # waiting for user chart-click to set MWL/CWL
        self._last_ui_tick = 0
        self._last_chart_tick = 0

    # ── Sensor thread ───────────────────────────────────────────────
    def read_thread(self):
        rx_buf = bytearray()
        try:
            port_idx = int(self.conn_params["io_port"].replace("Port ", ""))
        except ValueError:
            port_idx = 1
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
                                    # BUG-03 fix: unsigned decode (PI1789 uses unsigned 32-bit)
                                    raw = struct.unpack(">I", bytes.fromhex(hx[:8]))[0]
                                    p_bar = raw * _PRESSURE_SCALE_BAR_PER_LSB
                                    if not (_PRESSURE_MIN_BAR <= p_bar <= _PRESSURE_MAX_BAR):
                                        logging.debug(f"Pressure out of range: {p_bar:.4f} bar — sample discarded")
                                        rx_buf = rx_buf[idx+pkt_len:]
                                        continue
                                    h, v = interp_hv(p_bar, self.profile.points)
                                    # Temperature from bytes 4-7 of pdin (PI1789: 0.1°C/LSB)
                                    temp_c = None
                                    if len(hx) >= _PDIN_TEMP_END:
                                        raw_t = struct.unpack(">i", bytes.fromhex(hx[_PDIN_TEMP_OFFSET:_PDIN_TEMP_END]))[0]
                                        temp_c = raw_t * _PDIN_TEMP_SCALE
                                    with self.data_lock:
                                        t = time.time() - self.start_time
                                        f_rate = 0.0
                                        if len(self.t_buf) > 5:
                                            dt = t - self.t_buf[-5]
                                            if dt > 0:
                                                # Positive = outflow (volume decreasing).
                                                # Negate to match the intuitive convention
                                                # (positive = inflow / refill).
                                                f_rate = (self.v_buf[-5] - v) / dt
                                        self.current_pressure = p_bar
                                        self.current_height = h
                                        self.current_volume = v
                                        self.current_flow = f_rate
                                        if temp_c is not None:
                                            self.current_temperature = temp_c
                                        self.t_buf.append(t)
                                        self.h_buf.append(h)
                                        self.v_buf.append(v)
                                        self.f_buf.append(f_rate)

                                    # Track flush history for EN14055 flow rate calc
                                    # BUG-01 fix: protect flush state with _flush_lock
                                    if self.flush_measuring:
                                        with self._flush_lock:
                                            self.flush_vol_history.append(
                                                (time.time(), v, h))
                                            if h < self.flush_min_h:
                                                self.flush_min_h = h

                                    with self._csv_lock:
                                        if self.is_logging and self.csv_writer:
                                            t_str = f"{temp_c:.1f}" if temp_c is not None else ""
                                            self.csv_writer.writerow([
                                                datetime.now().isoformat(),
                                                p_format(p_bar).split()[0],
                                                f"{h:.1f}", f"{v:.2f}", f"{f_rate:.3f}", t_str
                                            ])
                                            # Flush CSV every 50 rows to prevent data loss on crash
                                            self._csv_row_count += 1
                                            if self.csv_file and self._csv_row_count % 50 == 0:
                                                self.csv_file.flush()
                        except (json.JSONDecodeError, struct.error, ValueError) as e:
                            logging.debug(f"Parse error: {e}")

                    # BUG-04 fix: search for sync marker before trimming so we
                    # never retain a partial packet at the start of the buffer.
                    if len(rx_buf) > _RX_BUF_HIGH_WATERMARK:
                        marker = b'\x01\x0110'
                        cut = rx_buf.find(marker, len(rx_buf) - _RX_BUF_LOW_WATERMARK)
                        if cut != -1:
                            rx_buf = rx_buf[cut:]
                        else:
                            # No marker in retained region — drop everything and resync
                            logging.debug("RX buffer overflow: no sync marker found, resetting buffer")
                            rx_buf = bytearray()
                except serial.SerialException as e:
                    logging.error(f"Serial read error: {e}")
                    break
                except OSError as e:
                    logging.error(f"Port I/O error: {e}")
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
        except serial.SerialException as e:
            self.is_connected = False
            self.last_error = f"Connection failed: {e}"
            logging.error(self.last_error)
        except OSError as e:
            self.is_connected = False
            self.last_error = f"Port error: {e}"
            logging.error(self.last_error)

    def disconnect(self):
        if not self.is_connected:
            return
        self.stop_event.set()
        if self.read_thread_obj:
            # BUG-05 fix: derive timeout from configured poll_ms so we always
            # wait long enough for the thread to wake from its sleep.
            join_timeout = max(3.0, self.conn_params.get("poll_ms", 1000) / 1000.0 + 1.0)
            self.read_thread_obj.join(timeout=join_timeout)
            if self.read_thread_obj.is_alive():
                logging.warning("Sensor thread did not exit within join timeout")
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception as e:
                logging.warning(f"Error closing serial port: {e}")
        self.is_connected = False
        self.serial_conn = None

    # ── Helpers ─────────────────────────────────────────────────────
    def get_avg_height(self):
        window = self.app_settings.get("avg_window", 0.5)
        with self.data_lock:
            if not self.h_buf:
                return self.current_height
            now = self.t_buf[-1]
            # Traverse from newest to oldest — stop as soon as we leave the window
            vals = []
            for t, h in zip(reversed(self.t_buf), reversed(self.h_buf)):
                if now - t > window:
                    break
                vals.append(h)
            return sum(vals) / len(vals) if vals else self.current_height

    def recalc_from_pressure(self):
        """Re-interpolate h/v from current pressure after calibration change."""
        if self.profile.points:
            h, v = interp_hv(self.current_pressure, self.profile.points)
            with self.data_lock:
                self.current_height = h
                self.current_volume = v

    def tick_rwl(self, h: float, h_history: list) -> bool:
        """Detect Residual Water Level — the minimum height after a flush.
        EN 14055: RWL is what remains in the cistern after a complete flush.
        Armed when MWL is captured. Detects a significant drop then waits 2 s
        for the level to stabilise before storing the minimum as residual_wl.
        Returns True when detection is complete (caller should refresh limits)."""
        if self.cwl_state == "ARMED":
            if self.app_settings.get("cwl_mode") == "Automatic":
                alg = self.app_settings.get("cwl_smooth", "None")
                sm_h = smooth(h_history, alg)
                val = sm_h[-1] if sm_h else h
                if val > self.cwl_peak:
                    self.cwl_peak = val
                thresh = self.app_settings.get("cwl_drop_thresh", 1.5)
                if self.cwl_peak - val >= thresh:
                    self.cwl_state = "WAITING"
                    self.cwl_timer = time.time()
        elif self.cwl_state == "WAITING":
            if time.time() - self.cwl_timer >= 2.0:
                self.profile.residual_wl = self.get_avg_height()  # RWL = flush minimum
                self.cwl_state = "DONE"
                return True
        return False

    def tick_cwl_auto(self, h: float,
                      h_history: list, t_history: list) -> bool:
        """Auto-detect CWL per EN 14055 §5.3.4.

        EN 14055 height order (top → bottom):
          MWL (≤+20mm above OF) — fault level, water stable here during test
          CWL (≤+10mm above OF) — 2s after supply cut-off
          Meniscus (≤+5mm above OF) — surface tension residual
          ── Overflow level (OF) ──
          NWL  — set by float/inlet valve; safety margin c = OF−NWL ≥ 20mm
          RWL  — residual after flush
          Seat — seals/components minimum; V=0L in calibration

        While ARMED, track the stable peak = MWL (water held above OF by
        continuous fault supply).  When the smoothed level drops ≥ 1.5 mm
        from peak (supply cut off):
          - Scan back through h_history + t_history to find the last sample
            still at/above (peak − 0.5 mm) — the true supply-cutoff moment.
          - Start the 2-second EN14055 window from that exact timestamp.
        2 s after cutoff → capture p.cwl (should be ≤ 10 mm above OF).
        Returns True when CWL is captured (caller should refresh limits).
        """
        alg = self.app_settings.get("cwl_smooth", "SMA-5")

        if self.cwl_auto_state == "ARMED":
            sm = smooth(h_history, alg)
            val = sm[-1] if sm else h

            # Track peak = MWL (level stable above OF while fault supply runs)
            if val > self.cwl_auto_peak:
                self.cwl_auto_peak = val

            # Detect ≥ 1.5 mm drop from peak (supply cut off)
            if self.cwl_auto_peak - val >= 1.5:
                # Scan back through smoothed history to find the true
                # cutoff moment: last sample at/above (peak − 0.5 mm)
                drop_start_wall = time.time()  # fallback: now
                if h_history and t_history:
                    sm_full = smooth(h_history, alg)
                    n = min(len(sm_full), len(t_history))
                    for i in range(n - 1, -1, -1):
                        if sm_full[i] >= self.cwl_auto_peak - 0.5:
                            # t_history stores elapsed seconds since start_time
                            elapsed_now  = time.time() - self.start_time
                            elapsed_drop = t_history[i]
                            drop_start_wall = time.time() - (elapsed_now - elapsed_drop)
                            break
                self.cwl_auto_timer = drop_start_wall
                self.cwl_auto_state = "WAITING"

        elif self.cwl_auto_state == "WAITING":
            elapsed = time.time() - self.cwl_auto_timer
            remaining = 2.0 - elapsed
            if remaining <= 0:
                self.profile.cwl = self.get_avg_height()
                self.profile.mwl_fault = self.cwl_auto_peak  # peak while armed = MWL
                self.cwl_auto_state = "DONE"
                return True

        return False

    def cleanup(self):
        save_settings(self.conn_params, self.app_settings)
        self.disconnect()  # joins sensor thread before we touch the file
        with self._csv_lock:
            if self.csv_file:
                try:
                    self.csv_file.close()
                except OSError as e:
                    logging.warning(f"CSV close on exit: {e}")
                finally:
                    self.csv_file = None
                    self.csv_writer = None

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

# Light-mode equivalents — saturated, dark enough on white background
_LT_ACCENT = (28,  95, 210)
_LT_GREEN  = (25, 130,  55)
_LT_RED    = (190,  38,  68)
_LT_ORANGE = (175,  88,   0)
_LT_GRAY   = ( 90,  90, 115)

def _toggle_connect():
    if app.is_connected:
        app.disconnect()
        dpg.set_item_label("btn_connect", "Connect Sensor")
        dpg.configure_item("lbl_conn",      default_value="Disconnected")
        dpg.configure_item("lbl_conn_icon", default_value="●", color=COL_GRAY)
        dpg.bind_item_theme("btn_connect", 0)  # reset to default theme
    else:
        app.last_error = ""
        app.connect()
        if app.is_connected:
            dpg.set_item_label("btn_connect", "Disconnect")
            dpg.configure_item("lbl_conn",      default_value=f"{app.conn_params['port']}  {app.conn_params['baud']}bd")
            dpg.configure_item("lbl_conn_icon", default_value="●", color=COL_GREEN)
            dpg.bind_item_theme("btn_connect", "theme_btn_danger")
        elif app.last_error:
            dpg.configure_item("lbl_conn",      default_value=app.last_error)
            dpg.configure_item("lbl_conn_icon", default_value="●", color=COL_RED)
            _bind_status("lbl_conn", "theme_red")

def _set_mwl():
    """Capture NWL — Nominal Water Level = normal fill level when inlet valve closes.
    EN 14055 §3.16/§5.2.6: Safety margin c = OF − NWL ≥ 20 mm.
    Also arms automatic RWL detection for the next flush."""
    val = app.get_avg_height()
    app.profile.mwl = val
    # Arm RWL (Residual WL) detection — will fire after next flush drop
    app.cwl_state = "ARMED"
    app.cwl_peak = val
    _refresh_limits()


def _arm_cwl_auto():
    """Arm automatic CWL detection (EN 14055 §5.3.4).
    Press while water is stable at MWL (fault supply running, water overflowing).
    Detector watches for supply-cutoff (≥1.5 mm smoothed drop from MWL peak),
    locates the exact cutoff moment in history, captures CWL 2 s after that."""
    if app.profile.overflow <= 0:
        _show_toast("⚠ Set Overflow level in Calibration first!")
        return
    app.cwl_auto_state = "ARMED"
    app.cwl_auto_peak  = app.get_avg_height()  # seed peak = current MWL
    app.cwl_auto_timer = 0.0
    _refresh_limits()
    _show_toast("CWL armed — water should be at MWL. Cut supply when ready.")

def _set_cwl():
    """Capture CWL manually — fallback if auto-detection is not used.
    EN 14055 §3.12/§5.2.4b: CWL − OF ≤ 10 mm.
    Press 2 s after cutting supply during fault overflow test."""
    if app.profile.overflow <= 0:
        _show_toast("⚠ Set Overflow level in Calibration first!")
        return
    app.cwl_auto_state = "IDLE"   # cancel auto if running
    app.profile.cwl = app.get_avg_height()
    _refresh_limits()


def _manual_mwl_cwl():
    """Toggle manual MWL/CWL selection mode.

    On first press: pause chart, enter pending state — next chart click selects
    the MWL moment. The app then:
      • averages the 1 s window before the click → mwl_fault
      • reads the smoothed value at click+2 s → cwl
    On second press (cancel): exit pending state, resume chart.
    """
    if app.manual_mwl_cwl_pending:
        # ── Cancel ───────────────────────────────────────────────────
        app.manual_mwl_cwl_pending = False
        dpg.set_item_label("btn_manual_mwlcwl", "Manual MWL/CWL")
        dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_action")
        if app.chart_paused:
            _toggle_pause()
        return

    # ── Arm ──────────────────────────────────────────────────────────
    if app.profile.overflow <= 0:
        _show_toast("⚠ Set Overflow level in Calibration first!")
        return
    if not app.chart_paused:
        _toggle_pause()   # pause so user can click a frozen point
    app.manual_mwl_cwl_pending = True
    dpg.set_item_label("btn_manual_mwlcwl", "Cancel Manual MWL/CWL")
    dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_danger")
    _show_toast("Chart paused — click the MWL moment on the graph")


def _set_meniscus():
    """Capture meniscus — surface tension level during overflow, just above OF.
    EN 14055 §3.15/§5.2.4c: Meniscus − OF ≤ 5 mm.
    Procedure: let cistern overflow until level stabilises, then press."""
    if app.profile.overflow <= 0:
        _show_toast("⚠ Set Overflow level in Calibration first!")
        return
    measured_level = app.get_avg_height()
    app.profile.meniscus = measured_level - app.profile.overflow
    _refresh_limits()

def _manual_rwl():
    """Manually trigger RWL 2-second timer (Manual mode)."""
    if app.cwl_state == "ARMED":
        app.cwl_state = "WAITING"
        app.cwl_timer = time.time()

def _refresh_flush_table():
    """Rebuild the flush results table inside flush_table_area."""
    if not dpg.does_item_exist("flush_table_area"):
        return
    dpg.delete_item("flush_table_area", children_only=True)

    results = app.flush_results
    if not results:
        dpg.add_text("No measurements yet.", parent="flush_table_area", color=COL_GRAY)
        return

    # Scrollable table — only element inside the child_window
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
            en_str = f"{en_rate:.2f}" if en_rate is not None else "—"
            type_col = COL_ACCENT if "Full" in r["type"] else COL_ORANGE
            with dpg.table_row():
                dpg.add_text(str(i + 1))
                dpg.add_text(r["type"][:4], color=type_col)
                dpg.add_text(f"{r['vol']:.2f}L")
                dpg.add_text(f"{r['time']:.1f}s")
                dpg.add_text(f"{total_rate:.2f}")
                dpg.add_text(en_str,
                             color=COL_ACCENT if en_rate is not None else COL_GRAY)
                dpg.add_button(label="X", width=22,
                               user_data=i, callback=_delete_flush_row)

def _delete_flush_row(sender, app_data, user_data):
    idx = user_data
    if 0 <= idx < len(app.flush_results):
        app.flush_results.pop(idx)
        _refresh_flush_table()

def _toggle_flush_measure():
    """Start/stop flush volume measurement (EN 14055 clause 6)."""
    if not app.flush_measuring:
        # BUG-01 fix: initialise flush state under _flush_lock so the sensor
        # thread cannot append to flush_vol_history while we are clearing it.
        with app._flush_lock:
            app.flush_start_vol = app.current_volume
            app.flush_start_h = app.current_height
            app.flush_start_time = time.time()
            app.flush_vol_history = []
            app.flush_min_h = float("inf")
            app.flush_rising = False
            app.flush_rising_timer = 0.0
            app.flush_measuring = True   # set last — sensor thread starts appending
        dpg.set_item_label("btn_flush", "Stop Flush Measurement")
        dpg.bind_item_theme("btn_flush", "theme_btn_danger")
    else:
        # BUG-01 fix: stop the sensor thread from appending, then snapshot history.
        with app._flush_lock:
            app.flush_measuring = False          # sensor thread sees False immediately
            history = list(app.flush_vol_history)  # snapshot
            elapsed = time.time() - app.flush_start_time
            start_vol = app.flush_start_vol
        flush_type = dpg.get_value("combo_flush_type") if dpg.does_item_exist("combo_flush_type") else "Full Flush"
        # EN-05: snapshot temperature at flush end for compliance report
        snap_temp = app.current_temperature

        # Total volume = start minus the minimum volume seen (lowest water level)
        if history:
            min_vol = min(r[1] for r in history)
            delta_vol = abs(start_vol - min_vol)
        else:
            delta_vol = abs(start_vol - app.current_volume)

        # EN 14055 effective flow rate: skip first 1L and last 2L
        en14055_rate = None
        en14055_note = None
        if history and delta_vol > 3.0:
            v_start = start_vol
            v_skip_start = v_start - 1.0   # after 1L lost (volume threshold)
            # end vol = lowest volume seen
            v_end = min(r[1] for r in history)
            v_skip_end = v_end + 2.0        # 2L before absolute end

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
            "temp_c": snap_temp,   # EN-05: temperature at measurement time
        })
        dpg.set_item_label("btn_flush", "Start Flush Measurement")
        dpg.bind_item_theme("btn_flush", "theme_btn_success")
        _refresh_flush_table()

def _clear_flush():
    app.flush_results.clear()
    _refresh_flush_table()

def _check_compliance():
    """Run EN 14055:2015 compliance checks.

    EN 14055 height order (top → bottom):
      MWL   fault level, stable above OF during continuous supply  (≤+20mm above OF)
      CWL   2 s after supply cut-off                               (≤+10mm above OF)
      Meniscus  surface tension residual at overflow lip            (≤+5mm above OF)
      ── Overflow level (OF) ──
      NWL   normal fill line set by float/inlet valve; in calibration
      RWL   residual after a complete flush
      Seat  seals/components minimum; V=0L calibration point

    Key requirements (§5.2.4, §5.2.6, §5.2.7):
      §5.2.6  Safety margin c = OF − NWL  ≥ 20 mm
      §5.2.4a MWL (fault)     − OF        ≤ 20 mm  (MWL above overflow)
      §5.2.4b CWL             − OF        ≤ 10 mm  (CWL above overflow, 2s after cutoff)
      §5.2.4c Meniscus        − OF        ≤  5 mm  (surface tension above overflow)
      §5.2.7  Air gap a = OF − inlet orifice ≥ 20 mm  (ruler measurement)
    """
    p = app.profile
    results = []

    # ── 1. Safety Margin c: OF − NWL ≥ 20 mm (§5.2.6) ───────────────
    # NWL = nominal fill level (= p.mwl, captured during normal fill).
    # Ensures the overflow does not activate during normal filling.
    if p.overflow > 0 and p.mwl > 0:
        sm = p.overflow - p.mwl
        if sm >= 20:
            results.append(f"[PASS] Safety margin c (OF−NWL): {sm:.1f} mm ≥ 20 mm")
        else:
            results.append(f"[FAIL] Safety margin c (OF−NWL): {sm:.1f} mm < 20 mm")
    else:
        results.append("[----] Safety margin c: capture NWL and set Overflow first")

    # ── 2. MWL − OF ≤ 20 mm (§5.2.4a) ──────────────────────────────
    # MWL = highest level during inlet valve malfunction (continuous supply).
    # Must overflow continuously at 0.28 L/s; MWL is where level stabilises.
    if p.overflow > 0 and p.mwl_fault > 0:
        diff = p.mwl_fault - p.overflow
        if diff <= 20:
            results.append(f"[PASS] MWL fault: +{diff:.1f} mm above OF ≤ 20 mm")
        else:
            results.append(f"[FAIL] MWL fault: +{diff:.1f} mm above OF > 20 mm")
    else:
        results.append("[----] MWL fault: run overflow fault test and press 'Set MWL (fault)'")

    # ── 3. CWL − OF ≤ 10 mm (§5.2.4b) ───────────────────────────────
    # CWL = highest level 2 s after supply is cut off during overflow test.
    # CWL should be at or slightly above OF (overflow draining residual).
    if p.overflow > 0 and p.cwl > 0:
        diff = p.cwl - p.overflow   # positive = above OF
        if diff <= 10:
            results.append(f"[PASS] CWL: {diff:+.1f} mm from OF ≤ 10 mm")
        else:
            results.append(f"[FAIL] CWL: +{diff:.1f} mm above OF > 10 mm")
    else:
        results.append("[----] CWL: run fault test, cut supply, wait 2s, press 'Set CWL'")

    # ── 4. Meniscus − OF ≤ 5 mm (§5.2.4c) ───────────────────────────
    # Surface tension holds water just above the overflow lip during overflowing.
    if p.meniscus != 0:
        m = p.meniscus          # = measured_level − OF
        if 0 <= m <= 5:
            results.append(f"[PASS] Meniscus: +{m:.1f} mm above OF ≤ 5 mm")
        elif m > 5:
            results.append(f"[FAIL] Meniscus: +{m:.1f} mm above OF > 5 mm")
        else:
            results.append(f"[WARN] Meniscus: {m:.1f} mm (below OF — check capture)")
    else:
        results.append("[----] Meniscus: let cistern overflow, stabilise, press 'Set Meniscus'")

    # ── 5. Air gap a: OF − inlet orifice ≥ 20 mm (§5.2.7) ───────────
    # Prevents backflow into supply pipe. The lowest point of the inlet valve
    # air orifice must be ≥ 20 mm above overflow level (ruler measurement).
    results.append("[INFO] Air gap a (§5.2.7): measure OF − inlet valve air orifice ≥ 20 mm "
                   "(ruler/tape — cannot be measured by pressure sensor)")

    # ── 6. Residual WL (informational) ────────────────────────────────
    if p.residual_wl > 0:
        results.append(f"[INFO] Residual WL (RWL): {p.residual_wl:.1f} mm after flush")

    # ── 7. Flush volume (§5.2.1, §6.5) ────────────────────────────────
    if app.flush_results:
        full = [r for r in app.flush_results if "Full" in r["type"]]
        part = [r for r in app.flush_results if "Part" in r["type"]]
        if full:
            # EN-02: warn if fewer than the required 3 measurements
            if len(full) < EN14055_REQUIRED_FLUSH_COUNT:
                results.append(f"[WARN] Full flush: only {len(full)}/{EN14055_REQUIRED_FLUSH_COUNT} measurements (§5.2.1 requires 3)")
            avg_full = sum(r["vol"] for r in full) / len(full)
            # EN-01 fix: exceeding the volume limit is a FAIL, not a WARN
            tag = "PASS" if avg_full <= EN14055_FULL_FLUSH_MAX_L else "FAIL"
            results.append(f"[{tag}] Full flush avg: {avg_full:.2f} L (limit {EN14055_FULL_FLUSH_MAX_L} L)")
            en_rates = [r["en14055_rate"] for r in full if r.get("en14055_rate") is not None]
            if en_rates:
                avg_rate = sum(en_rates) / len(en_rates)
                results.append(f"[INFO] Full flush EN14055 flow rate (V2 method): {avg_rate:.3f} L/s")
            # EN-05: report temperature range across flush measurements
            temps = [r["temp_c"] for r in full if r.get("temp_c") is not None]
            if temps:
                results.append(f"[INFO] Water temp during full flushes: {min(temps):.1f}–{max(temps):.1f} °C (EN 14055 §5.1: 15±5 °C)")
        if part:
            if len(part) < EN14055_REQUIRED_FLUSH_COUNT:
                results.append(f"[WARN] Part flush: only {len(part)}/{EN14055_REQUIRED_FLUSH_COUNT} measurements (§5.2.1 requires 3)")
            avg_part = sum(r["vol"] for r in part) / len(part)
            tag = "PASS" if avg_part <= EN14055_PART_FLUSH_MAX_L else "FAIL"
            results.append(f"[{tag}] Part flush avg: {avg_part:.2f} L (limit {EN14055_PART_FLUSH_MAX_L} L)")
    else:
        results.append("[----] Flush volume: no measurements yet")

    # Show in dialog — CQ-09: use a scrollable child window so content never overflows
    if dpg.does_item_exist("dlg_comply"):
        dpg.delete_item("dlg_comply")
    with dpg.window(label="EN 14055 Compliance Check", modal=True, tag="dlg_comply",
                     width=560, height=440, no_resize=True, pos=[320, 180]):
        with dpg.child_window(height=370, border=False):
            for line in results:
                col = (COL_GREEN  if "[PASS]" in line else
                       COL_RED    if "[FAIL]" in line else
                       COL_ORANGE if "[WARN]" in line else COL_GRAY)
                dpg.add_text(line, color=col)
        dpg.add_separator()
        dpg.add_button(label="Close", width=120,
                       callback=lambda: dpg.delete_item("dlg_comply"))

def _refresh_limits():
    """Refresh all EN 14055 limit labels and status indicators.

    EN 14055:2015 height order (top → bottom):
      MWL        fault level, stable above OF       (≤+20mm above OF)
      CWL        2s after supply cut-off            (≤+10mm above OF)
      Meniscus   surface tension at overflow lip    (≤+5mm  above OF)
      ── Overflow level (OF) ──
      NWL        normal fill (float/inlet valve; also in calibration)
      RWL        residual after flush
      Seat       seals minimum; V=0L calibration point

    §5.2.6  Safety margin c = OF − NWL   ≥ 20 mm
    §5.2.4a MWL (fault)     − OF         ≤ 20 mm
    §5.2.4b CWL             − OF         ≤ 10 mm
    §5.2.4c Meniscus        − OF         ≤  5 mm
    §5.2.7  Air gap a = OF − inlet orifice ≥ 20 mm  (ruler measurement)
    """
    p = app.profile
    of = p.overflow

    def _mm(v):
        return f"{v:.1f} mm"

    def _mm_rel(v):
        """Format level with signed distance from OF (+above, −below)."""
        if of > 0 and v > 0:
            d = v - of   # positive = above OF
            return f"{v:.1f} mm  ({d:+.1f} mm OF)"
        return f"{v:.1f} mm"

    # NWL (stored as p.mwl): normal fill level, should be BELOW OF
    dpg.set_value("lbl_mwl", _mm_rel(p.mwl))
    # MWL fault: overflow test max level, should be ABOVE OF by ≤20mm
    if dpg.does_item_exist("lbl_mwl_fault"):
        dpg.set_value("lbl_mwl_fault", _mm_rel(p.mwl_fault) if p.mwl_fault > 0 else "—")
    # Meniscus: delta above OF (+ve = above OF), ≤ 5mm
    dpg.set_value("lbl_menis", f"{p.meniscus:+.1f} mm" if p.meniscus != 0 else "0.0 mm")
    # CWL: 2s-after-cutoff level, should be ABOVE OF by ≤10mm
    dpg.set_value("lbl_cwl", _mm_rel(p.cwl) if p.cwl > 0 else "—")
    dpg.set_value("lbl_wd",       _mm(p.water_discharge))
    dpg.set_value("lbl_overflow", _mm(of))
    dpg.set_value("lbl_profile",  f"Active Profile: {p.name}")
    if dpg.does_item_exist("lbl_residual"):
        dpg.set_value("lbl_residual", _mm(p.residual_wl))

    w = app.app_settings.get("avg_window", 0.5)
    dpg.set_item_label("btn_mwl",   f"Set NWL  (avg {w}s)")
    dpg.set_item_label("btn_menis", f"Set Meniscus (avg {w}s)")

    show_manual = app.app_settings.get("cwl_mode") == "Manual" and app.cwl_state == "ARMED"
    if dpg.does_item_exist("btn_manual_rwl"):
        dpg.configure_item("btn_manual_rwl", show=show_manual)

    # ── Safety Margin c = OF − NWL (§5.2.6) ──────────────────────────
    if dpg.does_item_exist("lbl_safety_margin_static"):
        if of > 0 and p.mwl > 0:
            sm = of - p.mwl
            col = COL_GREEN if sm >= 20 else COL_RED
            dpg.set_value("lbl_safety_margin_static", f"{sm:.1f} mm")
            dpg.configure_item("lbl_safety_margin_static", color=col)
        else:
            dpg.set_value("lbl_safety_margin_static", "—")

    # ── CWL compliance status (§5.2.4b) ─────────────────────────────
    if of > 0 and p.cwl > 0:
        diff = p.cwl - of   # positive = CWL above OF (expected)
        if diff <= 10:
            dpg.set_value("lbl_airgap", f"CWL: {diff:+.1f} mm OF  ✓ (≤10)")
            _bind_status("lbl_airgap", "theme_green")
        else:
            dpg.set_value("lbl_airgap", f"CWL: +{diff:.1f} mm OF  ✗ (>10)")
            _bind_status("lbl_airgap", "theme_red")
    else:
        dpg.set_value("lbl_airgap", "CWL: — (capture during fault test)")
        _bind_status("lbl_airgap", "theme_gray")

def _toggle_log():
    if not app.is_logging:
        EXPORT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)[:80]   # SEC-01: length cap
        fn = str(EXPORT_DIR / f"{safe_name}_{ts}.csv")
        try:
            app.csv_file = open(fn, "w", newline="", encoding="utf-8")   # CQ-04: explicit encoding
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
        # BUG-02 fix: set flag first so the sensor thread stops writing,
        # then acquire the lock to wait for any in-flight write to finish
        # before closing the underlying file.
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

def _toggle_pause():
    app.chart_paused = not app.chart_paused
    if app.chart_paused:
        # Take one final snapshot so the frozen chart shows latest data
        update_chart()
        dpg.set_item_label("btn_pause", "Resume")
        dpg.bind_item_theme("btn_pause", "theme_btn_success")
        # Release axes so user can pan/scroll freely over frozen data
        dpg.set_axis_limits_auto("x_axis")
        dpg.set_axis_limits_auto("y_axis")
        # Show drag lines at current limit values (height mode only)
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

def _snap_to_line(mouse_t):
    """Find nearest point on the smoothed line by time coordinate (binary search)."""
    if not app.last_t or not app.last_y:
        return None, None
    i = bisect.bisect_left(app.last_t, mouse_t)
    # Check i-1 and i for closest
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

    # ── Manual MWL/CWL selection mode ───────────────────────────────
    if app.manual_mwl_cwl_pending:
        t_click = st
        ts = app.last_t
        ys = app.last_y

        # MWL = average of smoothed line in [t_click-1s … t_click]
        mwl_pts = [y for t, y in zip(ts, ys) if t_click - 1.0 <= t <= t_click]
        mwl = sum(mwl_pts) / len(mwl_pts) if mwl_pts else sy

        # CWL = smoothed value at t_click + 2s (nearest point at or after)
        target_t = t_click + 2.0
        cwl = None
        for t, y in zip(ts, ys):
            if t >= target_t:
                cwl = y
                break
        if cwl is None:
            cwl = ys[-1] if ys else sy   # fallback: last available point

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
        # Reset both markers
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
        dpg.configure_item("lbl_delta", default_value=f"Pt1: T={st:.1f}s  Y={sy:.2f} {unit} — Click Pt2...")
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


# ── Dialogs ─────────────────────────────────────────────────────────
def _export_screenshot():
    """Save a PNG of the entire viewport to the exports folder."""
    EXPORT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)[:80]   # SEC-01
    filename = str(EXPORT_DIR / f"{safe_name}_{ts}.png")
    try:
        dpg.output_frame_buffer(filename)
        dpg.set_value("lbl_delta", f"Saved: {Path(filename).name}")
    except Exception as e:
        dpg.set_value("lbl_delta", f"Screenshot failed: {e}")

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
    _cal_edit_idx = [None]  # None = adding new, int = editing existing index

    def refresh_table():
        # Recreate entire table to avoid DPG children_only bugs
        if dpg.does_item_exist("cal_table"):
            dpg.delete_item("cal_table")
        unit = app.app_settings.get("pressure_unit", "bar")
        _, unit_label = PRESSURE_UNITS.get(unit, (1.0, "bar"))
        clone.points.sort(key=lambda x: x["p"])
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
                    # pt['p'] is always stored in bar; convert for display
                    dpg.add_text(p_format(pt["p"], unit=unit).split()[0])
                    dpg.add_text(f"{pt['h']:.1f}")
                    dpg.add_text(f"{pt['v']:.2f}")
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Edit", width=42, user_data=idx,
                                       callback=lambda s, a, u: edit_point(u))
                        dpg.add_button(label="Del", width=42, user_data=idx,
                                       callback=lambda s, a, u: delete_point(u))

    def edit_point(idx):
        """Load point into input fields for editing (show P in display unit)."""
        if 0 <= idx < len(clone.points):
            pt = clone.points[idx]
            unit = app.app_settings.get("pressure_unit", "bar")
            dpg.set_value("cal_p", p_format(pt["p"], unit=unit).split()[0])
            dpg.set_value("cal_h", f"{pt['h']:.1f}")
            dpg.set_value("cal_v", f"{pt['v']:.2f}")
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
            # P field is entered in current display unit → convert to bar for storage
            p = p_parse_to_bar(dpg.get_value("cal_p"))
            h = float(dpg.get_value("cal_h").replace(",", "."))
            v = float(dpg.get_value("cal_v").replace(",", "."))
        except ValueError:
            return
        if p < 0 or h < 0 or v < 0:
            _show_toast("⚠ Values must be non-negative.")
            return
        # BUG-09: reject duplicate pressure values (would cause division-by-zero in interp_hv)
        edit_idx = _cal_edit_idx[0]
        for i, pt in enumerate(clone.points):
            if i != edit_idx and abs(pt["p"] - p) < 1e-9:
                _show_toast("⚠ Duplicate pressure value — adjust and retry.")
                return
        if edit_idx is not None and 0 <= edit_idx < len(clone.points):
            clone.points[edit_idx] = {"p": p, "h": h, "v": v}
            _cal_edit_idx[0] = None
            dpg.set_item_label("btn_cal_add", "Add Point")
        else:
            clone.points.append({"p": p, "h": h, "v": v})
            _cal_edit_idx[0] = None
        dpg.set_value("cal_p", "")
        dpg.set_value("cal_h", "")
        dpg.set_value("cal_v", "")
        refresh_table()

    def read_sensor():
        unit = app.app_settings.get("pressure_unit", "bar")
        # Show in display unit (strip the unit label — only the number goes in the field)
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

        # Points table (wrapper group so refresh_table can recreate the table inside it)
        dpg.add_text("Calibration Points:", color=COL_ACCENT)
        with dpg.group(tag="cal_table_wrap"):
            pass
        refresh_table()

        dpg.add_separator()

        # Add/Edit point inputs
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

        # Export / Import calibration points
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

# ── Calibration points export / import ──────────────────────────────
def _cal_export_json(profile_clone):
    """Export calibration points as JSON to exports folder."""
    EXPORT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r'[^\w\-]', '_', profile_clone.name)[:80]   # SEC-01
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = EXPORT_DIR / f"{safe}_cal_points_{ts}.json"
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"name": profile_clone.name, "points": profile_clone.points}, f, indent=4)
        _show_toast(f"Exported: {fp.name}")
    except OSError as e:
        logging.error(f"Cal JSON export failed: {e}")

def _cal_import(profile_clone, refresh_fn):
    """Open file dialog to import calibration points (JSON/CSV)."""
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
        # SEC-02: guard against enormous files
        if fp.stat().st_size > EN14055_MAX_CAL_FILE_BYTES:
            _show_toast(f"⚠ File too large (>{EN14055_MAX_CAL_FILE_BYTES // (1024*1024)} MB) — import rejected")
            return
        new_pts = []
        skipped = 0
        if fp.suffix.lower() == ".csv":
            with open(fp, newline="", encoding="utf-8") as f:   # CQ-04
                reader = csv.DictReader(f)
                for row in reader:
                    p = float(row.get("P_bar", row.get("p", 0)))
                    h = float(row.get("H_mm",  row.get("h", 0)))
                    v = float(row.get("Vol_L",  row.get("v", 0)))
                    # CQ-07: reject NaN / Inf values
                    if not all(math.isfinite(x) for x in (p, h, v)):
                        skipped += 1
                        continue
                    new_pts.append({"p": p, "h": h, "v": v})
        else:  # JSON
            with open(fp, encoding="utf-8") as f:               # CQ-04
                data = json.load(f)
            raw = data if isinstance(data, list) else data.get("points", [])
            for row in raw:
                p = float(row.get("p", 0))
                h = float(row.get("h", 0))
                v = float(row.get("v", 0))
                if not all(math.isfinite(x) for x in (p, h, v)):
                    skipped += 1
                    continue
                new_pts.append({"p": p, "h": h, "v": v})
        profile_clone.points = new_pts
        refresh_fn()
        msg = f"Imported {len(new_pts)} points from {fp.name}"
        if skipped:
            msg += f" ({skipped} invalid row(s) skipped)"
        _show_toast(msg)
    except Exception as e:
        logging.error(f"Cal import failed: {e}")
        _show_toast(f"⚠ Import failed: {e}")

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
                app.profile.from_dict(json.load(f))
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
    """Save current profile to config/default_profile.json — loaded automatically on startup."""
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
    """Reset all program settings to factory defaults and save."""
    for k, v in DEFAULT_APP.items():
        app.app_settings[k] = v if not isinstance(v, dict) else dict(v)
    # Re-apply colors and theme
    for key, col in DEFAULT_LINE_COLORS.items():
        if key in _line_color_dpg_ids:
            dpg.set_value(_line_color_dpg_ids[key], col)
    _apply_theme("Dark")
    save_settings(app.conn_params, app.app_settings)
    _refresh_limits()
    _show_toast("Settings reset to defaults.")

_toast_msg: str = ""
_toast_clear_at: float = 0.0

def _show_toast(msg: str, duration: float = 3.0):
    """Display a message in the delta label; auto-clears after `duration` seconds."""
    global _toast_msg, _toast_clear_at
    _toast_msg = msg
    _toast_clear_at = time.time() + duration
    dpg.set_value("lbl_delta", msg)

def _check_toast_dismiss(now: float):
    """Call once per UI tick to auto-clear the toast when its timer expires."""
    global _toast_clear_at, _toast_msg
    if _toast_clear_at > 0 and now >= _toast_clear_at:
        if dpg.get_value("lbl_delta") == _toast_msg:
            dpg.set_value("lbl_delta", "---")
        _toast_clear_at = 0.0
        _toast_msg = ""

def _bind_status(item: str, base_tag: str):
    """Bind the correct dark or light variant of a status text-color theme."""
    is_light = app.app_settings.get("ui_theme", "Dark") == "Light"
    tag = base_tag + ("_lt" if is_light else "")
    if not dpg.does_item_exist(tag):
        tag = base_tag          # fall back to dark if light variant missing
    dpg.bind_item_theme(item, tag)
    # CQ-06: track using explicit dict instead of ad-hoc setattr
    app._item_themes[item] = base_tag

_left_panel_visible = True

def _toggle_left_panel():
    global _left_panel_visible
    _left_panel_visible = not _left_panel_visible
    dpg.configure_item("left_panel", show=_left_panel_visible)
    dpg.set_item_label("btn_collapse", "▶" if not _left_panel_visible else "◀")

# ── Main loop callbacks ────────────────────────────────────────────
def update_ui():
    with app.data_lock:
        h = app.current_height
        v = app.current_volume
        p = app.current_pressure
        f = app.current_flow
        temp = app.current_temperature

    dpg.set_value("lbl_h", f"{h:.1f} mm")
    dpg.set_value("lbl_v", f"{v:.2f} L")
    dpg.set_value("lbl_p", p_format(p))
    if dpg.does_item_exist("lbl_f"):
        dpg.set_value("lbl_f", f"{f:.3f} L/s")
    if dpg.does_item_exist("lbl_temp"):
        dpg.set_value("lbl_temp", f"{temp:.1f} \u00b0C" if temp is not None else _TEMP_PLACEHOLDER)

    # Live headroom to overflow = OF − current height (live indicator, not EN14055 §5.2.6 "c")
    of = app.profile.overflow
    if dpg.does_item_exist("lbl_safety_margin"):
        if of > 0:
            sm = of - h   # how far current level is below overflow
            col = COL_GREEN if sm >= 20 else (COL_ORANGE if sm >= 5 else COL_RED)
            dpg.set_value("lbl_safety_margin", f"{sm:.1f} mm")
            dpg.configure_item("lbl_safety_margin", color=col)
        else:
            dpg.set_value("lbl_safety_margin", "— mm")

    # RWL detection state label (auto-detects Residual WL after flush)
    thresh = app.app_settings.get("cwl_drop_thresh", 1.5)
    if app.cwl_state == "ARMED":
        if app.app_settings.get("cwl_mode") == "Automatic":
            dpg.set_value("lbl_cwl_st", f"RWL: ARMED (drop ≥ {thresh}mm)")
        else:
            dpg.set_value("lbl_cwl_st", "RWL: ARMED (Manual)")
        _bind_status("lbl_cwl_st", "theme_blue")
    elif app.cwl_state == "WAITING":
        rem = max(0.0, 2.0 - (time.time() - app.cwl_timer))
        dpg.set_value("lbl_cwl_st", f"RWL: TIMER {rem:.1f}s")
        _bind_status("lbl_cwl_st", "theme_orange")
    elif app.cwl_state == "DONE":
        dpg.set_value("lbl_cwl_st", f"RWL: {app.profile.residual_wl:.1f}mm captured")
        _bind_status("lbl_cwl_st", "theme_green")
    else:
        dpg.set_value("lbl_cwl_st", "RWL: IDLE (set NWL to arm)")
        _bind_status("lbl_cwl_st", "theme_gray")

    # MWL/CWL detection state
    if dpg.does_item_exist("lbl_cwl_auto_st"):
        if app.manual_mwl_cwl_pending:
            dpg.set_value("lbl_cwl_auto_st", "MWL/CWL: click the MWL point on chart")
            _bind_status("lbl_cwl_auto_st", "theme_orange")
        elif app.cwl_auto_state == "ARMED":
            dpg.set_value("lbl_cwl_auto_st", "CWL: ARMED — watching for drop ≥1.5mm")
            _bind_status("lbl_cwl_auto_st", "theme_blue")
        elif app.cwl_auto_state == "WAITING":
            rem = max(0.0, 2.0 - (time.time() - app.cwl_auto_timer))
            dpg.set_value("lbl_cwl_auto_st", f"CWL: 2s WINDOW — {rem:.1f}s left")
            _bind_status("lbl_cwl_auto_st", "theme_orange")
        elif app.cwl_auto_state == "DONE":
            diff = app.profile.cwl - app.profile.overflow if app.profile.overflow > 0 else 0
            dpg.set_value("lbl_cwl_auto_st",
                          f"CWL: {app.profile.cwl:.1f}mm  ({diff:+.1f}mm OF)")
            _bind_status("lbl_cwl_auto_st", "theme_green")
        else:
            dpg.set_value("lbl_cwl_auto_st", "MWL/CWL: IDLE — use Auto-detect or Manual")
            _bind_status("lbl_cwl_auto_st", "theme_gray")

def update_chart():
    # PERF-01: snapshot deque references inside lock, then convert outside.
    # list() on a 12 000-element deque takes ~1 ms; holding data_lock that long
    # blocks the sensor thread from appending new samples.
    plot_idx = dpg.get_value("combo_plot")
    with app.data_lock:
        t_snap = collections.deque(app.t_buf)
        if plot_idx == "Volume (L)":
            y_snap = collections.deque(app.v_buf)
        elif plot_idx == "Flow Rate (L/s)":
            y_snap = collections.deque(app.f_buf)
        else:
            y_snap = collections.deque(app.h_buf)
    t_data = list(t_snap)
    raw_y = list(y_snap)

    if not t_data or not raw_y:
        return

    # Always put full buffer into the series — user can scroll anywhere
    alg = dpg.get_value("combo_smth")
    y_s = smooth(raw_y, alg)

    app.last_t = t_data
    app.last_y = y_s

    dpg.set_value("line_main", [app.last_t, app.last_y])

    # Auto-scroll: restrict the visible axis window, but all data remains accessible
    if not app.chart_paused:
        auto_scroll = dpg.get_value("chk_autoscroll")
        ws = dpg.get_value("combo_win")
        secs = {"10s": 10, "30s": 30, "60s": 60, "5min": 300}.get(ws, None)
        if auto_scroll and secs and t_data:
            x_max = t_data[-1]
            x_min = max(t_data[0], x_max - secs)
            if x_max - x_min < 1:
                x_max = x_min + 1
            dpg.set_axis_limits("x_axis", x_min, x_max)
            # Compute y range for the visible window only
            start_i = bisect.bisect_left(t_data, x_min)
            y_view = y_s[start_i:] if start_i < len(y_s) else y_s
            if y_view:
                y_lo, y_hi = min(y_view), max(y_view)
                margin = max((y_hi - y_lo) * 0.1, 0.5)
                dpg.set_axis_limits("y_axis", y_lo - margin, y_hi + margin)
        else:
            dpg.set_axis_limits_auto("x_axis")
            dpg.set_axis_limits_auto("y_axis")

    # Limit lines (height mode only) — keep full x-span of entire buffer
    plot_mode = dpg.get_value("combo_plot")
    if "Height" in plot_mode and t_data:
        x0, x1 = t_data[0], t_data[-1]
        if x1 - x0 < 1:
            x1 = x0 + 1
        # MWL, CWL, OF (overflow), WD limit lines
        # meniscus is OF+correction — show as absolute height: OF + meniscus
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
    """Update tooltip annotation that snaps to the smoothed line on hover."""
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
    """Auto-stop flush when water level reaches minimum and starts rising back.
    Uses the same drop-detection approach as CWL detection."""
    if not app.flush_measuring:
        return
    elapsed = now - app.flush_start_time
    if elapsed < 3.0:   # need at least 3 s before auto-stop is considered
        return
    if h < app.flush_min_h:
        app.flush_min_h = h
        app.flush_rising = False
    # Rising threshold: 5mm above minimum = flush complete
    if not app.flush_rising and h > app.flush_min_h + 5.0:
        app.flush_rising = True
        app.flush_rising_timer = now
    if app.flush_rising and (now - app.flush_rising_timer >= 2.0):
        # Water has been rising for 2 s — auto-stop
        _toggle_flush_measure()


def frame_callback():
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
        # PERF-03: rate-limit tooltip update to chart refresh interval
        update_hover_tooltip()

# ── CQ-01: guard so that `import sensor_app` in tests does not launch the GUI ──
# Everything from here to the end of the file is GUI construction and the main
# loop.  All class/function definitions above this point are importable safely.
if __name__ != "__main__":
    raise ImportError(
        "sensor_app.py is a standalone application and cannot be imported as a library. "
        "To unit-test pure logic, extract it into separate modules first."
    )

# ── Build GUI ───────────────────────────────────────────────────────
dpg.create_context()
dpg.create_viewport(title="EN 14055 Cistern Analytics - ifm PI1789", width=1200, height=850)

# ── Fonts ────────────────────────────────────────────────────────────
_font_ui = None       # 16 px — general UI
_font_medium = None   # 20 px — secondary readouts
_font_large = None    # 30 px — big H / V display

with dpg.font_registry():
    if _FONT_PATH_REGULAR:
        _font_ui     = dpg.add_font(_FONT_PATH_REGULAR, 16)
        _font_medium = dpg.add_font(_FONT_PATH_REGULAR, 20)
    if _FONT_PATH_BOLD:
        _font_large  = dpg.add_font(_FONT_PATH_BOLD,    30)
    elif _FONT_PATH_REGULAR:
        _font_large  = dpg.add_font(_FONT_PATH_REGULAR, 30)

if _font_ui:
    dpg.bind_font(_font_ui)   # apply globally — replaces ProggyClean

# Themes
# ── Status text color themes — dark variants ─────────────────────────
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

# ── Status text color themes — light variants ─────────────────────────
with dpg.theme(tag="theme_green_lt"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, _LT_GREEN)
with dpg.theme(tag="theme_red_lt"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, _LT_RED)
with dpg.theme(tag="theme_gray_lt"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, _LT_GRAY)
with dpg.theme(tag="theme_blue_lt"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, _LT_ACCENT)
with dpg.theme(tag="theme_orange_lt"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_Text, _LT_ORANGE)

with dpg.theme(tag="theme_dark"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 46))
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (42, 42, 61))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (55, 55, 77))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (65, 65, 90))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 85))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (75, 75, 110))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 90, 130))
        dpg.add_theme_color(dpg.mvThemeCol_Text, (205, 214, 244))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (35, 35, 55))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (45, 45, 70))
        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (35, 35, 55))
        dpg.add_theme_color(dpg.mvThemeCol_Header, (55, 55, 85))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (70, 70, 100))
        dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (45, 45, 68))
        dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (40, 40, 58))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (35, 35, 50))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (65, 65, 90))
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
    with dpg.theme_component(dpg.mvPlot):
        dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (25, 25, 40))
        dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, (60, 60, 80))
        dpg.add_theme_color(dpg.mvPlotCol_AxisText, (180, 190, 210))
        dpg.add_theme_color(dpg.mvPlotCol_AxisGrid, (60, 60, 80))

with dpg.theme(tag="theme_light"):
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (239, 241, 245))
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (220, 224, 232))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (204, 208, 218))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (188, 192, 206))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 185, 205))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (162, 168, 192))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (144, 151, 178))
        dpg.add_theme_color(dpg.mvThemeCol_Text, (76, 79, 105))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (210, 215, 228))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (188, 195, 215))
        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (210, 215, 228))
        dpg.add_theme_color(dpg.mvThemeCol_Header, (188, 195, 215))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (172, 180, 204))
        dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (172, 178, 200))
        dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (230, 233, 240))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (215, 218, 228))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (172, 176, 196))
        dpg.add_theme_color(dpg.mvThemeCol_Separator, (180, 185, 200))
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
    with dpg.theme_component(dpg.mvPlot):
        dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (248, 249, 252))
        dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, (180, 185, 200))
        dpg.add_theme_color(dpg.mvPlotCol_AxisText, (76, 79, 105))
        dpg.add_theme_color(dpg.mvPlotCol_AxisGrid, (200, 204, 215))

def _apply_theme(mode: str):
    """Switch between Dark and Light themes, recoloring all hardcoded items."""
    is_dark = (mode != "Light")
    dpg.bind_theme("theme_dark" if is_dark else "theme_light")

    # Colors for this mode
    acc   = COL_ACCENT if is_dark else _LT_ACCENT
    grn   = COL_GREEN  if is_dark else _LT_GREEN
    gry   = COL_GRAY   if is_dark else _LT_GRAY

    # ── Section headers & live-data labels (hardcoded color= at creation) ──
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
            dpg.configure_item(tag, color=COL_ORANGE if is_dark else _LT_ORANGE)

    # Store mode first so _bind_status picks the correct variant below
    app.app_settings["ui_theme"] = mode

    # Re-bind all tracked status labels — _bind_status reads ui_theme to choose dark/lt variant
    for item, base in app._item_themes.items():
        if dpg.does_item_exist(item):
            _bind_status(item, base)

dpg.bind_theme("theme_dark")

# ── Themed button styles ─────────────────────────────────────────────
# Action button (teal accent) — for measurement capture actions
with dpg.theme(tag="theme_btn_action"):
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
        dpg.add_theme_color(dpg.mvThemeCol_Button,        (40,  90,  90))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 115, 115))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (70, 140, 140))

# Danger button (red) — for stop/clear actions
with dpg.theme(tag="theme_btn_danger"):
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
        dpg.add_theme_color(dpg.mvThemeCol_Button,        (100, 40,  50))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (130, 55,  65))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (160, 70,  80))

# Success button (green) — for start/log actions
with dpg.theme(tag="theme_btn_success"):
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
        dpg.add_theme_color(dpg.mvThemeCol_Button,        (35,  90,  55))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 115,  70))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (65, 140,  85))

# Main window
with dpg.window(tag="main_win"):
    # ── Menu bar ─────────────────────────────────────────────────────
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

    # ── Status bar (top) ─────────────────────────────────────────────
    with dpg.table(header_row=False, borders_innerV=False, borders_outerH=False,
                   borders_outerV=False, resizable=False):
        dpg.add_table_column(width_fixed=True, init_width_or_weight=28)   # collapse btn
        dpg.add_table_column(init_width_or_weight=1.0)                    # profile name
        dpg.add_table_column(width_fixed=True, init_width_or_weight=180)  # conn status
        dpg.add_table_column(width_fixed=True, init_width_or_weight=160)  # connect btn
        with dpg.table_row():
            dpg.add_button(label="◀", tag="btn_collapse",
                           callback=_toggle_left_panel, width=24)
            dpg.add_text("Active Profile: Untitled Profile", tag="lbl_profile")
            with dpg.group(horizontal=True):
                dpg.add_text("", tag="lbl_conn_icon", color=COL_GRAY)
                dpg.add_text("Disconnected", tag="lbl_conn", color=COL_GRAY)
            dpg.add_button(label="Connect Sensor", tag="btn_connect",
                           callback=_toggle_connect, width=-1)

    dpg.add_separator()

    # ── Main layout ───────────────────────────────────────────────────
    with dpg.group(horizontal=True):

        # ── Left panel ───────────────────────────────────────────────
        with dpg.child_window(width=340, border=False, tag="left_panel"):

            # Section: Real-Time Data (two-column layout)
            dpg.add_text("  LIVE DATA", color=COL_ACCENT, tag="hdr_live")
            dpg.add_separator()
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                # Left column: primary measurements
                with dpg.group():
                    dpg.add_text("0.0 mm",     tag="lbl_h", color=COL_ACCENT)
                    dpg.add_text("0.00 L",     tag="lbl_v", color=COL_GREEN)
                    dpg.add_text("0.0000 bar", tag="lbl_p", color=COL_GRAY)
                dpg.add_spacer(width=12)
                # Right column: temperature + instantaneous flow
                with dpg.group():
                    dpg.add_text(_TEMP_PLACEHOLDER, tag="lbl_temp", color=COL_GRAY)
                    dpg.add_text("0.000 L/s",      tag="lbl_f",    color=COL_ORANGE)
                    with dpg.tooltip("lbl_f"):
                        dpg.add_text("Instantaneous flow rate (live).\n"
                                     "EN 14055 effective rate (skip first 1 L,\n"
                                     "last 2 L) is shown in the Flush table.")

            dpg.add_spacer(height=8)

            # Section: EN 14055 Limits
            dpg.add_text("  EN 14055 LIMITS", color=COL_ACCENT, tag="hdr_limits")
            dpg.add_separator()
            dpg.add_spacer(height=2)

            _avg = app.app_settings.get("avg_window", 0.5)
            # NWL + Meniscus on one row, each half-width
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

            # CWL auto-detect (§5.2.4b) — arm while water is at MWL (supply running, OF overflowing).
            # Auto-finds cutoff in history, captures CWL 2s later, and saves MWL from peak.
            dpg.add_button(label="Auto-detect MWL/CWL", callback=_arm_cwl_auto, width=-1)
            dpg.bind_item_theme(dpg.last_item(), "theme_btn_action")
            # Manual fallback — pause chart, click MWL moment, app computes 1s avg back = MWL and +2s = CWL
            dpg.add_button(label="Manual MWL/CWL", tag="btn_manual_mwlcwl",
                           callback=_manual_mwl_cwl, width=-1)
            dpg.bind_item_theme("btn_manual_mwlcwl", "theme_btn_action")

            # Manual RWL timer — only shown in Manual mode
            dpg.add_button(label="Start RWL 2s Timer", tag="btn_manual_rwl",
                           callback=_manual_rwl, width=-1, show=False)
            dpg.bind_item_theme("btn_manual_rwl", "theme_btn_action")

            dpg.add_spacer(height=4)

            # ── Two-column limits grid ──────────────────────────────
            with dpg.group(horizontal=True):
                # Left column
                with dpg.group(width=160):
                    dpg.add_text("NWL (fill):", color=COL_GRAY)
                    dpg.add_text("—", tag="lbl_mwl")
                    dpg.add_spacer(height=3)
                    dpg.add_text("MWL (fault):", color=COL_GRAY)
                    dpg.add_text("—", tag="lbl_mwl_fault")
                    dpg.add_spacer(height=3)
                    dpg.add_text("CWL (2s):", color=COL_GRAY)
                    dpg.add_text("—", tag="lbl_cwl")
                    dpg.add_spacer(height=3)
                    dpg.add_text("Residual WL:", color=COL_GRAY)
                    dpg.add_text("0.0 mm", tag="lbl_residual")

                # Right column
                with dpg.group():
                    dpg.add_text("Meniscus:", color=COL_GRAY)
                    dpg.add_text("0.0 mm", tag="lbl_menis")
                    dpg.add_spacer(height=3)
                    dpg.add_text("Overflow:", color=COL_GRAY)
                    dpg.add_text("0.0 mm", tag="lbl_overflow", color=COL_GRAY)
                    dpg.add_spacer(height=3)
                    dpg.add_text("Safety margin c:", color=COL_GRAY)
                    dpg.add_text("—", tag="lbl_safety_margin_static")
                    dpg.add_spacer(height=3)
                    dpg.add_text("Live headroom:", color=COL_GRAY)
                    dpg.add_text("— mm", tag="lbl_safety_margin")

            dpg.add_spacer(height=6)

            # Status indicators
            dpg.add_text("CWL: — (capture during fault test)", tag="lbl_airgap")
            _bind_status("lbl_airgap", "theme_gray")
            dpg.add_text("CWL: IDLE — arm while at MWL", tag="lbl_cwl_auto_st")
            _bind_status("lbl_cwl_auto_st", "theme_gray")
            dpg.add_text("RWL: IDLE (set NWL to arm)", tag="lbl_cwl_st")
            _bind_status("lbl_cwl_st", "theme_gray")

            dpg.add_spacer(height=8)

            # Section: Flush Test
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
            # Dynamic table — internal scroll, no outer scroll
            with dpg.child_window(tag="flush_table_area", height=145,
                                  border=False, no_scrollbar=True):
                dpg.add_text("No measurements yet.", color=COL_GRAY)

            dpg.add_spacer(height=3)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Clear All", callback=_clear_flush, width=90)
                dpg.bind_item_theme(dpg.last_item(), "theme_btn_danger")
                dpg.add_button(label="Compliance Check", callback=_check_compliance, width=-1)

            dpg.add_spacer(height=8)

            # Section: Data Log
            dpg.add_text("  DATA LOG", color=COL_ACCENT, tag="hdr_log")
            dpg.add_separator()
            dpg.add_spacer(height=2)
            dpg.add_button(label="Start Data Log (CSV)", tag="btn_log",
                           callback=_toggle_log, width=-1)
            dpg.bind_item_theme("btn_log", "theme_btn_success")

        # ── Right panel — chart ──────────────────────────────────────
        with dpg.child_window(border=False):

            # Single toolbar row
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

                # Drag lines — shown only during pause in height mode
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

# Style the chart lines — store color item IDs for live recoloring
_lc = app.app_settings.get("line_colors", {})

def _lc_get(key):
    return _lc.get(key, DEFAULT_LINE_COLORS[key])

_line_color_dpg_ids: dict[str, int] = {}

with dpg.theme(tag="theme_line_main"):
    with dpg.theme_component(dpg.mvLineSeries):
        _line_color_dpg_ids["sensor"] = dpg.add_theme_color(
            dpg.mvPlotCol_Line, _lc_get("sensor"))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0)
dpg.bind_item_theme("line_main", "theme_line_main")

with dpg.theme(tag="theme_line_mwl"):
    with dpg.theme_component(dpg.mvLineSeries):
        _line_color_dpg_ids["mwl"] = dpg.add_theme_color(
            dpg.mvPlotCol_Line, _lc_get("mwl"))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_mwl", "theme_line_mwl")

with dpg.theme(tag="theme_line_menis"):
    with dpg.theme_component(dpg.mvLineSeries):
        _line_color_dpg_ids["menis"] = dpg.add_theme_color(
            dpg.mvPlotCol_Line, _lc_get("menis"))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_menis", "theme_line_menis")

with dpg.theme(tag="theme_line_wd"):
    with dpg.theme_component(dpg.mvLineSeries):
        _line_color_dpg_ids["wd"] = dpg.add_theme_color(
            dpg.mvPlotCol_Line, _lc_get("wd"))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_wd", "theme_line_wd")

with dpg.theme(tag="theme_line_cwl"):
    with dpg.theme_component(dpg.mvLineSeries):
        _line_color_dpg_ids["cwl"] = dpg.add_theme_color(
            dpg.mvPlotCol_Line, _lc_get("cwl"))
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)
dpg.bind_item_theme("line_cwl", "theme_line_cwl")

with dpg.theme(tag="theme_click1"):
    with dpg.theme_component(dpg.mvScatterSeries):
        dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (243, 139, 168))
        dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (220, 80, 120))
        dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle)
        dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 8)
dpg.bind_item_theme("scatter_click1", "theme_click1")

with dpg.theme(tag="theme_click2"):
    with dpg.theme_component(dpg.mvScatterSeries):
        dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (166, 227, 161))
        dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (100, 190, 100))
        dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle)
        dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 8)
dpg.bind_item_theme("scatter_click2", "theme_click2")

dpg.set_primary_window("main_win", True)

# Bind larger fonts to the big readout labels
if _font_large:
    dpg.bind_item_font("lbl_h", _font_large)
    dpg.bind_item_font("lbl_v", _font_large)
if _font_medium:
    dpg.bind_item_font("lbl_p", _font_medium)
    dpg.bind_item_font("lbl_f", _font_medium)
    dpg.bind_item_font("lbl_temp", _font_medium)
    # Limit value labels — slightly larger for readability
    for _tag in ("lbl_mwl", "lbl_mwl_fault", "lbl_cwl", "lbl_menis",
                 "lbl_overflow", "lbl_residual", "lbl_safety_margin",
                 "lbl_safety_margin_static"):
        dpg.bind_item_font(_tag, _font_medium)

with dpg.handler_registry():
    dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=_plot_clicked)

dpg.setup_dearpygui()
dpg.show_viewport()

try:
    # Apply saved theme before first frame
    _apply_theme(app.app_settings.get("ui_theme", "Dark"))

    # Auto-connect on startup
    app.connect()
    if app.is_connected:
        dpg.set_item_label("btn_connect", "Disconnect")
        dpg.configure_item("lbl_conn",
                           default_value=f"{app.conn_params['port']}  {app.conn_params['baud']}bd")
        dpg.configure_item("lbl_conn_icon", default_value="●", color=COL_GREEN)
        dpg.bind_item_theme("btn_connect", "theme_btn_danger")
    elif app.last_error:
        # BUG-10: surface auto-connect failure so the user knows why they're disconnected
        dpg.configure_item("lbl_conn", default_value=app.last_error[:48])
        _bind_status("lbl_conn", "theme_red")

    while dpg.is_dearpygui_running():
        frame_callback()
        dpg.render_dearpygui_frame()

    app.cleanup()
    dpg.destroy_context()
except Exception as e:
    import traceback
    # SEC-05: write crash traceback to a log file (visible even in a windowless .exe)
    try:
        crash_log = BASE_DIR / "crash.log"
        with open(crash_log, "a", encoding="utf-8") as _cf:
            _cf.write(f"\n--- {datetime.now().isoformat()} ---\n")
            traceback.print_exc(file=_cf)
    except Exception:
        pass
    traceback.print_exc()
    input("Натиснете Enter за изход...")

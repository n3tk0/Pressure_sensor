"""
sensor_core.py — Pure logic for the EN 14055 Cistern Analytics app.

Contains every class, function, and constant that has zero GUI dependency.
Shared by both sensor_app.py (DearPyGui) and sensor_app_qt.py (Qt).
"""
import sys
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
import copy
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError as e:
    logging.error(f"pyserial not found: {e}. Install with: pip install pyserial")
    raise

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# ── IFM PI1789 protocol constants ───────────────────────────────────
_PRESSURE_SCALE_BAR_PER_LSB: float = 0.0001
_PRESSURE_MIN_BAR: float = 0.0
_PRESSURE_MAX_BAR: float = 10.0        # PI1789 rated range (0–10 bar)
_PACKET_HEADER_SIZE: int = 12          # 2 SOF + 8 length hex + 2 reserved
_RX_BUF_HIGH_WATERMARK: int = 4000
_RX_BUF_LOW_WATERMARK: int = 2000
# PI1789 PDIN data layout (12 bytes / 24 hex chars):
#   Bytes 0-3  (hex  0- 7) : Pressure  — unsigned 32-bit BE, 0.0001 bar/LSB
#   Byte  4    (hex  8- 9) : Status    — 0xFF = OK
#   Bytes 5-7  (hex 10-15) : Reserved
#   Bytes 8-9  (hex 16-19) : Temperature — unsigned 16-bit BE, 0.01 °C/LSB
#   Bytes 10-11(hex 20-23) : Device status
_PDIN_TEMP_OFFSET = 16   # hex-string start index of temperature word
_PDIN_TEMP_END    = 20   # hex-string end index  (2 bytes = 4 hex chars)
_PDIN_TEMP_SCALE  = 0.01 # °C per LSB
_PDIN_TEMP_MIN_C  = -40.0   # sanity min
_PDIN_TEMP_MAX_C  = 200.0   # sanity max
_TEMP_PLACEHOLDER = "-- °C"

# Byte 4 (hex chars 8-9) — Sensor status flags
_PDIN_STATUS_OFFSET = 8
_PDIN_STATUS_END    = 10
_STATUS_BIT_READY      = 0x80  # Bit 7: Device ready
_STATUS_BIT_OVERRANGE  = 0x40  # Bit 6: Over-range
_STATUS_BIT_UNDERRANGE = 0x20  # Bit 5: Under-range
_STATUS_BIT_SP2        = 0x02  # Bit 1: Switching output SP2
_STATUS_BIT_SP1        = 0x01  # Bit 0: Switching output SP1

def _decode_sensor_status(status_byte: int) -> tuple[str, bool]:
    """Decode PI1789 status byte into (label, is_ok).
    
    Bits are active-LOW: all bits = 1 (0xFF) means healthy.
    A bit going to 0 indicates that fault condition.
    """
    if not (status_byte & _STATUS_BIT_READY):
        return "FAULT", False
    if not (status_byte & _STATUS_BIT_OVERRANGE):
        return "Over-range", False
    if not (status_byte & _STATUS_BIT_UNDERRANGE):
        return "Under-range", False
    return "OK", True

# ── EN 14055:2015 constants ──────────────────────────────────────────
EN14055_CWL_WAIT_S: float = 2.0
EN14055_SAFETY_MARGIN_MIN_MM: float = 20.0
EN14055_MWL_MAX_ABOVE_OF_MM: float = 20.0
EN14055_CWL_MAX_ABOVE_OF_MM: float = 10.0
EN14055_MENISCUS_MAX_ABOVE_OF_MM: float = 5.0
EN14055_FULL_FLUSH_MAX_L: float = 6.0
EN14055_PART_FLUSH_MAX_L: float = 4.0
EN14055_REQUIRED_FLUSH_COUNT: int = 3
EN14055_CWL_DROP_THRESH_MM: float = 1.5
EN14055_FLUSH_MIN_DURATION_S: float = 3.0
EN14055_FLUSH_RISE_THRESH_MM: float = 5.0
EN14055_FLUSH_RISE_CONFIRM_S: float = 2.0
EN14055_CWL_HISTORY_SAMPLES: int = 150
# ARM auto-detect constants
EN14055_ARM_DROP_THRESH_MM: float = 1.5   # height drop in rolling window to start RECORDING
EN14055_ARM_DROP_WINDOW:    int   = 15    # samples in rolling window for drop detection
EN14055_ARM_RISE_THRESH_MM: float = 2.0   # height rise above floor to start stop-confirmation
EN14055_ARM_RISE_SAMPLES:   int   = 15    # consecutive above-floor samples → stop recording
EN14055_MAX_CAL_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MB import guard

# ── Runtime base directory ───────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_DIR = BASE_DIR / "config"
EXPORT_DIR = BASE_DIR / "exports"

for _d in (CONFIG_DIR, EXPORT_DIR):
    _d.mkdir(exist_ok=True)

# ── Font discovery ───────────────────────────────────────────────────
_SCRIPT_DIR = BASE_DIR

def _find_font(name_hints: list[str]) -> str | None:
    """Search for a TTF/OTF font by name hints.
    Checks: script dir → script/fonts/ → Windows Fonts → user Fonts.
    DearPyGui and Qt both load fonts from filesystem paths at runtime,
    so system fonts are accessible from a frozen exe without bundling.
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
FONT_PATH_REGULAR = (
    _find_font(["SamsungSans-Regular", "SamsungSans_v2.0", "SamsungSansV2", "SamsungSans"]) or
    _find_font(["SegoeUI-VF", "SegoeUIVariable-Text", "SegoeUIVariable"]) or
    _find_font(["segoeui"]) or
    _find_font(["Inter-Regular", "Inter_Regular", "Inter"]) or
    _find_font(["arial"])
)
FONT_PATH_BOLD = (
    _find_font(["SamsungSans-Bold", "SamsungSansBold"]) or
    _find_font(["segoeuib"]) or
    _find_font(["Inter-Bold", "Inter_Bold"]) or
    _find_font(["arialbd"]) or
    FONT_PATH_REGULAR
)

# ── Settings persistence ─────────────────────────────────────────────
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONN = {"port": "COM8", "baud": 115200, "io_port": "Port 1", "poll_ms": 50}
DEFAULT_LINE_COLORS = {
    "sensor": [137, 180, 250, 255],
    "mwl":    [100, 200, 255, 255],
    "menis":  [180, 130, 255, 255],
    "wd":     [243, 139, 168, 255],
    "cwl":    [250, 179,  90, 255],
}
DEFAULT_APP = {
    "avg_window": 0.5, "cwl_mode": "Automatic", "cwl_drop_thresh": 1.5,
    "cwl_smooth": "SMA-5", "ui_refresh_ms": 50, "chart_refresh_ms": 100,
    "pressure_unit": "bar",
    "temp_offset": 0.0,
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

# ── Profile dataclasses ──────────────────────────────────────────────
@dataclass
class CalibrationPoint:
    """Single pressure→height/volume calibration point (stored in bar/mm/L)."""
    p: float
    h: float
    v: float


@dataclass
class CisternProfile:
    name: str = "Untitled Profile"
    points: list = field(default_factory=list)   # list[CalibrationPoint]
    mwl: float = 0.0           # NWL — Nominal Water Level
    mwl_fault: float = 0.0     # MWL — Max Water Level during fault (≤+20mm above OF)
    meniscus: float = 0.0      # Meniscus delta above OF (≤+5mm)
    cwl: float = 0.0           # CWL — Critical WL 2s after cutoff (≤+10mm above OF)
    overflow: float = 0.0      # OF — overflow level (absolute height mm)
    water_discharge: float = 0.0
    residual_wl: float = 0.0   # RWL — Residual Water Level (min after flush)

    def to_dict(self):
        d = {k: getattr(self, k) for k in
             ["name", "mwl", "mwl_fault", "meniscus", "cwl", "overflow",
              "water_discharge", "residual_wl"]}
        d["points"] = [{"p": pt.p, "h": pt.h, "v": pt.v} for pt in self.points]
        return d

    @classmethod
    def from_dict(cls, d):
        pts = [CalibrationPoint(p=float(r.get("p", 0)), h=float(r.get("h", 0)),
                                v=float(r.get("v", 0)))
               for r in d.get("points", [])]
        return cls(
            name=d.get("name", "Untitled Profile"),
            points=pts,
            mwl=float(d.get("mwl", 0.0)),
            mwl_fault=float(d.get("mwl_fault", 0.0)),
            meniscus=float(d.get("meniscus", 0.0)),
            cwl=float(d.get("cwl", 0.0)),
            overflow=float(d.get("overflow", 0.0)),
            water_discharge=float(d.get("water_discharge", 0.0)),
            residual_wl=float(d.get("residual_wl", 0.0)),
        )

    def clone(self):
        return replace(self, points=copy.deepcopy(self.points))


# ── Pressure unit conversion ─────────────────────────────────────────
PRESSURE_UNITS = {"bar": (1.0, "bar"), "mbar": (1000.0, "mbar"), "kPa": (100.0, "kPa")}

def p_convert(bar_value, unit="bar"):
    factor, _ = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    return bar_value * factor

def p_format(bar_value, decimals=None, unit="bar"):
    factor, label = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    val = bar_value * factor
    if decimals is None:
        decimals = 4 if unit == "bar" else 2 if unit == "kPa" else 1
    return f"{val:.{decimals}f} {label}"

def p_parse_to_bar(text, unit="bar"):
    factor, _ = PRESSURE_UNITS.get(unit, (1.0, "bar"))
    return float(text.replace(",", ".")) / factor


# ── Smoothing ────────────────────────────────────────────────────────
def smooth(data, alg):
    # PERF-02: full O(N) rebuild on every chart refresh is acceptable at
    # max_pts=12 000 and ≥100 ms interval. Switch SMA to running-sum if needed.
    if alg == "None" or len(data) < 2:
        return list(data)
    n = len(data)
    r = []

    # ── SMA (Simple Moving Average) ──
    if alg.startswith("SMA"):
        try:
            w = int(alg.split("-")[1])
        except (IndexError, ValueError):
            w = 5  # safe default matches Rust fallback
        for i in range(n):
            s = max(0, i - w + 1)
            r.append(sum(data[s:i+1]) / (i - s + 1))
        return r

    # ── EMA (Exponential Moving Average) ──
    if alg.startswith("EMA"):
        a = 0.2 if "Fast" in alg else 0.05
        r.append(data[0])
        for i in range(1, n):
            r.append(a * data[i] + (1 - a) * r[-1])
        return r

    # ── DEMA (Double Exponential Moving Average) ──
    if alg == "DEMA":
        a = 0.15
        ema1 = [data[0]]
        for i in range(1, n):
            ema1.append(a * data[i] + (1 - a) * ema1[-1])
        ema2 = [ema1[0]]
        for i in range(1, n):
            ema2.append(a * ema1[i] + (1 - a) * ema2[-1])
        return [2 * e1 - e2 for e1, e2 in zip(ema1, ema2)]

    # ── Median Filter — parse window size from string, default 5 (matches Rust) ──
    if alg.startswith("Median"):
        try:
            w = int(alg.split("-")[1])
        except (IndexError, ValueError):
            w = 5
        half = w // 2
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            window = sorted(data[lo:hi])
            r.append(window[len(window) // 2])
        return r

    # ── 1D Kalman Filter ──
    if alg == "Kalman":
        # Simple scalar Kalman: Q = process noise, R = measurement noise
        Q = 0.01
        R = 0.5
        x_est = data[0]
        p_est = 1.0
        r.append(x_est)
        for i in range(1, n):
            # Predict
            p_pred = p_est + Q
            # Update
            K = p_pred / (p_pred + R)
            x_est = x_est + K * (data[i] - x_est)
            p_est = (1 - K) * p_pred
            r.append(x_est)
        return r

    # ── Savitzky-Golay (2nd-order polynomial, window 7) ──
    if alg == "Savitzky-Golay":
        # Pre-computed convolution coefficients for quadratic fit, window=7
        # These are the standard SG coefficients for M=3 (half-window), order=2
        coeffs = [-2, 3, 6, 7, 6, 3, -2]
        norm = sum(coeffs)  # = 21
        half = len(coeffs) // 2
        r = list(data)  # copy; edges stay as-is
        for i in range(half, n - half):
            val = 0.0
            for j, c in enumerate(coeffs):
                val += c * data[i - half + j]
            r[i] = val / norm
        return r

    return list(data)


# ── Interpolation cache (pre-sorted + bisect) ────────────────────────
_sorted_pts_cache: list = []
_sorted_pts_pressures: list = []
_interp_cache_version: int = 0
_interp_expected_version: int = -1
_interp_lock = threading.Lock()

def _rebuild_interp_cache(pts):
    """Pre-sort calibration points. Call on profile load/save/edit."""
    global _sorted_pts_cache, _sorted_pts_pressures, _interp_cache_version
    with _interp_lock:
        _sorted_pts_cache = sorted(pts, key=lambda x: x.p)
        _sorted_pts_pressures = [pt.p for pt in _sorted_pts_cache]
        _interp_cache_version += 1

def interp_hv(p_bar, pts):
    global _interp_expected_version
    if not pts:
        return 0.0, 0.0
    with _interp_lock:
        if _interp_expected_version != _interp_cache_version:
            _sorted_pts_cache[:] = sorted(pts, key=lambda x: x.p)
            _sorted_pts_pressures[:] = [pt.p for pt in _sorted_pts_cache]
            _interp_expected_version = _interp_cache_version
        sp = _sorted_pts_cache
        pressures = _sorted_pts_pressures
    if len(sp) == 1:
        return sp[0].h, sp[0].v
    if p_bar <= sp[0].p:
        d = sp[1].p - sp[0].p
        r = (p_bar - sp[0].p) / d if d else 0
        return sp[0].h + r*(sp[1].h - sp[0].h), sp[0].v + r*(sp[1].v - sp[0].v)
    if p_bar >= sp[-1].p:
        d = sp[-1].p - sp[-2].p
        r = (p_bar - sp[-2].p) / d if d else 0
        return sp[-2].h + r*(sp[-1].h - sp[-2].h), sp[-2].v + r*(sp[-1].v - sp[-2].v)
    i = bisect.bisect_right(pressures, p_bar) - 1
    i = max(0, min(i, len(sp) - 2))
    d = sp[i+1].p - sp[i].p
    r = (p_bar - sp[i].p) / d if d else 0
    return sp[i].h + r*(sp[i+1].h - sp[i].h), sp[i].v + r*(sp[i+1].v - sp[i].v)


# ── IFM AL1060 protocol ──────────────────────────────────────────────
def build_request(port_idx):
    payload = json.dumps({
        "code": 10, "cid": 1, "adr": "/getdatamulti",
        "data": {"datatosend": [f"/iolinkmaster/port[{port_idx}]/iolinkdevice/pdin"]}
    })
    return b'\x01\x0110' + f"{len(payload):08X}".encode() + payload.encode()


# ── Application logic ────────────────────────────────────────────────
class SensorApp:
    def __init__(self):
        self.conn_params, self.app_settings = load_settings()
        for k, v in DEFAULT_CONN.items():
            self.conn_params.setdefault(k, v)
        for k, v in DEFAULT_APP.items():
            self.app_settings.setdefault(k, v)

        self.profile = CisternProfile()
        _default = CONFIG_DIR / "default_profile.json"
        if _default.exists():
            try:
                with open(_default, encoding="utf-8") as _f:
                    self.profile = CisternProfile.from_dict(json.load(_f))
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
        self.p_buf = collections.deque(maxlen=self.max_pts)
        self.h_buf = collections.deque(maxlen=self.max_pts)
        self.v_buf = collections.deque(maxlen=self.max_pts)
        self.f_buf = collections.deque(maxlen=self.max_pts)
        self._start_monotonic = time.monotonic()

        self.start_time = time.time()
        self.current_pressure = 0.0
        self.current_height = 0.0
        self.current_volume = 0.0
        self.current_flow = 0.0
        self.current_temperature = None  # °C from pdin bytes 8-9; None if sensor doesn't report it
        self.current_sensor_status = "--"   # decoded status string from pdin byte 4
        self.current_sensor_status_ok = True # True if sensor is healthy

        self.cwl_state = "IDLE"
        self.cwl_peak = 0.0
        self.cwl_timer = 0.0

        self.cwl_auto_state = "IDLE"
        self.cwl_auto_peak  = 0.0
        self.cwl_auto_timer = 0.0

        self._flush_lock = threading.Lock()
        # ARM state machine: "IDLE" → arm_flush() → "ARMED" → auto → "RECORDING" → auto → "IDLE"
        self.flush_state: str = "IDLE"
        self.flush_type: str = "Full Flush"
        # Pre-recording circular buffer; holds samples before the drop is confirmed
        self.flush_arm_buf: collections.deque = collections.deque(maxlen=20)
        # Anchor point (retroactive local max before the drop)
        self.flush_start_t: float = 0.0   # monotonic elapsed time
        self.flush_start_v: float = 0.0   # volume at anchor
        self.flush_start_h: float = 0.0   # height at anchor (local max)
        # Recording buffer: list of (t, h, v) from start anchor to now
        self.flush_record_buf: list = []
        # Minimum height tracker during RECORDING
        self.flush_min_h: float = float("inf")
        self.flush_min_h_t: float = 0.0
        self.flush_min_h_v: float = 0.0
        # Consecutive samples above floor for stop-confirmation
        self.flush_rising_count: int = 0
        # Result list — UI reads this
        self.flush_results: list = []  # list of {type, vol, time, en14055_rate, en14055_note, temp_c}
        # Version counter incremented each time a new result is appended; UI uses it to detect new results
        self.flush_results_version: int = 0

        self.click_points = []
        self.last_t = []
        self.last_y = []
        self.chart_paused = False
        self.manual_mwl_cwl_pending = False
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
                                    raw = struct.unpack(">I", bytes.fromhex(hx[:8]))[0]
                                    p_bar = raw * _PRESSURE_SCALE_BAR_PER_LSB
                                    if not (_PRESSURE_MIN_BAR <= p_bar <= _PRESSURE_MAX_BAR):
                                        logging.debug(f"Pressure out of range: {p_bar:.4f} bar — sample discarded")
                                        continue
                                    h, v = interp_hv(p_bar, self.profile.points)
                                    temp_c = None
                                    sensor_status = "--"
                                    sensor_status_ok = True
                                    # Parse status byte (byte 4, hex chars 8-9)
                                    if len(hx) >= _PDIN_STATUS_END:
                                        sb = int(hx[_PDIN_STATUS_OFFSET:_PDIN_STATUS_END], 16)
                                        sensor_status, sensor_status_ok = _decode_sensor_status(sb)
                                    if len(hx) >= _PDIN_TEMP_END:
                                        raw_t = struct.unpack(">H", bytes.fromhex(hx[_PDIN_TEMP_OFFSET:_PDIN_TEMP_END]))[0]
                                        t_val = raw_t * _PDIN_TEMP_SCALE
                                        t_val += self.app_settings.get("temp_offset", 0.0)
                                        if _PDIN_TEMP_MIN_C <= t_val <= _PDIN_TEMP_MAX_C:
                                            temp_c = t_val
                                    with self.data_lock:
                                        t = time.monotonic() - self._start_monotonic
                                        f_rate = 0.0
                                        if len(self.t_buf) > 5:
                                            dt = t - self.t_buf[-5]
                                            if dt > 0:
                                                f_rate = (self.v_buf[-5] - v) / dt
                                        self.current_pressure = p_bar
                                        self.current_height = h
                                        self.current_volume = v
                                        self.current_flow = f_rate
                                        if temp_c is not None:
                                            self.current_temperature = temp_c
                                        self.current_sensor_status = sensor_status
                                        self.current_sensor_status_ok = sensor_status_ok
                                        self.t_buf.append(t)
                                        self.p_buf.append(p_bar)
                                        self.h_buf.append(h)
                                        self.v_buf.append(v)
                                        self.f_buf.append(f_rate)

                                    # ARM state machine — runs in sensor thread (no UI blocking)
                                    self.tick_flush(t, h, v)

                                    with self._csv_lock:
                                        if self.is_logging and self.csv_writer:
                                            unit = self.app_settings.get("pressure_unit", "bar")
                                            t_str = f"{temp_c:.1f}" if temp_c is not None else ""
                                            self.csv_writer.writerow([
                                                datetime.now().isoformat(),
                                                p_format(p_bar, unit=unit).split()[0],
                                                f"{h:.1f}", f"{v:.2f}", f"{f_rate:.3f}", t_str
                                            ])
                                            self._csv_row_count += 1
                                            if self.csv_file and self._csv_row_count % 50 == 0:
                                                self.csv_file.flush()
                        except (json.JSONDecodeError, struct.error, ValueError) as e:
                            logging.debug(f"Parse error: {e}")

                    if len(rx_buf) > _RX_BUF_HIGH_WATERMARK:
                        marker = b'\x01\x0110'
                        cut = rx_buf.find(marker, len(rx_buf) - _RX_BUF_LOW_WATERMARK)
                        if cut != -1:
                            rx_buf = rx_buf[cut:]
                        else:
                            logging.debug("RX buffer overflow: no sync marker found, resetting buffer")
                            rx_buf = bytearray()
                except serial.SerialException as e:
                    self.last_error = f"Serial read error: {e}"
                    logging.error(self.last_error)
                    self.is_connected = False
                    break
                except OSError as e:
                    self.last_error = f"Port I/O error: {e}"
                    logging.error(self.last_error)
                    self.is_connected = False
                    break

            elapsed = time.time() - t0
            slp = poll_sleep - elapsed
            if slp > 0:
                self.stop_event.wait(slp)
        # Ensure connection state is coherent even when the thread exits unexpectedly.
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        self.serial_conn = None
        self.is_connected = False

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
            self._start_monotonic = time.monotonic()
            with self.data_lock:
                self.t_buf.clear()
                self.p_buf.clear()
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
            if not self.h_buf or not self.t_buf:
                return self.current_height
            now = self.t_buf[-1]
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
        """Detect Residual Water Level — minimum height after a flush.
        Returns True when detection is complete."""
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
                self.profile.residual_wl = self.get_avg_height()
                self.cwl_state = "DONE"
                return True
        return False

    def tick_cwl_auto(self, h: float,
                      h_history: list, t_history: list) -> bool:
        """Auto-detect CWL per EN 14055 §5.3.4.
        Returns True when CWL is captured."""
        alg = self.app_settings.get("cwl_smooth", "SMA-5")

        if self.cwl_auto_state == "ARMED":
            sm = smooth(h_history, alg)
            val = sm[-1] if sm else h
            if val > self.cwl_auto_peak:
                self.cwl_auto_peak = val
            if self.cwl_auto_peak - val >= 1.5:
                drop_start_wall = time.time()
                if h_history and t_history:
                    sm_full = smooth(h_history, alg)
                    n = min(len(sm_full), len(t_history))
                    for i in range(n - 1, -1, -1):
                        if sm_full[i] >= self.cwl_auto_peak - 0.5:
                            elapsed_now  = time.time() - self.start_time
                            elapsed_drop = t_history[i]
                            drop_start_wall = time.time() - (elapsed_now - elapsed_drop)
                            break
                self.cwl_auto_timer = drop_start_wall
                self.cwl_auto_state = "WAITING"

        elif self.cwl_auto_state == "WAITING":
            if time.time() - self.cwl_auto_timer >= 2.0:
                self.profile.cwl = self.get_avg_height()
                self.profile.mwl_fault = self.cwl_auto_peak
                self.cwl_auto_state = "DONE"
                return True

        return False

    # ── ARM flush state machine ──────────────────────────────────────
    def arm_flush(self, flush_type: str = "Full Flush") -> bool:
        """UI calls this to arm the system. Returns False if already armed/recording."""
        with self._flush_lock:
            if self.flush_state != "IDLE":
                return False
            self.flush_type = flush_type
            self.flush_arm_buf.clear()
            self.flush_state = "ARMED"
            return True

    def cancel_flush(self):
        """UI calls this to disarm or abort an in-progress recording."""
        with self._flush_lock:
            self.flush_state = "IDLE"
            self.flush_arm_buf.clear()
            self.flush_record_buf = []

    def tick_flush(self, t: float, h: float, v: float):
        """Called from sensor thread on every new sample.
        Drives the ARMED→RECORDING→IDLE state machine without blocking.
        t is monotonic elapsed seconds; h height mm; v volume L."""
        with self._flush_lock:
            if self.flush_state == "ARMED":
                self.flush_arm_buf.append((t, h, v))
                buf = list(self.flush_arm_buf)
                if len(buf) >= 10:
                    # Rolling window: check for sudden drop ≥ threshold
                    win = buf[-EN14055_ARM_DROP_WINDOW:]
                    win_h = [s[1] for s in win]
                    peak = max(win_h)
                    if peak - h >= EN14055_ARM_DROP_THRESH_MM:
                        # Retroactively find the local-maximum sample
                        peak_idx_in_win = win_h.index(peak)
                        abs_start = len(buf) - len(win) + peak_idx_in_win
                        start_s = buf[abs_start]
                        self.flush_start_t = start_s[0]
                        self.flush_start_h = start_s[1]
                        self.flush_start_v = start_s[2]
                        # Seed record_buf with everything from the local max forward
                        self.flush_record_buf = list(buf[abs_start:])
                        self.flush_min_h   = min(s[1] for s in self.flush_record_buf)
                        self.flush_min_h_t = self.flush_record_buf[-1][0]
                        self.flush_min_h_v = self.flush_record_buf[-1][2]
                        self.flush_rising_count = 0
                        self.flush_state = "RECORDING"

            elif self.flush_state == "RECORDING":
                self.flush_record_buf.append((t, h, v))
                if h < self.flush_min_h:
                    # New floor — reset confirmation counter
                    self.flush_min_h   = h
                    self.flush_min_h_t = t
                    self.flush_min_h_v = v
                    self.flush_rising_count = 0
                elif h > self.flush_min_h + EN14055_ARM_RISE_THRESH_MM:
                    self.flush_rising_count += 1
                else:
                    self.flush_rising_count = 0

                elapsed = t - self.flush_start_t
                if (elapsed >= EN14055_FLUSH_MIN_DURATION_S and
                        self.flush_rising_count >= EN14055_ARM_RISE_SAMPLES):
                    self._finish_flush()

    def _finish_flush(self):
        """Compute result and append to flush_results. Must be called with _flush_lock held."""
        start_v = self.flush_start_v
        end_v   = self.flush_min_h_v
        time_s  = self.flush_min_h_t - self.flush_start_t
        delta_vol = abs(start_v - end_v)
        temp_c  = self.current_temperature  # snapshot (not lock-protected but only read)

        en14055_rate = None
        en14055_note = None
        if delta_vol > 3.0 and len(self.flush_record_buf) > 2:
            v_skip_start = start_v - 1.0          # skip first 1 L
            v_skip_end   = end_v   + 2.0           # skip last 2 L
            t1 = next((s[0] for s in self.flush_record_buf if s[2] <= v_skip_start), None)
            t2 = next((s[0] for s in self.flush_record_buf if s[2] <= v_skip_end),   None)
            if t1 is not None and t2 is not None and t2 > t1:
                eff_vol  = v_skip_start - v_skip_end
                eff_time = t2 - t1
                if eff_time > 0 and eff_vol > 0:
                    en14055_rate = eff_vol / eff_time
                    en14055_note = "Excl. first 1 L and last 2 L (V2 method)"
                else:
                    en14055_note = "N/A (flush window too short)"
            else:
                en14055_note = "N/A (flush too short for skip-window)"

        self.flush_results.append({
            "type":          self.flush_type,
            "vol":           delta_vol,
            "time":          max(time_s, 0.001),
            "en14055_rate":  en14055_rate,
            "en14055_note":  en14055_note,
            "temp_c":        temp_c,
        })
        self.flush_results_version += 1

        # Reset state
        self.flush_state = "IDLE"
        self.flush_arm_buf.clear()
        self.flush_record_buf = []
        self.flush_rising_count = 0
        self.flush_min_h = float("inf")

    def cleanup(self):
        save_settings(self.conn_params, self.app_settings)
        self.disconnect()
        with self._csv_lock:
            if self.csv_file:
                try:
                    self.csv_file.close()
                except OSError as e:
                    logging.warning(f"CSV close on exit: {e}")
                finally:
                    self.csv_file = None
                    self.csv_writer = None


# ── EN 14055 compliance checks (pure logic, no GUI) ──────────────────
def run_compliance_checks(
    profile: CisternProfile,
    flush_results: list,
) -> tuple[list[str], float | None]:
    """Pure EN 14055 compliance logic.

    Returns:
        results      — list of '[PASS]/[FAIL]/[WARN]/[INFO]/[----]' strings
        air_gap_auto — computed air gap in mm, or None if not computable
    """
    p = profile
    results = []

    # 1. Safety Margin c: OF − NWL ≥ 20 mm (§5.2.6)
    if p.overflow > 0 and p.mwl > 0:
        sm = p.overflow - p.mwl
        if sm >= 20:
            results.append(f"[PASS] Safety margin c (OF−NWL): {sm:.1f} mm ≥ 20 mm")
        else:
            results.append(f"[FAIL] Safety margin c (OF−NWL): {sm:.1f} mm < 20 mm")
    else:
        results.append("[----] Safety margin c: capture NWL and set Overflow first")

    # 2. MWL − OF ≤ 20 mm (§5.2.4a)
    if p.overflow > 0 and p.mwl_fault > 0:
        diff = p.mwl_fault - p.overflow
        if diff <= 20:
            results.append(f"[PASS] MWL fault: +{diff:.1f} mm above OF ≤ 20 mm")
        else:
            results.append(f"[FAIL] MWL fault: +{diff:.1f} mm above OF > 20 mm")
    else:
        results.append("[----] MWL fault: run overflow fault test and press 'Set MWL (fault)'")

    # 3. CWL − OF ≤ 10 mm (§5.2.4b)
    if p.overflow > 0 and p.cwl > 0:
        diff = p.cwl - p.overflow
        if diff <= 10:
            results.append(f"[PASS] CWL: {diff:+.1f} mm from OF ≤ 10 mm")
        else:
            results.append(f"[FAIL] CWL: +{diff:.1f} mm above OF > 10 mm")
    else:
        results.append("[----] CWL: run fault test, cut supply, wait 2s, press 'Set CWL'")

    # 4. Meniscus − OF ≤ 5 mm (§5.2.4c)
    if p.meniscus != 0:
        m = p.meniscus
        if 0 <= m <= 5:
            results.append(f"[PASS] Meniscus: +{m:.1f} mm above OF ≤ 5 mm")
        elif m > 5:
            results.append(f"[FAIL] Meniscus: +{m:.1f} mm above OF > 5 mm")
        else:
            results.append(f"[WARN] Meniscus: {m:.1f} mm (below OF — check capture)")
    else:
        results.append("[----] Meniscus: let cistern overflow, stabilise, press 'Set Meniscus'")

    # 5. Air gap a: water_discharge − CWL ≥ 20 mm (§5.2.7)
    air_gap_auto: float | None = None
    if p.water_discharge > 0 and p.cwl > 0:
        air_gap_auto = p.water_discharge - p.cwl
        if air_gap_auto >= 20:
            results.append(f"[PASS] Air gap a (§5.2.7): {air_gap_auto:.1f} mm (WD−CWL) ≥ 20 mm")
        else:
            results.append(f"[FAIL] Air gap a (§5.2.7): {air_gap_auto:.1f} mm (WD−CWL) < 20 mm")

    # 6. Residual WL (informational)
    if p.residual_wl > 0:
        results.append(f"[INFO] Residual WL (RWL): {p.residual_wl:.1f} mm after flush")

    # 7. Flush volume (§5.2.1, §6.5)
    if flush_results:
        full = [r for r in flush_results if "Full" in r["type"]]
        part = [r for r in flush_results if "Part" in r["type"]]
        if full:
            if len(full) < EN14055_REQUIRED_FLUSH_COUNT:
                results.append(f"[WARN] Full flush: only {len(full)}/{EN14055_REQUIRED_FLUSH_COUNT} measurements (§5.2.1 requires 3)")
            avg_full = sum(r["vol"] for r in full) / len(full)
            tag = "PASS" if avg_full <= EN14055_FULL_FLUSH_MAX_L else "FAIL"
            results.append(f"[{tag}] Full flush avg: {avg_full:.2f} L (limit {EN14055_FULL_FLUSH_MAX_L} L)")
            en_rates = [r["en14055_rate"] for r in full if r.get("en14055_rate") is not None]
            if en_rates:
                avg_rate = sum(en_rates) / len(en_rates)
                results.append(f"[INFO] Full flush EN14055 flow rate (V2 method): {avg_rate:.3f} L/s")
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

    return results, air_gap_auto

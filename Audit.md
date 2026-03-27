# Code Audit — `sensor_app.py`

**Date:** 2026-03-27
**Scope:** Full static review of `sensor_app.py` (~1892 lines)
**Purpose:** EN 14055 cistern analytics application using ifm PI1789 pressure sensor, IFM AL1060 IO-Link master, Dear PyGui GUI.

---

## 1. Overview

The script is a single-file desktop application that:
- Reads pressure data from a serial port (IFM AL1060 IO-Link master via JSON protocol)
- Converts raw pressure to water height and volume via user-defined calibration points
- Tracks EN 14055 compliance limits (MWL, CWL, meniscus correction, water discharge)
- Measures flush volumes and generates compliance reports
- Logs data to CSV and exports screenshots

---

## 2. Architecture Summary

| Layer | Description |
|---|---|
| `CisternProfile` | Data model for calibration and limit values |
| `SensorApp` | Main application state + sensor thread + serial I/O |
| `build_request` / `interp_hv` / `smooth` | Protocol helpers and computation |
| Module-level DPG code | GUI construction, theme setup, main loop |

---

## 3. Bugs

### BUG-01 — Thread race on interpolation cache (Critical)
**Lines:** 189–224, `read_thread` (background), `save_cal` / `_rebuild_interp_cache` (main thread)

The four globals `_sorted_pts_cache`, `_sorted_pts_pressures`, `_interp_cache_version`, `_interp_expected_version` are read and written from both the sensor background thread (`interp_hv` → `_rebuild_interp_cache`) and the main GUI thread (`save_cal` → `_rebuild_interp_cache`). There is no lock protecting these globals. A concurrent rebuild and read can produce a partially updated cache, causing incorrect interpolation results or an `IndexError`.

---

### BUG-02 — TOCTOU race on CSV writer (High)
**Lines:** 351–360 (sensor thread), 694–702 (main thread `_toggle_log`)

The sensor thread checks `self.is_logging` and then accesses `self.csv_writer`. The main thread can set `self.csv_writer = None` between those two operations. No lock protects the `csv_file`/`csv_writer`/`is_logging` triple, so a `NoneType` AttributeError or a write to a closed file is possible when logging is stopped while the sensor is actively writing a row.

---

### BUG-03 — `read_thread` join timeout may be too short (Medium)
**Line:** 409

`self.read_thread_obj.join(timeout=1.0)` uses a 1-second timeout. If `poll_ms` is set to 1000 ms, the thread may be sleeping for nearly a full second inside `time.sleep(slp)` when `stop_event` is set. The join could return before the thread actually exits, leaving a dangling thread that still holds the serial port.

---

### BUG-04 — RX buffer trimming can split a packet mid-frame (Medium)
**Lines:** 364–365

```python
if len(rx_buf) > 4000:
    rx_buf = rx_buf[-2000:]
```

This discards the oldest 2000 bytes, but the retained tail may start in the middle of a JSON payload. The next parse iteration will fail to find a valid `\x01\x0110` header inside that partial payload, silently discarding the data. A proper fix would scan for the next valid sync marker before truncating.

---

### BUG-05 — Pressure raw value decoded as signed integer (Medium)
**Line:** 333

```python
raw = struct.unpack(">i", bytes.fromhex(hx[:8]))[0]
```

`>i` is a signed 32-bit big-endian integer. If the sensor encodes the PDIN field as unsigned (`>I`), pressure values above `0x7FFFFFFF` (≈214,748 bar × 0.0001) would wrap to negative — producing physically impossible negative pressures that propagate into height/volume calculations and CSV logs without any validation.

---

### BUG-06 — No validation of calibration point pressure values (Low)
**Lines:** 892–908 (`add_or_update_point`)

There is no check that the entered pressure value is non-negative, or that the new point does not duplicate an existing pressure value. Duplicate pressure entries produce a zero-length segment (`d = 0`) in `interp_hv`, silently falling back to `r = 0` (uses the lower point's values) rather than warning the user.

---

### BUG-07 — `port_idx` parsing will crash on unexpected `io_port` format (Low)
**Line:** 295

```python
port_idx = int(self.conn_params["io_port"].replace("Port ", ""))
```

If `io_port` is saved as a bare number string or has different casing, `int(...)` raises `ValueError` and the read thread terminates silently (the exception is not caught at this level).

---

### BUG-08 — EN 14055 flush compliance check does not distinguish flush type (Low)
**Lines:** 629–635

All flush measurements are compared to a single 6.0 L threshold regardless of whether they were recorded as "Full Flush" or "Part Flush". EN 14055 specifies different maximum volumes for full and partial flushes; the current check is therefore incorrect for partial flush measurements.

---

## 4. Security Observations

### SEC-01 — Profile name used unsanitized in file paths (Low)
**Lines:** 682, 802, 994

```python
fn = str(EXPORT_DIR / f"{app.profile.name.replace(' ', '_')}_{ts}.csv")
```

Only spaces are replaced. Characters such as `../`, `\`, `:` (Windows), or null bytes are not stripped. Because the path is built via `pathlib` with a base directory prefix, directory traversal is prevented on most operating systems, but the resulting filename may still be invalid or unexpected on Windows (e.g., a name containing `:` or `*`).

---

### SEC-02 — Imported calibration JSON is not size-limited (Low)
**Lines:** 1027–1036 (`_cal_import_cb`)

`json.load(f)` is called on a user-supplied file with no size check. An extremely large file could cause high memory usage. The risk is low in a desktop application context but worth noting.

---

### SEC-03 — Settings file written non-atomically (Low)
**Lines:** 109–114 (`save_settings`)

`open(SETTINGS_FILE, "w")` truncates the file before writing. A crash or power loss mid-write leaves a zero-length or partial JSON file, making the application fail to load settings on next startup. An atomic write (write to a temp file, then rename) would prevent this.

---

## 5. Code Quality Issues

### CQ-01 — No `if __name__ == "__main__"` guard
The entire GUI setup, DPG context creation, and main loop execute at module level. Importing the module for testing or tooling immediately launches the application window.

---

### CQ-02 — Business logic tightly coupled to GUI
Functions like `p_convert`, `p_format`, and `p_parse_to_bar` call `app.app_settings` directly (the global singleton). This makes unit testing these utility functions impossible without a running `SensorApp` instance.

---

### CQ-03 — Module-level mutable globals
`_sorted_pts_cache`, `_sorted_pts_pressures`, `_interp_cache_version`, `_interp_expected_version`, `_toast_msg`, `_toast_clear_at`, `_line_color_dpg_ids`, `app` — all are module-level mutable state. This pattern makes the code harder to reason about and test.

---

### CQ-04 — Magic numbers
Several numeric literals are used without named constants:
- `0.0001` (bar per raw unit, line 334)
- `4000` / `2000` (RX buffer thresholds, lines 364–365)
- `12` (IFM packet header size, lines 312–325)
- `150` (CWL history window, line 1388)
- `2.0` (CWL timer seconds, lines 456, 1277)
- `20` (EN 14055 overflow margin mm, lines 611, 623, 669)
- `6.0` (flush volume limit L, line 632)

---

### CQ-05 — Implicit encoding for file I/O
**Lines:** 101, 1019, 1028, 1164, 1176, 1201

`open(...)` calls do not specify `encoding="utf-8"`. On Windows systems with non-UTF-8 default codepages, JSON files containing non-ASCII characters (e.g., Bulgarian strings in the profile name) may fail to load or save correctly.

---

### CQ-06 — `_bind_status` stores state as ad-hoc attributes on `app`
**Line:** 1253

```python
setattr(app, f"_{item}_theme", base_tag)
```

Arbitrary attributes are added to `SensorApp` at runtime based on DPG item tags. These are not declared in `__init__`, making the object's state hard to inspect or document.

---

### CQ-07 — Flow rate sign convention is not documented
**Lines:** 339–341

```python
f_rate = (self.v_buf[-5] - v) / dt
```

A positive `f_rate` means volume is *decreasing* (water flowing out). This is counter-intuitive (conventionally flow rate is positive when volume increases) and is not commented anywhere.

---

### CQ-08 — `lbl_flush` referenced in `_apply_theme` but never created
**Line:** 1548

`_apply_theme` iterates over `("lbl_margin", "lbl_cwl_st", "lbl_flush")`, but `lbl_flush` is never created as a DPG item. The `dpg.does_item_exist` guard silently skips it, but this is a latent item tag mismatch.

---

### CQ-09 — No unit tests
The project has no test suite. The computational core (`interp_hv`, `smooth`, `p_convert`, EN 14055 compliance checks) would benefit from isolated unit tests.

---

## 6. Performance Observations

### PERF-01 — `list()` conversions inside a locked section
**Lines:** 1289–1296 (`update_chart`)

`list(app.t_buf)` and `list(app.v_buf)` are called while holding `app.data_lock`. Converting a 12,000-element deque to a list under a lock blocks the sensor thread from appending new data. The lock should be held only long enough to snapshot the deque references, and conversion done outside the lock.

---

### PERF-02 — `smooth` reallocates on every chart refresh
`smooth` rebuilds the entire smoothed list from scratch on every `update_chart` call. For large time windows this is O(n). An incremental approach (maintain a running average) would reduce CPU usage.

---

## 7. Summary Table

| ID | Severity | Category | Line(s) | Issue |
|---|---|---|---|---|
| BUG-01 | Critical | Thread Safety | 189–224 | Race condition on interpolation cache globals |
| BUG-02 | High | Thread Safety | 351–360, 694–702 | TOCTOU race on CSV writer/file handle |
| BUG-03 | Medium | Correctness | 409 | `join(timeout=1.0)` may not wait long enough |
| BUG-04 | Medium | Correctness | 364–365 | RX buffer trim can split a mid-flight packet |
| BUG-05 | Medium | Correctness | 333 | Signed vs unsigned raw pressure decode |
| BUG-06 | Low | Correctness | 892–908 | No validation of calibration point values |
| BUG-07 | Low | Robustness | 295 | `io_port` parse crash on unexpected format |
| BUG-08 | Low | Compliance | 629–635 | Flush compliance check ignores type |
| SEC-01 | Low | Security | 682, 802, 994 | Profile name not sanitized for filenames |
| SEC-02 | Low | Security | 1027–1036 | No file size limit on imported JSON |
| SEC-03 | Low | Security | 109–114 | Non-atomic settings file write |
| CQ-01 | Medium | Maintainability | — | No `__main__` guard |
| CQ-02 | Medium | Maintainability | 145–167 | Business logic coupled to global singleton |
| CQ-03 | Low | Maintainability | — | Extensive module-level mutable state |
| CQ-04 | Low | Readability | various | Magic numbers without named constants |
| CQ-05 | Low | Robustness | various | Missing `encoding=` in `open()` calls |
| CQ-06 | Low | Maintainability | 1253 | Ad-hoc runtime attributes on `app` |
| CQ-07 | Low | Readability | 339–341 | Flow rate sign convention undocumented |
| CQ-08 | Low | Correctness | 1548 | `lbl_flush` referenced but never created |
| CQ-09 | Low | Testability | — | No unit tests |
| PERF-01 | Low | Performance | 1289–1296 | List copies taken inside data lock |
| PERF-02 | Low | Performance | 170–186 | Full smooth recalculation every frame |

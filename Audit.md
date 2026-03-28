# Code Audit — `sensor_app.py`

**Date:** 2026-03-28
**Auditor:** Senior Python Architect / Embedded Systems Expert
**Scope:** Full static review of `sensor_app.py` (2 362 lines, single-file desktop app)
**Standard reference:** EN 14055:2015 — WC and urinal flushing cisterns
**Hardware:** ifm PI1789 pressure sensor + ifm AL1060 IO-Link master (serial/JSON protocol)
**UI framework:** Dear PyGui

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Critical Problems](#2-critical-problems)
3. [Architectural Improvements](#3-architectural-improvements)
4. [EN 14055 Compliance Notes](#4-en-14055-compliance-notes)
5. [Code Quality Findings](#5-code-quality-findings)
6. [Performance Issues](#6-performance-issues)
7. [Security Risks](#7-security-risks)
8. [Refactoring Plan](#8-refactoring-plan)
9. [Improved Code Snippets](#9-improved-code-snippets)

---

## 1. Executive Summary

`sensor_app.py` is a capable single-file desktop application that reads pressure data
from an ifm PI1789 sensor via an AL1060 IO-Link master, converts it to water height and
volume, and drives an EN 14055 compliance workflow.  The implementation is well-commented
and functionally correct for the happy path.  However, several **critical thread-safety
defects** create data corruption and crash risks that must be resolved before this tool can
be trusted for compliance measurements.

**High-level scorecard:**

| Area | Rating | Key concern |
|---|---|---|
| Thread safety | ⚠ Poor | 3 unprotected shared data structures |
| EN 14055 correctness | ⚠ Partial | flush result uses WARN not FAIL; §5.2.1 sequence not enforced |
| Architecture | ⚠ Monolithic | 2 362 lines in one file; business logic coupled to global GUI state |
| Error handling | ✓ Acceptable | Serial errors caught; CSV/JSON errors logged |
| Security | ✓ Low risk | Profile name sanitised; atomic settings write in place |
| Python quality | ⚠ Moderate | No type hints on most functions; no `__main__` guard; magic numbers |
| Performance | ✓ Acceptable | O(N) smooth recalc per frame; minor lock contention |
| Testability | ✗ None | Zero unit tests; business logic inseparable from DPG |

---

## 2. Critical Problems

### BUG-01 — `flush_vol_history` accessed without a lock *(Critical)*

**Lines:** sensor thread append: 396–398 · main thread read/clear: 766, 775–800

`SensorApp.flush_vol_history` is a plain Python `list`.  The sensor background thread
appends to it every polling cycle while `flush_measuring` is `True`.  The main thread
reads its contents and clears it in `_toggle_flush_measure()` without holding any mutex.

```python
# sensor thread (read_thread) — no lock:
if self.flush_measuring:
    self.flush_vol_history.append((time.time(), v, h))

# main thread — also no lock:
app.flush_measuring = False
history = app.flush_vol_history          # ← may be mutated concurrently
min_vol = min(r[1] for r in history)     # ← can see partial state
```

**Impact:** `min()` over a partially-appended list; potential `ValueError` on empty
iteration after `flush_vol_history = []` races with a concurrent append.  EN 14055 volume
and effective-flow-rate results can be silently wrong.

**Fix:** Protect `flush_measuring`, `flush_vol_history`, `flush_start_*` with a dedicated
`threading.Lock` (or `threading.RLock` if auto-stop re-enters).

---

### BUG-02 — `_toggle_log` closes csv_file without holding `_csv_lock` *(High)*

**Lines:** 1022–1030 (`_toggle_log`); 402–413 (sensor thread)

The sensor thread acquires `self._csv_lock` before writing a CSV row (line 402).
`_toggle_log` sets `app.is_logging = False`, then calls `app.csv_file.close()` — but
**without** acquiring `_csv_lock` first.  The sequence:

```
Main thread:   app.is_logging = False
               app.csv_file.close()        ← file closed
Sensor thread:                             with self._csv_lock:
                                               self.csv_writer.writerow(...)  ← write to closed file!
```

is possible because `is_logging` is checked *before* entering the lock on the sensor side.

**Impact:** `ValueError: I/O operation on closed file` in the sensor thread; logged row is
lost; subsequent poll cycle crashes the sensor loop, silently stopping all data collection
until reconnect.

**Fix:** In `_toggle_log`, set `is_logging = False` and **then** acquire `_csv_lock` before
closing `csv_file`:

```python
app.is_logging = False
with app._csv_lock:
    if app.csv_file:
        app.csv_file.close()
        app.csv_file = None
        app.csv_writer = None
        app._csv_row_count = 0
```

---

### BUG-03 — Signed vs. unsigned raw pressure decode *(High)*

**Line:** 369

```python
raw = struct.unpack(">i", bytes.fromhex(hx[:8]))[0]   # signed 32-bit
```

The IFM PI1789 PDIN field encodes pressure as an **unsigned** 32-bit integer (range
0 … 4 294 967 295 raw units, representing 0 … ~429 497 bar).  Using `">i"` (signed)
wraps any raw value above `0x7FFFFFFF` to a large negative number, producing a physically
impossible negative pressure.  That negative value is then stored in `current_pressure`,
used in `interp_hv`, written to CSV, and displayed without any sanity check.

**Fix:** Use `">I"` (unsigned) and add a post-decode range check:

```python
raw = struct.unpack(">I", bytes.fromhex(hx[:8]))[0]
p_bar = raw * 0.0001
if not (0.0 <= p_bar <= 10.0):      # PI1789 range: 0–10 bar
    logging.warning(f"Pressure out of range: {p_bar:.4f} bar — skipping sample")
    continue
```

The same issue applies to the temperature word at line 375 — verify whether the sensor
encodes temperature as signed (two's-complement, which would correctly represent sub-zero
°C) or as an unsigned offset.

---

### BUG-04 — RX buffer trim can split a JSON packet mid-frame *(Medium)*

**Lines:** 417–419

```python
if len(rx_buf) > 4000:
    cut = rx_buf.find(b'\x01\x0110', len(rx_buf) - 2000)
    rx_buf = rx_buf[cut:] if cut != -1 else rx_buf[-2000:]
```

This is an improvement over the naïve tail-slice seen in earlier versions, but it is only
correct when a complete sync marker exists in the retained tail.  If the search window
(`len(rx_buf) - 2000`) falls mid-marker, `find` returns `-1` and the fallback
`rx_buf[-2000:]` can still begin in the middle of a length field.  The next iteration will
then misparse the packet length, potentially reading a negative or enormous `exp_len`.

**Fix:** Search from offset 0 inside the retained region, not from a fixed tail position:

```python
if len(rx_buf) > 4000:
    marker = b'\x01\x0110'
    cut = rx_buf.find(marker, len(rx_buf) - 2000)
    rx_buf = rx_buf[cut:] if cut != -1 else bytearray()  # drop corrupt data; resync on next poll
```

Dropping to an empty buffer is safer than retaining garbage bytes — the sensor will be
polled again within `poll_ms`.

---

### BUG-05 — `read_thread` join timeout vs. maximum poll sleep *(Medium)*

**Line:** 463

```python
self.read_thread_obj.join(timeout=3.0)
```

The previous version used a 1 s timeout (flagged in the prior audit); the current code
uses 3 s, which is sufficient for the maximum supported `poll_ms` of 1000 ms.  However, if
a user manually edits `settings.json` and sets `poll_ms` to a value > 3000, the join can
return while the sensor thread is still sleeping, leaving a dangling thread that holds the
serial port handle open.

**Fix:** Derive the join timeout from the configured `poll_ms`:

```python
join_timeout = max(3.0, self.conn_params.get("poll_ms", 1000) / 1000.0 + 1.0)
self.read_thread_obj.join(timeout=join_timeout)
if self.read_thread_obj.is_alive():
    logging.warning("Sensor thread did not exit cleanly within timeout")
```

---

### BUG-06 — `interp_hv` reads cache globals after releasing lock *(Medium)*

**Lines:** 217–234

```python
with _interp_lock:
    if _interp_expected_version != _interp_cache_version:
        _sorted_pts_cache[:] = sorted(pts, ...)
        _sorted_pts_pressures[:] = [...]
        _interp_expected_version = _interp_cache_version
sp = _sorted_pts_cache     # ← lock already released
```

After the lock is released, the main thread can call `_rebuild_interp_cache`, which
**replaces** `_sorted_pts_cache` with a new list object (`_sorted_pts_cache = sorted(...)`).
In CPython the name binding is atomic (GIL), so `sp` captures either the old or the new
list — both are fully initialised and safe to iterate.  This is currently safe in CPython
but is undefined behaviour in PyPy or free-threaded Python 3.13+ (PEP 703).

**Fix:** Capture the reference inside the lock to be explicit and forward-compatible:

```python
with _interp_lock:
    if _interp_expected_version != _interp_cache_version:
        _sorted_pts_cache[:] = sorted(pts, key=lambda x: x["p"])
        _sorted_pts_pressures[:] = [pt["p"] for pt in _sorted_pts_cache]
        _interp_expected_version = _interp_cache_version
    sp = _sorted_pts_cache          # capture reference inside the lock
    pressures = _sorted_pts_pressures
```

---

### BUG-07 — `flush_measuring` flag read in sensor thread without synchronisation *(Low)*

**Line:** 396

```python
if self.flush_measuring:
    self.flush_vol_history.append(...)
```

`flush_measuring` is a plain `bool` set by the main thread.  Under CPython's GIL a boolean
read is atomic, so this will not crash.  However, under free-threaded CPython 3.13+ or
alternative runtimes, this is a data race.  The fix is subsumed by the `_flush_lock`
introduced to fix BUG-01.

---

### BUG-08 — `SensorApp.cleanup()` closes csv_file without `_csv_lock` *(Low)*

**Lines:** 580–584

```python
def cleanup(self):
    save_settings(...)
    self.disconnect()       # joins sensor thread (up to join_timeout seconds)
    if self.csv_file:
        self.csv_file.close()
```

`disconnect()` joins the sensor thread, so by the time `csv_file.close()` is called the
thread is guaranteed to have exited.  This is therefore safe **as long as `join()` succeeds
within the timeout** (see BUG-05).  If the join times out and the thread is still alive,
closing the file here is a race.  Adding `_csv_lock` acquisition here makes the intent
explicit and safe regardless:

```python
def cleanup(self):
    save_settings(self.conn_params, self.app_settings)
    self.disconnect()
    with self._csv_lock:
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
```

---

### BUG-09 — No input validation on calibration point duplicate pressures *(Low)*

**Lines:** 1253–1272 (`add_or_update_point`)

Duplicate pressure values are silently accepted.  In `interp_hv` a duplicate produces
`d = sp[i+1]["p"] - sp[i]["p"] == 0`, falling back to `r = 0` — i.e. the lower segment
endpoint's height/volume is returned regardless of the input pressure.  The user receives
no warning and the calibration table contains two rows with identical pressure but different
heights, making the chart misleading.

**Fix:** Before appending, check:
```python
if any(abs(pt["p"] - p) < 1e-9 for pt in clone.points
       if clone.points.index(pt) != (_cal_edit_idx[0] or -1)):
    _show_toast("⚠ Duplicate pressure value — adjust and retry.")
    return
```

---

### BUG-10 — Auto-connect failure gives no startup UI feedback *(Low)*

**Lines:** 2344–2351

```python
app.connect()
if app.is_connected:
    ...  # UI updated
# else: nothing — connection status label stays "Disconnected" (correct)
#       but app.last_error is set and never surfaced to the user
```

If the default port (e.g. COM8) does not exist at startup, `app.last_error` is populated
but the UI connection label is already initialised to "Disconnected" and never shows the
error reason.  The user must manually open the connection dialog to diagnose the issue.

**Fix:** After the auto-connect attempt, if `not app.is_connected and app.last_error`:
```python
if not app.is_connected and app.last_error:
    dpg.configure_item("lbl_conn", default_value=app.last_error[:40])
    _bind_status("lbl_conn", "theme_red")
```

---

## 3. Architectural Improvements

### 3.1 Current Architecture

The application is a **single-file procedural monolith** with three loosely-defined layers
all living in the same namespace:

```
sensor_app.py (2 362 lines)
├── Module-level constants & helpers   (lines 1–250)
├── CisternProfile — data class        (lines 122–149)
├── SensorApp — god object             (lines 253–584)
│   ├── All application state
│   ├── Serial I/O thread
│   ├── EN 14055 state machines (tick_rwl, tick_cwl_auto)
│   └── CSV logging
├── Module-level GUI callbacks (80+ functions)  (lines 586–1800)
├── Module-level GUI construction               (lines 1826–2316)
└── Main loop                                   (lines 2340–2362)
```

The `SensorApp` class is a **god object** — it owns serial I/O, data buffers, EN 14055
state machines, CSV writing, and application settings in a single mutable blob.  GUI
callbacks reference the module-level `app` singleton directly, making every function
impossible to unit-test without instantiating the entire DPG context.

### 3.2 Key Coupling Problems

| Problem | Impact |
|---|---|
| No `if __name__ == "__main__"` guard | Importing the module for testing launches the full GUI |
| `p_convert/p_format/p_parse_to_bar` call `app.app_settings` directly | Pure utilities cannot run without a live `SensorApp` |
| `_bind_status` does `setattr(app, f"_{item}_theme", …)` | Undeclared runtime attributes pollute `SensorApp` |
| All mutable globals at module scope | `_sorted_pts_cache`, `_toast_msg`, `app`, etc. — no encapsulation |
| GUI construction runs at import time | 500+ DPG calls execute immediately; cannot be deferred or mocked |

### 3.3 Recommended Module Structure

```
sensor_app/
├── __main__.py              # entry point + main loop (≤30 lines)
├── config.py                # Settings load/save, BASE_DIR, path constants
├── models.py                # CisternProfile dataclass, CalibrationPoint dataclass
├── sensor/
│   ├── protocol.py          # build_request(), packet parser
│   ├── interpolation.py     # interp_hv(), cache + lock
│   └── reader.py            # SensorReader thread (serial I/O only)
├── domain/
│   ├── smoothing.py         # smooth() — pure, stateless
│   ├── en14055.py           # RwlDetector, CwlDetector, FlushMeasurer
│   └── compliance.py        # check_compliance() — pure, returns list[CheckResult]
├── storage/
│   └── csv_logger.py        # CsvLogger with proper locking
└── ui/
    ├── app_state.py          # AppState dataclass — single UI source of truth
    ├── main_window.py        # GUI construction
    ├── callbacks.py          # DPG callbacks — thin wrappers
    └── themes.py             # Theme definitions
```

### 3.4 Immediate Wins (no full refactor needed)

1. **Add `if __name__ == "__main__":` guard** — move all GUI construction and the main
   loop inside it.  Alone this enables `import sensor_app` for testing.

2. **Pass `settings` as a parameter** to `p_convert`, `p_format`, `p_parse_to_bar` instead
   of reading the global `app`.

3. **Replace `CisternProfile` manual dict serialisation** with a `dataclasses.dataclass`
   using `dataclasses.asdict` / `dataclasses.replace` for safe cloning.

4. **Move EN 14055 state machines** (`tick_rwl`, `tick_cwl_auto`) out of `SensorApp` into
   dedicated classes with no dependency on the serial layer.

---

## 4. EN 14055 Compliance Notes

### 4.1 Correctly Implemented

- Safety margin c = OF − NWL ≥ 20 mm (§5.2.6) ✓
- MWL (fault) − OF ≤ 20 mm (§5.2.4a) ✓
- CWL − OF ≤ 10 mm (§5.2.4b) ✓
- Meniscus − OF ≤ 5 mm (§5.2.4c) ✓
- Full flush avg volume ≤ 6.0 L (§6.5) ✓
- Part flush avg volume ≤ 4.0 L (§6.5) ✓
- EN 14055 effective flow rate (skip first 1 L, last 2 L) ✓
- Automatic CWL detection (2 s window from supply cutoff) ✓
- Residual Water Level (RWL) detection after flush ✓

### 4.2 Missing or Incorrect

#### EN-01 — Flush volume compliance uses `[WARN]` not `[FAIL]` *(Medium)*

**Lines:** 901, 909 (`_check_compliance`)

```python
tag = "PASS" if avg_full <= 6.0 else "WARN"   # should be "FAIL"
tag = "PASS" if avg_part <= 4.0 else "WARN"   # should be "FAIL"
```

EN 14055 §6.5 is a **pass/fail** requirement, not an advisory warning.  A flush volume
exceeding the limit must be reported as `[FAIL]`.  Using `[WARN]` could cause an operator
to believe the cistern conditionally passes.

**Fix:** Change both `"WARN"` to `"FAIL"` for limit exceedance.

#### EN-02 — §5.2.1 test sequence not enforced *(Medium)*

EN 14055 §5.2.1 requires measurements from **three consecutive full flushes** and
**three consecutive part flushes** for a valid volume test.  The current implementation
accepts any number in any order.  Add a count check in `_check_compliance`:

```python
if len(full) < 3:
    results.append(f"[WARN] Full flush: only {len(full)}/3 required measurements recorded")
if len(part) < 3:
    results.append(f"[WARN] Part flush: only {len(part)}/3 required measurements recorded")
```

#### EN-03 — Effective flow rate silently returns None for 3–4 L flushes *(Low)*

**Lines:** 787–800

For a 3.1 L flush: `v_skip_start = start − 1.0` and `v_skip_end = end + 2.0`.  The
effective window spans only 0.1 L; `t2` may be ≤ `t1` due to sensor noise, silently
returning `en14055_rate = None` with no user explanation.  Surface this condition:

```python
if not (t1 and t2 and t2 > t1):
    # Record reason in flush result metadata
    app.flush_results[-1]["en14055_note"] = "N/A (flush too short for skip-window)"
```

#### EN-04 — Water Discharge not validated against EN 14055 *(Low)*

The `water_discharge` profile field is shown on the chart but never compared against any
EN 14055 limit in `_check_compliance`.  Either add the relevant check or document clearly
in the UI that this field is informational only.

#### EN-05 — Test temperature not recorded per measurement *(Informational)*

EN 14055 §5.1 specifies testing at 15 °C ± 5 °C.  Temperature is read from the sensor
(`current_temperature`) but is not recorded alongside individual flush results.  Log
the temperature snapshot in each `flush_results` entry so the test report is complete.

#### EN-06 — Air gap confirmation not tracked *(Informational)*

§5.2.7 requires manual verification of the air gap (OF − inlet orifice ≥ 20 mm).  The
compliance dialog correctly marks this as a manual ruler measurement, but there is no
checkbox or operator-signed acknowledgement stored in the profile or report.  Add a
boolean field `air_gap_confirmed` to the profile.

---

## 5. Code Quality Findings

### CQ-01 — No `if __name__ == "__main__"` guard *(Medium)*

All DPG context creation, theme registration, and the main loop execute at module level.
`import sensor_app` in a test runner opens a window and blocks.

### CQ-02 — Magic numbers throughout *(Low)*

Named constants should replace every bare literal with EN 14055 or protocol significance:

```python
# IFM PI1789 protocol
PRESSURE_SCALE_BAR_PER_LSB: float = 0.0001
PACKET_HEADER_SIZE: int = 12
RX_BUF_HIGH_WATERMARK: int = 4000
RX_BUF_LOW_WATERMARK: int = 2000

# EN 14055:2015
EN14055_CWL_WAIT_S: float = 2.0
EN14055_SAFETY_MARGIN_MIN_MM: float = 20.0
EN14055_MWL_MAX_ABOVE_OF_MM: float = 20.0
EN14055_CWL_MAX_ABOVE_OF_MM: float = 10.0
EN14055_MENISCUS_MAX_ABOVE_OF_MM: float = 5.0
EN14055_FULL_FLUSH_MAX_L: float = 6.0
EN14055_PART_FLUSH_MAX_L: float = 4.0
EN14055_REQUIRED_FLUSH_COUNT: int = 3
```

| Bare value | Meaning | Line(s) |
|---|---|---|
| `0.0001` | bar per raw LSB (PI1789 scale) | 370 |
| `4000` / `2000` | RX buffer watermarks | 417–419 |
| `12` | IFM packet header size | 348–356 |
| `150` | CWL/RWL history window (samples) | 1810–1811 |
| `2.0` | EN 14055 §5.2.4b wait time (s) | 513, 570, 1663 |
| `1.5` | Default CWL drop threshold (mm) | 509, 552 |
| `20` | Margin c and MWL limit (mm) | 841, 853, 986 |
| `6.0` / `4.0` | Flush volume limits (L) | 901, 909 |
| `5.0` | Flush auto-stop rise threshold (mm) | 1794 |
| `3.0` | Flush minimum duration guard (s) | 1788 |

### CQ-03 — `CisternProfile` should be a `dataclass` *(Low)*

The manual `to_dict` / `from_dict` / `clone()` pattern is error-prone when new fields are
added.  A `dataclass` with `dataclasses.asdict` and `dataclasses.replace` provides the
same functionality with compile-time field checking and IDE support.

### CQ-04 — `open()` calls missing `encoding="utf-8"` *(Low)*

**Lines:** 266, 1012, 1382, 1391, 1527, 1539, 1564

On Windows with non-UTF-8 codepage, JSON files containing Cyrillic profile names fail to
round-trip.  All text-mode `open()` calls must specify `encoding="utf-8"`.

### CQ-05 — Flow rate sign convention undocumented *(Low)*

**Lines:** 379–383

```python
f_rate = (self.v_buf[-5] - v) / dt
```

Positive `f_rate` = volume decreasing (flush/outflow).  This is counter-intuitive.
Add a comment explaining the convention, or negate to match the standard convention
(positive = inflow).

### CQ-06 — `_bind_status` adds undeclared runtime attributes to `SensorApp` *(Low)*

**Line:** 1616

```python
setattr(app, f"_{item}_theme", base_tag)
```

Undeclared attributes pollute `app`.  Replace with an explicit dict in `__init__`:
```python
self._item_themes: dict[str, str] = {}
```

### CQ-07 — Imported calibration points not validated for NaN/Inf *(Low)*

**Lines:** 1374–1404 (`_cal_import_cb`)

`float(row.get("p", 0))` can produce `nan` or `inf` from corrupted JSON.  NaN in one
calibration point silently propagates to every subsequent `interp_hv` result.  Add:
```python
import math
if not all(math.isfinite(x) for x in (p, h, v)):
    _show_toast(f"⚠ Skipping invalid point: p={p} h={h} v={v}")
    continue
```

### CQ-08 — `combo_smth` chart options and `combo_p_smth` settings options are misaligned *(Low)*

The chart toolbar offers `"EMA-Slow"` but the Program Settings CWL smooth combo does not.
If a user sets `cwl_smooth = "EMA-Slow"` in `settings.json`, the setting works but the
UI never shows it as an option.  Align both combo lists.

### CQ-09 — Compliance dialog has fixed size; will overflow with more checks *(Low)*

**Lines:** 917–918

The modal is `height=400` with no internal scroll.  Add:
```python
with dpg.child_window(height=320, parent="dlg_comply"):
    for line in results:
        dpg.add_text(line, color=col)
```

### CQ-10 — No unit tests *(Medium)*

Zero automated tests exist.  The EN 14055 boundary logic, `interp_hv`, `smooth`, and the
flush volume calculator are prime regression targets.  A minimal `pytest` suite with
parameterised boundary cases would catch threshold drift immediately.

---

## 6. Performance Issues

### PERF-01 — `list()` copies taken inside `data_lock` on every chart tick *(Low)*

**Lines:** 1695–1703 (`update_chart`)

```python
with app.data_lock:
    t_data = list(app.t_buf)        # converts 12 000-element deque to list
    raw_y  = list(app.v_buf)        # same
```

Both conversions iterate the full deque while holding `data_lock`, blocking the sensor
thread from appending new samples for the entire duration (~1–2 ms at 12 000 elements).
At 100 ms chart refresh this is a 1–2 % duty cycle block on the sensor thread.

**Fix:** Copy only the deque reference inside the lock; convert outside:

```python
with app.data_lock:
    t_snap  = app.t_buf    # deque reference — atomic in CPython
    raw_snap = app.v_buf
t_data = list(t_snap)
raw_y  = list(raw_snap)
```

(For true safety use a snapshot copy: `t_snap = collections.deque(app.t_buf)` inside the
lock — still faster than calling `list()` inside it for large deques.)

### PERF-02 — `smooth()` reallocates entire list on every chart refresh *(Low)*

**Lines:** 179–195, called from `update_chart` (every 100 ms) and `frame_callback`
(every 50 ms via `tick_cwl_auto`).

`smooth()` rebuilds the full smoothed list from scratch on every call: O(N) time and O(N)
memory allocation.  For `SMA-20` with 12 000 points this is ~96 kB allocated then GC'd
60+ times per second.

**Improvement options:**
- For the CWL detector (uses only the last 150 samples), pass only the tail slice:
  `smooth(h_history[-150:], alg)` — already partially done at line 1810, but
  `smooth` is still called with the full `h_history` slice (150 elements) every 50 ms.
- For the chart, maintain an incremental SMA ring buffer instead of a full rebuild.
- Cache the last smoothed result and only recompute the tail when new data arrives.

### PERF-03 — `update_hover_tooltip` called every frame (uncapped) *(Low)*

**Line:** 1824

```python
update_hover_tooltip()   # called every DPG frame, no rate limiting
```

`update_hover_tooltip` performs a `bisect_left` on `app.last_t` (up to 12 000 elements)
on every rendered frame (~60 fps).  At 60 fps × 12 000 elements this is ~720 000
comparisons/second for a cosmetic tooltip update.

**Fix:** Rate-limit the tooltip update to the UI refresh interval or only recalculate when
the mouse position has changed more than one pixel.

### PERF-04 — Font discovery scans filesystem at import time *(Low)*

**Lines:** 45–77

`_find_font` calls `Path.exists()` for up to 24 combinations (4 directories × 2 name
hints × 3 extensions) synchronously during module import.  On network drives or slow
storage this blocks startup.  Move font discovery to a background thread or cache the
result after first discovery.

---

## 7. Security Risks

### SEC-01 — Profile name sanitisation *(Low — mitigated)*

**Lines:** 1009, 1162, 1355

```python
safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)
fn = str(EXPORT_DIR / f"{safe_name}_{ts}.csv")
```

`re.sub(r'[^\w\-]', '_', ...)` strips most dangerous characters.  Combined with `pathlib`
path construction (which prevents directory traversal on all supported OS), this is
adequately safe for a desktop application.

However, `\w` on Windows includes Unicode word characters, and the resulting filename may
still be invalid on Windows if it exceeds 255 bytes or starts with a dot.  Add a length
guard:

```python
safe_name = re.sub(r'[^\w\-]', '_', app.profile.name)[:80]
```

### SEC-02 — Imported calibration JSON is not size-limited *(Low)*

**Lines:** 1391–1393 (`_cal_import_cb`)

```python
with open(fp) as f:
    data = json.load(f)
```

A maliciously crafted or accidentally enormous JSON file could consume all available
memory.  For a local desktop tool this risk is low, but adding a size guard is trivial:

```python
MAX_CAL_FILE_BYTES = 10 * 1024 * 1024   # 10 MB
if fp.stat().st_size > MAX_CAL_FILE_BYTES:
    _show_toast("⚠ Calibration file too large (>10 MB)")
    return
```

### SEC-03 — Settings file write is atomic *(Already Fixed — noted for completeness)*

`save_settings` writes to a `.tmp` file then calls `shutil.move` (line 115–118).  This
is an atomic rename on POSIX and effectively atomic on Windows (NTFS).  A prior version
truncated the file in-place; that race has been correctly resolved.

### SEC-04 — Serial port name passed directly to `serial.Serial` *(Informational)*

**Line:** 437

```python
self.serial_conn = serial.Serial(self.conn_params["port"], ...)
```

The port name comes from a settings file.  On POSIX, a path like `/dev/../../etc/passwd`
could theoretically be passed.  `pyserial` opens the path via the OS serial driver, which
will refuse non-device paths.  Risk is negligible for a local desktop tool but worth noting
if the settings file is ever managed externally.

### SEC-05 — Stack trace printed to console on unhandled exception *(Low)*

**Lines:** 2359–2362**

```python
except Exception as e:
    import traceback
    traceback.print_exc()
    input("Натиснете Enter за изход...")
```

`traceback.print_exc()` writes the full call stack to stdout.  In a console-attached
process this is acceptable for debugging.  In a bundled `.exe` with no visible console,
this output is lost.  Replace with logging to a crash file:

```python
except Exception as e:
    import traceback
    crash_log = BASE_DIR / "crash.log"
    with open(crash_log, "a", encoding="utf-8") as cf:
        cf.write(f"\n--- {datetime.now().isoformat()} ---\n")
        traceback.print_exc(file=cf)
    # Also show a user-friendly message via DPG or messagebox
```

---

## 8. Refactoring Plan

### Priority 1 — Critical (fix before any compliance measurement)

| ID | Action | File / Lines |
|---|---|---|
| BUG-01 | Add `_flush_lock` protecting `flush_measuring`, `flush_vol_history`, `flush_start_*` | `SensorApp.__init__`, `read_thread`, `_toggle_flush_measure` |
| BUG-02 | Acquire `_csv_lock` before closing `csv_file` in `_toggle_log` | lines 1022–1030 |
| BUG-03 | Change `">i"` → `">I"` for pressure decode; add range guard | line 369 |
| EN-01 | Change `"WARN"` → `"FAIL"` for flush volume limit exceedance | lines 901, 909 |

### Priority 2 — High (fix before production/lab use)

| ID | Action | File / Lines |
|---|---|---|
| BUG-04 | Improve RX buffer trim to scan for valid sync marker | lines 417–419 |
| BUG-05 | Derive `join_timeout` from `poll_ms` | line 463 |
| BUG-06 | Capture `_sorted_pts_cache` reference inside `_interp_lock` | lines 217–234 |
| EN-02 | Add §5.2.1 flush count check (min 3 of each type) | `_check_compliance` |
| CQ-01 | Add `if __name__ == "__main__":` guard | module level |
| CQ-04 | Add `encoding="utf-8"` to all `open()` calls | 7 locations |

### Priority 3 — Medium (improve reliability and maintainability)

| ID | Action | File / Lines |
|---|---|---|
| BUG-08 | Acquire `_csv_lock` in `cleanup()` before closing file | lines 580–584 |
| BUG-09 | Reject duplicate pressure values in `add_or_update_point` | lines 1253–1272 |
| BUG-10 | Show auto-connect error in status bar on startup failure | lines 2344–2351 |
| CQ-02 | Replace magic numbers with named constants | throughout |
| CQ-05 | Document / fix flow rate sign convention | lines 379–383 |
| CQ-07 | Validate NaN/Inf on imported calibration points | lines 1374–1404 |
| CQ-10 | Add `pytest` suite for `interp_hv`, `smooth`, EN 14055 checks | new `tests/` directory |
| SEC-05 | Write crash traceback to `crash.log` instead of stdout | lines 2359–2362 |

### Priority 4 — Nice-to-have (long-term quality)

| ID | Action |
|---|---|
| ARCH-01 | Extract `SensorReader`, `CsvLogger`, EN14055 state machines into separate modules |
| ARCH-02 | Convert `CisternProfile` to `dataclass` with typed `CalibrationPoint` |
| ARCH-03 | Pass `settings` dict as parameter to `p_format`, `p_convert`, `p_parse_to_bar` |
| CQ-03 | Use `dataclasses.replace` for calibration dialog clone |
| CQ-06 | Replace `setattr(app, …)` in `_bind_status` with `app._item_themes` dict |
| CQ-08 | Align `combo_smth` and `combo_p_smth` option lists |
| CQ-09 | Add scroll to compliance dialog |
| EN-04 | Validate / document `water_discharge` field against EN 14055 |
| EN-05 | Record temperature snapshot in each flush result |
| EN-06 | Add `air_gap_confirmed` boolean to profile + compliance dialog checkbox |
| PERF-01 | Copy deque reference, convert to list outside `data_lock` |
| PERF-02 | Incremental SMA for chart; pass tail slice to CWL detector |
| PERF-03 | Rate-limit `update_hover_tooltip` to UI refresh interval |
| SEC-01 | Add 80-char length cap to `safe_name` |
| SEC-02 | Add 10 MB file size guard on calibration import |

---

## 9. Improved Code Snippets

### 9.1 BUG-01 Fix — `_flush_lock` for flush state

```python
# In SensorApp.__init__:
self._flush_lock = threading.Lock()
self.flush_measuring = False
self.flush_start_vol = 0.0
self.flush_start_h = 0.0
self.flush_start_time = 0.0
self.flush_vol_history: list[tuple[float, float, float]] = []
self.flush_min_h = float("inf")
self.flush_rising = False
self.flush_rising_timer = 0.0

# In read_thread — sensor side:
if self.flush_measuring:          # volatile read — safe under GIL
    with self._flush_lock:
        self.flush_vol_history.append((time.time(), v, h))
        if h < self.flush_min_h:
            self.flush_min_h = h

# In _toggle_flush_measure — main thread stop path:
with app._flush_lock:
    app.flush_measuring = False
    history = list(app.flush_vol_history)   # snapshot
    # … compute delta_vol / en14055_rate from snapshot …
    app.flush_vol_history.clear()
```

### 9.2 BUG-02 Fix — CSV close sequence in `_toggle_log`

```python
def _toggle_log():
    if not app.is_logging:
        # ... open file, set is_logging = True ...
    else:
        app.is_logging = False                  # sensor thread sees False on next poll
        with app._csv_lock:                     # wait for any in-flight write to finish
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
        dpg.set_item_label("btn_log", "Start Data Log (CSV)")
        dpg.bind_item_theme("btn_log", "theme_btn_success")
```

### 9.3 BUG-03 Fix — Unsigned pressure decode with range guard

```python
# Constants (add near top of file):
PRESSURE_SCALE_BAR_PER_LSB: float = 0.0001
PRESSURE_MIN_BAR: float = 0.0
PRESSURE_MAX_BAR: float = 10.0   # PI1789 rated range

# In read_thread:
raw = struct.unpack(">I", bytes.fromhex(hx[:8]))[0]   # unsigned
p_bar = raw * PRESSURE_SCALE_BAR_PER_LSB
if not (PRESSURE_MIN_BAR <= p_bar <= PRESSURE_MAX_BAR):
    logging.debug(f"Pressure out of range ({p_bar:.4f} bar) — sample discarded")
    continue
```

### 9.4 EN-01 Fix — Correct flush compliance severity

```python
# In _check_compliance:
if full:
    avg_full = sum(r["vol"] for r in full) / len(full)
    if len(full) < EN14055_REQUIRED_FLUSH_COUNT:
        results.append(f"[WARN] Full flush: only {len(full)}/{EN14055_REQUIRED_FLUSH_COUNT} measurements")
    tag = "PASS" if avg_full <= EN14055_FULL_FLUSH_MAX_L else "FAIL"
    results.append(f"[{tag}] Full flush avg: {avg_full:.2f} L (limit {EN14055_FULL_FLUSH_MAX_L} L)")

if part:
    avg_part = sum(r["vol"] for r in part) / len(part)
    if len(part) < EN14055_REQUIRED_FLUSH_COUNT:
        results.append(f"[WARN] Part flush: only {len(part)}/{EN14055_REQUIRED_FLUSH_COUNT} measurements")
    tag = "PASS" if avg_part <= EN14055_PART_FLUSH_MAX_L else "FAIL"
    results.append(f"[{tag}] Part flush avg: {avg_part:.2f} L (limit {EN14055_PART_FLUSH_MAX_L} L)")
```

### 9.5 CQ-03 — `CisternProfile` as a `dataclass`

```python
from dataclasses import dataclass, field, asdict, replace
from typing import Any
import copy

@dataclass
class CalibrationPoint:
    p: float    # pressure (bar)
    h: float    # height (mm)
    v: float    # volume (L)

    def to_dict(self) -> dict[str, float]:
        return {"p": self.p, "h": self.h, "v": self.v}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CalibrationPoint":
        return cls(p=float(d["p"]), h=float(d["h"]), v=float(d["v"]))


@dataclass
class CisternProfile:
    name: str = "Untitled Profile"
    points: list[CalibrationPoint] = field(default_factory=list)
    mwl: float = 0.0           # NWL — Nominal Water Level
    mwl_fault: float = 0.0     # MWL — fault peak level
    meniscus: float = 0.0      # Meniscus delta above OF
    cwl: float = 0.0           # Critical Water Level (2 s after cutoff)
    overflow: float = 0.0      # Overflow level (absolute mm)
    water_discharge: float = 0.0
    residual_wl: float = 0.0   # RWL — Residual Water Level

    def clone(self) -> "CisternProfile":
        return replace(self, points=copy.deepcopy(self.points))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["points"] = [p.to_dict() for p in self.points]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CisternProfile":
        pts = [CalibrationPoint.from_dict(p) for p in d.get("points", [])]
        return cls(
            name=d.get("name", "Untitled Profile"),
            points=pts,
            mwl=d.get("mwl", 0.0),
            mwl_fault=d.get("mwl_fault", 0.0),
            meniscus=d.get("meniscus", 0.0),
            cwl=d.get("cwl", 0.0),
            overflow=d.get("overflow", 0.0),
            water_discharge=d.get("water_discharge", 0.0),
            residual_wl=d.get("residual_wl", 0.0),
        )
```

### 9.6 PERF-01 Fix — Lock-free deque snapshot

```python
def update_chart():
    # Snapshot deque references (atomic in CPython); convert outside lock
    with app.data_lock:
        t_snap   = collections.deque(app.t_buf)   # O(N) copy inside lock — unavoidable
        plot_idx = dpg.get_value("combo_plot")
        if plot_idx == "Volume (L)":
            y_snap = collections.deque(app.v_buf)
        elif plot_idx == "Flow Rate (L/s)":
            y_snap = collections.deque(app.f_buf)
        else:
            y_snap = collections.deque(app.h_buf)

    t_data = list(t_snap)
    raw_y  = list(y_snap)
    # ... rest of update_chart unchanged ...
```

---

## 10. Summary Table

All findings consolidated, ordered by severity:

| ID | Severity | Category | Lines | Issue |
|---|---|---|---|---|
| BUG-01 | **Critical** | Thread Safety | 396–398, 766–800 | `flush_vol_history` accessed without lock |
| BUG-02 | **High** | Thread Safety | 1022–1030, 402–413 | CSV file closed without `_csv_lock` |
| BUG-03 | **High** | Correctness | 369 | Signed vs. unsigned pressure decode |
| EN-01 | **Medium** | EN 14055 | 901, 909 | Flush limit uses WARN not FAIL |
| BUG-04 | **Medium** | Correctness | 417–419 | RX buffer trim can split a packet |
| BUG-05 | **Medium** | Reliability | 463 | `join()` timeout not derived from `poll_ms` |
| BUG-06 | **Medium** | Thread Safety | 217–234 | Cache reference captured outside lock |
| EN-02 | **Medium** | EN 14055 | `_check_compliance` | §5.2.1 flush count not validated |
| CQ-01 | **Medium** | Maintainability | module level | No `if __name__ == "__main__":` guard |
| CQ-10 | **Medium** | Testability | — | No unit tests |
| BUG-07 | Low | Thread Safety | 396 | `flush_measuring` bool without lock |
| BUG-08 | Low | Thread Safety | 580–584 | `cleanup()` closes CSV without lock |
| BUG-09 | Low | Correctness | 1253–1272 | Duplicate calibration pressure accepted |
| BUG-10 | Low | UX | 2344–2351 | Auto-connect failure not surfaced in UI |
| EN-03 | Low | EN 14055 | 787–800 | EN rate silently None for short flushes |
| EN-04 | Low | EN 14055 | — | Water Discharge not validated |
| EN-05 | Info | EN 14055 | — | Temperature not recorded per flush |
| EN-06 | Info | EN 14055 | — | Air gap confirmation not tracked |
| CQ-02 | Low | Readability | throughout | Magic numbers |
| CQ-03 | Low | Maintainability | 122–149 | `CisternProfile` should be a dataclass |
| CQ-04 | Low | Robustness | 7 locations | `encoding="utf-8"` missing from `open()` |
| CQ-05 | Low | Readability | 379–383 | Flow rate sign convention undocumented |
| CQ-06 | Low | Maintainability | 1616 | Ad-hoc runtime attributes on `app` |
| CQ-07 | Low | Correctness | 1374–1404 | Imported cal points not checked for NaN/Inf |
| CQ-08 | Low | UX | `combo_smth` | Chart / CWL smooth option mismatch |
| CQ-09 | Low | UX | 917–918 | Compliance dialog fixed height, no scroll |
| SEC-01 | Low | Security | 1009, 1162, 1355 | `safe_name` has no length cap |
| SEC-02 | Low | Security | 1391–1393 | No file size limit on imported JSON |
| SEC-03 | ✓ Fixed | Security | 115–118 | Atomic settings write (already correct) |
| SEC-04 | Info | Security | 437 | Serial port name from settings file |
| SEC-05 | Low | Reliability | 2359–2362 | Crash traceback goes to stdout, not log |
| PERF-01 | Low | Performance | 1695–1703 | `list()` copy inside `data_lock` |
| PERF-02 | Low | Performance | 179–195 | Full smooth rebuild on every frame |
| PERF-03 | Low | Performance | 1824 | Tooltip `bisect` on every render frame |
| PERF-04 | Low | Performance | 45–77 | Synchronous font filesystem scan at import |


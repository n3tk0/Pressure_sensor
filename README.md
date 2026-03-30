<div align="center">
  <h1>EN 14055 Cistern Analytics</h1>
  <p><b>A desktop application for testing and verifying WC flushing cistern compliance with EN 14055:2015 using an IFM PI1789 pressure sensor over IO-Link via an IFM AL1060 master.</b></p>

  [![ESP32-C3](https://img.shields.io/badge/IFM-PI1789-blue)](#)
  [![FreeRTOS](https://img.shields.io/badge/IFM-AL1060-green)](#)
  [![C++](https://img.shields.io/badge/Language-Python-00599C?logo=python)](#)
  [![SPA](https://img.shields.io/badge/Frontend-DearPyGui-F7DF1E)](#)
</div>



---

## Screenshots

### Live monitoring — flush cycle and fault test

![Main window showing live water level graph with EN14055 limit lines, left panel with live data and limit captures, and flush measurement table](screenshots/screenshot_main.png)

### EN 14055 compliance check dialog

![Compliance check dialog showing all pass/fail results for safety margin, MWL, CWL, meniscus and flush volumes](screenshots/screenshot_compliance.png)

---

## Features

### Real-time monitoring
- Live water level (mm), volume (L), pressure (bar) and flow rate (L/s)
- Configurable smoothing algorithms: None, SMA-5, SMA-10, SMA-20, Median-5
- Full scrollable graph history — pan and zoom into any past moment
- Pause freezes the entire data snapshot, not just the view window
- Dark and light theme (Catppuccin-inspired)

### EN 14055:2015 compliance testing

All seven levels are tracked with their correct height ordering:

```
MWL   (fault level, stable above OF  ≤ +20 mm)   §5.2.4a
CWL   (2 s after supply cut-off      ≤ +10 mm)   §5.2.4b
Meniscus (surface tension             ≤  +5 mm)   §5.2.4c
──── Overflow level (OF) ────
NWL   (normal fill, set by float / inlet valve)   §5.2.6
RWL   (residual after flush)
Seat  (seals minimum — V = 0 L calibration point)
```

**Automatic CWL detection** — arm while water is stable at MWL, then cut the supply. The detector finds the exact cut-off moment in the smoothed history and captures the level precisely 2 seconds later per §5.3.4.

**Automatic RWL detection** — arms when NWL is captured; detects the flush drop and captures the minimum level after a 2-second stability wait.

**EN 14055 Compliance Check dialog** reports:
- Safety margin c = OF − NWL ≥ 20 mm
- MWL − OF ≤ 20 mm
- CWL − OF ≤ 10 mm
- Meniscus − OF ≤ 5 mm
- Air gap note (ruler measurement per §5.2.7)
- Full flush ≤ 6 L, part flush ≤ 4 L

### Flush volume measurement
- Start/stop timing of full and part flush cycles
- EN 14055 V2 flow rate method: skips first 1 L and last 2 L of each flush
- Auto-stop when cistern level rises again (refill detected)
- Results table with volume, duration, average rate, and EN 14055 effective rate

### Chart
- Height (mm) or volume (L) view
- Horizontal limit lines: NWL, MWL, CWL, Meniscus, Overflow
- Customisable line colours via dialog
- Drag lines to adjust NWL and CWL while paused
- Click two points to measure the difference

### Data export
- CSV logging: timestamp, pressure, height, volume, flow rate
- Atomic file writes (no partial files on crash)

---

## Hardware

| Component | Description |
|-----------|-------------|
| IFM PI1789 | Relative pressure transmitter, 0–25 mbar, IO-Link |
| IFM AL1060 | IO-Link master, USB/RS-232 |

The sensor is mounted at the cistern base. Water height and volume are calculated from pressure via a user-defined calibration table (pressure → height → volume interpolation).

---

## Calibration

Open **Settings → Calibration Profile** and add pressure/height/volume points:

1. **Seat point** — cistern empty (seals only): measure pressure, enter height, set volume = 0.0 L.
2. **NWL point** — normal fill level (where the float closes the inlet valve): measure pressure and height, enter fill volume.
3. Additional intermediate points improve accuracy at part-fill levels.
4. Set **Overflow (mm)** — height of the overflow pipe inlet.

---

## Installation

### Run from source

```bash
pip install dearpygui pyserial
python sensor_app.py
```

### Windows EXE

Pre-built executables are in the `output/` folder or download from [GitHub Actions artifacts](../../actions/workflows/build-exe.yml).

To build locally on Windows:

```bat
pip install pyinstaller dearpygui pyserial
pyinstaller sensor_app.spec --clean --noconfirm
```

The spec file bundles the Samsung Sans fonts and the application icon automatically. The resulting EXE has no console window.

---

## Sensor connection

1. Connect the IFM AL1060 master via USB.
2. In the app: **Settings → Program Settings**, select the COM port.
3. Click **Connect Sensor**.

---

This project is maintained in free time. If it saved you development hours, consider supporting it.
<p align="center">
  <a href="https://revolut.me/petk0g">
    <img src="https://img.shields.io/badge/Support-Revolut-0666EB?style=for-the-badge&logo=revolut&logoColor=white" />
  </a>
</p>


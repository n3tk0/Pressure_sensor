"""
sensor_app_qml.py — EN 14055 Cistern Analytics — PySide6 / QML edition.

Loads all QML from Python string constants (qml_sources.py) so the entire
app is a single PyInstaller --onefile exe with no external assets required.

QML component files are written to a per-run temp directory so the QML engine
can resolve type names (e.g. LiveDataCard) by filename — the standard Qt Quick
mechanism for component discovery.

Entry point:  python sensor_app_qml.py
Build:        pyinstaller sensor_app_qml.spec
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from PySide6.QtCore import QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from sensor_bridge import SensorBridge
import qml_sources as Q


def main():
    # PySide6 requires QApplication (not QGuiApplication) when mixing
    # QML with Qt Widgets dialogs used by SensorBridge
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("EN 14055 Cistern Analytics")
    app.setOrganizationName("Cistern Analytics")

    # Create bridge first so it's available before QML loads
    bridge = SensorBridge()

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)

    # Write all QML components to a temp directory.
    # Qt Quick automatically makes every *.qml file in the same directory
    # available as a named component type — no explicit registration needed.
    tmpdir = tempfile.mkdtemp(prefix="cistern_qml_")

    components = {
        "LiveDataCard": Q.LIVE_DATA_CARD,
        "LimitsCard":   Q.LIMITS_CARD,
        "FlushCard":    Q.FLUSH_CARD,
        "LogCard":      Q.LOG_CARD,
        "SensorChart":  Q.SENSOR_CHART,
        "ActionButton": Q.ACTION_BUTTON,
        "LimitLabel":   Q.LIMIT_LABEL,
        "LimitValue":   Q.LIMIT_VALUE,
    }

    for name, src in components.items():
        with open(os.path.join(tmpdir, f"{name}.qml"), "w", encoding="utf-8") as f:
            f.write(src)

    main_path = os.path.join(tmpdir, "Main.qml")
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(Q.MAIN_QML)

    engine.load(QUrl.fromLocalFile(main_path))

    if not engine.rootObjects():
        print("Failed to load QML root object — check console for errors.")
        sys.exit(1)

    ret = app.exec()
    bridge.cleanup()

    # Remove temp QML files
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except OSError:
        pass

    sys.exit(ret)


if __name__ == "__main__":
    main()

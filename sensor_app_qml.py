"""
sensor_app_qml.py — EN 14055 Cistern Analytics — PySide6 / QML edition.

Loads all QML from Python string constants (qml_sources.py) so the entire
app is a single PyInstaller --onefile exe with no external assets required.

Entry point:  python sensor_app_qml.py
Build:        pyinstaller sensor_app_qml.spec
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QGuiApplication, QFont
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtWidgets import QApplication

from sensor_bridge import SensorBridge
import qml_sources as Q


def _load_component(engine: QQmlApplicationEngine, name: str, qml_src: str):
    """Register a QML string as a named component in the 'App' module (1.0)."""
    comp = engine.createComponent()  # type: ignore[attr-defined]
    # Use QQmlComponent via setData approach
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtCore import QByteArray
    comp = QQmlComponent(engine)
    comp.setData(QByteArray(qml_src.encode()), QUrl(f"qml/{name}.qml"))
    return comp


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

    # Expose bridge to QML root context
    engine.rootContext().setContextProperty("bridge", bridge)

    # Register all QML components as in-memory URLs.
    # Each component is set as data on a named URL so QML import resolution works.
    components = {
        "LiveDataCard":  Q.LIVE_DATA_CARD,
        "LimitsCard":    Q.LIMITS_CARD,
        "FlushCard":     Q.FLUSH_CARD,
        "LogCard":       Q.LOG_CARD,
        "SensorChart":   Q.SENSOR_CHART,
        "ActionButton":  Q.ACTION_BUTTON,
        "LimitLabel":    Q.LIMIT_LABEL,
        "LimitValue":    Q.LIMIT_VALUE,
    }

    from PySide6.QtQml import QQmlComponent
    from PySide6.QtCore import QByteArray

    # Pre-load all components into the engine's URL cache
    _comps = {}
    for name, src in components.items():
        url = QUrl(f"memory:/{name}.qml")
        comp = QQmlComponent(engine)
        comp.setData(QByteArray(src.encode()), url)
        _comps[name] = comp  # keep reference

    # Register each component URL in the QML import path via a virtual file map
    # by adding them as named import URLs in the import path
    for name in components:
        engine.addImportPath(f"memory:/")

    # Load main window from string
    main_url = QUrl("memory:/Main.qml")
    from PySide6.QtCore import QByteArray
    main_comp = QQmlComponent(engine)
    main_comp.setData(QByteArray(Q.MAIN_QML.encode()), main_url)

    if main_comp.status() == QQmlComponent.Error:
        for err in main_comp.errors():
            print(f"QML Error: {err.toString()}")
        sys.exit(1)

    obj = main_comp.create()
    if obj is None:
        print("Failed to create QML root object")
        for err in main_comp.errors():
            print(f"  {err.toString()}")
        sys.exit(1)

    # Keep python object alive
    _root = obj

    ret = app.exec()
    bridge.cleanup()
    sys.exit(ret)


if __name__ == "__main__":
    main()

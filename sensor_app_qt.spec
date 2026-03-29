# sensor_app_qt.spec — PyInstaller spec for the Qt edition of EN 14055 Cistern Analytics.
#
# Build command (from repo root):
#   pyinstaller sensor_app_qt.spec
#
# Output: dist/sensor_app_qt.exe  (single onefile executable)
#
# Requirements in the build environment:
#   pip install pyinstaller PySide6 pyqtgraph pyserial numpy

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# collect_all('PySide6') bundles DLLs + Qt plugins the standard hook misses.
# pyqtgraph: only collect binaries/datas — NOT collect_all (that pulls examples).
_ps6 = collect_all('PySide6')
_pg_datas    = collect_data_files('pyqtgraph')
_pg_binaries = []

a = Analysis(
    ['sensor_app_qt.py'],
    pathex=[str(Path('.').resolve())],
    binaries=_ps6[1] + _pg_binaries,
    datas=_ps6[0] + _pg_datas,
    hiddenimports=(
        _ps6[2]
        + [
            # pyqtgraph core — only what sensor_plot_widget.py uses
            'pyqtgraph',
            'pyqtgraph.graphicsItems.PlotDataItem',
            'pyqtgraph.graphicsItems.InfiniteLine',
            'pyqtgraph.graphicsItems.TextItem',
            'pyqtgraph.graphicsItems.LegendItem',
            'pyqtgraph.graphicsItems.ViewBox',
            'pyqtgraph.graphicsItems.AxisItem',
            'pyqtgraph.graphicsItems.PlotItem',
            'pyqtgraph.widgets.PlotWidget',
            'pyqtgraph.widgets.RemoteGraphicsView',
            'pyqtgraph.multiprocess',
            'pyqtgraph.multiprocess.remoteproxy',
            'pyqtgraph.multiprocess.processes',
            # numpy
            'numpy',
            'numpy.core._multiarray_umath',
            # pyserial
            'serial',
            'serial.tools',
            'serial.tools.list_ports',
            'serial.tools.list_ports_windows',
            'serial.tools.list_ports_posix',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # stdlib junk
        'tkinter', 'matplotlib', 'scipy', 'IPython', 'pytest', 'dearpygui',
        # pyqtgraph extras we don't use
        'pyqtgraph.examples', 'pyqtgraph.opengl',
        'pyqtgraph.dockarea', 'pyqtgraph.flowchart',
        'pyqtgraph.console',
        # NOTE: pyqtgraph.multiprocess must NOT be excluded —
        # pyqtgraph/__init__.py imports RemoteGraphicsView which depends on it.
        # Unused PySide6 modules
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
        'PySide6.QtQuickControls2', 'PySide6.QtQuickWidgets',
        'PySide6.QtQuickTest', 'PySide6.QtWebView',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtGraphs', 'PySide6.QtGraphsWidgets',
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtWebSockets', 'PySide6.QtWebChannel',
        'PySide6.QtNetwork', 'PySide6.QtNetworkAuth', 'PySide6.QtHttpServer',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtLocation', 'PySide6.QtPositioning',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
        'PySide6.QtSql', 'PySide6.QtXml',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtHelp', 'PySide6.QtDesigner', 'PySide6.QtUiTools',
        'PySide6.QtTest', 'PySide6.QtConcurrent',
        'PySide6.QtRemoteObjects', 'PySide6.QtSensors', 'PySide6.QtSerialBus',
        'PySide6.QtSerialPort', 'PySide6.QtAxContainer',
        'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech',
        'PySide6.QtSpatialAudio', 'PySide6.QtScxml',
        'PySide6.QtDBus', 'PySide6.QtCanvasPainter',
        'PySide6.scripts', 'PySide6.support',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='sensor_app_qt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        # Qt6 DLLs crash when UPX-compressed
        'Qt6Core.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll',
        'Qt6OpenGL.dll', 'Qt6OpenGLWidgets.dll',
        'shiboken6.abi3.dll',
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)

# sensor_app_qml.spec — PyInstaller spec for the QML edition.
#
# Build:
#   pyinstaller sensor_app_qml.spec
#
# Output: dist/sensor_app_qml.exe  (single onefile, ~60-90 MB)
#
# Requirements:
#   pip install pyinstaller PySide6 pyserial
#
# QML files are written to a temp directory at startup from Python strings
# in qml_sources.py — no external .qml files or Qt resource steps needed.
#
# Qt Charts is required for the SensorChart component.

block_cipher = None

from pathlib import Path

a = Analysis(
    ['sensor_app_qml.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        # PySide6 QML runtime + Qt Charts
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickControls2',
        'PySide6.QtCharts',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'PySide6.QtCore',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        # pyserial
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'serial.tools.list_ports_windows',
        'serial.tools.list_ports_posix',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev / test tools
        'tkinter', 'matplotlib', 'scipy', 'IPython',
        'pytest', 'dearpygui', 'pyqtgraph', 'numpy',
        # Unused PySide6 / Qt modules
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtAxContainer',
        'PySide6.QtBluetooth',
        'PySide6.QtConcurrent',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtLocation',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtNetworkAuth',
        'PySide6.QtNfc',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtPositioning',
        'PySide6.QtPrintSupport',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtScxml',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio',
        'PySide6.QtSql',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtTest',
        'PySide6.QtTextToSpeech',
        'PySide6.QtUiTools',
        'PySide6.QtVirtualKeyboard',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtXml',
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
    name='sensor_app_qml',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        # Qt DLLs incompatible with UPX
        'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
        'Qt6Qml.dll', 'Qt6Quick.dll', 'Qt6Charts.dll',
        'Qt6QuickControls2.dll', 'Qt6OpenGL.dll',
        'Qt6QmlModels.dll', 'Qt6QmlWorkerScript.dll',
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

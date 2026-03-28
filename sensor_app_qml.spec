# sensor_app_qml.spec — PyInstaller spec for the QML edition.
#
# Build:
#   pyinstaller sensor_app_qml.spec
#
# Output: dist/sensor_app_qml.exe  (single onefile, ~80-120 MB)
#
# Requirements:
#   pip install pyinstaller PySide6 pyserial
#
# QML files are embedded as Python strings in qml_sources.py — NO external
# .qml files or Qt resource compilation steps needed.
#
# Qt Charts is required for the SensorChart component.  Ensure PySide6 was
# installed with the Charts module:
#   pip install PySide6          # includes QtCharts on most platforms

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
        # QML plugins (platform-specific; include both to be safe)
        'PySide6.QtQuick3D',
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
        'tkinter', 'matplotlib', 'scipy', 'IPython',
        'pytest', 'dearpygui', 'pyqtgraph', 'numpy',
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
        'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
        'Qt6Qml.dll', 'Qt6Quick.dll', 'Qt6Charts.dll',
        'Qt6QuickControls2.dll',
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

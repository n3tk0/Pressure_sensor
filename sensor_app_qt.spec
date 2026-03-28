# sensor_app_qt.spec — PyInstaller spec for the Qt edition of EN 14055 Cistern Analytics.
#
# Build command (from repo root):
#   pyinstaller sensor_app_qt.spec
#
# Output: dist/sensor_app_qt.exe  (single onefile executable, ~60-100 MB)
#
# Requirements in the build environment:
#   pip install pyinstaller PySide6 pyqtgraph pyserial numpy
#
# Notes:
#   • pyqtgraph pulls in numpy; both are collected automatically.
#   • PySide6 Qt platform plugins (qwindows.dll on Windows) are bundled by the
#     PySide6 hook — no manual datas entries needed for them.
#   • sensor_core.py, sensor_plot_widget.py, dialogs_qt.py are imported normally;
#     PyInstaller follows imports automatically.
#   • The config/ and exports/ directories are created at runtime next to the .exe
#     (BASE_DIR logic in sensor_core.py handles frozen vs. script paths).

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['sensor_app_qt.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        # PySide6 platform / image plugins that the hook may miss on some builds
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'PySide6.QtCore',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        # pyqtgraph internals
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
        'pyqtgraph.graphicsItems.PlotDataItem',
        'pyqtgraph.graphicsItems.InfiniteLine',
        'pyqtgraph.graphicsItems.TextItem',
        'pyqtgraph.widgets.PlotWidget',
        # numpy (transitive dep of pyqtgraph)
        'numpy',
        'numpy.core._multiarray_umath',
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
        # Exclude heavy unused packages to keep exe size down
        'tkinter', 'matplotlib', 'scipy', 'IPython', 'pytest',
        'dearpygui',
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
    upx=True,              # compress with UPX if available (reduces size ~20%)
    upx_exclude=[
        # PySide6 DLLs are incompatible with UPX compression
        'Qt6Core.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll',
        'Qt6OpenGL.dll', 'Qt6OpenGLWidgets.dll',
    ],
    runtime_tmpdir=None,
    console=False,         # no terminal window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Optional: set icon  icon='assets/icon.ico',
    onefile=True,
)

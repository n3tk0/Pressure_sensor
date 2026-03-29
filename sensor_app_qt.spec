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
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# collect_all ensures PySide6 DLLs, plugins, and Qt resources are bundled.
# Without this, the frozen exe throws "No module named 'PySide6'" at runtime.
_ps6 = collect_all('PySide6')
_pg  = collect_all('pyqtgraph')

a = Analysis(
    ['sensor_app_qt.py'],
    pathex=[str(Path('.').resolve())],
    binaries=_ps6[1] + _pg[1],
    datas=_ps6[0] + _pg[0],
    hiddenimports=(
        _ps6[2] + _pg[2]
        + collect_submodules('PySide6')
        + [
            # pyqtgraph internals that collect_all may still miss
            'pyqtgraph.graphicsItems.PlotDataItem',
            'pyqtgraph.graphicsItems.InfiniteLine',
            'pyqtgraph.graphicsItems.TextItem',
            'pyqtgraph.widgets.PlotWidget',
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

# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for EN 14055 Cistern Analytics
# Builds a Windows GUI executable (no console window).
#
# Usage (on Windows):
#   pip install pyinstaller dearpygui pyserial
#   pyinstaller sensor_app.spec
#
# Output: dist/sensor_app.exe
 
import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
 
block_cipher = None
 
# ── Collect DearPyGui data (shaders bundled inside the package) ──────
dpg_datas = collect_data_files('dearpygui')
dpg_bins  = collect_dynamic_libs('dearpygui')
 
# ── Application fonts bundled next to the exe ────────────────────────
app_fonts = [
    (os.path.join('fonts', 'Samsung Sans Bold.ttf'),   'fonts'),
    (os.path.join('fonts', 'SamsungSans-Regular.ttf'), 'fonts'),
]
 
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=dpg_bins,
    datas=dpg_datas + app_fonts,
    hiddenimports=[
        'dearpygui',
        'dearpygui.dearpygui',
        'dearpygui._dearpygui',
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        # sensor_core and dpg_theme are imported by main.py at module level,
        # so PyInstaller traces them automatically — listed here as a safety net.
        'sensor_core',
        'dpg_theme',
        'bisect',
        'threading',
        'json',
        'csv',
        'shutil',
        'logging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL',
        'PyQt5', 'PyQt6', 'wx', 'gi',
        'unittest', 'pydoc', 'doctest',
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
    name='sensor_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
    version=None,
    uac_admin=False,
)

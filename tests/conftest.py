"""
Pytest configuration for sensor_app logic tests.

sensor_app.py is a standalone DearPyGui application with a __main__ guard that
raises ImportError when the module is imported as a library.  We use
importlib.util to execute the module source and intentionally catch that
ImportError — at that point every class, function, and constant defined before
the GUI-build block is already loaded in the module namespace.

We also inject MagicMock stubs for dearpygui, serial and serial.tools so that
the import block at the top of sensor_app.py succeeds even when those packages
are not installed in the test environment.
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── Stub heavy runtime dependencies ─────────────────────────────────────────
for _mod in (
    "dearpygui",
    "dearpygui.dearpygui",
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ── Load sensor_app up to the __main__ guard ─────────────────────────────────
_src = Path(__file__).parent.parent / "sensor_app.py"
_spec = importlib.util.spec_from_file_location("sensor_app", _src)
_module = importlib.util.module_from_spec(_spec)
sys.modules["sensor_app"] = _module
try:
    _spec.loader.exec_module(_module)
except ImportError:
    # Expected: the __main__ guard fires, but all definitions above it are loaded.
    pass

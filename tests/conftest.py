"""
Pytest configuration for sensor logic tests.

sensor_core.py is a pure-logic library with no GUI dependency — import it
directly.  We also register it as 'sensor_app' so that test_logic.py can
continue to use `import sensor_app as sa` without modification.

serial / serial.tools are stubbed because sensor_core imports them at the
top level and they are not available in the CI test environment.
"""
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

# ── Stub serial (not available in test environment) ──────────────────────────
for _mod in ("serial", "serial.tools", "serial.tools.list_ports"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ── Load sensor_core ─────────────────────────────────────────────────────────
_src = Path(__file__).parent.parent / "sensor_core.py"
_spec = importlib.util.spec_from_file_location("sensor_core", _src)
_module = importlib.util.module_from_spec(_spec)
sys.modules["sensor_core"] = _module
_spec.loader.exec_module(_module)

# Expose as 'sensor_app' so test_logic.py works without modification
sys.modules["sensor_app"] = _module

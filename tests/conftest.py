"""
Pytest configuration for sensor_core logic tests.

sensor_core.py is a pure-logic module with no GUI dependency.
We still stub out 'serial' so the import works in CI without hardware.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── Stub serial (used only for type hints in sensor_core) ───────────────────
for _mod in ("serial", "serial.tools", "serial.tools.list_ports"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ── Add project root to sys.path so sensor_core is importable ───────────────
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

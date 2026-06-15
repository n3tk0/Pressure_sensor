"""Cross-language parity: the Python EN 14055 tables must match the shared
golden file (tests/en14055_golden.csv). The Rust suite asserts the same file,
so the two implementations cannot drift apart silently."""
from pathlib import Path

import sensor_core as sa

GOLDEN = Path(__file__).parent / "en14055_golden.csv"


def _golden_rows():
    for raw in GOLDEN.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, cls, nom, part, a, b = line.split(",")
        yield kind, int(cls), float(nom), bool(int(part)), float(a), float(b)


def test_python_matches_golden():
    rows = list(_golden_rows())
    assert rows, "golden file is empty"
    for kind, cls, nom, is_part, a, b in rows:
        if kind == "limits":
            mn, mx = sa.get_en14055_volume_limits(nom, cls, is_part_flush=is_part)
            assert (round(mn, 2), round(mx, 2)) == (round(a, 2), round(b, 2)), \
                f"limits class={cls} nom={nom} part={is_part}: got ({mn},{mx}) want ({a},{b})"
        elif kind == "skip":
            v1, v3 = sa.get_en14055_skip_volumes(nom, cls, is_part)
            assert (round(v1, 2), round(v3, 2)) == (round(a, 2), round(b, 2)), \
                f"skip class={cls} nom={nom} part={is_part}: got ({v1},{v3}) want ({a},{b})"
        else:
            raise AssertionError(f"unknown row kind: {kind}")

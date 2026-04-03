"""
Unit tests for pure logic in sensor_core.py.

Coverage:
  - smooth()                — SMA, EMA, DEMA, Median, Kalman, Savitzky-Golay, None, edge cases
  - interp_hv()             — interpolation, extrapolation, edge cases
  - p_convert / p_format / p_parse_to_bar — pressure unit round-trips
  - CalibrationPoint        — dataclass construction
  - CisternProfile          — to_dict / from_dict / clone round-trips
  - EN 14055 arithmetic     — safety margin, MWL, CWL, meniscus, air gap thresholds
  - Flush compliance        — PASS/FAIL volume thresholds, count checks
  - run_compliance_checks() — full output tag verification
  - _decode_sensor_status() — PI1789 status byte decoding
  - tick_cwl_auto()         — CWL state machine transitions
"""
import math
import time
import pytest
import sensor_core as sa


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_pt(p, h, v):
    """Shorthand for CalibrationPoint."""
    return sa.CalibrationPoint(p=p, h=h, v=v)


def fresh_cache(pts):
    """Rebuild the interpolation cache with the given CalibrationPoint list."""
    sa._rebuild_interp_cache(pts)


def make_flush(type_str, vol, time_s=10.0, en14055_rate=None, temp_c=None):
    """Shorthand for a flush result dict."""
    return {"type": type_str, "vol": vol, "time": time_s,
            "en14055_rate": en14055_rate, "temp_c": temp_c}


# ─────────────────────────────────────────────────────────────────────────────
# smooth()
# ─────────────────────────────────────────────────────────────────────────────

class TestSmooth:
    def test_none_returns_copy(self):
        data = [1.0, 2.0, 3.0]
        result = sa.smooth(data, "None")
        assert result == data
        assert result is not data  # must be a new list

    def test_single_element_returns_unchanged(self):
        assert sa.smooth([42.0], "SMA-3") == [42.0]

    def test_empty_list_returns_empty(self):
        assert sa.smooth([], "SMA-3") == []

    def test_sma3_basic(self):
        # First element: just itself; second: avg of 2; third+: avg of 3
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = sa.smooth(data, "SMA-3")
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(1.5)
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_sma5_fewer_than_window(self):
        # With only 2 elements, SMA-5 should still produce values
        data = [2.0, 4.0]
        result = sa.smooth(data, "SMA-5")
        assert result[0] == pytest.approx(2.0)
        assert result[1] == pytest.approx(3.0)

    def test_sma_length_preserved(self):
        data = list(range(20))
        result = sa.smooth(data, "SMA-5")
        assert len(result) == 20

    def test_ema_fast_starts_at_first(self):
        data = [10.0, 10.0, 10.0]
        result = sa.smooth(data, "EMA-Fast")
        assert result[0] == pytest.approx(10.0)
        # Steady input → output stays constant
        assert result[-1] == pytest.approx(10.0)

    def test_ema_slow_starts_at_first(self):
        data = [5.0, 5.0, 5.0, 5.0]
        result = sa.smooth(data, "EMA-Slow")
        assert result[0] == pytest.approx(5.0)
        assert result[-1] == pytest.approx(5.0)

    def test_ema_fast_converges_faster_than_slow(self):
        # Step from 0 to 100 — "Fast" (α=0.2) should be closer to 100 than "Slow" (α=0.05)
        data = [0.0] + [100.0] * 50
        fast = sa.smooth(data, "EMA-Fast")
        slow = sa.smooth(data, "EMA-Slow")
        assert fast[-1] > slow[-1]

    def test_unknown_alg_returns_list_copy(self):
        data = [3.0, 1.0, 4.0]
        result = sa.smooth(data, "BOGUS")
        assert result == data


# ─────────────────────────────────────────────────────────────────────────────
# interp_hv()
# ─────────────────────────────────────────────────────────────────────────────

class TestInterpHV:
    def test_empty_points_returns_zeros(self):
        h, v = sa.interp_hv(0.5, [])
        assert h == 0.0 and v == 0.0

    def test_single_point_returns_that_point(self):
        pts = [make_pt(p=1.0, h=100.0, v=5.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(1.0, pts)
        assert h == pytest.approx(100.0)
        assert v == pytest.approx(5.0)

    def test_single_point_extrapolation_below(self):
        pts = [make_pt(p=1.0, h=100.0, v=5.0)]
        fresh_cache(pts)
        # Only one point — no second point to extrapolate with, returns the single value
        h, v = sa.interp_hv(0.5, pts)
        assert h == pytest.approx(100.0)
        assert v == pytest.approx(5.0)

    def test_two_points_midpoint(self):
        pts = [make_pt(p=0.0, h=0.0, v=0.0),
               make_pt(p=2.0, h=200.0, v=10.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(1.0, pts)
        assert h == pytest.approx(100.0)
        assert v == pytest.approx(5.0)

    def test_exact_pressure_match_lower(self):
        pts = [make_pt(p=0.5, h=50.0, v=2.0),
               make_pt(p=1.5, h=150.0, v=8.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(0.5, pts)
        assert h == pytest.approx(50.0)
        assert v == pytest.approx(2.0)

    def test_exact_pressure_match_upper(self):
        pts = [make_pt(p=0.5, h=50.0, v=2.0),
               make_pt(p=1.5, h=150.0, v=8.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(1.5, pts)
        assert h == pytest.approx(150.0)
        assert v == pytest.approx(8.0)

    def test_extrapolation_below_first_point(self):
        pts = [make_pt(p=1.0, h=100.0, v=5.0),
               make_pt(p=2.0, h=200.0, v=10.0)]
        fresh_cache(pts)
        # p=0.5 is below the first point → extrapolate using first two points
        h, v = sa.interp_hv(0.5, pts)
        # slope: dh/dp = 100/1 = 100, so h = 100 + (0.5-1.0)*100 = 50
        assert h == pytest.approx(50.0)
        assert v == pytest.approx(2.5)

    def test_extrapolation_above_last_point(self):
        pts = [make_pt(p=1.0, h=100.0, v=5.0),
               make_pt(p=2.0, h=200.0, v=10.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(3.0, pts)
        # slope: dh/dp = 100, so h = 200 + (3.0-2.0)*100 = 300
        assert h == pytest.approx(300.0)
        assert v == pytest.approx(15.0)

    def test_unsorted_input_sorted_by_cache(self):
        # Points supplied out of order — interp_hv must sort them
        pts = [make_pt(p=2.0, h=200.0, v=10.0),
               make_pt(p=0.0, h=0.0, v=0.0),
               make_pt(p=1.0, h=100.0, v=5.0)]
        fresh_cache(pts)
        h, v = sa.interp_hv(1.0, pts)
        assert h == pytest.approx(100.0)
        assert v == pytest.approx(5.0)

    def test_three_point_inner_segment(self):
        pts = [make_pt(p=0.0, h=0.0, v=0.0),
               make_pt(p=1.0, h=100.0, v=5.0),
               make_pt(p=2.0, h=180.0, v=9.0)]
        fresh_cache(pts)
        # Between point 1 and 2: p=1.5 → h = 100 + 0.5*(180-100) = 140
        h, v = sa.interp_hv(1.5, pts)
        assert h == pytest.approx(140.0)
        assert v == pytest.approx(7.0)

    def test_zero_pressure_segment_no_division_by_zero(self):
        # Two points at the same pressure — segment width = 0.
        # interp_hv guards against this with `if d else 0`.
        pts = [make_pt(p=1.0, h=100.0, v=5.0),
               make_pt(p=1.0, h=200.0, v=10.0)]
        fresh_cache(pts)
        # Should not raise; exact result is implementation-defined but finite
        h, v = sa.interp_hv(1.0, pts)
        assert math.isfinite(h) and math.isfinite(v)


# ─────────────────────────────────────────────────────────────────────────────
# Pressure unit conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestPressureConversion:
    @pytest.mark.parametrize("unit, factor", [
        ("bar",  1.0),
        ("mbar", 1000.0),
        ("kPa",  100.0),
    ])
    def test_p_convert(self, unit, factor):
        assert sa.p_convert(1.0, unit=unit) == pytest.approx(factor)

    @pytest.mark.parametrize("unit", ["bar", "mbar", "kPa"])
    def test_round_trip_parse_format(self, unit):
        original_bar = 0.3456
        formatted = sa.p_format(original_bar, unit=unit)
        # p_format returns "value unit", e.g. "34.56 mbar"; extract the number part
        number_str = formatted.split()[0]
        recovered = sa.p_parse_to_bar(number_str, unit=unit)
        assert recovered == pytest.approx(original_bar, rel=1e-4)

    def test_p_format_bar_decimals(self):
        s = sa.p_format(0.1234, unit="bar")
        assert "0.1234" in s
        assert "bar" in s

    def test_p_format_mbar_decimals(self):
        s = sa.p_format(0.1, unit="mbar")
        assert "100.0" in s
        assert "mbar" in s

    def test_p_parse_comma_decimal_separator(self):
        # European locale uses comma; p_parse_to_bar must handle it
        result = sa.p_parse_to_bar("1,5", unit="bar")
        assert result == pytest.approx(1.5)

    def test_unknown_unit_falls_back_to_bar(self):
        assert sa.p_convert(2.5, unit="unknown") == pytest.approx(2.5)


# ─────────────────────────────────────────────────────────────────────────────
# CalibrationPoint dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestCalibrationPoint:
    def test_construction(self):
        pt = sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)
        assert pt.p == 1.0
        assert pt.h == 100.0
        assert pt.v == 5.0

    def test_equality(self):
        a = sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)
        b = sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)
        assert a == b

    def test_inequality(self):
        a = sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)
        b = sa.CalibrationPoint(p=2.0, h=100.0, v=5.0)
        assert a != b


# ─────────────────────────────────────────────────────────────────────────────
# CisternProfile dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestCisternProfile:
    def test_default_construction(self):
        p = sa.CisternProfile()
        assert p.name == "Untitled Profile"
        assert p.points == []
        assert p.mwl == 0.0
        assert p.overflow == 0.0

    def test_to_dict_no_points(self):
        p = sa.CisternProfile(name="Test", overflow=250.0)
        d = p.to_dict()
        assert d["name"] == "Test"
        assert d["overflow"] == 250.0
        assert d["points"] == []

    def test_to_dict_with_points(self):
        p = sa.CisternProfile()
        p.points = [sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)]
        d = p.to_dict()
        assert len(d["points"]) == 1
        assert d["points"][0] == {"p": 1.0, "h": 100.0, "v": 5.0}

    def test_from_dict_round_trip(self):
        original = sa.CisternProfile(
            name="Cistern A",
            overflow=250.0,
            mwl=200.0,
            cwl=255.0,
            water_discharge=300.0,
        )
        original.points = [sa.CalibrationPoint(p=0.5, h=50.0, v=2.0),
                           sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)]
        d = original.to_dict()
        restored = sa.CisternProfile.from_dict(d)
        assert restored.name == "Cistern A"
        assert restored.overflow == pytest.approx(250.0)
        assert restored.mwl == pytest.approx(200.0)
        assert restored.cwl == pytest.approx(255.0)
        assert restored.water_discharge == pytest.approx(300.0)
        assert len(restored.points) == 2
        assert restored.points[0].p == pytest.approx(0.5)
        assert restored.points[1].h == pytest.approx(100.0)

    def test_from_dict_missing_keys_use_defaults(self):
        p = sa.CisternProfile.from_dict({})
        assert p.name == "Untitled Profile"
        assert p.points == []
        assert p.overflow == 0.0

    def test_clone_is_independent(self):
        original = sa.CisternProfile(name="Original", overflow=200.0)
        original.points = [sa.CalibrationPoint(p=1.0, h=100.0, v=5.0)]
        cloned = original.clone()

        # Mutate clone — original must be unchanged
        cloned.name = "Modified"
        cloned.points.append(sa.CalibrationPoint(p=2.0, h=200.0, v=10.0))
        cloned.points[0] = sa.CalibrationPoint(p=99.0, h=99.0, v=99.0)

        assert original.name == "Original"
        assert len(original.points) == 1
        assert original.points[0].p == pytest.approx(1.0)

    def test_clone_copies_float_fields(self):
        original = sa.CisternProfile(mwl=150.0, cwl=260.0, meniscus=3.0)
        cloned = original.clone()
        assert cloned.mwl == pytest.approx(150.0)
        assert cloned.cwl == pytest.approx(260.0)
        assert cloned.meniscus == pytest.approx(3.0)


# ─────────────────────────────────────────────────────────────────────────────
# EN 14055 arithmetic boundary conditions
# ─────────────────────────────────────────────────────────────────────────────
# These test the formulas directly without invoking the DPG dialog.

class TestEN14055Arithmetic:
    """Verify boundary conditions for the six EN 14055 checks."""

    # ── Safety margin c = OF − NWL ≥ 20 mm (§5.2.6) ─────────────────────────
    @pytest.mark.parametrize("overflow, nwl, expected_pass", [
        (250.0, 230.0, True),   # exactly 20 mm → PASS
        (250.0, 231.0, False),  # 19 mm → FAIL
        (250.0, 200.0, True),   # 50 mm → PASS
        (250.0, 250.0, False),  # 0 mm → FAIL
    ])
    def test_safety_margin(self, overflow, nwl, expected_pass):
        sm = overflow - nwl
        passes = sm >= sa.EN14055_SAFETY_MARGIN_MIN_MM
        assert passes == expected_pass

    # ── MWL fault − OF ≤ 20 mm (§5.2.4a) ────────────────────────────────────
    @pytest.mark.parametrize("mwl_fault, overflow, expected_pass", [
        (270.0, 250.0, True),   # +20 mm → PASS
        (271.0, 250.0, False),  # +21 mm → FAIL
        (250.0, 250.0, True),   # 0 mm above → PASS
    ])
    def test_mwl_fault(self, mwl_fault, overflow, expected_pass):
        diff = mwl_fault - overflow
        passes = diff <= sa.EN14055_MWL_MAX_ABOVE_OF_MM
        assert passes == expected_pass

    # ── CWL − OF ≤ 10 mm (§5.2.4b) ──────────────────────────────────────────
    @pytest.mark.parametrize("cwl, overflow, expected_pass", [
        (260.0, 250.0, True),   # +10 mm → PASS
        (261.0, 250.0, False),  # +11 mm → FAIL
        (250.0, 250.0, True),   # 0 mm → PASS
    ])
    def test_cwl(self, cwl, overflow, expected_pass):
        diff = cwl - overflow
        passes = diff <= sa.EN14055_CWL_MAX_ABOVE_OF_MM
        assert passes == expected_pass

    # ── Meniscus − OF ≤ 5 mm (§5.2.4c) ──────────────────────────────────────
    @pytest.mark.parametrize("meniscus_delta, expected_pass", [
        (5.0, True),    # exactly 5 → PASS
        (5.1, False),   # 5.1 → FAIL
        (0.0, True),    # 0 (at overflow lip) → PASS
        (-1.0, False),  # below overflow → WARN/FAIL
    ])
    def test_meniscus(self, meniscus_delta, expected_pass):
        passes = 0 <= meniscus_delta <= sa.EN14055_MENISCUS_MAX_ABOVE_OF_MM
        assert passes == expected_pass

    # ── Air gap a = water_discharge − CWL ≥ 20 mm (§5.2.7) ──────────────────
    @pytest.mark.parametrize("water_discharge, cwl, expected_pass", [
        (300.0, 280.0, True),   # 20 mm → PASS
        (300.0, 281.0, False),  # 19 mm → FAIL
        (300.0, 260.0, True),   # 40 mm → PASS
    ])
    def test_air_gap(self, water_discharge, cwl, expected_pass):
        air_gap = water_discharge - cwl
        passes = air_gap >= sa.EN14055_SAFETY_MARGIN_MIN_MM
        assert passes == expected_pass


# ─────────────────────────────────────────────────────────────────────────────
# Flush volume compliance
# ─────────────────────────────────────────────────────────────────────────────

class TestFlushCompliance:
    """Test PASS/FAIL volume thresholds and count requirements."""

    @pytest.mark.parametrize("vol, expected_pass", [
        (6.0, True),    # exactly at limit → PASS
        (6.01, False),  # just over → FAIL
        (3.5, True),    # well under → PASS
    ])
    def test_full_flush_volume_threshold(self, vol, expected_pass):
        passes = vol <= sa.EN14055_FULL_FLUSH_MAX_L
        assert passes == expected_pass

    @pytest.mark.parametrize("vol, expected_pass", [
        (4.0, True),
        (4.01, False),
        (2.0, True),
    ])
    def test_part_flush_volume_threshold(self, vol, expected_pass):
        passes = vol <= sa.EN14055_PART_FLUSH_MAX_L
        assert passes == expected_pass

    def test_required_flush_count_constant(self):
        assert sa.EN14055_REQUIRED_FLUSH_COUNT == 3

    @pytest.mark.parametrize("count, expected_warn", [
        (3, False),   # exactly 3 → no warning
        (2, True),    # only 2 → warn
        (1, True),    # only 1 → warn
    ])
    def test_flush_count_warning(self, count, expected_warn):
        warns = count < sa.EN14055_REQUIRED_FLUSH_COUNT
        assert warns == expected_warn

    def test_full_flush_avg_passes_at_boundary(self):
        volumes = [6.0, 6.0, 6.0]
        avg = sum(volumes) / len(volumes)
        assert avg <= sa.EN14055_FULL_FLUSH_MAX_L

    def test_full_flush_avg_fails_over_boundary(self):
        volumes = [6.0, 6.0, 6.1]
        avg = sum(volumes) / len(volumes)
        assert avg > sa.EN14055_FULL_FLUSH_MAX_L


# ─────────────────────────────────────────────────────────────────────────────
# smooth() — extended algorithms
# ─────────────────────────────────────────────────────────────────────────────

class TestSmoothExtended:
    def test_dema_length_preserved(self):
        data = [float(i) for i in range(20)]
        result = sa.smooth(data, "DEMA")
        assert len(result) == 20

    def test_dema_steady_state(self):
        data = [10.0] * 30
        result = sa.smooth(data, "DEMA")
        assert result[-1] == pytest.approx(10.0, abs=1e-6)

    def test_median5_length_preserved(self):
        data = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0]
        result = sa.smooth(data, "Median")
        assert len(result) == 7

    def test_median5_removes_spike(self):
        # A single spike in the middle should be removed
        data = [5.0, 5.0, 100.0, 5.0, 5.0]
        result = sa.smooth(data, "Median")
        assert result[2] == pytest.approx(5.0)

    def test_kalman_length_preserved(self):
        data = [float(i) for i in range(10)]
        result = sa.smooth(data, "Kalman")
        assert len(result) == 10

    def test_kalman_first_element_unchanged(self):
        data = [42.0] + [0.0] * 9
        result = sa.smooth(data, "Kalman")
        assert result[0] == pytest.approx(42.0)

    def test_savitzky_golay_length_preserved(self):
        data = [float(i) for i in range(20)]
        result = sa.smooth(data, "Savitzky-Golay")
        assert len(result) == 20

    def test_savitzky_golay_flat_signal_unchanged(self):
        data = [7.0] * 20
        result = sa.smooth(data, "Savitzky-Golay")
        for v in result:
            assert v == pytest.approx(7.0, abs=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# _decode_sensor_status()
# ─────────────────────────────────────────────────────────────────────────────

class TestDecodeSensorStatus:
    def test_all_bits_set_is_ok(self):
        label, ok = sa._decode_sensor_status(0xFF)
        assert label == "OK"
        assert ok is True

    def test_ready_bit_clear_is_fault(self):
        # Bit 7 (0x80) clear → FAULT
        label, ok = sa._decode_sensor_status(0x7F)
        assert label == "FAULT"
        assert ok is False

    def test_overrange_bit_clear(self):
        # Bit 6 (0x40) clear (but bit 7 set) → Over-range
        label, ok = sa._decode_sensor_status(0xBF)
        assert label == "Over-range"
        assert ok is False

    def test_underrange_bit_clear(self):
        # Bit 5 (0x20) clear (bits 7+6 set) → Under-range
        label, ok = sa._decode_sensor_status(0xDF)
        assert label == "Under-range"
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# run_compliance_checks()
# ─────────────────────────────────────────────────────────────────────────────

class TestRunComplianceChecks:
    def _make_profile(self, **kwargs):
        p = sa.CisternProfile()
        for k, v in kwargs.items():
            setattr(p, k, v)
        return p

    def test_empty_flushes_reports_no_measurements(self):
        p = self._make_profile()
        results, _ = sa.run_compliance_checks(p, [])
        assert any("no measurements" in r for r in results)

    def test_safety_margin_pass(self):
        p = self._make_profile(overflow=250.0, mwl=220.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[PASS]" in r and "Safety margin" in r for r in results)

    def test_safety_margin_fail(self):
        p = self._make_profile(overflow=250.0, mwl=235.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[FAIL]" in r and "Safety margin" in r for r in results)

    def test_mwl_fault_pass(self):
        p = self._make_profile(overflow=250.0, mwl_fault=265.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[PASS]" in r and "MWL fault" in r for r in results)

    def test_mwl_fault_fail(self):
        p = self._make_profile(overflow=250.0, mwl_fault=275.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[FAIL]" in r and "MWL fault" in r for r in results)

    def test_cwl_pass(self):
        p = self._make_profile(overflow=250.0, cwl=258.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[PASS]" in r and "CWL" in r for r in results)

    def test_cwl_fail(self):
        p = self._make_profile(overflow=250.0, cwl=265.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[FAIL]" in r and "CWL" in r for r in results)

    def test_meniscus_pass(self):
        p = self._make_profile(meniscus=3.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[PASS]" in r and "Meniscus" in r for r in results)

    def test_meniscus_fail(self):
        p = self._make_profile(meniscus=6.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[FAIL]" in r and "Meniscus" in r for r in results)

    def test_air_gap_pass(self):
        p = self._make_profile(water_discharge=300.0, cwl=275.0)
        results, air_gap = sa.run_compliance_checks(p, [])
        assert any("[PASS]" in r and "Air gap" in r for r in results)
        assert air_gap == pytest.approx(25.0)

    def test_air_gap_fail(self):
        p = self._make_profile(water_discharge=300.0, cwl=285.0)
        results, air_gap = sa.run_compliance_checks(p, [])
        assert any("[FAIL]" in r and "Air gap" in r for r in results)
        assert air_gap == pytest.approx(15.0)

    def test_full_flush_pass(self):
        p = self._make_profile()
        flushes = [make_flush("Full", 5.5) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[PASS]" in r and "Full flush" in r for r in results)

    def test_full_flush_fail(self):
        p = self._make_profile()
        flushes = [make_flush("Full", 6.5) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[FAIL]" in r and "Full flush" in r for r in results)

    def test_part_flush_pass(self):
        p = self._make_profile()
        flushes = [make_flush("Part", 3.5) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[PASS]" in r and "Part flush" in r for r in results)

    def test_part_flush_fail(self):
        p = self._make_profile()
        flushes = [make_flush("Part", 4.5) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[FAIL]" in r and "Part flush" in r for r in results)

    def test_warns_fewer_than_3_full_flushes(self):
        p = self._make_profile()
        flushes = [make_flush("Full", 5.0), make_flush("Full", 5.0)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[WARN]" in r and "Full flush" in r for r in results)

    def test_warns_fewer_than_3_part_flushes(self):
        p = self._make_profile()
        flushes = [make_flush("Part", 3.0)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[WARN]" in r and "Part flush" in r for r in results)

    def test_residual_wl_info(self):
        p = self._make_profile(residual_wl=42.0)
        results, _ = sa.run_compliance_checks(p, [])
        assert any("[INFO]" in r and "Residual WL" in r for r in results)

    def test_en14055_flow_rate_info(self):
        p = self._make_profile()
        flushes = [make_flush("Full", 5.5, en14055_rate=0.35) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[INFO]" in r and "flow rate" in r.lower() for r in results)

    def test_water_temp_info(self):
        p = self._make_profile()
        flushes = [make_flush("Full", 5.5, temp_c=18.0) for _ in range(3)]
        results, _ = sa.run_compliance_checks(p, flushes)
        assert any("[INFO]" in r and "temp" in r.lower() for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# tick_cwl_auto() — CWL state machine
# ─────────────────────────────────────────────────────────────────────────────

class TestTickCwlAuto:
    def _make_app(self):
        app = sa.SensorApp()
        # Disable smoothing so peak/drop arithmetic uses raw values
        app.app_settings["cwl_smooth"] = "None"
        return app

    def test_idle_state_no_action(self):
        app = self._make_app()
        app.cwl_auto_state = "IDLE"
        result = app.tick_cwl_auto(100.0, [100.0] * 10, list(range(10)))
        assert result is False
        assert app.cwl_auto_state == "IDLE"

    def test_armed_tracks_peak(self):
        app = self._make_app()
        app.cwl_auto_state = "ARMED"
        app.cwl_auto_peak = 100.0
        # Feed a height higher than peak
        app.tick_cwl_auto(110.0, [100.0, 105.0, 110.0], [0.0, 0.5, 1.0])
        assert app.cwl_auto_peak == pytest.approx(110.0)

    def test_armed_transitions_to_waiting_after_drop(self):
        app = self._make_app()
        app.cwl_auto_state = "ARMED"
        app.cwl_auto_peak = 110.0
        # Feed height that dropped 1.5 mm below peak
        h_history = [110.0] * 8 + [108.5, 108.5]
        t_history = [float(i) * 0.1 for i in range(10)]
        app.tick_cwl_auto(108.5, h_history, t_history)
        assert app.cwl_auto_state == "WAITING"

    def test_waiting_captures_cwl_after_2s(self):
        app = self._make_app()
        app.cwl_auto_state = "WAITING"
        # Set timer 3 seconds in the past so 2s have elapsed
        app.cwl_auto_timer = time.time() - 3.0
        # Populate height buffer so get_avg_height can return something
        app.t_buf.extend([0.0, 0.1])
        app.h_buf.extend([105.0, 106.0])
        result = app.tick_cwl_auto(106.0, [106.0] * 5, [0.0] * 5)
        assert result is True
        assert app.cwl_auto_state == "DONE"
        assert app.profile.cwl > 0.0

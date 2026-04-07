use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CalibrationPoint {
    pub p: f64, // Pressure (bar)
    pub h: f64, // Height (mm)
    pub v: f64, // Volume (L)
}

#[derive(Clone, Default, Serialize, Deserialize)]
pub struct CisternProfile {
    pub name: String,
    pub points: Vec<CalibrationPoint>,
    pub mwl: f64,
    pub mwl_fault: f64,
    pub meniscus: f64,
    pub cwl: f64,
    pub overflow: f64,
    pub water_discharge: f64,
    pub residual_wl: f64,
}

impl CisternProfile {
    pub fn sort_points(&mut self) {
        self.points.sort_by(|a, b| a.p.total_cmp(&b.p));
    }

    /// Linear interpolation returning (height_mm, volume_l) for the given pressure.
    pub fn interp_hv(&self, p_bar: f64) -> (f64, f64) {
        let pts = &self.points;
        if pts.is_empty() { return (0.0, 0.0); }
        if pts.len() == 1 { return (pts[0].h, pts[0].v); }

        let first = &pts[0];
        if p_bar <= first.p {
            let next = &pts[1];
            let d = next.p - first.p;
            let r = if d > 0.0 { (p_bar - first.p) / d } else { 0.0 };
            return (first.h + r * (next.h - first.h), first.v + r * (next.v - first.v));
        }

        let last_idx = pts.len() - 1;
        let last = &pts[last_idx];
        if p_bar >= last.p {
            let prev = &pts[last_idx - 1];
            let d = last.p - prev.p;
            let r = if d > 0.0 { (p_bar - prev.p) / d } else { 0.0 };
            return (prev.h + r * (last.h - prev.h), prev.v + r * (last.v - prev.v));
        }

        let i = pts.partition_point(|pt| pt.p <= p_bar).saturating_sub(1);
        let ptr_c = &pts[i];
        let ptr_n = &pts[i + 1];
        let d = ptr_n.p - ptr_c.p;
        let r = if d > 0.0 { (p_bar - ptr_c.p) / d } else { 0.0 };
        (ptr_c.h + r * (ptr_n.h - ptr_c.h), ptr_c.v + r * (ptr_n.v - ptr_c.v))
    }
}

/// EN 14055 cistern classification.
#[derive(PartialEq, Clone, Copy, Debug, Default, Serialize, Deserialize)]
pub enum CisternClass {
    Class1,
    #[default]
    Class2,
}

/// Specific type/volume variant — Class 1 types from §4, Class 2 volumes from §4.2.
#[derive(PartialEq, Clone, Copy, Debug, Default, Serialize, Deserialize)]
pub enum CisternTypeVariant {
    // ── Class 1 ─────────────────────────────────────────────────────
    Type6,   // Full: 6.0–6.5 L  | Part: 3.0–4.0 L
    Type5,   // Full: 4.5–5.5 L  | Part: 3.0–4.0 L
    Type4,   // Full: 4.0–4.5 L  | Part: 2.0–3.0 L
    // ── Class 2 ─────────────────────────────────────────────────────
    #[default]
    Max6_0,  // Full: ≤ 6.0 L    | Part: ≤ 2/3 of full flush
    L4_5,    // Full: 4.15–4.85 L (4.5 ± 0.35) | Part: ≤ 2/3 of full flush
    L4_0,    // Full: 3.70–4.30 L (4.0 ± 0.30) | Part: ≤ 2/3 of full flush
}

#[derive(Clone, Debug)]
pub struct FlushResult {
    pub is_full: bool,
    pub vol_l: f64,
    pub time_s: f64,
    /// EN 14055 flow rate: volume excluding first 1 L and last 2 L, divided by time.
    pub en14055_rate: Option<f64>,
    /// Water temperature at flush time (°C).
    pub temp_c: Option<f64>,
    /// Result of EN 14055 volume compliance check at time of recording.
    pub compliance_pass: Option<bool>,
}

/// Validate a single flush measurement against EN 14055 volume limits.
///
/// * `class`             – cistern classification (Class1 / Class2)
/// * `variant`           – specific type/volume variant for that class
/// * `is_part_flush`     – true if this is a part flush
/// * `measured_vol_l`    – measured flush volume in litres
/// * `last_full_vol_l`   – most-recent full-flush volume (Class 2 needs it for the 2/3 part rule)
pub fn validate_flush(
    class: CisternClass,
    variant: CisternTypeVariant,
    is_part_flush: bool,
    measured_vol_l: f64,
    last_full_vol_l: Option<f64>,
) -> bool {
    match class {
        // ── Class 1: fixed absolute ranges from EN 14055 §4 ──────────
        CisternClass::Class1 => match (variant, is_part_flush) {
            (CisternTypeVariant::Type6, false) => (6.0..=6.5).contains(&measured_vol_l),
            (CisternTypeVariant::Type6, true)  => (3.0..=4.0).contains(&measured_vol_l),
            (CisternTypeVariant::Type5, false) => (4.5..=5.5).contains(&measured_vol_l),
            (CisternTypeVariant::Type5, true)  => (3.0..=4.0).contains(&measured_vol_l),
            (CisternTypeVariant::Type4, false) => (4.0..=4.5).contains(&measured_vol_l),
            (CisternTypeVariant::Type4, true)  => (2.0..=3.0).contains(&measured_vol_l),
            _ => false, // Class 2 variant mis-selected with Class 1
        },
        // ── Class 2: absolute tolerances; part flush = ≤ 2/3 full ───
        CisternClass::Class2 => match (variant, is_part_flush) {
            (CisternTypeVariant::Max6_0, false) => measured_vol_l <= 6.0,
            (CisternTypeVariant::L4_5,   false) => (4.15..=4.85).contains(&measured_vol_l),
            (CisternTypeVariant::L4_0,   false) => (3.70..=4.30).contains(&measured_vol_l),
            // Part flush: ≤ 2/3 × last measured full flush
            (_, true) => last_full_vol_l
                .map_or(false, |fv| measured_vol_l <= (2.0 / 3.0) * fv),
            _ => false, // Class 1 variant mis-selected with Class 2
        },
    }
}

// ── EN 14055 constants ─────────────────────────────────────────────────
const EN14055_REQUIRED_FLUSH_COUNT: usize = 3;
const EN14055_FULL_FLUSH_MAX_L: f64 = 6.0;
const EN14055_PART_FLUSH_MAX_L: f64 = 4.0;
const EN14055_SAFETY_MARGIN_MIN_MM: f64 = 20.0;
const EN14055_MWL_MAX_ABOVE_OF_MM: f64 = 20.0;
const EN14055_CWL_MAX_ABOVE_OF_MM: f64 = 10.0;
const EN14055_MENISCUS_MAX_ABOVE_OF_MM: f64 = 5.0;
const EN14055_AIR_GAP_MIN_MM: f64 = 20.0;

pub fn run_compliance_checks(profile: &CisternProfile, results: &[FlushResult]) -> Vec<String> {
    let mut flags = Vec::new();
    let p = profile;

    // 1. Safety margin c: OF − NWL ≥ 20 mm (§5.2.6)
    if p.overflow > 0.0 && p.mwl > 0.0 {
        let sm = p.overflow - p.mwl;
        if sm >= EN14055_SAFETY_MARGIN_MIN_MM {
            flags.push(format!("[PASS] Safety margin c (OF−NWL): {:.1} mm ≥ 20 mm", sm));
        } else {
            flags.push(format!("[FAIL] Safety margin c (OF−NWL): {:.1} mm < 20 mm", sm));
        }
    } else {
        flags.push("[----] Safety margin c: capture NWL and set Overflow first".to_string());
    }

    // 2. MWL fault: MWL_fault − OF ≤ 20 mm (§5.2.4a)
    if p.overflow > 0.0 && p.mwl_fault > 0.0 {
        let diff = p.mwl_fault - p.overflow;
        if diff <= EN14055_MWL_MAX_ABOVE_OF_MM {
            flags.push(format!("[PASS] MWL fault: +{:.1} mm above OF ≤ 20 mm", diff));
        } else {
            flags.push(format!("[FAIL] MWL fault: +{:.1} mm above OF > 20 mm", diff));
        }
    } else {
        flags.push("[----] MWL fault: run overflow fault test and capture first".to_string());
    }

    // 3. CWL − OF ≤ 10 mm (§5.2.4b)
    if p.overflow > 0.0 && p.cwl > 0.0 {
        let diff = p.cwl - p.overflow;
        if diff <= EN14055_CWL_MAX_ABOVE_OF_MM {
            flags.push(format!("[PASS] CWL: {:+.1} mm from OF ≤ 10 mm", diff));
        } else {
            flags.push(format!("[FAIL] CWL: +{:.1} mm above OF > 10 mm", diff));
        }
    } else {
        flags.push("[----] CWL: run fault test, cut supply, wait 2s, capture".to_string());
    }

    // 4. Meniscus − OF ≤ 5 mm (§5.2.4c)
    if p.meniscus != 0.0 {
        let m = p.meniscus;
        if m >= 0.0 && m <= EN14055_MENISCUS_MAX_ABOVE_OF_MM {
            flags.push(format!("[PASS] Meniscus: +{:.1} mm above OF ≤ 5 mm", m));
        } else if m > EN14055_MENISCUS_MAX_ABOVE_OF_MM {
            flags.push(format!("[FAIL] Meniscus: +{:.1} mm above OF > 5 mm", m));
        } else {
            flags.push(format!("[WARN] Meniscus: {:.1} mm (below OF — check capture)", m));
        }
    } else {
        flags.push("[----] Meniscus: let cistern overflow, stabilise, then capture".to_string());
    }

    // 5. Air gap a: water_discharge − CWL ≥ 20 mm (§5.2.7)
    if p.water_discharge > 0.0 && p.cwl > 0.0 {
        let air_gap = p.water_discharge - p.cwl;
        if air_gap >= EN14055_AIR_GAP_MIN_MM {
            flags.push(format!("[PASS] Air gap a (§5.2.7): {:.1} mm (WD−CWL) ≥ 20 mm", air_gap));
        } else {
            flags.push(format!("[FAIL] Air gap a (§5.2.7): {:.1} mm (WD−CWL) < 20 mm", air_gap));
        }
    } else {
        flags.push("[----] Air gap a: set Water Discharge and capture CWL first".to_string());
    }

    // 6. Residual WL (informational)
    if p.residual_wl > 0.0 {
        flags.push(format!("[INFO] Residual WL (RWL): {:.1} mm after flush", p.residual_wl));
    }

    // 7. Flush volume (§5.2.1, §6.5)
    if results.is_empty() {
        flags.push("[----] Flush volume: no measurements yet".to_string());
    } else {
        let full_flushes: Vec<&FlushResult> = results.iter().filter(|r| r.is_full).collect();
        let part_flushes: Vec<&FlushResult> = results.iter().filter(|r| !r.is_full).collect();

        // Full flush checks
        if !full_flushes.is_empty() {
            if full_flushes.len() < EN14055_REQUIRED_FLUSH_COUNT {
                flags.push(format!(
                    "[WARN] Full flush: only {}/{} measurements (§5.2.1 requires 3)",
                    full_flushes.len(), EN14055_REQUIRED_FLUSH_COUNT
                ));
            }
            let avg_full = full_flushes.iter().map(|f| f.vol_l).sum::<f64>() / full_flushes.len() as f64;
            if avg_full <= EN14055_FULL_FLUSH_MAX_L {
                flags.push(format!("[PASS] Full flush avg: {:.2} L (limit {:.1} L)", avg_full, EN14055_FULL_FLUSH_MAX_L));
            } else {
                flags.push(format!("[FAIL] Full flush avg: {:.2} L (limit {:.1} L)", avg_full, EN14055_FULL_FLUSH_MAX_L));
            }
            // EN14055 flow rate
            let en_rates: Vec<f64> = full_flushes.iter()
                .filter_map(|f| f.en14055_rate)
                .collect();
            if !en_rates.is_empty() {
                let avg_rate = en_rates.iter().sum::<f64>() / en_rates.len() as f64;
                flags.push(format!("[INFO] Full flush EN 14055 flow rate (V2 method): {:.3} L/s", avg_rate));
            }
            // Water temperature
            let temps: Vec<f64> = full_flushes.iter().filter_map(|f| f.temp_c).collect();
            if !temps.is_empty() {
                let t_min = temps.iter().cloned().fold(f64::INFINITY, f64::min);
                let t_max = temps.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
                flags.push(format!("[INFO] Water temp during full flushes: {:.1}–{:.1} °C (EN 14055 §5.1: 15±5 °C)", t_min, t_max));
            }
        }

        // Part flush checks
        if !part_flushes.is_empty() {
            if part_flushes.len() < EN14055_REQUIRED_FLUSH_COUNT {
                flags.push(format!(
                    "[WARN] Part flush: only {}/{} measurements (§5.2.1 requires 3)",
                    part_flushes.len(), EN14055_REQUIRED_FLUSH_COUNT
                ));
            }
            let avg_part = part_flushes.iter().map(|f| f.vol_l).sum::<f64>() / part_flushes.len() as f64;
            if avg_part <= EN14055_PART_FLUSH_MAX_L {
                flags.push(format!("[PASS] Part flush avg: {:.2} L (limit {:.1} L)", avg_part, EN14055_PART_FLUSH_MAX_L));
            } else {
                flags.push(format!("[FAIL] Part flush avg: {:.2} L (limit {:.1} L)", avg_part, EN14055_PART_FLUSH_MAX_L));
            }
        }
    }

    flags
}

/// Apply a smoothing algorithm to a slice of f64 values.
/// Matches all algorithms supported by the Python sensor_core.smooth().
pub fn smooth(data: &[f64], alg: &str) -> Vec<f64> {
    if data.len() < 2 || alg == "None" { return data.to_vec(); }
    let n = data.len();

    // ── SMA (Simple Moving Average) — O(N) sliding-window sum ──
    if alg.starts_with("SMA") {
        let w: usize = alg.split('-').nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(5)
            .max(1); // guard: SMA-0 would cause divide-by-zero
        let mut r = Vec::with_capacity(n);
        let mut window_sum = 0.0_f64;
        for i in 0..n {
            window_sum += data[i];
            if i >= w { window_sum -= data[i - w]; }
            r.push(window_sum / (i + 1).min(w) as f64);
        }
        return r;
    }

    // ── EMA (Exponential Moving Average) ──
    if alg.starts_with("EMA") {
        let a = if alg.contains("Fast") { 0.2_f64 } else { 0.05_f64 };
        let mut r = Vec::with_capacity(n);
        r.push(data[0]);
        for i in 1..n {
            r.push(a * data[i] + (1.0 - a) * r[i - 1]);
        }
        return r;
    }

    // ── DEMA (Double Exponential Moving Average) ──
    if alg == "DEMA" {
        let a = 0.15_f64;
        let mut ema1 = Vec::with_capacity(n);
        ema1.push(data[0]);
        for i in 1..n {
            ema1.push(a * data[i] + (1.0 - a) * ema1[i - 1]);
        }
        let mut ema2 = Vec::with_capacity(n);
        ema2.push(ema1[0]);
        for i in 1..n {
            ema2.push(a * ema1[i] + (1.0 - a) * ema2[i - 1]);
        }
        return ema1.iter().zip(ema2.iter()).map(|(e1, e2)| 2.0 * e1 - e2).collect();
    }

    // ── Median Filter — fixed stack array, no per-element heap allocation ──
    if alg.starts_with("Median") {
        let w: usize = alg.split('-').nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(5)
            .min(9); // cap at 9 to fit the fixed-size scratch array below
        let half = w / 2;
        let mut r = Vec::with_capacity(n);
        let mut scratch = [0.0_f64; 9];
        for i in 0..n {
            let lo = i.saturating_sub(half);
            let hi = (i + half + 1).min(n);
            let slice = &data[lo..hi];
            scratch[..slice.len()].copy_from_slice(slice);
            let buf = &mut scratch[..slice.len()];
            buf.sort_by(|a, b| a.total_cmp(b));
            r.push(buf[buf.len() / 2]);
        }
        return r;
    }

    // ── 1D Kalman Filter ──
    if alg == "Kalman" {
        let q = 0.01_f64; // process noise
        let r_noise = 0.5_f64; // measurement noise
        let mut x_est = data[0];
        let mut p_est = 1.0_f64;
        let mut r = Vec::with_capacity(n);
        r.push(x_est);
        for i in 1..n {
            let p_pred = p_est + q;
            let k = p_pred / (p_pred + r_noise);
            x_est += k * (data[i] - x_est);
            p_est = (1.0 - k) * p_pred;
            r.push(x_est);
        }
        return r;
    }

    // ── Savitzky-Golay (quadratic, window 7) ──
    if alg == "Savitzky-Golay" {
        // Standard SG coefficients for M=3 (half-window=3), order=2
        let coeffs: [f64; 7] = [-2.0, 3.0, 6.0, 7.0, 6.0, 3.0, -2.0];
        let norm: f64 = coeffs.iter().sum(); // 21.0
        let half = coeffs.len() / 2;
        let mut r = data.to_vec(); // edges stay as-is
        for i in half..n.saturating_sub(half) {
            let val: f64 = coeffs.iter().enumerate()
                .map(|(j, &c)| c * data[i + j - half])
                .sum();
            r[i] = val / norm;
        }
        return r;
    }

    data.to_vec()
}

/// Compute only the *last* smoothed value without allocating the full output vector.
/// Used by the CWL/RWL state machines which need only `smooth(history).last()`.
pub fn smooth_last(data: &[f64], alg: &str) -> f64 {
    if data.is_empty() { return 0.0; }
    let raw_last = *data.last().unwrap();
    if data.len() < 2 || alg == "None" { return raw_last; }
    let n = data.len();

    // SMA: average of last W samples — O(W)
    if alg.starts_with("SMA") {
        let w: usize = alg.split('-').nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(5)
            .max(1)  // guard: SMA-0 would cause divide-by-zero
            .min(n);
        return data[n - w..].iter().sum::<f64>() / w as f64;
    }

    // EMA: single forward pass — O(N) but no allocation
    if alg.starts_with("EMA") {
        let a = if alg.contains("Fast") { 0.2_f64 } else { 0.05_f64 };
        let mut ema = data[0];
        for &x in &data[1..] { ema = a * x + (1.0 - a) * ema; }
        return ema;
    }

    // For heavier algorithms fall back to the full smooth and take the last element
    smooth(data, alg).last().copied().unwrap_or(raw_last)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── interp_hv ───────────────────────────────────────────────────────────

    fn pt(p: f64, h: f64, v: f64) -> CalibrationPoint { CalibrationPoint { p, h, v } }

    fn profile_with(pts: Vec<CalibrationPoint>) -> CisternProfile {
        let mut prof = CisternProfile::default();
        prof.points = pts;
        prof.sort_points();
        prof
    }

    #[test]
    fn interp_empty_returns_zeros() {
        let prof = profile_with(vec![]);
        assert_eq!(prof.interp_hv(1.0), (0.0, 0.0));
    }

    #[test]
    fn interp_single_point_returns_that_point() {
        let prof = profile_with(vec![pt(1.0, 100.0, 5.0)]);
        assert_eq!(prof.interp_hv(1.0), (100.0, 5.0));
    }

    #[test]
    fn interp_midpoint_two_points() {
        let prof = profile_with(vec![pt(0.0, 0.0, 0.0), pt(2.0, 200.0, 10.0)]);
        let (h, v) = prof.interp_hv(1.0);
        assert!((h - 100.0).abs() < 1e-9);
        assert!((v - 5.0).abs() < 1e-9);
    }

    #[test]
    fn interp_extrapolates_below() {
        let prof = profile_with(vec![pt(1.0, 100.0, 5.0), pt(2.0, 200.0, 10.0)]);
        let (h, v) = prof.interp_hv(0.5);
        assert!((h - 50.0).abs() < 1e-9);
        assert!((v - 2.5).abs() < 1e-9);
    }

    #[test]
    fn interp_extrapolates_above() {
        let prof = profile_with(vec![pt(1.0, 100.0, 5.0), pt(2.0, 200.0, 10.0)]);
        let (h, _) = prof.interp_hv(3.0);
        assert!((h - 300.0).abs() < 1e-9);
    }

    #[test]
    fn interp_three_points_inner_segment() {
        let prof = profile_with(vec![
            pt(0.0, 0.0, 0.0), pt(1.0, 100.0, 5.0), pt(2.0, 180.0, 9.0),
        ]);
        let (h, v) = prof.interp_hv(1.5);
        assert!((h - 140.0).abs() < 1e-9);
        assert!((v - 7.0).abs() < 1e-9);
    }

    // ── smooth ──────────────────────────────────────────────────────────────

    #[test]
    fn smooth_none_returns_copy() {
        let d = vec![1.0, 2.0, 3.0];
        assert_eq!(smooth(&d, "None"), d);
    }

    #[test]
    fn smooth_sma5_length_preserved() {
        let d: Vec<f64> = (0..20).map(|i| i as f64).collect();
        assert_eq!(smooth(&d, "SMA-5").len(), 20);
    }

    #[test]
    fn smooth_ema_fast_steady_stays_constant() {
        let d = vec![10.0; 20];
        let r = smooth(&d, "EMA-Fast");
        assert!((r.last().unwrap() - 10.0).abs() < 1e-9);
    }

    #[test]
    fn smooth_dema_length_preserved() {
        let d: Vec<f64> = (0..15).map(|i| i as f64).collect();
        assert_eq!(smooth(&d, "DEMA").len(), 15);
    }

    #[test]
    fn smooth_dema_steady_state() {
        let d = vec![5.0f64; 30];
        let r = smooth(&d, "DEMA");
        assert!((r.last().unwrap() - 5.0).abs() < 1e-6);
    }

    #[test]
    fn smooth_median_removes_spike() {
        let mut d = vec![5.0f64; 5];
        d[2] = 100.0;
        let r = smooth(&d, "Median-5");
        assert!((r[2] - 5.0).abs() < 1e-9);
    }

    #[test]
    fn smooth_kalman_length_preserved() {
        let d: Vec<f64> = (0..10).map(|i| i as f64).collect();
        assert_eq!(smooth(&d, "Kalman").len(), 10);
    }

    #[test]
    fn smooth_kalman_first_element_unchanged() {
        let d = vec![42.0, 0.0, 0.0, 0.0];
        assert!((smooth(&d, "Kalman")[0] - 42.0).abs() < 1e-9);
    }

    #[test]
    fn smooth_savitzky_golay_flat_signal() {
        let d = vec![7.0f64; 20];
        let r = smooth(&d, "Savitzky-Golay");
        for v in r { assert!((v - 7.0).abs() < 1e-9); }
    }

    // ── run_compliance_checks ───────────────────────────────────────────────

    fn full_flush(vol: f64) -> FlushResult {
        FlushResult { is_full: true, vol_l: vol, time_s: 10.0, en14055_rate: None, temp_c: None, compliance_pass: None }
    }
    fn part_flush(vol: f64) -> FlushResult {
        FlushResult { is_full: false, vol_l: vol, time_s: 10.0, en14055_rate: None, temp_c: None, compliance_pass: None }
    }

    #[test]
    fn compliance_safety_margin_pass() {
        let mut p = CisternProfile::default();
        p.overflow = 250.0; p.mwl = 220.0;
        let flags = run_compliance_checks(&p, &[]);
        assert!(flags.iter().any(|f| f.contains("[PASS]") && f.contains("Safety margin")));
    }

    #[test]
    fn compliance_safety_margin_fail() {
        let mut p = CisternProfile::default();
        p.overflow = 250.0; p.mwl = 235.0;
        let flags = run_compliance_checks(&p, &[]);
        assert!(flags.iter().any(|f| f.contains("[FAIL]") && f.contains("Safety margin")));
    }

    #[test]
    fn compliance_air_gap_pass() {
        let mut p = CisternProfile::default();
        p.water_discharge = 300.0; p.cwl = 275.0;
        let flags = run_compliance_checks(&p, &[]);
        assert!(flags.iter().any(|f| f.contains("[PASS]") && f.contains("Air gap")));
    }

    #[test]
    fn compliance_air_gap_fail() {
        let mut p = CisternProfile::default();
        p.water_discharge = 300.0; p.cwl = 285.0;
        let flags = run_compliance_checks(&p, &[]);
        assert!(flags.iter().any(|f| f.contains("[FAIL]") && f.contains("Air gap")));
    }

    #[test]
    fn compliance_full_flush_pass() {
        let p = CisternProfile::default();
        let results: Vec<FlushResult> = (0..3).map(|_| full_flush(5.5)).collect();
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[PASS]") && f.contains("Full flush")));
    }

    #[test]
    fn compliance_full_flush_fail() {
        let p = CisternProfile::default();
        let results: Vec<FlushResult> = (0..3).map(|_| full_flush(6.5)).collect();
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[FAIL]") && f.contains("Full flush")));
    }

    #[test]
    fn compliance_part_flush_pass() {
        let p = CisternProfile::default();
        let results: Vec<FlushResult> = (0..3).map(|_| part_flush(3.5)).collect();
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[PASS]") && f.contains("Part flush")));
    }

    #[test]
    fn compliance_warns_fewer_than_3_full_flushes() {
        let p = CisternProfile::default();
        let results = vec![full_flush(5.0), full_flush(5.0)];
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[WARN]") && f.contains("Full flush")));
    }

    #[test]
    fn compliance_warns_fewer_than_3_part_flushes() {
        let p = CisternProfile::default();
        let results = vec![part_flush(3.0)];
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[WARN]") && f.contains("Part flush")));
    }

    #[test]
    fn compliance_residual_wl_info() {
        let mut p = CisternProfile::default();
        p.residual_wl = 42.0;
        let flags = run_compliance_checks(&p, &[]);
        assert!(flags.iter().any(|f| f.contains("[INFO]") && f.contains("Residual")));
    }

    #[test]
    fn compliance_temp_info_shown() {
        let p = CisternProfile::default();
        let results: Vec<FlushResult> = (0..3).map(|_| FlushResult {
            is_full: true, vol_l: 5.5, time_s: 10.0,
            en14055_rate: None, temp_c: Some(18.0), compliance_pass: None,
        }).collect();
        let flags = run_compliance_checks(&p, &results);
        assert!(flags.iter().any(|f| f.contains("[INFO]") && f.to_lowercase().contains("temp")));
    }
}

#[cfg(test)]
mod smooth_last_tests {
    use super::*;

    fn check(data: &[f64], alg: &str) {
        let expected = smooth(data, alg).last().copied().unwrap_or(0.0);
        let got = smooth_last(data, alg);
        assert!((got - expected).abs() < 1e-9,
            "alg={} expected={} got={}", alg, expected, got);
    }

    #[test]
    fn smooth_last_matches_smooth_for_all_algorithms() {
        let data: Vec<f64> = (0..50).map(|i| (i as f64).sin() * 100.0 + 200.0).collect();
        for alg in ["None","SMA-5","SMA-20","EMA-Fast","EMA-Slow","DEMA","Median-5","Kalman","Savitzky-Golay"] {
            check(&data, alg);
        }
    }

    #[test]
    fn smooth_last_short_slice() {
        let data = vec![1.0, 2.0, 3.0];
        for alg in ["SMA-5","SMA-20","EMA-Fast","EMA-Slow"] {
            check(&data, alg);
        }
    }

    #[test]
    fn smooth_last_single_element() {
        assert_eq!(smooth_last(&[42.0], "SMA-5"), 42.0);
        assert_eq!(smooth_last(&[42.0], "EMA-Fast"), 42.0);
        assert_eq!(smooth_last(&[], "SMA-5"), 0.0);
    }
}

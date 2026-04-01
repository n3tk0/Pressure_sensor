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
    // 🚀 Оптимизация 3: Сортираме *само* при запазване на профила.
    pub fn sort_points(&mut self) {
        self.points.sort_by(|a, b| a.p.partial_cmp(&b.p).unwrap());
    }

    // 🚀 Оптимизация 3: Премахнти алокации (clone) и вътрешни сортове.
    // Интерполацията използва изцяло O(log n) двоично търсене и zero-copy референции!
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
        
        // Много бързо двоично търсене в предварително сортиран масив
        let i = pts.partition_point(|pt| pt.p <= p_bar).saturating_sub(1);
        let ptr_c = &pts[i];
        let ptr_n = &pts[i+1];
        let d = ptr_n.p - ptr_c.p;
        let r = if d > 0.0 { (p_bar - ptr_c.p) / d } else { 0.0 };
        (ptr_c.h + r * (ptr_n.h - ptr_c.h), ptr_c.v + r * (ptr_n.v - ptr_c.v))
    }
}

pub struct FlushResult {
    pub is_full: bool,
    pub vol_l: f64,
    pub time_s: f64,
}

pub fn run_compliance_checks(profile: &CisternProfile, results: &[FlushResult]) -> Vec<String> {
    let mut flags = Vec::new();
    
    // Safety margin c
    if profile.overflow > 0.0 && profile.mwl > 0.0 {
        let sm = profile.overflow - profile.mwl;
        flags.push(format!("[{}] Safety margin c (OF−NWL): {:.1} mm", if sm >= 20.0 { "PASS" } else { "FAIL" }, sm));
    } else {
        flags.push("[----] Safety margin c: capture NWL and set Overflow first".to_string());
    }

    // MWL Fault
    if profile.overflow > 0.0 && profile.mwl_fault > 0.0 {
        let diff_mwl = profile.mwl_fault - profile.overflow;
        flags.push(format!("[{}] MWL fault: +{:.1} mm above OF <= 20 mm", if diff_mwl <= 20.0 { "PASS" } else { "FAIL" }, diff_mwl));
    } else {
        flags.push("[----] MWL fault: run overflow fault test and capture first".to_string());
    }

    // CWL 
    if profile.overflow > 0.0 && profile.cwl > 0.0 {
        let diff_cwl = profile.cwl - profile.overflow;
        flags.push(format!("[{}] CWL gap: +{:.1} mm above OF <= 10 mm", if diff_cwl <= 10.0 { "PASS" } else { "FAIL" }, diff_cwl));
    } else {
        flags.push("[----] CWL gap: capture during fault test".to_string());
    }
    
    // Meniscus
    if profile.meniscus != 0.0 {
        let m = profile.meniscus;
        if m >= 0.0 && m <= 5.0 {
            flags.push(format!("[PASS] Meniscus: +{:.1} mm above OF <= 5 mm", m));
        } else {
            flags.push(format!("[FAIL] Meniscus: +{:.1} mm above OF > 5 mm", m));
        }
    } else {
        flags.push("[----] Meniscus: capture stable after overflow".to_string());
    }

    // Full capacity flushes validation
    let fulls: Vec<_> = results.iter().filter(|r| r.is_full).collect();
    if !fulls.is_empty() {
        let avg_full = fulls.iter().map(|f| f.vol_l).sum::<f64>() / (fulls.len() as f64);
        let tag = if avg_full <= 6.0 { "PASS" } else { "FAIL" };
        flags.push(format!("[{}] Full flush avg: {:.2} L (limit 6.0 L)", tag, avg_full));
    } else {
        flags.push("[----] Flush volume: no measurements yet".to_string());
    }
    
    flags
}

#[allow(dead_code)]
pub fn smooth(data: &[f64], alg: &str) -> Vec<f64> {
    if data.len() < 2 || alg == "None" { return data.to_vec(); }
    let n = data.len();
    let mut r = Vec::with_capacity(n);
    
    if alg.starts_with("SMA") {
        let w = if let Some(w_str) = alg.split('-').nth(1) { w_str.parse::<usize>().unwrap_or(5) } else { 5 };
        for i in 0..n {
            let s = i.saturating_sub(w.saturating_sub(1));
            let sum: f64 = data[s..=i].iter().sum();
            r.push(sum / (i - s + 1) as f64);
        }
        return r;
    }
    if alg.starts_with("EMA") {
        let a = if alg.contains("Fast") { 0.2 } else { 0.05 };
        r.push(data[0]);
        for i in 1..n {
            r.push(a * data[i] + (1.0 - a) * r.last().unwrap());
        }
        return r;
    }
    data.to_vec() 
}

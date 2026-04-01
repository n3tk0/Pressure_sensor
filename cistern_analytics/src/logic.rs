pub struct CisternProfile {
    pub mwl: f64,
    pub overflow: f64,
    pub mwl_fault: f64,
    pub meniscus: f64,
    pub cwl: f64,
    pub water_discharge: f64,
    pub residual_wl: f64,
}

pub struct FlushResult {
    pub is_full: bool,
    pub vol_l: f64,
}

pub fn run_compliance_checks(profile: &CisternProfile, results: &[FlushResult]) -> Vec<String> {
    let mut flags = Vec::new();
    
    let sm = profile.overflow - profile.mwl;
    flags.push(format!("[{}] Safety margin c (OF−NWL): {:.1} mm", if sm >= 20.0 { "PASS" } else { "FAIL" }, sm));

    let diff_mwl = profile.mwl_fault - profile.overflow;
    flags.push(format!("[{}] MWL fault: +{:.1} mm above OF <= 20 mm", if diff_mwl <= 20.0 { "PASS" } else { "FAIL" }, diff_mwl));

    let diff_cwl = profile.cwl - profile.overflow;
    flags.push(format!("[{}] CWL gap: +{:.1} mm above OF <= 10 mm", if diff_cwl <= 10.0 { "PASS" } else { "FAIL" }, diff_cwl));

    // Full capacity flushes validation
    let fulls: Vec<_> = results.iter().filter(|r| r.is_full).collect();
    if !fulls.is_empty() {
        let avg_full = fulls.iter().map(|f| f.vol_l).sum::<f64>() / (fulls.len() as f64);
        let tag = if avg_full <= 6.0 { "PASS" } else { "FAIL" };
        flags.push(format!("[{}] Full flush avg: {:.2} L (limit 6.0 L)", tag, avg_full));
    }
    
    flags
}

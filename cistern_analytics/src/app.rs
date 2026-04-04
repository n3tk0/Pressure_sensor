use crate::sensor::{SensorCore, SensorEvent};
use crate::logic::{CisternProfile, CalibrationPoint, FlushResult, run_compliance_checks, smooth, smooth_last};
use eframe::egui;
use egui::{Color32, RichText, Key, Modifiers};
use egui_extras::{TableBuilder, Column};
use egui_plot::{Line, Plot, HLine, Points, VLine};
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::time::Instant;
use chrono::Local;
use serde::{Serialize, Deserialize};

/// CWL / RWL auto-detection state machine stages.
#[derive(PartialEq, Clone, Debug)]
enum AutoState { Idle, Armed, Waiting, Done }

/// Typed settings struct — serialised to/from settings.json.
#[derive(Serialize, Deserialize, Clone)]
struct AppSettings {
    port:            String,
    baud:            u32,
    io_port:         u32,
    polling_ms:      u32,
    pressure_unit:   String,
    avg_window:      String,
    cwl_mode:        String,
    cwl_drop:        String,
    cwl_smooth:      String,
    ui_refresh:      String,
    ch_refresh:      String,
    temp_offset:     String,
    chart_window:    String,
    chart_smooth:    String,
    dark_mode:       bool,
    #[serde(default)]
    recent_profiles: Vec<String>,
    #[serde(default = "default_font_scale")]
    font_scale:      f32,
    /// false after wizard is completed once
    #[serde(default = "default_true")]
    first_run:       bool,
}

fn default_font_scale() -> f32 { 1.0 }
fn default_true()       -> bool { true }

const BUFFER_MAX: usize = 12000;

pub struct FastRingBuffer {
    buffer: Vec<[f64; 2]>,
    head: usize,
    capacity: usize,
    wrapped: bool,
}

impl FastRingBuffer {
    pub fn new(capacity: usize) -> Self { Self { buffer: vec![[0.0, 0.0]; capacity], head: 0, capacity, wrapped: false } }
    pub fn push(&mut self, point: [f64; 2]) {
        self.buffer[self.head] = point;
        self.head += 1;
        if self.head >= self.capacity { self.head = 0; self.wrapped = true; }
    }
    pub fn get_line_points(&self) -> Vec<[f64; 2]> {
        if self.wrapped {
            let mut out = Vec::with_capacity(self.capacity);
            out.extend_from_slice(&self.buffer[self.head..self.capacity]);
            out.extend_from_slice(&self.buffer[..self.head]);
            out
        } else { self.buffer[..self.head].to_vec() }
    }
    pub fn last_y(&self) -> Option<f64> {
        let idx = if self.head == 0 { if self.wrapped { self.capacity - 1 } else { return None }  } else { self.head - 1 };
        Some(self.buffer[idx][1])
    }
    pub fn last_x(&self) -> Option<f64> {
        let idx = if self.head == 0 { if self.wrapped { self.capacity - 1 } else { return None }  } else { self.head - 1 };
        Some(self.buffer[idx][0])
    }
    pub fn clear(&mut self) { self.head = 0; self.wrapped = false; }
}


#[derive(PartialEq, Clone, Copy)]
enum PlotMode { Pressure, Height, Volume, Flow }

pub struct CisternApp {
    sensor: SensorCore,
    pub profile: CisternProfile,

    target_port: String,
    target_baud: u32,
    target_polling: u32,
    target_io_port: u32,
    
    // UI Theme
    is_dark_mode: bool,
    theme_applied: bool,

    // UI Dialogs flags
    show_calibration_modal: bool,
    show_connection_modal: bool,
    show_compliance_modal: bool,
    show_program_modal: bool,
    show_colors_modal: bool,
    show_help_modal: bool,
    show_about_modal: bool,
    left_panel_visible: bool,

    // Line Colors
    color_sensor: Color32,
    color_cwl: Color32,
    color_mwl: Color32,
    color_menis: Color32,
    color_wd: Color32,
    color_of: Color32,

    // Combobox Option States
    setting_pressure_unit: String,
    setting_avg_window: String,
    setting_cwl_mode: String,
    setting_cwl_drop: String,
    setting_cwl_smooth: String,
    setting_ui_refresh: String,
    setting_ch_refresh: String,
    setting_temp_offset: String,
    chart_window_val: String,
    chart_smooth_val: String,

    // Temp states for calibration modal
    cal_p_in: String,
    cal_h_in: String,
    cal_v_in: String,

    p_buf: FastRingBuffer,
    h_buf: FastRingBuffer,
    v_buf: FastRingBuffer,
    f_buf: FastRingBuffer,
    
    chart_paused: bool,
    chart_auto_scroll: bool,
    plot_mode: PlotMode,
    click_points: Vec<[f64; 2]>,
    
    csv_file: Option<std::io::BufWriter<File>>, // buffered to avoid per-sample syscalls
    is_logging: bool,
    
    flush_type_idx: usize,
    flushes: Vec<FlushResult>,
    is_flushing: bool,
    flush_start_vol: f64,
    flush_start_time: f64,
    // Snapshot captured at the moment "Stop" is clicked (before confirmation dialog)
    flush_pending_end_vol: f64,
    flush_pending_end_t: f64,
    
    compliance_results: Vec<String>,
    
    current_p: f64,
    current_h: f64,
    current_v: f64,
    current_f: f64,
    current_temp: Option<f64>,
    sensor_status_text: String,

    // CWL / RWL auto-detection state
    cwl_state: AutoState,
    cwl_peak: f64,
    cwl_timer: Option<Instant>,
    rwl_state: AutoState,
    rwl_timer: Option<Instant>,

    // Rolling height history for smoothing during CWL/RWL detection (last N samples)
    h_history: Vec<f64>,

    // Toast notification
    toast_msg: String,
    toast_until: Option<Instant>,

    // Paths for profile persistence
    config_dir: PathBuf,

    // Unsaved changes tracking
    profile_dirty: bool,

    // Undo stack for calibration edits
    cal_undo_stack: Vec<CisternProfile>,

    // Auto-reconnect
    auto_reconnect: bool,
    reconnect_after: Option<Instant>,

    // Cal validation message
    cal_validation_msg: String,

    // Item 18: session elapsed time
    session_start: Option<Instant>,
    // Item 16: last sensor fault
    last_fault_text: String,
    last_fault_at: Option<Instant>,
    // Item 14: "Clear All" confirmation guard
    clear_flushes_confirm: bool,
    // Item 4: "Stop Flush" confirmation guard
    stop_flush_confirm: bool,
    // Item 15: one-shot zoom reset flag
    reset_zoom: bool,
    // Item 7: flush event markers (time_s, is_full_flush)
    flush_vlines: Vec<(f64, bool)>,
    // Item 17: volume display unit
    vol_unit_ml: bool,
    // Item 19: CSV log size tracking for auto-rollover (10 MB)
    csv_bytes_written: u64,
    // "Log last N minutes" window selector index (0=1, 1=2, 2=5, 3=10)
    log_window_idx: usize,
    // Cached avg_window parsed value — updated on settings load/save
    avg_window_s: f64,
    // Chart display cache — avoids re-smoothing every frame
    chart_cache_gen: usize,
    chart_cache_key: (usize, PlotMode, String, String), // (gen, mode, window, smooth)
    display_pts_cache: Vec<[f64; 2]>,
    // Item 6: recently used profile paths (persisted in settings)
    recent_profiles: Vec<String>,
    // Item 20: UI font scale factor
    font_scale: f32,
    // Item 1: first-run guided wizard
    show_wizard: bool,
    wizard_step: usize,
}

impl CisternApp {
    pub fn new() -> Self {
        let mut prof = CisternProfile::default();
        prof.name = "Untitled Profile".to_string();
        
        Self {
            sensor: SensorCore::new(),
            profile: prof,
            target_port: "COM8".to_string(),
            target_baud: 115200,
            target_polling: 50,
            target_io_port: 1,
            is_dark_mode: true, // Defaulting to Dark
            theme_applied: false,
            show_calibration_modal: false,
            show_connection_modal: false,
            show_compliance_modal: false,
            show_program_modal: false,
            show_colors_modal: false,
            show_help_modal: false,
            show_about_modal: false,
            left_panel_visible: true,
            color_sensor: Color32::from_rgb(137, 180, 250),
            color_cwl: Color32::from_rgb(250, 179, 135),
            color_mwl: Color32::from_rgb(166, 227, 161),
            color_menis: Color32::from_rgb(180, 130, 255),
            color_wd: Color32::from_rgb(243, 139, 168),
            color_of: Color32::from_rgb(243, 139, 168),
            setting_pressure_unit: "bar".to_string(),
            setting_avg_window: "0.5".to_string(),
            setting_cwl_mode: "Automatic".to_string(),
            setting_cwl_drop: "1.5".to_string(),
            setting_cwl_smooth: "SMA-5".to_string(),
            setting_ui_refresh: "50".to_string(),
            setting_ch_refresh: "100".to_string(),
            setting_temp_offset: "0.0".to_string(),
            chart_window_val: "30s".to_string(),
            chart_smooth_val: "None".to_string(),
            cal_p_in: String::new(), cal_h_in: String::new(), cal_v_in: String::new(),
            p_buf: FastRingBuffer::new(BUFFER_MAX),
            h_buf: FastRingBuffer::new(BUFFER_MAX),
            v_buf: FastRingBuffer::new(BUFFER_MAX),
            f_buf: FastRingBuffer::new(BUFFER_MAX),
            chart_paused: false,
            chart_auto_scroll: true,
            plot_mode: PlotMode::Height,
            click_points: Vec::new(),
            csv_file: None,
            is_logging: false,
            flush_type_idx: 0,
            flushes: Vec::new(),
            is_flushing: false,
            flush_start_vol: 0.0,
            flush_start_time: 0.0,
            flush_pending_end_vol: 0.0,
            flush_pending_end_t: 0.0,
            compliance_results: Vec::new(),
            current_p: 0.0, current_h: 0.0, current_v: 0.0, current_f: 0.0, current_temp: None,
            sensor_status_text: "--".to_string(),
            cwl_state: AutoState::Idle,
            cwl_peak: 0.0,
            cwl_timer: None,
            rwl_state: AutoState::Idle,
            rwl_timer: None,
            h_history: Vec::new(),
            toast_msg: String::new(),
            toast_until: None,
            config_dir: {
                std::env::current_exe()
                    .ok()
                    .and_then(|p| p.parent().map(|d| d.join("config")))
                    .unwrap_or_else(|| PathBuf::from("config"))
            },
            profile_dirty: false,
            cal_undo_stack: Vec::new(),
            auto_reconnect: true,
            reconnect_after: None,
            cal_validation_msg: String::new(),
            session_start: None,
            last_fault_text: String::new(),
            last_fault_at: None,
            clear_flushes_confirm: false,
            stop_flush_confirm: false,
            reset_zoom: false,
            flush_vlines: Vec::new(),
            vol_unit_ml: false,
            csv_bytes_written: 0,
            log_window_idx: 0,
            avg_window_s: 0.5,
            chart_cache_gen: 0,
            chart_cache_key: (usize::MAX, PlotMode::Height, String::new(), String::new()),
            display_pts_cache: Vec::new(),
            recent_profiles: Vec::new(),
            font_scale: 1.0,
            show_wizard: false,
            wizard_step: 0,
        }
    }

    // Dynamic Theming Palette
    fn col_accent(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(137, 180, 250) } else { Color32::from_rgb(30, 102, 245) } }
    fn col_green(&self)  -> Color32 { if self.is_dark_mode { Color32::from_rgb(166, 227, 161) } else { Color32::from_rgb(64, 160, 43) } }
    fn col_red(&self)    -> Color32 { if self.is_dark_mode { Color32::from_rgb(243, 139, 168) } else { Color32::from_rgb(210, 15, 57) } }
    fn col_orange(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(250, 179, 135) } else { Color32::from_rgb(254, 100, 11) } }
    fn col_text(&self)   -> Color32 { if self.is_dark_mode { Color32::from_rgb(205, 214, 244) } else { Color32::from_rgb(76, 79, 105) } }
    fn col_gray(&self)   -> Color32 { if self.is_dark_mode { Color32::from_rgb(166, 173, 200) } else { Color32::from_rgb(140, 143, 160) } }
    fn col_bg_btn(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(55, 55, 85) }    else { Color32::from_rgb(180, 185, 205) } }
    fn col_btn_success(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(35, 90, 55) } else { Color32::from_rgb(45, 145, 75) } }
    fn col_btn_danger(&self)  -> Color32 { if self.is_dark_mode { Color32::from_rgb(100, 40, 50) } else { Color32::from_rgb(175, 45, 60) } }

    // ── Toast helpers ────────────────────────────────────────────────────
    fn show_toast(&mut self, msg: &str) {
        self.toast_msg = msg.to_string();
        self.toast_until = Some(Instant::now() + std::time::Duration::from_secs(3));
    }

    fn toast_active(&self) -> bool {
        self.toast_until.map_or(false, |t| Instant::now() < t)
    }

    // ── Averaging window helper (mirrors Python get_avg_height) ──────────
    fn get_avg_height(&self) -> f64 {
        let window_s = self.avg_window_s;
        let pts = self.h_buf.get_line_points();
        if pts.is_empty() { return self.current_h; }
        let now = pts.last().map(|p| p[0]).unwrap_or(0.0);
        let vals: Vec<f64> = pts.iter()
            .rev()
            .take_while(|p| now - p[0] <= window_s)
            .map(|p| p[1])
            .collect();
        if vals.is_empty() { self.current_h } else { vals.iter().sum::<f64>() / vals.len() as f64 }
    }

    // ── Profile persistence helpers ──────────────────────────────────────
    fn profile_path(&self, name: &str) -> PathBuf {
        self.config_dir.join(name)
    }

    fn save_profile_to(&mut self, path: &PathBuf) {
        if let Ok(json) = serde_json::to_string_pretty(&self.profile) {
            let _ = std::fs::create_dir_all(&self.config_dir);
            if std::fs::write(path, json).is_ok() {
                self.profile_dirty = false;
            }
        }
    }

    fn load_profile_from(&mut self, path: &PathBuf) {
        if let Ok(data) = std::fs::read_to_string(path) {
            if let Ok(prof) = serde_json::from_str::<CisternProfile>(&data) {
                self.profile = prof;
                self.profile.sort_points();
                self.sensor.update_profile(self.profile.clone());
                self.profile_dirty = false;
            }
        }
    }

    /// Record a path in the recent profiles list (item 6).
    fn push_recent(&mut self, path: &PathBuf) {
        let s = path.to_string_lossy().to_string();
        self.recent_profiles.retain(|r| r != &s);
        self.recent_profiles.insert(0, s);
        self.recent_profiles.truncate(5);
        self.save_settings();
    }

    /// Open a native Save-file dialog and save the current profile.
    fn save_profile_dialog(&mut self) {
        let default = self.config_dir.clone();
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Save Profile")
            .set_directory(&default)
            .add_filter("JSON profile", &["json"])
            .set_file_name(&format!("{}.json", self.profile.name))
            .save_file()
        {
            self.save_profile_to(&path);
            self.push_recent(&path); // Item 6
            self.show_toast("Profile saved.");
        }
    }

    /// Open a native Open-file dialog and load a profile.
    fn load_profile_dialog(&mut self) {
        let default = self.config_dir.clone();
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Load Profile")
            .set_directory(&default)
            .add_filter("JSON profile", &["json"])
            .pick_file()
        {
            self.load_profile_from(&path);
            self.push_recent(&path); // Item 6
            self.show_toast("Profile loaded.");
        }
    }

    /// Import calibration points from a CSV file (columns: P,H,V).
    fn import_cal_csv_dialog(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Import Calibration CSV")
            .add_filter("CSV", &["csv"])
            .pick_file()
        {
            match std::fs::read_to_string(&path) {
                Ok(data) => {
                    let mut imported = 0usize;
                    for line in data.lines().skip(1) { // skip header
                        let cols: Vec<&str> = line.split(',').collect();
                        if cols.len() >= 3 {
                            if let (Ok(p), Ok(h), Ok(v)) = (
                                cols[0].trim().parse::<f64>(),
                                cols[1].trim().parse::<f64>(),
                                cols[2].trim().parse::<f64>(),
                            ) {
                                if p >= 0.0 && h >= 0.0 && v >= 0.0 {
                                    self.profile.points.retain(|pt| (pt.p - p).abs() > 1e-6);
                                    self.profile.points.push(CalibrationPoint { p, h, v });
                                    imported += 1;
                                }
                            }
                        }
                    }
                    self.profile.sort_points();
                    self.sensor.update_profile(self.profile.clone());
                    self.profile_dirty = true;
                    self.show_toast(&format!("Imported {} calibration points.", imported));
                }
                Err(e) => self.show_toast(&format!("CSV read error: {}", e)),
            }
        }
    }

    fn save_settings(&self) {
        let settings = AppSettings {
            port:            self.target_port.clone(),
            baud:            self.target_baud,
            io_port:         self.target_io_port,
            polling_ms:      self.target_polling,
            pressure_unit:   self.setting_pressure_unit.clone(),
            avg_window:      self.setting_avg_window.clone(),
            cwl_mode:        self.setting_cwl_mode.clone(),
            cwl_drop:        self.setting_cwl_drop.clone(),
            cwl_smooth:      self.setting_cwl_smooth.clone(),
            ui_refresh:      self.setting_ui_refresh.clone(),
            ch_refresh:      self.setting_ch_refresh.clone(),
            temp_offset:     self.setting_temp_offset.clone(),
            chart_window:    self.chart_window_val.clone(),
            chart_smooth:    self.chart_smooth_val.clone(),
            dark_mode:       self.is_dark_mode,
            recent_profiles: self.recent_profiles.clone(),
            font_scale:      self.font_scale,
            first_run:       false, // mark wizard completed on first save
        };
        let _ = std::fs::create_dir_all(&self.config_dir);
        let path = self.config_dir.join("settings.json");
        if let Ok(json) = serde_json::to_string_pretty(&settings) {
            let _ = std::fs::write(path, json);
        }
    }

    fn load_settings(&mut self) {
        let path = self.config_dir.join("settings.json");
        if let Ok(data) = std::fs::read_to_string(&path) {
            if let Ok(s) = serde_json::from_str::<AppSettings>(&data) {
                self.target_port           = s.port;
                self.target_baud           = s.baud;
                self.target_io_port        = s.io_port;
                self.target_polling        = s.polling_ms;
                self.setting_pressure_unit = s.pressure_unit;
                self.setting_avg_window    = s.avg_window;
                self.setting_cwl_mode      = s.cwl_mode;
                self.setting_cwl_drop      = s.cwl_drop;
                self.setting_cwl_smooth    = s.cwl_smooth;
                self.setting_ui_refresh    = s.ui_refresh;
                self.setting_ch_refresh    = s.ch_refresh;
                self.setting_temp_offset   = s.temp_offset;
                self.chart_window_val      = s.chart_window;
                self.chart_smooth_val      = s.chart_smooth;
                self.is_dark_mode          = s.dark_mode;
                self.theme_applied         = false;
                self.recent_profiles       = s.recent_profiles;
                self.font_scale            = s.font_scale;
                self.show_wizard           = s.first_run; // show on first-ever run
                self.avg_window_s          = self.setting_avg_window.parse().unwrap_or(0.5);
            }
        }
        // Push loaded temp offset to sensor thread
        if let Ok(off) = self.setting_temp_offset.parse::<f64>() {
            self.sensor.set_temp_offset(off);
        }
        // Also load default profile if present
        let default_path = self.profile_path("default_profile.json");
        if default_path.exists() {
            self.load_profile_from(&default_path);
        }
    }

    /// Decode PI1789 status byte (active-LOW bits) into a short string.
    fn decode_status_text(status: u8) -> String {
        if status & 0x80 == 0 { return "FAULT".to_string(); }
        if status & 0x40 == 0 { return "Over-range".to_string(); }
        if status & 0x20 == 0 { return "Under-range".to_string(); }
        "OK".to_string()
    }

    fn apply_theme(&self, ctx: &egui::Context) {
        let mut vis = if self.is_dark_mode { egui::Visuals::dark() } else { egui::Visuals::light() };
        vis.widgets.noninteractive.rounding = egui::Rounding::same(8.0);
        vis.widgets.inactive.rounding = egui::Rounding::same(6.0);
        vis.widgets.hovered.rounding = egui::Rounding::same(6.0);
        vis.widgets.active.rounding = egui::Rounding::same(6.0);
        vis.window_rounding = egui::Rounding::same(8.0);
        
        vis.widgets.inactive.bg_stroke = egui::Stroke::NONE;

        if self.is_dark_mode {
            vis.window_fill = Color32::from_rgb(30, 30, 46); // WindowBg
            vis.panel_fill  = Color32::from_rgb(42, 42, 61); // ChildBg
            vis.extreme_bg_color = Color32::from_rgb(55, 55, 77); // Input Fields Background
            vis.widgets.inactive.bg_fill = Color32::from_rgb(55, 55, 85);
            vis.widgets.hovered.bg_fill  = Color32::from_rgb(75, 75, 110);
            vis.widgets.active.bg_fill   = Color32::from_rgb(90, 90, 130);
        } else {
            vis.window_fill = Color32::from_rgb(239, 241, 245);
            vis.panel_fill  = Color32::from_rgb(220, 224, 232);
            vis.extreme_bg_color = Color32::from_rgb(204, 208, 218);
            vis.widgets.inactive.bg_fill = Color32::from_rgb(180, 185, 205);
            vis.widgets.hovered.bg_fill  = Color32::from_rgb(162, 168, 192);
            vis.widgets.active.bg_fill   = Color32::from_rgb(144, 151, 178);
        }
        ctx.set_visuals(vis);
    }

    /// Export the last `window_mins` minutes of ring-buffer data to a timestamped CSV file.
    fn export_last_minutes(&mut self, window_mins: u32) {
        let window_s = window_mins as f64 * 60.0;
        let pts_p = self.p_buf.get_line_points();
        let pts_h = self.h_buf.get_line_points();
        let pts_v = self.v_buf.get_line_points();
        let pts_f = self.f_buf.get_line_points();
        let n = pts_p.len().min(pts_h.len()).min(pts_v.len()).min(pts_f.len());
        if n == 0 { self.show_toast("No data in buffer."); return; }

        let last_t = pts_p[n - 1][0];
        let cutoff  = last_t - window_s;

        let fname = format!("log_last{}min_{}.csv",
            window_mins, Local::now().format("%Y%m%d_%H%M%S"));
        match File::create(&fname) {
            Ok(f) => {
                let mut bw = std::io::BufWriter::new(f);
                let _ = writeln!(bw, "Time(s),P(bar),H(mm),V(L),Flow(L/s)");
                let mut rows = 0usize;
                for i in 0..n {
                    if pts_p[i][0] >= cutoff {
                        let _ = writeln!(bw, "{:.3},{:.5},{:.1},{:.2},{:.3}",
                            pts_p[i][0], pts_p[i][1],
                            pts_h[i][1], pts_v[i][1], pts_f[i][1]);
                        rows += 1;
                    }
                }
                self.show_toast(&format!("Exported {} rows → {}", rows, fname));
            }
            Err(e) => self.show_toast(&format!("Export failed: {}", e)),
        }
    }

    fn toggle_csv_log(&mut self) {
        if self.is_logging {
            self.is_logging = false;
            if let Some(mut f) = self.csv_file.take() { let _ = f.flush(); }
        } else {
            if !self.open_new_csv_segment() {
                self.show_toast("⚠ Could not create CSV log file.");
            }
        }
    }

    /// Open a fresh BufWriter CSV segment. Returns true on success.
    fn open_new_csv_segment(&mut self) -> bool {
        let fname = format!("EN14055_Record_{}.csv", Local::now().format("%Y%m%d_%H%M%S"));
        match File::create(&fname) {
            Ok(f) => {
                let mut bw = std::io::BufWriter::new(f);
                let _ = writeln!(bw, "Time(s),P(bar),H(mm),V(L),Flow(L/s),Temp(C)");
                self.csv_file = Some(bw);
                self.csv_bytes_written = 0;
                self.is_logging = true;
                true
            }
            Err(_) => false,
        }
    }

    fn draw_calibration_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_calibration_modal;
        egui::Window::new("Edit Calibration Profile").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("Profile Name:");
                if ui.text_edit_singleline(&mut self.profile.name).changed() {
                    self.profile_dirty = true;
                }
            });
            ui.add_space(8.0);

            ui.horizontal(|ui| {
                ui.label("Overflow (OF):");
                if ui.add(egui::DragValue::new(&mut self.profile.overflow).speed(0.1)).changed() {
                    self.profile_dirty = true;
                }
                ui.label("Water Discharge (WD):");
                if ui.add(egui::DragValue::new(&mut self.profile.water_discharge).speed(0.1)).changed() {
                    self.profile_dirty = true;
                }
            });
            ui.add_space(8.0);

            ui.horizontal(|ui| {
                ui.heading("Points Mapping");
                ui.add_space(8.0);
                let undo_n = self.cal_undo_stack.len();
                let undo_lbl = if undo_n > 0 { format!("↩ Undo ({})", undo_n) } else { "↩ Undo".to_string() };
                let undo_btn = egui::Button::new(&undo_lbl);
                if ui.add_enabled(undo_n > 0, undo_btn).clicked() {
                    if let Some(prev) = self.cal_undo_stack.pop() {
                        self.profile = prev;
                        self.sensor.update_profile(self.profile.clone());
                        self.profile_dirty = true;
                    }
                }
                if ui.button("📂 Import CSV").clicked() {
                    self.import_cal_csv_dialog();
                }
            });

            // Item 10: Delete per row (Up/Down removed — points must stay sorted by P for interp_hv)
            TableBuilder::new(ui)
                .striped(true).cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                .column(Column::initial(80.0)).column(Column::initial(80.0))
                .column(Column::initial(80.0)).column(Column::remainder())
                .header(24.0, |mut header| {
                    header.col(|ui| { ui.strong("P (bar)"); });
                    header.col(|ui| { ui.strong("H (mm)"); });
                    header.col(|ui| { ui.strong("V (L)"); });
                    header.col(|ui| { ui.strong("Del"); });
                })
                .body(|mut body| {
                    let mut to_remove: Option<usize> = None;
                    for (i, pt) in self.profile.points.iter().enumerate() {
                        body.row(24.0, |mut row| {
                            row.col(|ui| { ui.label(format!("{:.4}", pt.p)); });
                            row.col(|ui| { ui.label(format!("{:.1}", pt.h)); });
                            row.col(|ui| { ui.label(format!("{:.2}", pt.v)); });
                            row.col(|ui| {
                                if ui.button("X").clicked() { to_remove = Some(i); }
                            });
                        });
                    }
                    if let Some(idx) = to_remove {
                        self.cal_undo_stack.push(self.profile.clone());
                        if self.cal_undo_stack.len() > 20 { self.cal_undo_stack.remove(0); }
                        self.profile.points.remove(idx);
                        self.profile.sort_points();
                        self.sensor.update_profile(self.profile.clone());
                        self.profile_dirty = true;
                    }
                });

            // Item 11: mini calibration curve preview (H vs P)
            if self.profile.points.len() >= 2 {
                ui.add_space(6.0);
                ui.label(RichText::new("Calibration curve preview (H vs P):").color(self.col_gray()).size(12.0));
                let preview_pts: Vec<[f64; 2]> = self.profile.points.iter()
                    .map(|pt| [pt.p, pt.h])
                    .collect();
                let line = egui_plot::Line::new(preview_pts).color(self.col_accent()).width(1.5);
                egui_plot::Plot::new("cal_preview")
                    .height(100.0)
                    .allow_drag(false).allow_zoom(false).allow_scroll(false)
                    .show_axes([true, true])
                    .label_formatter(|_, v| format!("P={:.4} bar\nH={:.1} mm", v.x, v.y))
                    .show(ui, |plot_ui| { plot_ui.line(line); });
            }

            // Validation message
            if !self.cal_validation_msg.is_empty() {
                ui.colored_label(self.col_red(), &self.cal_validation_msg.clone());
            }
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                ui.label("P(bar):"); ui.add(egui::TextEdit::singleline(&mut self.cal_p_in).desired_width(50.0));
                if ui.button("Read").clicked() { self.cal_p_in = format!("{:.4}", self.current_p); }
                ui.label("H(mm):"); ui.add(egui::TextEdit::singleline(&mut self.cal_h_in).desired_width(50.0));
                ui.label("V(L):"); ui.add(egui::TextEdit::singleline(&mut self.cal_v_in).desired_width(50.0));
                if ui.button("+ Add").clicked() {
                    self.cal_validation_msg.clear();
                    match (self.cal_p_in.parse::<f64>(), self.cal_h_in.parse::<f64>(), self.cal_v_in.parse::<f64>()) {
                        (Ok(p), Ok(h), Ok(v)) => {
                            if p < 0.0 { self.cal_validation_msg = "P must be ≥ 0".to_string(); }
                            else if h < 0.0 { self.cal_validation_msg = "H must be ≥ 0".to_string(); }
                            else if v < 0.0 { self.cal_validation_msg = "V must be ≥ 0".to_string(); }
                            else {
                                // Check monotonicity: H should increase with P
                                let sorted = {
                                    let mut tmp = self.profile.points.clone();
                                    tmp.retain(|pt| (pt.p - p).abs() > 1e-6);
                                    tmp.push(CalibrationPoint { p, h, v });
                                    tmp.sort_by(|a, b| a.p.total_cmp(&b.p));
                                    tmp
                                };
                                let monotone = sorted.windows(2).all(|w| w[1].h >= w[0].h);
                                if !monotone {
                                    self.cal_validation_msg = "⚠ H is not monotonically increasing with P".to_string();
                                }
                                // Add even if warning (user may override)
                                self.cal_undo_stack.push(self.profile.clone());
                                if self.cal_undo_stack.len() > 20 { self.cal_undo_stack.remove(0); }
                                self.profile.points.retain(|pt| (pt.p - p).abs() > 1e-6);
                                self.profile.points.push(CalibrationPoint { p, h, v });
                                self.profile.sort_points();
                                self.sensor.update_profile(self.profile.clone());
                                self.profile_dirty = true;
                                self.cal_p_in.clear(); self.cal_h_in.clear(); self.cal_v_in.clear();
                            }
                        }
                        _ => { self.cal_validation_msg = "Invalid number format".to_string(); }
                    }
                }
            });
            ui.add_space(10.0);
            if ui.button("Close").clicked() {
                self.cal_validation_msg.clear();
                self.show_calibration_modal = false;
            }
        });
        self.show_calibration_modal = is_open;
    }

    fn draw_connection_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_connection_modal;
        egui::Window::new("Hardware Connection").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.label("Connect to AL1060 IO-Link Master:");
            ui.add_space(5.0);
            egui::Grid::new("conn_grid").show(ui, |ui| {
                ui.label("COM Port:");
                // Item 5: show port type / device description alongside port name
                egui::ComboBox::from_id_source("conn_port").selected_text(&self.target_port).show_ui(ui, |ui| {
                    if let Ok(ports) = serialport::available_ports() {
                        for p in ports {
                            let desc = match &p.port_type {
                                serialport::SerialPortType::UsbPort(info) => {
                                    let prod = info.product.clone().unwrap_or_default();
                                    if prod.is_empty() { p.port_name.clone() }
                                    else { format!("{} — {}", p.port_name, prod) }
                                }
                                serialport::SerialPortType::BluetoothPort => {
                                    format!("{} — Bluetooth", p.port_name)
                                }
                                _ => p.port_name.clone(),
                            };
                            ui.selectable_value(&mut self.target_port, p.port_name, desc);
                        }
                    }
                });
                ui.end_row();

                ui.label("Baud Rate:");
                egui::ComboBox::from_id_source("conn_baud").selected_text(self.target_baud.to_string()).show_ui(ui, |ui| {
                    for &b in &[9600, 19200, 38400, 57600, 115200, 230400, 460800] {
                        ui.selectable_value(&mut self.target_baud, b, b.to_string());
                    }
                });
                ui.end_row();

                ui.label("IO-Link Port:");
                egui::ComboBox::from_id_source("conn_ioport").selected_text(format!("Port {}", self.target_io_port)).show_ui(ui, |ui| {
                    for p in 1u32..=8 {
                        ui.selectable_value(&mut self.target_io_port, p, format!("Port {}", p));
                    }
                });
                ui.end_row();

                ui.label("Polling (ms):");
                egui::ComboBox::from_id_source("conn_poll").selected_text(self.target_polling.to_string()).show_ui(ui, |ui| {
                    for &p in &[10, 20, 50, 100, 200, 500] {
                        ui.selectable_value(&mut self.target_polling, p, p.to_string());
                    }
                });
                ui.end_row();
            });
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if ui.button("Close").clicked() { self.show_connection_modal = false; }
            });
        });
        self.show_connection_modal = is_open;
    }

    fn draw_colors_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_colors_modal;
        egui::Window::new("Chart Line Colors").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.label("Click a swatch to change color.");
            ui.add_space(6.0);
            egui::Grid::new("line_colors_grid").show(ui, |ui| {
                ui.label("Sensor:"); ui.color_edit_button_srgba(&mut self.color_sensor); ui.end_row();
                ui.label("MWL:"); ui.color_edit_button_srgba(&mut self.color_mwl); ui.end_row();
                ui.label("Meniscus:"); ui.color_edit_button_srgba(&mut self.color_menis); ui.end_row();
                ui.label("Water Disch.:"); ui.color_edit_button_srgba(&mut self.color_wd); ui.end_row();
                ui.label("CWL (fault):"); ui.color_edit_button_srgba(&mut self.color_cwl); ui.end_row();
                ui.label("Overflow:"); ui.color_edit_button_srgba(&mut self.color_of); ui.end_row();
            });
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if ui.button("Close").clicked() { self.show_colors_modal = false; }
            });
        });
        self.show_colors_modal = is_open;
    }

    fn draw_help_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_help_modal;
        egui::Window::new("Help Guide").open(&mut is_open).collapsible(false).min_width(420.0).show(ctx, |ui| {
            ui.heading("Usage Guide");
            ui.add_space(4.0);
            ui.label("1. Connect to your AL1060 IO-Link Master via the Connections menu.");
            ui.label("2. Observe live metrics in the Left Panel and set EN 14055 limits.");
            ui.label("3. Run flush tests, then press \"Compliance Check\" to see pass/fail.");
            ui.add_space(8.0);
            ui.separator();
            ui.add_space(4.0);
            ui.strong("Keyboard Shortcuts");
            ui.add_space(4.0);
            egui::Grid::new("help_shortcuts_grid").num_columns(2).spacing([20.0, 3.0]).show(ui, |ui| {
                let shortcuts = [
                    ("F5",       "Connect / Disconnect sensor"),
                    ("Space",    "Pause / Resume chart"),
                    ("Ctrl + S", "Save current profile"),
                    ("Ctrl + O", "Open (load) a profile"),
                    ("Ctrl + Z", "Undo last calibration point change"),
                ];
                for (key, desc) in &shortcuts {
                    ui.label(RichText::new(*key).strong().monospace());
                    ui.label(*desc);
                    ui.end_row();
                }
            });
            ui.add_space(10.0);
            if ui.button("Close").clicked() { self.show_help_modal = false; }
        });
        self.show_help_modal = is_open;
    }

    fn draw_about_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_about_modal;
        egui::Window::new("About").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.heading("Cistern Analytics");
            ui.add_space(4.0);
            ui.label("Version 1.0 (Rust Edition)");
            ui.label("Rebuilt with Rust & egui for high-speed robust industrial testing.");
            ui.add_space(10.0);
            if ui.button("Close").clicked() { self.show_about_modal = false; }
        });
        self.show_about_modal = is_open;
    }

    fn draw_program_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_program_modal;
        egui::Window::new("Program Settings").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            egui::Grid::new("prog_settings_grid").num_columns(2).spacing([40.0, 4.0]).show(ui, |ui| {
                ui.label("Interface Theme:");
                let mut is_dark = self.is_dark_mode;
                egui::ComboBox::from_id_source("dlg_p_theme").selected_text(if is_dark { "Dark" } else { "Light" }).show_ui(ui, |ui| {
                    if ui.selectable_value(&mut is_dark, true, "Dark").changed() { self.is_dark_mode = true; self.theme_applied = false; }
                    if ui.selectable_value(&mut is_dark, false, "Light").changed() { self.is_dark_mode = false; self.theme_applied = false; }
                });
                ui.end_row();

                ui.label("Pressure Display Unit:");
                egui::ComboBox::from_id_source("dlg_p_unit").selected_text(&self.setting_pressure_unit).show_ui(ui, |ui| {
                    for val in ["bar", "mbar", "kPa"] { ui.selectable_value(&mut self.setting_pressure_unit, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("Averaging Window (s):");
                egui::ComboBox::from_id_source("dlg_p_avg").selected_text(&self.setting_avg_window).show_ui(ui, |ui| {
                    for val in ["0.1", "0.5", "1.0", "2.0"] { ui.selectable_value(&mut self.setting_avg_window, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("CWL Mode:");
                egui::ComboBox::from_id_source("dlg_p_mode").selected_text(&self.setting_cwl_mode).show_ui(ui, |ui| {
                    for val in ["Automatic", "Manual"] { ui.selectable_value(&mut self.setting_cwl_mode, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("Auto CWL Drop (mm):");
                egui::ComboBox::from_id_source("dlg_p_thresh").selected_text(&self.setting_cwl_drop).show_ui(ui, |ui| {
                    for val in ["0.5", "1.0", "1.5", "2.0", "5.0"] { ui.selectable_value(&mut self.setting_cwl_drop, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("CWL Smooth:");
                egui::ComboBox::from_id_source("dlg_p_smth").selected_text(&self.setting_cwl_smooth).show_ui(ui, |ui| {
                    for val in ["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow", "DEMA", "Median-5", "Kalman", "Savitzky-Golay"] {
                        ui.selectable_value(&mut self.setting_cwl_smooth, val.to_string(), val);
                    }
                });
                ui.end_row();

                ui.label("UI Refresh (ms):");
                egui::ComboBox::from_id_source("dlg_p_ui_ref").selected_text(&self.setting_ui_refresh).show_ui(ui, |ui| {
                    for val in ["20", "50", "100"] { ui.selectable_value(&mut self.setting_ui_refresh, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("Chart Refresh (ms):");
                egui::ComboBox::from_id_source("dlg_p_ch_ref").selected_text(&self.setting_ch_refresh).show_ui(ui, |ui| {
                    for val in ["30", "50", "100", "200"] { ui.selectable_value(&mut self.setting_ch_refresh, val.to_string(), val); }
                });
                ui.end_row();

                ui.label("Temperature Offset (°C):");
                ui.text_edit_singleline(&mut self.setting_temp_offset);
                ui.end_row();

                // Item 20: font / UI scale
                ui.label("UI Scale:");
                egui::ComboBox::from_id_source("dlg_p_scale")
                    .selected_text(format!("{:.0}%", self.font_scale * 100.0))
                    .show_ui(ui, |ui| {
                        for &s in &[0.80f32, 0.90, 1.00, 1.10, 1.25, 1.50] {
                            ui.selectable_value(&mut self.font_scale, s, format!("{:.0}%", s * 100.0));
                        }
                    });
                ui.end_row();
            });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                if ui.button("Save").clicked() {
                    self.avg_window_s = self.setting_avg_window.parse().unwrap_or(0.5);
                    self.save_settings();
                    if let Ok(off) = self.setting_temp_offset.parse::<f64>() {
                        self.sensor.set_temp_offset(off);
                    }
                    self.show_program_modal = false;
                }
                if ui.button("Cancel").clicked() {
                    self.load_settings(); // also refreshes avg_window_s
                    self.show_program_modal = false;
                }
                if ui.button("Reset to Defaults").clicked() {
                    self.setting_pressure_unit = "bar".to_string();
                    self.setting_avg_window    = "0.5".to_string();
                    self.setting_cwl_mode      = "Automatic".to_string();
                    self.setting_cwl_drop      = "1.5".to_string();
                    self.setting_cwl_smooth    = "SMA-5".to_string();
                    self.setting_ui_refresh    = "50".to_string();
                    self.setting_ch_refresh    = "100".to_string();
                    self.setting_temp_offset   = "0.0".to_string();
                    self.chart_window_val      = "30s".to_string();
                    self.chart_smooth_val      = "None".to_string();
                    self.is_dark_mode          = true;
                    self.theme_applied         = false;
                    self.font_scale            = 1.0;
                    self.avg_window_s          = 0.5;
                    self.save_settings();
                    self.show_program_modal = false;
                }
            });
        });
        self.show_program_modal = is_open;
    }

    /// Item 1: 4-step guided setup wizard shown on first run.
    fn draw_wizard(&mut self, ctx: &egui::Context) {
        if !self.show_wizard { return; }
        egui::Window::new("Setup Wizard")
            .collapsible(false)
            .resizable(false)
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .show(ctx, |ui| {
                let steps = ["Step 1 of 4 — Connect Sensor",
                             "Step 2 of 4 — Load or Create a Profile",
                             "Step 3 of 4 — Set EN 14055 Limits",
                             "Step 4 of 4 — Run a Flush Test"];
                ui.heading(steps[self.wizard_step]);
                ui.separator();
                ui.add_space(6.0);

                match self.wizard_step {
                    0 => {
                        ui.label("Open Settings → Hardware Connection (or press F5) to connect to your AL1060 IO-Link Master.");
                        ui.add_space(4.0);
                        ui.label("Select the correct COM port and baud rate, then press Connect.");
                        ui.add_space(8.0);
                        let connected = self.sensor.is_connected;
                        if connected {
                            ui.label(RichText::new("✓ Sensor connected!").color(self.col_green()));
                        } else {
                            ui.label(RichText::new("● Not connected yet.").color(self.col_gray()));
                        }
                    }
                    1 => {
                        ui.label("Use File → Load Profile (Ctrl+O) to load an existing profile,");
                        ui.label("or rename \"Untitled Profile\" in Settings → Edit Calibration Profile.");
                        ui.add_space(8.0);
                        ui.label(RichText::new(format!("Current profile: \"{}\"", self.profile.name)).strong());
                    }
                    2 => {
                        ui.label("In the left panel, under EN 14055 LIMITS:");
                        ui.add_space(4.0);
                        ui.label("① Press \"Set Overflow (OF)\" when water is at the overflow lip.");
                        ui.label("② Press \"Auto-detect MWL/CWL\" while the cistern is full, then cut the supply.");
                        ui.label("③ Press \"Set Meniscus\" after the water surface stabilises.");
                        ui.label("④ Press \"Set Water Discharge (WD)\" at the inlet valve height.");
                    }
                    3 => {
                        ui.label("Select Full Flush or Part Flush in the FLUSH TEST section.");
                        ui.add_space(4.0);
                        ui.label("Press \"Start Flush Measurement\", perform the flush, then \"Stop\".");
                        ui.label("Repeat for at least 3 full flushes and 3 part flushes.");
                        ui.add_space(4.0);
                        ui.label("Finally press \"Compliance Check\" to see the EN 14055 result.");
                    }
                    _ => {}
                }

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    if self.wizard_step > 0 && ui.button("← Back").clicked() {
                        self.wizard_step -= 1;
                    }
                    if self.wizard_step < 3 {
                        if ui.button("Next →").clicked() { self.wizard_step += 1; }
                    } else {
                        if ui.button("Finish").clicked() {
                            self.show_wizard = false;
                            self.save_settings(); // persists first_run = false
                        }
                    }
                    if ui.button("Skip Wizard").clicked() {
                        self.show_wizard = false;
                        self.save_settings();
                    }
                });
            });
    }
}

impl eframe::App for CisternApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {

        // Load settings + default profile once on first frame; re-apply on theme change
        if !self.theme_applied {
            if self.session_start.is_none() {
                // First frame: also load settings
                self.load_settings();
            }
            self.apply_theme(ctx);
            self.theme_applied = true;
        }
        // Item 20: apply font scale only when it differs from current value
        let target_ppp = self.font_scale * ctx.native_pixels_per_point().unwrap_or(1.0);
        if (ctx.pixels_per_point() - target_ppp).abs() > 0.001 {
            ctx.set_pixels_per_point(target_ppp);
        }

        // ---- KEYBOARD SHORTCUTS ----
        ctx.input_mut(|i| {
            // F5 — toggle connect/disconnect
            if i.key_pressed(Key::F5) {
                if self.sensor.is_connected {
                    self.sensor.disconnect();
                    self.reconnect_after = None;
                } else {
                    self.sensor.connect(
                        self.target_port.clone(), self.target_baud,
                        self.target_io_port, self.target_polling,
                    );
                }
            }
            // Space — pause/resume chart
            if i.key_pressed(Key::Space) && !i.modifiers.any() {
                self.chart_paused = !self.chart_paused;
            }
            // Ctrl+S — save profile
            if i.key_pressed(Key::S) && i.modifiers.matches_logically(Modifiers::CTRL) {
                self.save_profile_dialog();
            }
            // Ctrl+O — load profile
            if i.key_pressed(Key::O) && i.modifiers.matches_logically(Modifiers::CTRL) {
                self.load_profile_dialog();
            }
            // Ctrl+Z — undo last calibration change
            if i.key_pressed(Key::Z) && i.modifiers.matches_logically(Modifiers::CTRL) {
                if let Some(prev) = self.cal_undo_stack.pop() {
                    self.profile = prev;
                    self.sensor.update_profile(self.profile.clone());
                }
            }
        });

        // ---- AUTO-RECONNECT ----
        if let Some(when) = self.reconnect_after {
            if Instant::now() >= when && !self.sensor.is_connected {
                self.reconnect_after = None;
                self.sensor.connect(
                    self.target_port.clone(), self.target_baud,
                    self.target_io_port, self.target_polling,
                );
            }
        }

        // ---- DATA PARSING ----
        for evt in self.sensor.poll_events() {
            match evt {
                SensorEvent::Connected => {
                    self.sensor.update_profile(self.profile.clone());
                    // Push current temp offset on (re)connect
                    if let Ok(off) = self.setting_temp_offset.parse::<f64>() {
                        self.sensor.set_temp_offset(off);
                    }
                    // Item 18: start session timer on first connect
                    if self.session_start.is_none() {
                        self.session_start = Some(Instant::now());
                    }
                }
                SensorEvent::Disconnected => { self.sensor_status_text = "--".to_string(); },
                SensorEvent::Error(err) => {
                    eprintln!("Sensor Error: {}", err);
                    // Schedule auto-reconnect in 3 seconds if enabled
                    if self.auto_reconnect && self.reconnect_after.is_none() {
                        self.reconnect_after = Some(Instant::now() + std::time::Duration::from_secs(3));
                        self.show_toast("Connection lost — reconnecting in 3 s…");
                    }
                },
                SensorEvent::Data(pt) => {
                    self.current_p = pt.pressure_bar;
                    self.current_h = pt.height_mm;
                    self.current_v = pt.volume_l;
                    self.current_temp = pt.temp_c;

                    // Decode status byte for status bar display
                    self.sensor_status_text = Self::decode_status_text(pt.status_byte);
                    // Item 16: track last non-OK fault
                    if self.sensor_status_text != "OK" {
                        self.last_fault_text = self.sensor_status_text.clone();
                        self.last_fault_at   = Some(Instant::now());
                    }

                    // Flow rate from last volume sample
                    let last_v = self.v_buf.last_y().unwrap_or(0.0);
                    let l_t = self.v_buf.last_x().unwrap_or(pt.time_s - 0.05);
                    let dt = if pt.time_s - l_t > 0.001 { pt.time_s - l_t } else { 0.05 };
                    self.current_f = (pt.volume_l - last_v) / dt;

                    self.p_buf.push([pt.time_s, pt.pressure_bar]);
                    self.h_buf.push([pt.time_s, pt.height_mm]);
                    self.v_buf.push([pt.time_s, pt.volume_l]);
                    self.f_buf.push([pt.time_s, self.current_f]);
                    self.chart_cache_gen = self.chart_cache_gen.wrapping_add(1);

                    // Keep rolling height history for CWL/RWL smoothing (last 200 samples)
                    self.h_history.push(pt.height_mm);
                    if self.h_history.len() > 200 { self.h_history.remove(0); }

                    // ── CWL auto-detect state machine ──────────────────
                    if self.cwl_state == AutoState::Armed {
                        if pt.height_mm > self.cwl_peak { self.cwl_peak = pt.height_mm; }
                        // smooth_last: O(W) for SMA, O(N) for EMA — no full-vector allocation
                        let smoothed = smooth_last(&self.h_history, &self.setting_cwl_smooth);
                        let drop_thresh: f64 = self.setting_cwl_drop.parse().unwrap_or(1.5);
                        if self.cwl_peak - smoothed >= drop_thresh {
                            self.cwl_state = AutoState::Waiting;
                            self.cwl_timer = Some(Instant::now());
                        }
                    } else if self.cwl_state == AutoState::Waiting {
                        if self.cwl_timer.map_or(false, |t| t.elapsed().as_secs_f64() >= 2.0) {
                            self.profile.cwl = self.get_avg_height();
                            self.profile.mwl_fault = self.cwl_peak;
                            self.cwl_state = AutoState::Done;
                            self.show_toast("CWL & MWL fault captured automatically.");
                        }
                    }

                    // ── RWL auto-detect state machine ──────────────────
                    if self.rwl_state == AutoState::Armed {
                        let smoothed = smooth_last(&self.h_history, &self.setting_cwl_smooth);
                        let drop_thresh: f64 = self.setting_cwl_drop.parse().unwrap_or(1.5);
                        if self.cwl_peak - smoothed >= drop_thresh {
                            self.rwl_state = AutoState::Waiting;
                            self.rwl_timer = Some(Instant::now());
                        }
                    } else if self.rwl_state == AutoState::Waiting {
                        if self.rwl_timer.map_or(false, |t| t.elapsed().as_secs_f64() >= 2.0) {
                            self.profile.residual_wl = self.get_avg_height();
                            self.rwl_state = AutoState::Done;
                            self.show_toast("Residual WL captured.");
                        }
                    }

                    if self.is_logging {
                        // Item 19: auto-rollover at 10 MB
                        if self.csv_bytes_written >= 10 * 1024 * 1024 {
                            if let Some(mut old) = self.csv_file.take() { let _ = old.flush(); }
                            let ok = self.open_new_csv_segment();
                            if ok {
                                self.show_toast("CSV log rolled over (10 MB).");
                            } else {
                                // open failed — stop logging and reset counter to prevent retry storm
                                self.is_logging = false;
                                self.csv_bytes_written = 0;
                                self.show_toast("⚠ CSV rollover failed — logging stopped.");
                            }
                        }
                        if let Some(f) = &mut self.csv_file {
                            let temp_str = pt.temp_c.map_or(String::new(), |t| format!("{:.1}", t));
                            let line = format!("{:.3},{:.5},{:.1},{:.2},{:.3},{}\n",
                                pt.time_s, pt.pressure_bar, pt.height_mm, pt.volume_l,
                                self.current_f, temp_str);
                            self.csv_bytes_written += line.len() as u64;
                            let _ = f.write_all(line.as_bytes());
                        }
                    }
                }
            }
        }

        // ---- MODALS ----
        self.draw_wizard(ctx); // Item 1: first-run wizard
        self.draw_calibration_modal(ctx);
        self.draw_connection_modal(ctx);
        self.draw_program_modal(ctx);
        self.draw_colors_modal(ctx);
        self.draw_help_modal(ctx);
        self.draw_about_modal(ctx);
        
        let mut sc = self.show_compliance_modal;
        egui::Window::new("Compliance Report").open(&mut sc).min_width(420.0).show(ctx, |ui| {
            for r in &self.compliance_results {
                let col = if r.starts_with("[PASS]")   { self.col_green()  }
                     else if r.starts_with("[FAIL]")   { self.col_red()    }
                     else if r.starts_with("[WARN]")   { self.col_orange() }
                     else if r.starts_with("[INFO]")   { self.col_accent() }
                     else                              { self.col_gray()   };
                ui.label(RichText::new(r).color(col));
            }
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                if ui.button("Close").clicked() { self.show_compliance_modal = false; }
                if ui.button("Export TXT").clicked() {
                    let fname = format!("compliance_{}.txt", Local::now().format("%Y%m%d_%H%M%S"));
                    if let Ok(mut f) = File::create(&fname) {
                        let _ = writeln!(f, "EN 14055 Compliance Report — {}", Local::now().format("%Y-%m-%d %H:%M:%S"));
                        let _ = writeln!(f, "Profile: {}", self.profile.name);
                        let _ = writeln!(f, "{}", "─".repeat(60));
                        for r in &self.compliance_results { let _ = writeln!(f, "{}", r); }
                    }
                    self.show_toast(&format!("Report exported: {}", fname));
                }
            });
        });
        self.show_compliance_modal = sc;

        // ---- MENU BAR ----
        egui::TopBottomPanel::top("menu_bar").show(ctx, |ui| {
            egui::menu::bar(ui, |ui| {
                ui.menu_button("File", |ui| {
                    if ui.button("Load Profile…").clicked() {
                        self.load_profile_dialog();
                        ui.close_menu();
                    }
                    if ui.button("Save Profile As…").clicked() {
                        self.save_profile_dialog();
                        ui.close_menu();
                    }
                    // Item 6: recent profiles sub-menu
                    ui.menu_button("Recent Profiles", |ui| {
                        if self.recent_profiles.is_empty() {
                            ui.label(RichText::new("(none)").color(self.col_gray()));
                        } else {
                            let recents = self.recent_profiles.clone();
                            for r in &recents {
                                let label = std::path::Path::new(r)
                                    .file_name()
                                    .map(|n| n.to_string_lossy().to_string())
                                    .unwrap_or_else(|| r.clone());
                                if ui.button(&label).on_hover_text(r).clicked() {
                                    let p = PathBuf::from(r);
                                    self.load_profile_from(&p);
                                    self.push_recent(&p);
                                    self.show_toast("Profile loaded.");
                                    ui.close_menu();
                                }
                            }
                            ui.separator();
                            if ui.button("Clear Recent").clicked() {
                                self.recent_profiles.clear();
                                self.save_settings();
                                ui.close_menu();
                            }
                        }
                    });
                    ui.separator();
                    if ui.button("Set as Default Profile").clicked() {
                        let path = self.profile_path("default_profile.json");
                        self.save_profile_to(&path);
                        self.show_toast("Default profile set.");
                        ui.close_menu();
                    }
                    if ui.button("Clear Default Profile").clicked() {
                        let path = self.profile_path("default_profile.json");
                        let _ = std::fs::remove_file(&path);
                        self.show_toast("Default profile cleared.");
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("Exit").clicked() {
                        self.save_settings();
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                });
                ui.menu_button("Settings", |ui| {
                    if ui.button("Hardware Connection...").clicked() { self.show_connection_modal = true; ui.close_menu(); }
                    if ui.button("Edit Calibration Profile...").clicked() { self.show_calibration_modal = true; ui.close_menu(); }
                    if ui.button("Program Settings...").clicked() { self.show_program_modal = true; ui.close_menu(); }
                    if ui.button("Chart Line Colors...").clicked() { self.show_colors_modal = true; ui.close_menu(); }
                });
                ui.menu_button("Test", |ui| {
                    if ui.button("EN 14055 Compliance Check").clicked() { 
                        self.compliance_results = run_compliance_checks(&self.profile, &self.flushes);
                        self.show_compliance_modal = true;
                        ui.close_menu();
                    }
                });
                ui.menu_button("Help", |ui| {
                    if ui.button("Setup Wizard…").clicked() { self.show_wizard = true; self.wizard_step = 0; ui.close_menu(); }
                    if ui.button("Help...").clicked() { self.show_help_modal = true; ui.close_menu(); }
                    ui.separator();
                    if ui.button("About...").clicked() { self.show_about_modal = true; ui.close_menu(); }
                });
            });
        });

        // ---- STATUS BAR ----
        egui::TopBottomPanel::top("status_bar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("<<").clicked() { self.left_panel_visible = !self.left_panel_visible; }
                // Item 13: theme toggle button
                let theme_icon = if self.is_dark_mode { "☀" } else { "🌙" };
                if ui.button(theme_icon).on_hover_text("Toggle Dark / Light theme").clicked() {
                    self.is_dark_mode = !self.is_dark_mode;
                    self.theme_applied = false;
                    self.save_settings();
                }
                let dirty_marker = if self.profile_dirty { "*" } else { "" };
                ui.label(RichText::new(format!("Active Profile: {}{}", self.profile.name, dirty_marker)).strong());

                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if !self.sensor.is_connected {
                        if ui.button("Connect Sensor").clicked() {
                            self.sensor.connect(
                                self.target_port.clone(),
                                self.target_baud,
                                self.target_io_port,
                                self.target_polling,
                            );
                        }
                        ui.label(RichText::new("Disconnected").color(self.col_gray()));
                    } else {
                        let btn = egui::Button::new(RichText::new("Disconnect").color(Color32::WHITE)).fill(self.col_btn_danger());
                        if ui.add(btn).clicked() { self.sensor.disconnect(); }
                        ui.label(RichText::new(
                            format!("{} {} Port {}", self.target_port, self.target_baud, self.target_io_port)
                        ).color(self.col_green()));
                    }
                    // Item 18: session elapsed time
                    if let Some(start) = self.session_start {
                        let e = start.elapsed().as_secs();
                        let hms = format!("{:02}:{:02}:{:02}", e / 3600, (e % 3600) / 60, e % 60);
                        ui.label(RichText::new(format!("Session: {}", hms)).color(self.col_gray()));
                    }
                    // Item 16: sensor status indicator with fault history in hover
                    let (status_icon, status_col) = if self.sensor.is_connected {
                        let ok = self.sensor_status_text == "OK";
                        ("●", if ok { self.col_green() } else { self.col_red() })
                    } else {
                        ("●", self.col_gray())
                    };
                    let fault_hover = if !self.last_fault_text.is_empty() {
                        if let Some(at) = self.last_fault_at {
                            let secs = at.elapsed().as_secs();
                            format!("{}\nLast fault: {} ({}s ago)", self.sensor_status_text, self.last_fault_text, secs)
                        } else {
                            self.sensor_status_text.clone()
                        }
                    } else {
                        self.sensor_status_text.clone()
                    };
                    ui.label(RichText::new(status_icon).color(status_col))
                        .on_hover_text(fault_hover);
                });
            });
        });

        // ---- LEFT PANEL ----
        if self.left_panel_visible {
            egui::SidePanel::left("left_panel").exact_width(340.0).show(ctx, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| {

                    // 1. LIVE DATA
                    egui::CollapsingHeader::new(RichText::new("LIVE DATA").strong()).default_open(true).show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.vertical(|ui| {
                                ui.label(RichText::new(format!("{:.1} mm", self.current_h)).color(self.col_accent()).size(24.0));
                                let vol_str = if self.vol_unit_ml {
                                    format!("{:.0} mL", self.current_v * 1000.0)
                                } else {
                                    format!("{:.2} L", self.current_v)
                                };
                                ui.label(RichText::new(vol_str).color(self.col_green()).size(24.0));
                                ui.label(RichText::new(format!("{:.4} bar", self.current_p)).color(self.col_gray()).size(16.0));
                            });
                            ui.add_space(20.0);
                            ui.vertical(|ui| {
                                ui.add_space(6.0);
                                if let Some(t) = self.current_temp { ui.label(RichText::new(format!("{:.1} °C", t)).color(self.col_gray()).size(16.0)); } 
                                else { ui.label(RichText::new("-- °C").color(self.col_gray()).size(16.0)); }
                                ui.add_space(4.0);
                                ui.label(RichText::new(format!("{:.3} L/s", self.current_f)).color(self.col_orange()).size(16.0));
                            });
                        });
                    });
                    ui.add_space(5.0);

                    // 2. EN 14055 LIMITS
                    egui::CollapsingHeader::new(RichText::new("EN 14055 LIMITS").strong()).default_open(true).show(ui, |ui| {
                        ui.horizontal(|ui| {
                            let avg_lbl = format!("Set WL (avg {}s)", self.setting_avg_window);
                            let b1 = egui::Button::new(RichText::new(&avg_lbl).color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b1).clicked() {
                                let avg = self.get_avg_height();
                                self.profile.mwl = avg;
                                self.cwl_state = AutoState::Armed;
                                self.cwl_peak  = avg;
                                self.cwl_timer = None;
                                self.rwl_state = AutoState::Armed;
                                self.rwl_timer = None;
                                self.profile_dirty = true;
                            }

                            let b2 = egui::Button::new(RichText::new("Set Meniscus").color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b2).clicked() {
                                if self.profile.overflow > 0.0 {
                                    self.profile.meniscus = self.get_avg_height() - self.profile.overflow;
                                } else {
                                    self.show_toast("⚠ Set Overflow level in Calibration first!");
                                }
                            }
                        });

                        // Set Overflow and Water Discharge from live reading
                        ui.horizontal(|ui| {
                            let b_of = egui::Button::new(RichText::new("Set Overflow (OF)").color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b_of).on_hover_text("Capture current height as Overflow level (OF). §5.2.6").clicked() {
                                self.profile.overflow = self.get_avg_height();
                                self.profile_dirty = true;
                                self.show_toast(format!("Overflow set to {:.1} mm", self.profile.overflow).as_str());
                            }
                            let b_wd = egui::Button::new(RichText::new("Set Water Discharge (WD)").color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b_wd).on_hover_text("Capture current height as Water Discharge inlet level (WD). §5.2.7 air gap = WD − CWL ≥ 20 mm").clicked() {
                                self.profile.water_discharge = self.get_avg_height();
                                self.profile_dirty = true;
                                self.show_toast(format!("Water Discharge set to {:.1} mm", self.profile.water_discharge).as_str());
                            }
                        });

                        // Auto-detect MWL/CWL — arms the state machine
                        let auto_armed = self.cwl_state == AutoState::Armed || self.cwl_state == AutoState::Waiting;
                        let auto_lbl = if auto_armed { "⏳ Auto-detect Armed…" } else { "Auto-detect MWL/CWL" };
                        let auto_col = if auto_armed { self.col_btn_danger() } else { self.col_bg_btn() };
                        let bb1 = egui::Button::new(RichText::new(auto_lbl).color(self.col_text())).fill(auto_col);
                        if ui.add_sized([ui.available_width(), 24.0], bb1).clicked() {
                            if auto_armed {
                                // Cancel
                                self.cwl_state = AutoState::Idle;
                                self.rwl_state = AutoState::Idle;
                            } else if self.profile.overflow <= 0.0 {
                                self.show_toast("⚠ Set Overflow level in Calibration first!");
                            } else {
                                let h = self.get_avg_height();
                                self.cwl_state = AutoState::Armed;
                                self.cwl_peak  = h;
                                self.cwl_timer = None;
                                self.rwl_state = AutoState::Armed;
                                self.rwl_timer = None;
                                self.show_toast("Auto-detect armed — cut supply when ready.");
                            }
                        }

                        // Manual CWL capture
                        let bb2 = egui::Button::new(RichText::new("Manual Set CWL").color(self.col_text())).fill(self.col_bg_btn());
                        if ui.add_sized([ui.available_width(), 24.0], bb2).clicked() {
                            if self.profile.overflow <= 0.0 {
                                self.show_toast("⚠ Set Overflow level in Calibration first!");
                            } else {
                                self.profile.cwl = self.get_avg_height();
                                self.cwl_state   = AutoState::Done;
                                self.show_toast("CWL captured manually.");
                            }
                        }

                        ui.add_space(6.0);
                        egui::Grid::new("limits_grid").num_columns(2).spacing([40.0, 4.0]).show(ui, |ui| {
                            ui.vertical(|ui| {
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("WL (fill):").color(self.col_gray())).on_hover_text("Nominal Water Level (NWL). Safety margin c = OF − NWL ≥ 20 mm (§5.2.6)");
                                    ui.label(format!("{:.1} mm", self.profile.mwl));
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("MWL (fault):").color(self.col_gray())).on_hover_text("Max Water Level during fault. Must be ≤ OF + 20 mm (§5.2.4a)");
                                    if self.profile.mwl_fault > 0.0 {
                                        ui.label(format!("{:.1} mm", self.profile.mwl_fault));
                                    } else { ui.label("\u{2014}"); }
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("CWL (2s):").color(self.col_gray())).on_hover_text("Critical Water Level 2 s after supply cut. Must be ≤ OF + 10 mm (§5.2.4b)");
                                    if self.profile.cwl > 0.0 {
                                        ui.label(format!("{:.1} mm", self.profile.cwl));
                                    } else { ui.label("\u{2014}"); }
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Residual WL:").color(self.col_gray())).on_hover_text("Residual Water Level — minimum height after flush stabilises (§6.5)");
                                    if self.profile.residual_wl > 0.0 {
                                        ui.label(format!("{:.1} mm", self.profile.residual_wl));
                                    } else { ui.label("\u{2014}"); }
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Water Disch.:").color(self.col_gray())).on_hover_text("Water Discharge inlet height. Air gap a = WD − CWL ≥ 20 mm (§5.2.7)");
                                    ui.label(format!("{:.1} mm", self.profile.water_discharge));
                                });
                            });
                            ui.vertical(|ui| {
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Meniscus:").color(self.col_gray())).on_hover_text("Meniscus delta above OF after overflow stabilises. Must be ≤ 5 mm (§5.2.4c)");
                                    ui.label(format!("{:.1} mm", self.profile.meniscus));
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Overflow:").color(self.col_gray())).on_hover_text("Overflow level (OF) — absolute height at which water overflows (§5.2.6)");
                                    ui.label(format!("{:.1} mm", self.profile.overflow));
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Safety c:").color(self.col_gray()));
                                    ui.label(if self.profile.overflow > 0.0 && self.profile.mwl > 0.0 {
                                        format!("{:.1} mm", self.profile.overflow - self.profile.mwl)
                                    } else { "\u{2014}".to_string() });
                                });
                                ui.horizontal(|ui|{
                                    ui.label(RichText::new("Live headroom:").color(self.col_gray()));
                                    ui.label(if self.profile.overflow > 0.0 {
                                        format!("{:.1} mm", self.profile.overflow - self.current_h)
                                    } else { "\u{2014}".to_string() });
                                });
                            });
                        });

                        // Dynamic CWL / RWL status labels
                        ui.add_space(6.0);
                        let cwl_label = match self.cwl_state {
                            AutoState::Idle    => "CWL: IDLE — arm while at MWL".to_string(),
                            AutoState::Armed   => format!("CWL: ARMED (peak {:.1} mm)", self.cwl_peak),
                            AutoState::Waiting => {
                                let remaining = (2.0 - self.cwl_timer.map_or(0.0, |t| t.elapsed().as_secs_f64())).max(0.0);
                                format!("CWL: WAITING {:.1} s…", remaining)
                            },
                            AutoState::Done    => format!("CWL: DONE → {:.1} mm", self.profile.cwl),
                        };
                        let cwl_col = match self.cwl_state {
                            AutoState::Idle | AutoState::Done => self.col_gray(),
                            AutoState::Armed   => self.col_orange(),
                            AutoState::Waiting => self.col_accent(),
                        };
                        ui.label(RichText::new(&cwl_label).color(cwl_col));

                        let rwl_label = match self.rwl_state {
                            AutoState::Idle    => "RWL: IDLE (set WL to arm)".to_string(),
                            AutoState::Armed   => "RWL: ARMED — waiting for flush drop…".to_string(),
                            AutoState::Waiting => {
                                let remaining = (2.0 - self.rwl_timer.map_or(0.0, |t| t.elapsed().as_secs_f64())).max(0.0);
                                format!("RWL: WAITING {:.1} s…", remaining)
                            },
                            AutoState::Done    => format!("RWL: DONE → {:.1} mm", self.profile.residual_wl),
                        };
                        let rwl_col = match self.rwl_state {
                            AutoState::Idle | AutoState::Done => self.col_gray(),
                            AutoState::Armed   => self.col_orange(),
                            AutoState::Waiting => self.col_accent(),
                        };
                        ui.label(RichText::new(&rwl_label).color(rwl_col));
                    });
                    ui.add_space(5.0);

                    // 3. FLUSH TEST
                    egui::CollapsingHeader::new(RichText::new("FLUSH TEST (EN 14055)").strong()).default_open(true).show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("Type:").color(self.col_gray()));
                            egui::ComboBox::from_id_source("cb_flush_type")
                                .selected_text(if self.flush_type_idx == 0 { "Full Flush" } else { "Part Flush" })
                                .show_ui(ui, |ui| {
                                    ui.selectable_value(&mut self.flush_type_idx, 0, "Full Flush");
                                    ui.selectable_value(&mut self.flush_type_idx, 1, "Part Flush");
                                });
                        });
                        
                        // Item 4: Stop Flush confirmation guard
                        if self.stop_flush_confirm {
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("Stop and save this measurement?").color(self.col_orange()));
                                if ui.button("Yes, Stop").clicked() {
                                    self.stop_flush_confirm = false;
                                    self.is_flushing = false;
                                    // Use snapshot captured at "Stop" click — not dialog-close time
                                    let is_full = self.flush_type_idx == 0;
                                    let vol_l = (self.flush_start_vol - self.flush_pending_end_vol).abs();
                                    let time_s = (self.flush_pending_end_t - self.flush_start_time).abs();
                                    let en14055_rate = if vol_l > 3.0 && time_s > 0.0 {
                                        Some((vol_l - 3.0) / time_s)
                                    } else { None };
                                    // Item 7: VLine at the moment "Stop" was clicked
                                    self.flush_vlines.push((self.flush_pending_end_t, is_full));
                                    self.flushes.push(FlushResult { is_full, vol_l, time_s, en14055_rate, temp_c: self.current_temp });
                                }
                                if ui.button("Cancel").clicked() { self.stop_flush_confirm = false; }
                            });
                        } else {
                            let btn_text = if self.is_flushing { "Stop Flush Measurement" } else { "Start Flush Measurement" };
                            let btn_col = if self.is_flushing { self.col_btn_danger() } else { self.col_btn_success() };
                            let btn = egui::Button::new(RichText::new(btn_text).color(Color32::WHITE)).fill(btn_col);
                            if ui.add_sized([ui.available_width(), 26.0], btn).clicked() {
                                if !self.is_flushing {
                                    self.is_flushing = true;
                                    self.flush_start_vol = self.v_buf.last_y().unwrap_or(0.0);
                                    self.flush_start_time = self.v_buf.last_x().unwrap_or(0.0);
                                } else {
                                    // Capture end snapshot immediately at "Stop" click
                                    self.flush_pending_end_vol = self.v_buf.last_y().unwrap_or(0.0);
                                    self.flush_pending_end_t   = self.v_buf.last_x().unwrap_or(0.0);
                                    self.stop_flush_confirm = true;
                                }
                            }
                        }
                        ui.label(RichText::new("* EN col = rate excluding first 1 L and last 2 L").color(self.col_gray()));

                        TableBuilder::new(ui)
                            .striped(true).cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                            .column(Column::initial(20.0)).column(Column::initial(38.0)).column(Column::initial(46.0))
                            .column(Column::initial(42.0)).column(Column::initial(40.0)).column(Column::initial(40.0)).column(Column::initial(26.0))
                            .header(24.0, |mut h| {
                                h.col(|ui|{ui.strong("#");}); h.col(|ui|{ui.strong("Type");}); h.col(|ui|{ui.strong("Vol");});
                                h.col(|ui|{ui.strong("Time");}); h.col(|ui|{ui.strong("L/s");}); h.col(|ui|{ui.strong("EN*");}); h.col(|ui|{ui.strong("Del");});
                            })
                            .body(|mut body| {
                                let mut to_del = None;
                                for (i, f) in self.flushes.iter().enumerate() {
                                    body.row(24.0, |mut row| {
                                        row.col(|ui|{ui.label(format!("{}", i+1));});
                                        row.col(|ui|{ ui.label(RichText::new(if f.is_full {"Full"} else {"Part"}).color(if f.is_full {self.col_accent()} else {self.col_orange()})); });
                                        row.col(|ui|{
                                            let vs = if self.vol_unit_ml { format!("{:.0}mL", f.vol_l * 1000.0) } else { format!("{:.1}L", f.vol_l) };
                                            ui.label(vs);
                                        });
                                        row.col(|ui|{ui.label(format!("{:.1}s", f.time_s));});
                                        row.col(|ui|{ui.label(format!("{:.2}", if f.time_s > 0.0 { f.vol_l / f.time_s } else { 0.0 }));});
                                        row.col(|ui|{
                                            if let Some(en) = f.en14055_rate {
                                                ui.label(RichText::new(format!("{:.2}", en)).color(self.col_accent()));
                                            } else {
                                                ui.label(RichText::new("\u{2014}").color(self.col_gray()));
                                            }
                                        });
                                        row.col(|ui|{if ui.button("X").clicked() { to_del = Some(i); }});
                                    });
                                }
                                if let Some(idx) = to_del {
                                    self.flushes.remove(idx);
                                    if idx < self.flush_vlines.len() { self.flush_vlines.remove(idx); }
                                }
                            });
                        if !self.flushes.is_empty() {
                            ui.horizontal(|ui| {
                                // Item 14: confirm before clearing all flushes
                                if self.clear_flushes_confirm {
                                    ui.label(RichText::new("Delete ALL flush records?").color(self.col_red()));
                                    if ui.button("Yes, Clear").clicked() {
                                        self.flushes.clear();
                                        self.flush_vlines.clear();
                                        self.clear_flushes_confirm = false;
                                    }
                                    if ui.button("Cancel").clicked() { self.clear_flushes_confirm = false; }
                                } else {
                                    if ui.button("Clear All").clicked() { self.clear_flushes_confirm = true; }
                                    if ui.button("Compliance Check").clicked() {
                                        self.compliance_results = run_compliance_checks(&self.profile, &self.flushes);
                                        self.show_compliance_modal = true;
                                    }
                                }
                            });
                        }
                    });
                    ui.add_space(5.0);

                    // 4. DATA LOG
                    egui::CollapsingHeader::new(RichText::new("DATA LOG").strong()).default_open(true).show(ui, |ui| {
                        // Continuous logging
                        let l_text = if self.is_logging { "Stop Data Log (CSV)" } else { "Start Data Log (CSV)" };
                        let l_col = if self.is_logging { self.col_btn_danger() } else { self.col_btn_success() };
                        if ui.add_sized([ui.available_width(), 26.0], egui::Button::new(RichText::new(l_text).color(Color32::WHITE)).fill(l_col)).clicked() {
                            self.toggle_csv_log();
                        }
                        if self.is_logging {
                            ui.label(RichText::new("● Recording…").color(self.col_red()).size(12.0));
                        }

                        ui.add_space(4.0);
                        ui.separator();
                        ui.add_space(4.0);

                        // Snapshot export: last 1 / 2 / 5 / 10 minutes from ring buffer
                        ui.label(RichText::new("Export last:").color(self.col_gray()).size(12.0));
                        ui.horizontal(|ui| {
                            const WINDOWS: &[(&str, u32)] = &[("1 min", 1), ("2 min", 2), ("5 min", 5), ("10 min", 10)];
                            egui::ComboBox::from_id_source("cb_log_win")
                                .selected_text(WINDOWS[self.log_window_idx].0)
                                .show_ui(ui, |ui| {
                                    for (i, &(label, _)) in WINDOWS.iter().enumerate() {
                                        ui.selectable_value(&mut self.log_window_idx, i, label);
                                    }
                                });
                            let mins = WINDOWS[self.log_window_idx].1;
                            if ui.button("💾 Save CSV").on_hover_text(
                                format!("Export the last {} minute(s) of buffered data to a CSV file", mins)
                            ).clicked() {
                                self.export_last_minutes(mins);
                            }
                        });
                    });

                    // Toast notification
                    if self.toast_active() {
                        ui.add_space(8.0);
                        ui.label(RichText::new(&self.toast_msg).color(self.col_orange()));
                    }
                });
            });
        }

        // ---- RIGHT PANEL (PLOT & TOOLBAR) ----
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("Axis:"); 
                egui::ComboBox::from_id_source("cb_axis").selected_text(match self.plot_mode { PlotMode::Height=>"Height (mm)", PlotMode::Volume=>"Volume (L)", PlotMode::Flow=>"Flow Rate (L/s)", _=>"Pressure" })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.plot_mode, PlotMode::Pressure, "Pressure (bar)");
                        ui.selectable_value(&mut self.plot_mode, PlotMode::Height, "Height (mm)");
                        ui.selectable_value(&mut self.plot_mode, PlotMode::Volume, "Volume (L)");
                        ui.selectable_value(&mut self.plot_mode, PlotMode::Flow, "Flow Rate (L/s)");
                    });
                ui.add_space(4.0);
                ui.label("Window:");
                egui::ComboBox::from_id_source("cb_win").selected_text(&self.chart_window_val).show_ui(ui, |ui| {
                    for val in ["10s", "30s", "60s", "5min", "All"] { ui.selectable_value(&mut self.chart_window_val, val.to_string(), val); }
                });
                ui.add_space(4.0);
                ui.label("Smooth:");
                egui::ComboBox::from_id_source("cb_smo").selected_text(&self.chart_smooth_val).show_ui(ui, |ui|{ 
                    for val in ["None", "SMA-5", "SMA-20", "EMA-Fast", "EMA-Slow", "DEMA", "Median-5", "Kalman", "Savitzky-Golay"] {
                        ui.selectable_value(&mut self.chart_smooth_val, val.to_string(), val);
                    }
                });
                ui.add_space(4.0);
                // Item 17: volume unit toggle
                egui::ComboBox::from_id_source("cb_volunit").selected_text(if self.vol_unit_ml { "mL" } else { "L" }).show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.vol_unit_ml, false, "L");
                    ui.selectable_value(&mut self.vol_unit_ml, true, "mL");
                });
                ui.add_space(4.0);
                ui.checkbox(&mut self.chart_auto_scroll, "Auto-scroll");
                ui.add_space(4.0);
                if ui.button(if self.chart_paused { "▶ Resume" } else { "⏸ Pause" }).clicked() { self.chart_paused = !self.chart_paused; }
                if ui.button("📷 Screenshot").on_hover_text("Export current chart data to CSV").clicked() {
                    let fname = format!("chart_export_{}.csv", Local::now().format("%Y%m%d_%H%M%S"));
                    if let Ok(mut f) = File::create(&fname) {
                        let _ = writeln!(f, "Time(s),P(bar),H(mm),V(L),Flow(L/s)");
                        let pts_p = self.p_buf.get_line_points();
                        let pts_h = self.h_buf.get_line_points();
                        let pts_v = self.v_buf.get_line_points();
                        let pts_f = self.f_buf.get_line_points();
                        let n = pts_p.len().min(pts_h.len()).min(pts_v.len()).min(pts_f.len());
                        for i in 0..n {
                            let _ = writeln!(f, "{:.3},{:.5},{:.1},{:.2},{:.3}",
                                pts_p[i][0], pts_p[i][1], pts_h[i][1], pts_v[i][1], pts_f[i][1]);
                        }
                        self.show_toast(&format!("Chart exported: {}", fname));
                    }
                }
                if ui.button("Clear Chart").clicked() { self.p_buf.clear(); self.h_buf.clear(); self.v_buf.clear(); self.f_buf.clear(); self.click_points.clear(); }
                // Item 15: zoom reset
                if ui.button("⟳ Reset Zoom").on_hover_text("Reset chart pan/zoom to fit all data").clicked() {
                    self.reset_zoom = true;
                }
                ui.add_space(6.0);
                ui.label(RichText::new("Delta:").color(self.col_gray()));
                if self.click_points.len() == 2 {
                    let dt = (self.click_points[1][0] - self.click_points[0][0]).abs();
                    let dy = self.click_points[1][1] - self.click_points[0][1];
                    // Item 8: slope = Δy/Δt
                    let slope = if dt > 0.001 { dy / dt } else { 0.0 };
                    ui.label(RichText::new(format!("{:.1}s | Δ{:.2} | {:.3}/s", dt, dy, slope)).color(self.col_accent()));
                } else { ui.label(RichText::new("\u{2014}").color(self.col_accent())); }
                if ui.button("Clear").clicked() { self.click_points.clear(); }
            });
            ui.add_space(4.0);

            // PLOT AREA — rebuild display cache only when data or settings change
            let current_key = (
                self.chart_cache_gen,
                self.plot_mode,
                self.chart_window_val.clone(),
                self.chart_smooth_val.clone(),
            );
            if current_key != self.chart_cache_key {
                self.chart_cache_key = current_key;

                let raw_pts = match self.plot_mode {
                    PlotMode::Pressure => self.p_buf.get_line_points(),
                    PlotMode::Height   => self.h_buf.get_line_points(),
                    PlotMode::Volume   => self.v_buf.get_line_points(),
                    PlotMode::Flow     => self.f_buf.get_line_points(),
                };

                let window_secs: Option<f64> = match self.chart_window_val.as_str() {
                    "10s"  => Some(10.0),
                    "30s"  => Some(30.0),
                    "60s"  => Some(60.0),
                    "5min" => Some(300.0),
                    _      => None,
                };
                let windowed_pts: Vec<[f64; 2]> = if let Some(ws) = window_secs {
                    if let Some(last_t) = raw_pts.last().map(|p| p[0]) {
                        raw_pts.into_iter().filter(|p| last_t - p[0] <= ws).collect()
                    } else { raw_pts }
                } else { raw_pts };

                self.display_pts_cache = if self.chart_smooth_val != "None" && windowed_pts.len() > 1 {
                    let ys: Vec<f64> = windowed_pts.iter().map(|p| p[1]).collect();
                    let smoothed = smooth(&ys, &self.chart_smooth_val);
                    windowed_pts.iter().zip(smoothed.iter()).map(|(p, &sy)| [p[0], sy]).collect()
                } else { windowed_pts };
            }

            let line = Line::new(self.display_pts_cache.clone()).color(self.color_sensor).width(1.5).name("Sensor");

            let plot_bg = if self.is_dark_mode { Color32::from_rgb(25, 25, 40) } else { Color32::from_rgb(248, 249, 252) };
            let grid_col = if self.is_dark_mode { Color32::from_rgb(60, 60, 80) } else { Color32::from_rgb(200, 204, 215) };
            let axis_text = if self.is_dark_mode { Color32::from_rgb(180, 190, 210) } else { Color32::from_rgb(76, 79, 105) };

            ui.scope(|ui| {
                let vis = ui.visuals_mut();
                vis.extreme_bg_color = plot_bg;
                vis.widgets.noninteractive.bg_stroke = egui::Stroke::new(1.0, grid_col);
                // override_text_color applies to axis text digits
                vis.override_text_color = Some(axis_text);

                let mut plot = Plot::new("telemetry_plot")
                    .allow_drag(self.chart_paused)
                    .allow_zoom(self.chart_paused)
                    .allow_scroll(false) // Directs standard mouse wheel to Zoom instead of Pan
                    .legend(egui_plot::Legend::default());
                
                if (self.chart_auto_scroll && !self.chart_paused) || self.reset_zoom {
                    plot = plot.auto_bounds(egui::Vec2b::new(true, true));
                    self.reset_zoom = false;
                }

                let _response = plot.show(ui, |plot_ui| {
                        // Plot crosshairs mimicking crosshairs=True in DPG
                        if let Some(pos) = plot_ui.pointer_coordinate() {
                            let cross_col = grid_col.linear_multiply(0.8);
                            plot_ui.hline(HLine::new(pos.y).color(cross_col));
                            plot_ui.vline(VLine::new(pos.x).color(cross_col));
                        }

                        plot_ui.line(line);
                        if self.plot_mode == PlotMode::Height {
                            if self.profile.overflow > 0.0 { plot_ui.hline(HLine::new(self.profile.overflow).color(self.color_of).name("Overflow")); }
                            if self.profile.mwl > 0.0 { plot_ui.hline(HLine::new(self.profile.mwl).color(self.color_mwl).name("MWL")); }
                            if self.profile.cwl > 0.0 { plot_ui.hline(HLine::new(self.profile.cwl).color(self.color_cwl).name("CWL")); }
                            if self.profile.water_discharge > 0.0 { plot_ui.hline(HLine::new(self.profile.water_discharge).color(self.color_wd).name("Water Disch.")); }
                            let menis_abs = if self.profile.overflow > 0.0 { self.profile.overflow + self.profile.meniscus } else { 0.0 };
                            if menis_abs > 0.0 { plot_ui.hline(HLine::new(menis_abs).color(self.color_menis).name("Meniscus")); }
                        }
                        // Item 7: flush event VLine markers
                        for (i, &(t, is_full)) in self.flush_vlines.iter().enumerate() {
                            let col = if is_full { self.col_accent() } else { self.col_orange() };
                            let lbl = format!("#{} {}", i + 1, if is_full { "Full" } else { "Part" });
                            plot_ui.vline(VLine::new(t).color(col).name(lbl));
                        }
                        if !self.click_points.is_empty() {
                            let pts = Points::new(self.click_points.clone()).radius(6.0).color(self.col_red());
                            plot_ui.points(pts);
                        }
                        if plot_ui.response().clicked() && self.chart_paused {
                            if let Some(pos) = plot_ui.pointer_coordinate() {
                                if self.click_points.len() >= 2 { self.click_points.clear(); }
                                self.click_points.push([pos.x, pos.y]);
                            }
                        }
                    });
            });
        });

        // Keep repainting while connected or while detection timers are running
        let detection_active = self.cwl_state == AutoState::Waiting
            || self.rwl_state == AutoState::Waiting
            || self.toast_active();
        if self.sensor.is_connected || detection_active {
            ctx.request_repaint();
        }
    }
}

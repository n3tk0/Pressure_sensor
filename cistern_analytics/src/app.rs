use crate::sensor::{SensorCore, SensorEvent};
use crate::logic::{CisternProfile, CalibrationPoint, FlushResult, run_compliance_checks};
use eframe::egui;
use egui::{Color32, RichText};
use egui_extras::{TableBuilder, Column};
use egui_plot::{Line, Plot, HLine, Points, VLine};
use std::fs::File;
use std::io::Write;
use chrono::Local;

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
    
    // UI Theme
    is_dark_mode: bool,
    theme_applied: bool,

    // UI Dialogs flags
    show_calibration_modal: bool,
    show_connection_modal: bool,
    show_compliance_modal: bool,
    show_program_modal: bool,
    left_panel_visible: bool,

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
    
    csv_file: Option<File>,
    is_logging: bool,
    
    flush_type_idx: usize,
    flushes: Vec<FlushResult>,
    is_flushing: bool,
    flush_start_vol: f64,
    flush_start_time: f64,
    
    compliance_results: Vec<String>,
    
    current_p: f64,
    current_h: f64,
    current_v: f64,
    current_f: f64,
    current_temp: Option<f64>,
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
            is_dark_mode: true, // Defaulting to Dark
            theme_applied: false,
            show_calibration_modal: false,
            show_connection_modal: false,
            show_compliance_modal: false,
            show_program_modal: false,
            left_panel_visible: true,
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
            compliance_results: Vec::new(),
            current_p: 0.0, current_h: 0.0, current_v: 0.0, current_f: 0.0, current_temp: None,
        }
    }

    // Dynamic Theming Palette
    fn col_accent(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(137, 180, 250) } else { Color32::from_rgb(30, 102, 245) } }
    fn col_green(&self)  -> Color32 { if self.is_dark_mode { Color32::from_rgb(166, 227, 161) } else { Color32::from_rgb(64, 160, 43) } }
    fn col_red(&self)    -> Color32 { if self.is_dark_mode { Color32::from_rgb(243, 139, 168) } else { Color32::from_rgb(210, 15, 57) } }
    fn col_orange(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(250, 179, 135) } else { Color32::from_rgb(254, 100, 11) } }
    fn col_text(&self)   -> Color32 { if self.is_dark_mode { Color32::WHITE }                  else { Color32::BLACK } }
    fn col_gray(&self)   -> Color32 { if self.is_dark_mode { Color32::GRAY }                   else { Color32::DARK_GRAY } }
    fn col_bg_btn(&self) -> Color32 { if self.is_dark_mode { Color32::from_rgb(60, 60, 90) }   else { Color32::from_rgb(210, 210, 230) } }

    fn toggle_csv_log(&mut self) {
        if self.is_logging {
            self.is_logging = false;
            if let Some(mut f) = self.csv_file.take() { let _ = f.flush(); }
        } else {
            let fname = format!("EN14055_Record_{}.csv", Local::now().format("%Y%m%d_%H%M%S"));
            if let Ok(mut f) = File::create(&fname) {
                let _ = writeln!(f, "Time(s),P(bar),H(mm),V(L)");
                self.csv_file = Some(f);
                self.is_logging = true;
            }
        }
    }

    fn draw_calibration_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_calibration_modal;
        egui::Window::new("Edit Calibration Profile").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("Profile Name:");
                ui.text_edit_singleline(&mut self.profile.name);
            });
            ui.add_space(8.0);
            
            ui.horizontal(|ui| {
                ui.label("Overflow (OF):");
                ui.add(egui::DragValue::new(&mut self.profile.overflow).speed(0.1));
                ui.label("Water Discharge (WD):");
                ui.add(egui::DragValue::new(&mut self.profile.water_discharge).speed(0.1));
            });
            ui.add_space(8.0);

            ui.heading("Points Mapping");
            TableBuilder::new(ui)
                .striped(true).cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                .column(Column::initial(80.0)).column(Column::initial(80.0)).column(Column::initial(80.0)).column(Column::remainder())
                .header(24.0, |mut header| {
                    header.col(|ui| { ui.strong("P (bar)"); }); header.col(|ui| { ui.strong("H (mm)"); });
                    header.col(|ui| { ui.strong("V (L)"); }); header.col(|ui| { ui.strong("Del"); });
                })
                .body(|mut body| {
                    let mut to_remove = None;
                    for (i, pt) in self.profile.points.iter().enumerate() {
                        body.row(24.0, |mut row| {
                            row.col(|ui| { ui.label(format!("{:.4}", pt.p)); }); row.col(|ui| { ui.label(format!("{:.1}", pt.h)); });
                            row.col(|ui| { ui.label(format!("{:.2}", pt.v)); });
                            row.col(|ui| { if ui.button("X").clicked() { to_remove = Some(i); } });
                        });
                    }
                    if let Some(idx) = to_remove {
                        self.profile.points.remove(idx);
                        self.profile.sort_points();
                        self.sensor.update_profile(self.profile.clone());
                    }
                });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                ui.label("P(bar):"); ui.add(egui::TextEdit::singleline(&mut self.cal_p_in).desired_width(50.0));
                if ui.button("Read").clicked() { self.cal_p_in = format!("{:.4}", self.current_p); }
                ui.label("H(mm):"); ui.add(egui::TextEdit::singleline(&mut self.cal_h_in).desired_width(50.0));
                ui.label("V(L):"); ui.add(egui::TextEdit::singleline(&mut self.cal_v_in).desired_width(50.0));
                if ui.button("+ Add").clicked() {
                    if let (Ok(p), Ok(h), Ok(v)) = (self.cal_p_in.parse::<f64>(), self.cal_h_in.parse::<f64>(), self.cal_v_in.parse::<f64>()) {
                        self.profile.points.retain(|pt| (pt.p - p).abs() > 1e-6);
                        self.profile.points.push(CalibrationPoint { p, h, v });
                        self.profile.sort_points();
                        self.sensor.update_profile(self.profile.clone());
                    }
                }
            });
            ui.add_space(10.0);
            if ui.button("Close").clicked() { self.show_calibration_modal = false; }
        });
        self.show_calibration_modal = is_open;
    }

    fn draw_connection_modal(&mut self, ctx: &egui::Context) {
        let mut is_open = self.show_connection_modal;
        egui::Window::new("Hardware Connection").open(&mut is_open).collapsible(false).show(ctx, |ui| {
            ui.label("Connect to AL1060 IO-Link Master:");
            ui.add_space(5.0);
            egui::Grid::new("conn_grid").show(ui, |ui| {
                ui.label("COM Port:"); ui.text_edit_singleline(&mut self.target_port); ui.end_row();
                ui.label("Baud Rate:"); ui.add(egui::DragValue::new(&mut self.target_baud)); ui.end_row();
                ui.label("Polling (ms):"); ui.add(egui::DragValue::new(&mut 50)); ui.label("(fixed config)"); ui.end_row();
            });
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if ui.button("Close").clicked() { self.show_connection_modal = false; }
            });
        });
        self.show_connection_modal = is_open;
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
            });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                if ui.button("Save").clicked() { self.show_program_modal = false; }
                if ui.button("Cancel").clicked() { self.show_program_modal = false; }
                if ui.button("Reset to Defaults").clicked() { self.show_program_modal = false; }
            });
        });
        self.show_program_modal = is_open;
    }
}

impl eframe::App for CisternApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {

        // Theme Switch Logic
        if !self.theme_applied {
            if self.is_dark_mode { ctx.set_visuals(egui::Visuals::dark()); } 
            else { ctx.set_visuals(egui::Visuals::light()); }
            self.theme_applied = true;
        }

        // ---- DATA PARSING ----
        for evt in self.sensor.poll_events() {
            match evt {
                SensorEvent::Connected => self.sensor.update_profile(self.profile.clone()),
                SensorEvent::Disconnected | SensorEvent::Error(_) => {},
                SensorEvent::Data(pt) => { 
                    self.current_p = pt.pressure_bar; self.current_h = pt.height_mm; self.current_v = pt.volume_l; self.current_temp = pt.temp_c;
                    let last_v = self.v_buf.last_y().unwrap_or(0.0);
                    let l_t = self.v_buf.last_x().unwrap_or(pt.time_s - 0.05); // naive diff
                    let dt = if pt.time_s - l_t > 0.001 { pt.time_s - l_t } else { 0.05 };
                    self.current_f = (pt.volume_l - last_v) / dt;

                    self.p_buf.push([pt.time_s, pt.pressure_bar]);
                    self.h_buf.push([pt.time_s, pt.height_mm]);
                    self.v_buf.push([pt.time_s, pt.volume_l]);
                    self.f_buf.push([pt.time_s, self.current_f]);
                    
                    if self.is_logging {
                        if let Some(f) = &mut self.csv_file {
                            let _ = writeln!(f, "{:.3},{:.5},{:.1},{:.2}", pt.time_s, pt.pressure_bar, pt.height_mm, pt.volume_l);
                        }
                    }
                }
            }
        }

        // ---- MODALS ----
        self.draw_calibration_modal(ctx);
        self.draw_connection_modal(ctx);
        self.draw_program_modal(ctx);
        
        let mut sc = self.show_compliance_modal;
        egui::Window::new("Compliance Report").open(&mut sc).show(ctx, |ui| {
            for r in &self.compliance_results { ui.label(r); }
            if ui.button("Close").clicked() { self.show_compliance_modal = false; }
        });
        self.show_compliance_modal = sc;

        // ---- MENU BAR ----
        egui::TopBottomPanel::top("menu_bar").show(ctx, |ui| {
            egui::menu::bar(ui, |ui| {
                ui.menu_button("File", |ui| {
                    let _ = ui.button("Load Profile..."); 
                    let _ = ui.button("Save Profile As..."); 
                    ui.separator();
                    let _ = ui.button("Set as Default Profile");
                    let _ = ui.button("Clear Default Profile");
                    ui.separator();
                    if ui.button("Exit").clicked() { ctx.send_viewport_cmd(egui::ViewportCommand::Close); }
                });
                ui.menu_button("Settings", |ui| {
                    if ui.button("Hardware Connection...").clicked() { self.show_connection_modal = true; ui.close_menu(); }
                    if ui.button("Edit Calibration Profile...").clicked() { self.show_calibration_modal = true; ui.close_menu(); }
                    if ui.button("Program Settings...").clicked() { self.show_program_modal = true; ui.close_menu(); }
                    let _ = ui.button("Chart Line Colors...");
                });
                ui.menu_button("Test", |ui| {
                    if ui.button("EN 14055 Compliance Check").clicked() { 
                        self.compliance_results = run_compliance_checks(&self.profile, &self.flushes);
                        self.show_compliance_modal = true;
                        ui.close_menu();
                    }
                });
                ui.menu_button("Help", |ui| { 
                    let _ = ui.button("Help...");
                    ui.separator();
                    let _ = ui.button("About..."); 
                });
            });
        });

        // ---- STATUS BAR ----
        egui::TopBottomPanel::top("status_bar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("<<").clicked() { self.left_panel_visible = !self.left_panel_visible; }
                ui.label(RichText::new(format!("Active Profile: {}", self.profile.name)).strong());
                
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if !self.sensor.is_connected {
                        if ui.button("Connect Sensor").clicked() { self.sensor.connect(self.target_port.clone(), self.target_baud); }
                        ui.label(RichText::new("Disconnected").color(self.col_gray()));
                    } else {
                        let btn = egui::Button::new(RichText::new("Disconnect").color(self.col_text())).fill(self.col_red());
                        if ui.add(btn).clicked() { self.sensor.disconnect(); }
                        ui.label(RichText::new(format!("{} {}", self.target_port, self.target_baud)).color(self.col_green()));
                    }
                    if self.sensor.is_connected { ui.label("●").on_hover_text("Status OK"); } else { ui.label("--"); }
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
                                ui.label(RichText::new(format!("{:.2} L", self.current_v)).color(self.col_green()).size(24.0));
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
                            let b1 = egui::Button::new(RichText::new("Set WL  (avg 0.5s)").color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b1).clicked() { self.profile.mwl = self.current_h; }
                            
                            let b2 = egui::Button::new(RichText::new("Set Meniscus (avg 0.5s)").color(self.col_text())).fill(self.col_bg_btn());
                            if ui.add_sized([150.0, 24.0], b2).clicked() { self.profile.meniscus = self.current_h - self.profile.overflow; }
                        });
                        let bb1 = egui::Button::new(RichText::new("Auto-detect MWL/CWL").color(self.col_text())).fill(self.col_bg_btn());
                        let _ = ui.add_sized([ui.available_width(), 24.0], bb1);
                        let bb2 = egui::Button::new(RichText::new("Manual MWL/CWL").color(self.col_text())).fill(self.col_bg_btn());
                        let _ = ui.add_sized([ui.available_width(), 24.0], bb2);
                        
                        ui.add_space(6.0);
                        egui::Grid::new("limits_grid").num_columns(2).spacing([40.0, 4.0]).show(ui, |ui| {
                            ui.vertical(|ui| {
                                ui.horizontal(|ui|{ ui.label(RichText::new("WL (fill):").color(self.col_gray())); ui.label(format!("{:.1} mm", self.profile.mwl)); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("MWL (fault):").color(self.col_gray())); ui.label("\u{2014}"); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("CWL (2s):").color(self.col_gray())); ui.label(format!("{:.1} mm", self.profile.cwl)); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("Residual WL:").color(self.col_gray())); ui.label("0.0 mm"); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("Water Disch.:").color(self.col_gray())); ui.label(format!("{:.1} mm", self.profile.water_discharge)); });
                            });
                            ui.vertical(|ui| {
                                ui.horizontal(|ui|{ ui.label(RichText::new("Meniscus:").color(self.col_gray())); ui.label(format!("{:.1} mm", self.profile.meniscus)); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("Overflow:").color(self.col_gray())); ui.label(format!("{:.1} mm", self.profile.overflow)); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("Safety c:").color(self.col_gray())); ui.label(if self.profile.overflow > 0.0 { format!("{:.1} mm", self.profile.overflow - self.profile.mwl) } else { "\u{2014}".to_string() }); });
                                ui.horizontal(|ui|{ ui.label(RichText::new("Live headroom:").color(self.col_gray())); ui.label(if self.profile.overflow > 0.0 { format!("{:.1} mm", self.profile.overflow - self.current_h) } else { "\u{2014}".to_string() }); });
                            });
                        });
                        ui.add_space(6.0);
                        ui.label(RichText::new("CWL: IDLE \u{2014} arm while at MWL").color(self.col_gray()));
                        ui.label(RichText::new("RWL: IDLE (set WL to arm)").color(self.col_gray()));
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
                        
                        let btn_text = if self.is_flushing { "Stop Flush Measurement" } else { "Start Flush Measurement" };
                        let btn_col = if self.is_flushing { self.col_red() } else { self.col_green() };
                        let btn = egui::Button::new(RichText::new(btn_text).color(self.col_text())).fill(btn_col);
                        if ui.add_sized([ui.available_width(), 26.0], btn).clicked() {
                            if !self.is_flushing {
                                self.is_flushing = true;
                                self.flush_start_vol = self.v_buf.last_y().unwrap_or(0.0);
                                self.flush_start_time = self.v_buf.last_x().unwrap_or(0.0);
                            } else {
                                self.is_flushing = false;
                                let end_vol = self.v_buf.last_y().unwrap_or(0.0);
                                let end_t = self.v_buf.last_x().unwrap_or(0.0);
                                let is_full = self.flush_type_idx == 0;
                                self.flushes.push(FlushResult {
                                    is_full,
                                    vol_l: (self.flush_start_vol - end_vol).abs(),
                                    time_s: (end_t - self.flush_start_time).abs(),
                                });
                            }
                        }
                        ui.label(RichText::new("* EN col = rate ignoring first 1L and last 2L").color(self.col_gray()));

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
                                        row.col(|ui|{ui.label(format!("{:.1}L", f.vol_l));});
                                        row.col(|ui|{ui.label(format!("{:.1}s", f.time_s));});
                                        row.col(|ui|{ui.label(format!("{:.2}", if f.time_s > 0.0 { f.vol_l / f.time_s } else { 0.0 }));});
                                        row.col(|ui|{ui.label(RichText::new("\u{2014}").color(self.col_gray()));}); // placeholder EN
                                        row.col(|ui|{if ui.button("X").clicked() { to_del = Some(i); }});
                                    });
                                }
                                if let Some(idx) = to_del { self.flushes.remove(idx); }
                            });
                        if !self.flushes.is_empty() {
                            ui.horizontal(|ui| {
                                if ui.button("Clear All").clicked() { self.flushes.clear(); }
                                if ui.button("Compliance Check").clicked() { 
                                    self.compliance_results = run_compliance_checks(&self.profile, &self.flushes);
                                    self.show_compliance_modal = true;
                                }
                            });
                        }
                    });
                    ui.add_space(5.0);

                    // 4. DATA LOG
                    egui::CollapsingHeader::new(RichText::new("DATA LOG").strong()).default_open(true).show(ui, |ui| {
                        let l_text = if self.is_logging { "Stop Data Log (CSV)" } else { "Start Data Log (CSV)" };
                        let l_col = if self.is_logging { self.col_red() } else { self.col_green() };
                        if ui.add_sized([ui.available_width(), 26.0], egui::Button::new(RichText::new(l_text).color(self.col_text())).fill(l_col)).clicked() {
                            self.toggle_csv_log();
                        }
                    });
                });
            });
        }

        // ---- RIGHT PANEL (PLOT & TOOLBAR) ----
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("Axis:"); 
                egui::ComboBox::from_id_source("cb_axis").selected_text(match self.plot_mode { PlotMode::Height=>"Height (mm)", PlotMode::Volume=>"Volume (L)", PlotMode::Flow=>"Flow Rate (L/s)", _=>"Pressure" })
                    .show_ui(ui, |ui| {
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
                ui.checkbox(&mut self.chart_auto_scroll, "Auto-scroll");
                ui.add_space(4.0);
                if ui.button(if self.chart_paused { "▶ Resume" } else { "⏸ Pause" }).clicked() { self.chart_paused = !self.chart_paused; }
                let _ = ui.button("Screenshot"); // Mocked
                if ui.button("Clear Chart").clicked() { self.p_buf.clear(); self.h_buf.clear(); self.v_buf.clear(); self.f_buf.clear(); self.click_points.clear(); }
                ui.add_space(6.0);
                ui.label(RichText::new("Delta:").color(self.col_gray()));
                if self.click_points.len() == 2 {
                    let dt = (self.click_points[1][0] - self.click_points[0][0]).abs();
                    let dy = self.click_points[1][1] - self.click_points[0][1];
                    ui.label(RichText::new(format!("{:.1}s | {:.2}", dt, dy)).color(self.col_accent()));
                } else { ui.label(RichText::new("\u{2014}").color(self.col_accent())); }
                if ui.button("Clear").clicked() { self.click_points.clear(); }
            });
            ui.add_space(4.0);

            // PLOT AREA
            let active_buf = match self.plot_mode { PlotMode::Pressure=>&self.p_buf, PlotMode::Height=>&self.h_buf, PlotMode::Volume=>&self.v_buf, PlotMode::Flow=>&self.f_buf };
            let line = Line::new(active_buf.get_line_points()).color(self.col_accent()).width(1.5).name("Sensor");

            let plot_bg = if self.is_dark_mode { Color32::from_rgb(25, 25, 40) } else { Color32::from_rgb(248, 249, 252) };
            let grid_col = if self.is_dark_mode { Color32::from_rgb(60, 60, 80) } else { Color32::from_rgb(200, 204, 215) };
            let axis_text = if self.is_dark_mode { Color32::from_rgb(180, 190, 210) } else { Color32::from_rgb(76, 79, 105) };

            ui.scope(|ui| {
                let vis = ui.visuals_mut();
                vis.extreme_bg_color = plot_bg;
                vis.widgets.noninteractive.bg_stroke = egui::Stroke::new(1.0, grid_col);
                // override_text_color applies to axis text digits
                vis.override_text_color = Some(axis_text);

                Plot::new("telemetry_plot")
                    .view_aspect(2.0)
                    .auto_bounds(egui::Vec2b::new(self.chart_auto_scroll && !self.chart_paused, self.chart_auto_scroll && !self.chart_paused))
                    .allow_drag(self.chart_paused).allow_zoom(self.chart_paused).legend(egui_plot::Legend::default())
                    .show(ui, |plot_ui| {
                        // Plot crosshairs mimicking crosshairs=True in DPG
                        if let Some(pos) = plot_ui.pointer_coordinate() {
                            let cross_col = grid_col.linear_multiply(0.8);
                            plot_ui.hline(HLine::new(pos.y).color(cross_col));
                            plot_ui.vline(VLine::new(pos.x).color(cross_col));
                        }

                        plot_ui.line(line);
                        if self.plot_mode == PlotMode::Height {
                            if self.profile.overflow > 0.0 { plot_ui.hline(HLine::new(self.profile.overflow).color(self.col_red()).name("Overflow")); }
                            if self.profile.mwl > 0.0 { plot_ui.hline(HLine::new(self.profile.mwl).color(self.col_accent()).name("MWL")); }
                            if self.profile.cwl > 0.0 { plot_ui.hline(HLine::new(self.profile.cwl).color(self.col_orange()).name("CWL")); }
                            if self.profile.water_discharge > 0.0 { plot_ui.hline(HLine::new(self.profile.water_discharge).color(self.col_accent()).name("Water Disch.")); }
                            let menis_abs = if self.profile.overflow > 0.0 { self.profile.overflow + self.profile.meniscus } else { 0.0 };
                            if menis_abs > 0.0 { plot_ui.hline(HLine::new(menis_abs).color(Color32::from_rgb(180, 130, 255)).name("Meniscus")); }
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

        if self.sensor.is_connected { ctx.request_repaint(); }
    }
}

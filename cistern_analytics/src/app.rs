use crate::sensor::{SensorCore, SensorEvent};
use eframe::egui;
use egui_plot::{Line, Plot, PlotPoints};
use std::collections::VecDeque;

const BUFFER_MAX: usize = 12000;

pub struct CisternApp {
    sensor: SensorCore,

    // Controls Panel parameters
    target_port: String,
    target_baud: u32,
    profile_name: String,
    overflow_mm: f64,
    water_discharge_l: f64,

    // Performance Buffers for Graph
    time_buf: VecDeque<f64>,
    pressure_buf: VecDeque<f64>,
    system_logs: Vec<String>,
    
    // Interactive Graph State
    chart_paused: bool,
}

impl CisternApp {
    pub fn new() -> Self {
        Self {
            sensor: SensorCore::new(),
            target_port: "COM8".to_string(), // Typical serial default
            target_baud: 115200,
            profile_name: "Untitled Profile".to_string(),
            overflow_mm: 0.0,
            water_discharge_l: 0.0,
            time_buf: VecDeque::with_capacity(BUFFER_MAX),
            pressure_buf: VecDeque::with_capacity(BUFFER_MAX),
            system_logs: vec!["[INFO] Loaded Rust Interface.".to_string()],
            chart_paused: false,
        }
    }

    pub fn log(&mut self, text: &str) {
        self.system_logs.push(text.to_string());
    }
}

impl eframe::App for CisternApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Safe channel message receiving unblocks graphics thread
        for evt in self.sensor.poll_events() {
            match evt {
                SensorEvent::Connected => self.log(&format!("[SYS] Active connection to {}", self.target_port)),
                SensorEvent::Disconnected => self.log("[SYS] Serial closed."),
                SensorEvent::Error(err) => self.log(&format!("[ERR] {}", err)),
                SensorEvent::Data(pt) => {
                    if self.time_buf.len() >= BUFFER_MAX {
                        self.time_buf.pop_front();
                        self.pressure_buf.pop_front();
                    }
                    self.time_buf.push_back(pt.time_s);
                    self.pressure_buf.push_back(pt.pressure_bar);
                }
            }
        }

        egui::SidePanel::left("left_panel")
            .exact_width(360.0)
            .show(ctx, |ui| {
                ui.heading("Telemetry Controls");
                ui.add_space(6.0);
                
                egui::ScrollArea::vertical().show(ui, |ui| {
                    egui::CollapsingHeader::new("Connection")
                        .default_open(true)
                        .show(ui, |ui| {
                            egui::ComboBox::from_label("COM Port")
                                .selected_text(&self.target_port)
                                .show_ui(ui, |ui| {
                                    ui.selectable_value(&mut self.target_port, "COM1".to_string(), "COM1");
                                    ui.selectable_value(&mut self.target_port, "COM2".to_string(), "COM2");
                                    ui.selectable_value(&mut self.target_port, "COM8".to_string(), "COM8");
                                });

                            ui.horizontal(|ui| {
                                ui.label("Baud Rate:");
                                ui.add(egui::DragValue::new(&mut self.target_baud));
                            });

                            ui.horizontal(|ui| {
                                if !self.sensor.is_connected {
                                    if ui.button("Connect").clicked() {
                                        self.log("Attempting connect..");
                                        self.sensor.connect(self.target_port.clone(), self.target_baud);
                                    }
                                } else {
                                    if ui.button("Disconnect").clicked() {
                                        self.sensor.disconnect();
                                    }
                                }
                            });
                        });
                        
                    egui::CollapsingHeader::new("EN 14055 Setup & Test")
                        .default_open(true)
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.label("Profile Name:");
                                ui.text_edit_singleline(&mut self.profile_name);
                            });
                            ui.horizontal(|ui| {
                                ui.label("Overflow (mm):");
                                ui.add(egui::DragValue::new(&mut self.overflow_mm).speed(0.1));
                            });
                            ui.horizontal(|ui| {
                                ui.label("Water Discharge (L):");
                                ui.add(egui::DragValue::new(&mut self.water_discharge_l).speed(0.1));
                            });

                            ui.horizontal(|ui| {
                                if ui.button("▶ START TEST").clicked() {
                                    self.log("[ACT] Sequence initialized.");
                                }
                                if ui.button("⏹ STOP & SAVE").clicked() {
                                    self.log("[ACT] Flush record saved.");
                                }
                            });
                        });
                        
                    egui::CollapsingHeader::new("Tools & Settings")
                        .show(ui, |ui| {
                            if ui.button("Clear Chart Canvas").clicked() {
                                self.time_buf.clear();
                                self.pressure_buf.clear();
                                self.log("[ACT] Canvas cleared.");
                            }
                        });
                        
                    ui.separator();
                    ui.heading("System Log");
                    egui::ScrollArea::vertical().stick_to_bottom(true).max_height(240.0).show(ui, |ui| {
                        for log in &self.system_logs {
                            ui.label(log);
                        }
                    });
                });
            });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading("Live Telemetry Plot");
                let pause_txt = if self.chart_paused { "▶ Resume Auto-Bounds" } else { "⏸ Pause Chart (Manual Pan)" };
                if ui.button(pause_txt).clicked() {
                    self.chart_paused = !self.chart_paused;
                }
            });

            // Map fixed queues directly correctly matching the zip iteration bounds:
            let points: PlotPoints = self.time_buf.iter().zip(self.pressure_buf.iter())
                .map(|(&t, &p)| [t, p]).collect();
                
            let line = Line::new(points)
                .color(egui::Color32::from_rgb(137, 180, 250)) // Catppuccin Blue
                .name("Pressure (bar)");

            Plot::new("live_telemetry_plot")
                .view_aspect(2.0)
                // When paused, auto_bounds is False, allowing manual zoom/pan with mouse wheel/drag
                // When resumed, auto_bounds is True mapping tight tracking lines.
                .auto_bounds(egui::Vec2b::new(!self.chart_paused, !self.chart_paused))
                .allow_drag(self.chart_paused)
                .allow_zoom(self.chart_paused)
                .show(ui, |plot_ui| plot_ui.line(line));
        });

        // Request constant frame re-renders to achieve buttery smooth 60 FPS while connected natively, 
        // avoiding CPU overhead when not running
        if self.sensor.is_connected {
            ctx.request_repaint();
        }
    }
}

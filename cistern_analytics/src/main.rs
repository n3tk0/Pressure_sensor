#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")] // hide console window on Windows

mod app;
mod logic;
mod sensor;

use app::CisternApp;
use eframe::egui;

fn main() -> Result<(), eframe::Error> {
    // Application window configuration
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1200.0, 800.0])
            .with_min_inner_size([800.0, 600.0])
            .with_title("EN 14055 Cistern Analytics (Rust)"),
        ..Default::default()
    };

    eframe::run_native(
        "EN 14055 Cistern Analytics",
        options,
        Box::new(|cc| {
            // Force dark mode UI Theme
            cc.egui_ctx.set_visuals(egui::Visuals::dark());
            Ok(Box::new(CisternApp::new()))
        }),
    )
}

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")] // hide console window on Windows

mod app;
mod logic;
mod sensor;

use app::CisternApp;
use eframe::egui;

fn load_icon() -> Option<egui::IconData> {
    let icon_bytes = include_bytes!("../../icon.ico");
    let image = image::load_from_memory(icon_bytes).ok()?.to_rgba8();
    let (width, height) = image.dimensions();
    Some(egui::IconData {
        rgba: image.into_raw(),
        width,
        height,
    })
}

fn main() -> Result<(), eframe::Error> {
    // Application window configuration
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1200.0, 800.0])
            .with_min_inner_size([800.0, 600.0])
            .with_title("EN 14055 Cistern Analytics (Rust)")
            .with_icon(std::sync::Arc::new(load_icon().unwrap())),
        ..Default::default()
    };

    eframe::run_native(
        "EN 14055 Cistern Analytics",
        options,
        Box::new(|_cc| {
            Box::new(CisternApp::new()) as Box<dyn eframe::App>
        }),
    )
}

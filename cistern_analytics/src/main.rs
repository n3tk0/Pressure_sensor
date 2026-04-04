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
    let mut viewport = egui::ViewportBuilder::default()
        .with_inner_size([1400.0, 900.0])
        .with_min_inner_size([1320.0, 720.0])
        .with_title("EN 14055 Cistern Analytics (Rust)");
    // Icon is optional — missing file must not prevent startup
    if let Some(icon) = load_icon() {
        viewport = viewport.with_icon(std::sync::Arc::new(icon));
    }
    let options = eframe::NativeOptions { viewport, ..Default::default() };

    eframe::run_native(
        "EN 14055 Cistern Analytics",
        options,
        Box::new(|cc| {
            let mut fonts = egui::FontDefinitions::default();
            fonts.font_data.insert("samsung".to_owned(), egui::FontData::from_static(include_bytes!("../../fonts/SamsungSans-Regular.ttf")));
            // Font family map is always populated by egui; log a warning if somehow missing
            if let Some(list) = fonts.families.get_mut(&egui::FontFamily::Proportional) {
                list.insert(0, "samsung".to_owned());
            }
            // Increase global font size slightly
            let mut style = (*cc.egui_ctx.style()).clone();
            for (_ts, font_id) in style.text_styles.iter_mut() { font_id.size *= 1.25; }
            cc.egui_ctx.set_style(style);
            cc.egui_ctx.set_fonts(fonts);

            Box::new(CisternApp::new()) as Box<dyn eframe::App>
        }),
    )
}

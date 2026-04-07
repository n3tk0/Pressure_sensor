use std::io;

fn main() -> io::Result<()> {
    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        let mut res = winres::WindowsResource::new();
        // Path relative to Cargo.toml
        res.set_icon("../icon.ico");
        res.compile()?;
    }
    Ok(())
}

use crate::logic::CisternProfile;
use crossbeam_channel::{unbounded, bounded, Receiver, Sender, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

const PRESSURE_SCALE_BAR_PER_LSB: f64 = 0.0001;
const PRESSURE_MIN_BAR: f64 = 0.0;
const PRESSURE_MAX_BAR: f64 = 10.0;
const TEMP_SCALE: f64 = 0.01;
const TEMP_MIN_C: f64 = -40.0;
const TEMP_MAX_C: f64 = 200.0;
/// RX buffer high-watermark: trim when the buffer exceeds this size.
const RX_BUF_HIGH_WATERMARK: usize = 4000;
/// After trimming, keep at least this many bytes (searching back from the end).
const RX_BUF_LOW_WATERMARK: usize = 2000;

#[derive(Clone, Debug)]
pub struct TelemetryData {
    pub time_s: f64,
    pub pressure_bar: f64,
    pub temp_c: Option<f64>,
    pub status_byte: u8,
    pub height_mm: f64,
    pub volume_l: f64,
}

pub enum SensorCommand {
    Connect { port: String, baud: u32, io_port: u32, polling_ms: u32 },
    Disconnect,
    UpdateProfile(CisternProfile),
    /// Update temperature calibration offset (°C).
    SetTempOffset(f64),
}

pub enum SensorEvent {
    Connected,
    Disconnected,
    Error(String),
    Data(TelemetryData),
}

pub struct SensorCore {
    tx_cmd: Sender<SensorCommand>,
    rx_evt: Receiver<SensorEvent>,
    pub is_connected: bool,
}

impl SensorCore {
    pub fn new() -> Self {
        let (tx_cmd, rx_cmd) = bounded(100);
        let (tx_evt, rx_evt) = unbounded();
        Self::spawn_background_thread(rx_cmd, tx_evt);
        Self { tx_cmd, rx_evt, is_connected: false }
    }

    pub fn connect(&self, port: String, baud: u32, io_port: u32, polling_ms: u32) {
        let _ = self.tx_cmd.send(SensorCommand::Connect { port, baud, io_port, polling_ms });
    }

    pub fn disconnect(&self) {
        let _ = self.tx_cmd.send(SensorCommand::Disconnect);
    }

    pub fn update_profile(&self, mut profile: CisternProfile) {
        profile.sort_points();
        let _ = self.tx_cmd.send(SensorCommand::UpdateProfile(profile));
    }

    pub fn set_temp_offset(&self, offset: f64) {
        let _ = self.tx_cmd.send(SensorCommand::SetTempOffset(offset));
    }

    pub fn poll_events(&mut self) -> Vec<SensorEvent> {
        let mut events = Vec::new();
        while let Ok(evt) = self.rx_evt.try_recv() {
            match &evt {
                SensorEvent::Connected => self.is_connected = true,
                SensorEvent::Disconnected | SensorEvent::Error(_) => self.is_connected = false,
                _ => {}
            }
            events.push(evt);
        }
        events
    }

    fn spawn_background_thread(rx_cmd: Receiver<SensorCommand>, tx_evt: Sender<SensorEvent>) {
        thread::spawn(move || {
            let mut serial: Option<Box<dyn serialport::SerialPort>> = None;
            let mut read_buf: Vec<u8> = Vec::with_capacity(8192);
            let start_time = Instant::now();
            let marker: &[u8] = b"\x01\x0110";
            let mut profile = CisternProfile::default();
            let mut io_port: u32 = 1;
            let mut temp_offset: f64 = 0.0;
            // Pre-built request bytes — rebuilt only on Connect (not every loop iteration)
            let mut request_bytes: Vec<u8> = Vec::new();

            loop {
                match rx_cmd.try_recv() {
                    Ok(SensorCommand::Connect { port, baud, io_port: p, polling_ms: ms }) => {
                        io_port = p;
                        // Build request once per connection
                        let payload = format!(
                            r#"{{"code": 10, "cid": 1, "adr": "/getdatamulti", "data": {{"datatosend": ["/iolinkmaster/port[{}]/iolinkdevice/pdin"]}}}}"#,
                            io_port
                        );
                        request_bytes = format!("\x01\x0110{:08X}{}", payload.len(), payload).into_bytes();
                        match serialport::new(&port, baud)
                            .timeout(Duration::from_millis(ms as u64))
                            .open()
                        {
                            Ok(s) => {
                                serial = Some(s);
                                let _ = tx_evt.send(SensorEvent::Connected);
                            }
                            Err(e) => {
                                let _ = tx_evt.send(SensorEvent::Error(format!("Connect Error: {}", e)));
                            }
                        }
                    }
                    Ok(SensorCommand::Disconnect) => {
                        serial = None;
                        let _ = tx_evt.send(SensorEvent::Disconnected);
                    }
                    Ok(SensorCommand::UpdateProfile(p)) => {
                        profile = p;
                    }
                    Ok(SensorCommand::SetTempOffset(off)) => {
                        temp_offset = off;
                    }
                    Err(TryRecvError::Disconnected) => break,
                    Err(TryRecvError::Empty) => {}
                }

                if let Some(port_handle) = &mut serial {
                    if let Err(e) = port_handle.write_all(&request_bytes) {
                        let _ = tx_evt.send(SensorEvent::Error(format!("Write fault: {}", e)));
                        serial = None;
                        continue;
                    }

                    let mut chunk = vec![0u8; 4096];
                    match port_handle.read(&mut chunk) {
                        Ok(n) if n > 0 => {
                            read_buf.extend_from_slice(&chunk[..n]);

                            // RX watermark: trim the buffer to avoid unbounded growth
                            if read_buf.len() > RX_BUF_HIGH_WATERMARK {
                                let search_from = read_buf.len().saturating_sub(RX_BUF_LOW_WATERMARK);
                                if let Some(pos) = read_buf[search_from..]
                                    .windows(marker.len())
                                    .position(|w| w == marker)
                                    .map(|p| p + search_from)
                                {
                                    read_buf.drain(..pos);
                                } else {
                                    read_buf.clear();
                                }
                            }

                            while let Some(idx) = read_buf
                                .windows(marker.len())
                                .position(|w| w == marker)
                            {
                                if read_buf.len() < idx + 12 { break; }

                                if let Ok(len_str) = std::str::from_utf8(&read_buf[idx + 4..idx + 12]) {
                                    if let Ok(exp_len) = usize::from_str_radix(len_str, 16) {
                                        let pkt_len = 12 + exp_len;
                                        if read_buf.len() < idx + pkt_len { break; }

                                        if let Ok(js_str) = std::str::from_utf8(&read_buf[idx + 12..idx + pkt_len]) {
                                            if let Some((pressure, temp, status)) =
                                                Self::fast_extract_pdin(js_str, io_port)
                                            {
                                                // Validate pressure range
                                                if pressure >= PRESSURE_MIN_BAR && pressure <= PRESSURE_MAX_BAR {
                                                    // Apply temp offset then validate range
                                                    let validated_temp = temp.map(|t| t + temp_offset).filter(|&t| {
                                                        t >= TEMP_MIN_C && t <= TEMP_MAX_C
                                                    });
                                                    let (h, v) = profile.interp_hv(pressure);
                                                    let _ = tx_evt.send(SensorEvent::Data(TelemetryData {
                                                        time_s: start_time.elapsed().as_secs_f64(),
                                                        pressure_bar: pressure,
                                                        temp_c: validated_temp,
                                                        status_byte: status,
                                                        height_mm: h,
                                                        volume_l: v,
                                                    }));
                                                }
                                            }
                                        }
                                        read_buf.drain(..idx + pkt_len);
                                        continue;
                                    }
                                }
                                read_buf.drain(..idx + marker.len());
                            }
                        }
                        Ok(_) => {}
                        Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                        Err(e) => {
                            let _ = tx_evt.send(SensorEvent::Error(format!("Read connection lost: {}", e)));
                            serial = None;
                        }
                    }
                } else {
                    thread::sleep(Duration::from_millis(100));
                }
            }
        });
    }

    /// Zero-copy PDIN extraction for the given IO-Link port index.
    fn fast_extract_pdin(js: &str, io_port: u32) -> Option<(f64, Option<f64>, u8)> {
        // Build the search key for the configured port
        let key = format!("\"/iolinkmaster/port[{}]/iolinkdevice/pdin\"", io_port);
        let target = js.find(key.as_str())?;
        let obj_start = js[target..].find("\"data\"")?;
        let val_start = target + obj_start + 6;
        let colon = js[val_start..].find(':')?;
        let match_start = val_start + colon + 1;
        let q1 = js[match_start..].find('"')?;
        let hex_start = match_start + q1 + 1;
        let q2 = js[hex_start..].find('"')?;
        let hex_val = &js[hex_start..hex_start + q2];
        Self::decode_hex_string(hex_val)
    }

    fn decode_hex_string(hx: &str) -> Option<(f64, Option<f64>, u8)> {
        if hx.len() < 8 { return None; }
        let raw = u32::from_str_radix(&hx[..8], 16).ok()?;
        let p_bar = (raw as f64) * PRESSURE_SCALE_BAR_PER_LSB;
        let status = if hx.len() >= 10 {
            u8::from_str_radix(&hx[8..10], 16).unwrap_or(0xFF)
        } else {
            0xFF
        };
        let temp = if hx.len() >= 20 {
            u16::from_str_radix(&hx[16..20], 16)
                .ok()
                .map(|t| (t as f64) * TEMP_SCALE)
        } else {
            None
        };
        Some((p_bar, temp, status))
    }
}

use serde_json::Value;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

const PRESSURE_SCALE_BAR_PER_LSB: f64 = 0.0001;
const POLL_INTERVAL_MS: u64 = 50; // Sync with Python's 50ms default polling

#[derive(Clone, Debug)]
pub struct TelemetryData {
    pub time_s: f64,
    pub pressure_bar: f64,
    pub temp_c: Option<f64>,
    pub status_byte: u8,
}

pub enum SensorCommand {
    Connect { port: String, baud: u32 },
    Disconnect,
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
        let (tx_cmd, rx_cmd) = mpsc::channel();
        let (tx_evt, rx_evt) = mpsc::channel();

        Self::spawn_background_thread(rx_cmd, tx_evt);

        Self {
            tx_cmd,
            rx_evt,
            is_connected: false,
        }
    }

    pub fn connect(&self, port: String, baud: u32) {
        let _ = self.tx_cmd.send(SensorCommand::Connect { port, baud });
    }

    pub fn disconnect(&self) {
        let _ = self.tx_cmd.send(SensorCommand::Disconnect);
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
            let marker = b"\x01\x0110";

            loop {
                // 1. Process standard commands safely
                if let Ok(cmd) = rx_cmd.try_recv() {
                    match cmd {
                        SensorCommand::Connect { port, baud } => {
                            match serialport::new(&port, baud)
                                .timeout(Duration::from_millis(100))
                                .open()
                            {
                                Ok(p) => {
                                    serial = Some(p);
                                    let _ = tx_evt.send(SensorEvent::Connected);
                                }
                                Err(e) => {
                                    let _ = tx_evt.send(SensorEvent::Error(format!("Connect Error: {}", e)));
                                }
                            }
                        }
                        SensorCommand::Disconnect => {
                            serial = None;
                            let _ = tx_evt.send(SensorEvent::Disconnected);
                        }
                    }
                }

                // 2. Transmit and collect Telemetry if currently active
                if let Some(port) = &mut serial {
                    // Send IFM multi-get protocol
                    let payload = r#"{"code": 10, "cid": 1, "adr": "/getdatamulti", "data": {"datatosend": ["/iolinkmaster/port[1]/iolinkdevice/pdin"]}}"#;
                    let req = format!("\x01\x0110{:08X}{}", payload.len(), payload);

                    if let Err(e) = port.write_all(req.as_bytes()) {
                        let _ = tx_evt.send(SensorEvent::Error(format!("Write fault: {}", e)));
                        serial = None;
                        continue;
                    }

                    thread::sleep(Duration::from_millis(POLL_INTERVAL_MS)); // Prevent spamming link

                    let mut chunk = vec![0; 4096];
                    match port.read(&mut chunk) {
                        Ok(n) if n > 0 => {
                            read_buf.extend_from_slice(&chunk[..n]);

                            // 3. Hex parsing identical to sensor_core.py
                            while let Some(idx) = read_buf.windows(marker.len()).position(|w| w == marker) {
                                if read_buf.len() < idx + 12 { break; }

                                if let Ok(len_str) = std::str::from_utf8(&read_buf[idx + 4..idx + 12]) {
                                    if let Ok(exp_len) = usize::from_str_radix(len_str, 16) {
                                        let pkt_len = 12 + exp_len;
                                        if read_buf.len() < idx + pkt_len { break; }

                                        if let Ok(js_str) = std::str::from_utf8(&read_buf[idx + 12..idx + pkt_len]) {
                                            if let Ok(js) = serde_json::from_str::<Value>(js_str) {
                                                if let Some(data_str) = js.pointer("/data/iolinkmaster/port[1]/iolinkdevice/pdin/data").and_then(|d| d.as_str()) {
                                                    if let Some((pressure, temp, status)) = Self::decode_hex_string(data_str) {
                                                        let _ = tx_evt.send(SensorEvent::Data(TelemetryData {
                                                            time_s: start_time.elapsed().as_secs_f64(),
                                                            pressure_bar: pressure,
                                                            temp_c: temp,
                                                            status_byte: status,
                                                        }));
                                                    }
                                                }
                                            }
                                        }
                                        read_buf.drain(..idx + pkt_len);
                                        continue;
                                    }
                                }
                                read_buf.drain(..idx + marker.len()); // Bypass bad packet
                            }
                            
                            // Prevent out-of-memory buffer conditions
                            if read_buf.len() > 8000 { read_buf.clear(); } 
                        }
                        Ok(_) => {}
                        Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                        Err(e) => {
                            let _ = tx_evt.send(SensorEvent::Error(format!("Read connection lost: {}", e)));
                            serial = None;
                        }
                    }
                } else {
                    thread::sleep(Duration::from_millis(100)); // Sleep slowly when disconnected
                }
            }
        });
    }

    /// Mirrors PI1789 mapping byte translations matching `_decode_sensor_status()` in Python
    fn decode_hex_string(hx: &str) -> Option<(f64, Option<f64>, u8)> {
        if hx.len() < 8 { return None; }
        
        let raw = u32::from_str_radix(&hx[..8], 16).ok()?;
        let p_bar = (raw as f64) * PRESSURE_SCALE_BAR_PER_LSB;
        
        let status = if hx.len() >= 10 {
            u8::from_str_radix(&hx[8..10], 16).unwrap_or(0xFF)
        } else { 0xFF };
        
        let temp = if hx.len() >= 20 {
            if let Ok(raw_t) = u16::from_str_radix(&hx[16..20], 16) {
                Some((raw_t as f64) * 0.01)
            } else { None }
        } else { None };

        Some((p_bar, temp, status))
    }
}

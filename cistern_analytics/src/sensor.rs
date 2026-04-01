use crate::logic::CisternProfile;
use crossbeam_channel::{unbounded, bounded, Receiver, Sender, TryRecvError}; // 🚀 Оптимизация 4
use std::thread;
use std::time::{Duration, Instant};

const PRESSURE_SCALE_BAR_PER_LSB: f64 = 0.0001;

#[derive(Clone, Debug)]
pub struct TelemetryData {
    pub time_s: f64,
    pub pressure_bar: f64,
    #[allow(dead_code)]
    pub temp_c: Option<f64>,
    #[allow(dead_code)]
    pub status_byte: u8,
    pub height_mm: f64,
    pub volume_l: f64,
}

pub enum SensorCommand {
    Connect { port: String, baud: u32 },
    Disconnect,
    UpdateProfile(CisternProfile),
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
        // Zero-capacity lock-free telemetry queues
        let (tx_evt, rx_evt) = unbounded();

        Self::spawn_background_thread(rx_cmd, tx_evt);

        Self { tx_cmd, rx_evt, is_connected: false }
    }

    pub fn connect(&self, port: String, baud: u32) {
        let _ = self.tx_cmd.send(SensorCommand::Connect { port, baud });
    }

    pub fn disconnect(&self) {
        let _ = self.tx_cmd.send(SensorCommand::Disconnect);
    }
    
    pub fn update_profile(&self, mut profile: CisternProfile) {
        profile.sort_points(); // Сортираме тук (само веднъж)
        let _ = self.tx_cmd.send(SensorCommand::UpdateProfile(profile));
    }

    pub fn poll_events(&mut self) -> Vec<SensorEvent> {
        let mut events = Vec::new();
        // Взимаме всичко налично без блокиране
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
            let mut profile = CisternProfile::default();

            loop {
                // Избягваме блокиране или loop overflow (try_recv е мигновен)
                match rx_cmd.try_recv() {
                    Ok(SensorCommand::Connect { port, baud }) => {
                        match serialport::new(&port, baud).timeout(Duration::from_millis(50)).open() {
                            Ok(p) => {
                                serial = Some(p);
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
                    Err(TryRecvError::Disconnected) => break, // Основната нишка е спряла
                    Err(TryRecvError::Empty) => {}
                }

                if let Some(port) = &mut serial {
                    let payload = r#"{"code": 10, "cid": 1, "adr": "/getdatamulti", "data": {"datatosend": ["/iolinkmaster/port[1]/iolinkdevice/pdin"]}}"#;
                    // Оптимизация 5: Директен request, след което не "заспиваме" с thread::sleep,
                    // а директно четем от хардуерния буфер (блокира до 50ms според COM таймаута).
                    let req = format!("\x01\x0110{:08X}{}", payload.len(), payload);

                    if let Err(e) = port.write_all(req.as_bytes()) {
                        let _ = tx_evt.send(SensorEvent::Error(format!("Write fault: {}", e)));
                        serial = None;
                        continue;
                    }

                    // Няма thread::sleep() тук. Оставяме timeout() на серийния порт да контролира потока.
                    let mut chunk = vec![0; 4096];
                    match port.read(&mut chunk) {
                        Ok(n) if n > 0 => {
                            read_buf.extend_from_slice(&chunk[..n]);

                            while let Some(idx) = read_buf.windows(marker.len()).position(|w| w == marker) {
                                if read_buf.len() < idx + 12 { break; }

                                if let Ok(len_str) = std::str::from_utf8(&read_buf[idx + 4..idx + 12]) {
                                    if let Ok(exp_len) = usize::from_str_radix(len_str, 16) {
                                        let pkt_len = 12 + exp_len;
                                        if read_buf.len() < idx + pkt_len { break; }

                                        if let Ok(js_str) = std::str::from_utf8(&read_buf[idx + 12..idx + pkt_len]) {
                                            
                                            // 🚀 Оптимизация 1: ZERO ALLOCATION JSON парсване!
                                            // Намираме hex стойностите директно без да строим DOM дърво.
                                            if let Some((pressure, temp, status)) = Self::fast_zero_copy_extract_pdin(js_str) {
                                                let (h, v) = profile.interp_hv(pressure);
                                                let _ = tx_evt.send(SensorEvent::Data(TelemetryData {
                                                    time_s: start_time.elapsed().as_secs_f64(),
                                                    pressure_bar: pressure,
                                                    temp_c: temp,
                                                    status_byte: status,
                                                    height_mm: h,
                                                    volume_l: v,
                                                }));
                                            }
                                        }
                                        read_buf.drain(..idx + pkt_len);
                                        continue;
                                    }
                                }
                                read_buf.drain(..idx + marker.len()); 
                            }
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
                    thread::sleep(Duration::from_millis(100)); 
                }
            }
        });
    }

    // 🚀 Оптимизация 1 (Имплементация): Убийствено бързо изваждане от стринга без алокация (memory heap)
    fn fast_zero_copy_extract_pdin(js: &str) -> Option<(f64, Option<f64>, u8)> {
        // Локализираме пътя до PDIN стойността
        let tag = "\"/iolinkmaster/port[1]/iolinkdevice/pdin\"";
        let target = js.find(tag)?;
        // Взимаме 'data: "hex..."'
        let obj_start = js[target..].find("\"data\"")?;
        let val_start = target + obj_start + 6;
        let colon = js[val_start..].find(':')?;
        let match_start = val_start + colon + 1;
        
        // Разделяме кавичките около hex стойността
        let q1 = js[match_start..].find('"')?;
        let hex_start = match_start + q1 + 1;
        let q2 = js[hex_start..].find('"')?;
        let hex_val = &js[hex_start..hex_start+q2];
        
        Self::decode_hex_string(hex_val)
    }

    fn decode_hex_string(hx: &str) -> Option<(f64, Option<f64>, u8)> {
        if hx.len() < 8 { return None; }
        let raw = u32::from_str_radix(&hx[..8], 16).ok()?;
        let p_bar = (raw as f64) * PRESSURE_SCALE_BAR_PER_LSB;
        let status = if hx.len() >= 10 { u8::from_str_radix(&hx[8..10], 16).unwrap_or(0xFF) } else { 0xFF };
        let temp = if hx.len() >= 20 { u16::from_str_radix(&hx[16..20], 16).ok().map(|t| (t as f64) * 0.01) } else { None };
        Some((p_bar, temp, status))
    }
}

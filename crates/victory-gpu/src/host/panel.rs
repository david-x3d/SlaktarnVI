//! Diagnostics: the run's numbers on the picture (`VICTORY_PANEL=1`; off by
//! default; F3 shows and hides it while our window has the focus): white
//! text on a black box in the upper left corner of what is shown, so that a
//! screenshot carries them, with what it costs itself on the submitting
//! thread. Made again twice a second from the counters of
//! the frame-rate line, memory and textures; a frame itself only copies the
//! box out of a buffer in the card's memory. Copied onto the swap chain's
//! buffer, behind the title's picture: saved frames and the frame recorder
//! see the title's own.

use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use windows::Win32::Graphics::Direct3D12::*;
use windows::Win32::Graphics::Dxgi::Common::*;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_F3, VK_F4};
use windows_sys::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use super::{Host, HostList, transition};

/// How often the numbers change.
const EVERY: Duration = Duration::from_millis(500);

/// A character's dots, and with the space beside and below it.
const GLYPH: (u32, u32) = (5, 7);
const CELL: (u32, u32) = (6, 10);
/// Characters in a line, lines, and the box's rim in dots.
const COLUMNS: u32 = 36;
/// The rows below the head: nine pairs, a rule, three lines of the PC's own.
const PAIRS: u32 = 10;
const NOTES: u32 = 3;
/// Dots of the head (the rate at twice the size) and of the rule.
const HEAD: u32 = 2 * CELL.1 + 2;
const RULE: u32 = 5;
const RIM: u32 = 5;

/// The texts are put into one of these in turn: the card reads the others,
/// at most half a second old.
const UPLOADS: usize = 4;

fn asked() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| crate::switch::on("VICTORY_PANEL"))
}

/// Whether it is shown now.
fn shown() -> &'static AtomicBool {
    static SHOWN: OnceLock<AtomicBool> = OnceLock::new();
    SHOWN.get_or_init(|| AtomicBool::new(asked()))
}

/// F3 was pressed since the last call, with one of our windows in front.
fn pressed() -> bool {
    static DOWN: AtomicBool = AtomicBool::new(false);
    key_pressed(VK_F3, &DOWN)
}

/// That key was pressed since the last call (`down` its last state), with
/// one of our windows in front.
fn key_pressed(key: u16, last: &AtomicBool) -> bool {
    // SAFETY: a plain query.
    let down = unsafe { GetAsyncKeyState(i32::from(key)) } < 0;
    if last.swap(down, Ordering::Relaxed) || !down {
        return false;
    }
    let mut process = 0;
    // SAFETY: plain queries; room for the answer.
    unsafe { GetWindowThreadProcessId(GetForegroundWindow(), &mut process) };
    process == std::process::id()
}

/// Seven rows of five dots, the leftmost the highest bit.
#[rustfmt::skip]
const fn glyph(c: u8) -> [u8; 7] {
    match c {
        b'0' => [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
        b'1' => [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        b'2' => [0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111],
        b'3' => [0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110],
        b'4' => [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
        b'5' => [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
        b'6' => [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
        b'7' => [0b11111, 0b00001, 0b00010, 0b00010, 0b00100, 0b00100, 0b00100],
        b'8' => [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
        b'9' => [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
        b'A' => [0b00100, 0b01010, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001],
        b'B' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
        b'C' => [0b01111, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b01111],
        b'D' => [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
        b'E' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
        b'F' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
        b'G' => [0b01111, 0b10000, 0b10000, 0b10011, 0b10001, 0b10001, 0b01111],
        b'H' => [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
        b'I' => [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        b'J' => [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
        b'K' => [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
        b'L' => [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
        b'M' => [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
        b'N' => [0b10001, 0b11001, 0b11001, 0b10101, 0b10011, 0b10011, 0b10001],
        b'O' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        b'P' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
        b'Q' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
        b'R' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
        b'S' => [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
        b'T' => [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
        b'U' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        b'V' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
        b'W' => [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
        b'X' => [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
        b'Y' => [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
        b'Z' => [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
        b'.' => [0, 0, 0, 0, 0, 0b01100, 0b01100],
        b'/' => [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
        b'-' => [0, 0, 0, 0b11111, 0, 0, 0],
        b'%' => [0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011],
        b':' => [0, 0b01100, 0b01100, 0, 0b01100, 0b01100, 0],
        b'+' => [0, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0],
        b' ' => [0; 7],
        _ => [0b11111, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11111],
    }
}

/// The box's size in dots.
const fn dots() -> (u32, u32) {
    (COLUMNS * CELL.0 + 2 * RIM, HEAD + (PAIRS + NOTES) * CELL.1 + RULE + 2 * RIM)
}

/// The box's size in pixels, a dot being `scale` of them each way.
const fn size(scale: u32) -> (u32, u32) {
    (dots().0 * scale, dots().1 * scale)
}

/// Pixels to a dot for a picture that high: a 720-line screenshot of it
/// shows one and a third.
fn scale(height: u32) -> u32 {
    (height / 540).max(1)
}

/// A row of the box.
#[derive(Clone, Debug, PartialEq)]
pub(super) enum Row {
    /// The rate, large, and the frame's time at the right.
    Head(String, String),
    /// A name (dim, left) and its value (right).
    Pair(String, String),
    Rule,
    /// A line of the PC's own (dim).
    Note(String),
}

/// The colours of a dot: the box, values, names, the rate.
const BOX: u8 = 0;
const VALUE: u8 = 1;
const NAME: u8 = 2;
const RATE: u8 = 3;
const SHADES: [[f32; 3]; 4] = [[0.06, 0.06, 0.07], [1.0, 1.0, 1.0], [0.62, 0.63, 0.66], [0.95, 0.26, 0.24]];

/// The dots of the box, a colour each ([`SHADES`]), row by row.
fn drawn(rows: &[Row]) -> Vec<u8> {
    let (width, height) = dots();
    let mut out = vec![BOX; (width * height) as usize];
    // `text` from dot (x, y) on, `k` dots to a glyph's dot.
    let mut put = |text: &str, x: u32, y: u32, k: u32, colour: u8| {
        for (i, c) in text.bytes().enumerate() {
            let rows = glyph(c.to_ascii_uppercase());
            for (gy, bits) in rows.iter().enumerate() {
                for gx in 0..GLYPH.0 {
                    if (bits >> (GLYPH.0 - 1 - gx)) & 1 == 0 {
                        continue;
                    }
                    for (dx, dy) in (0..k).flat_map(|dx| (0..k).map(move |dy| (dx, dy))) {
                        let (px, py) = (x + (i as u32 * CELL.0 + gx) * k + dx, y + gy as u32 * k + dy);
                        if px < width - RIM && py < height {
                            out[(py * width + px) as usize] = colour;
                        }
                    }
                }
            }
        }
    };
    let wide = |text: &str, k: u32| (text.len() as u32 * CELL.0).saturating_sub(1) * k;
    let right = |text: &str| (width - RIM).saturating_sub(wide(text, 1)).max(RIM);
    let mut y = RIM;
    for row in rows {
        match row {
            Row::Head(rate, time) => {
                put(rate, RIM, y, 2, RATE);
                // On the large digits' foot.
                let foot = y + 2 * GLYPH.1 - GLYPH.1;
                put("FPS", RIM + wide(rate, 2) + CELL.0, foot, 1, NAME);
                put(time, right(time), foot, 1, VALUE);
                y += HEAD;
            }
            Row::Pair(name, value) => {
                put(name, RIM, y, 1, NAME);
                put(value, right(value), y, 1, VALUE);
                y += CELL.1;
            }
            Row::Rule => {
                y += RULE;
            }
            Row::Note(text) => {
                put(text, RIM, y, 1, NAME);
                y += CELL.1;
            }
        }
    }
    // The rule's line, where there is one.
    let mut y = RIM;
    for row in rows {
        match row {
            Row::Head(..) => y += HEAD,
            Row::Rule => {
                for x in RIM..width - RIM {
                    out[((y + 1) * width + x) as usize] = NAME;
                }
                y += RULE;
            }
            _ => y += CELL.1,
        }
    }
    out
}

/// A float as a half (for the few shades of the box: finite, 0 to 1).
fn half(v: f32) -> u16 {
    if v <= 0.0 {
        return 0;
    }
    let bits = v.to_bits();
    let exponent = ((bits >> 23) & 0xFF) as i32 - 127 + 15;
    if exponent <= 0 {
        return 0;
    }
    ((exponent as u16) << 10) | ((bits >> 13) & 0x3FF) as u16
}

/// The bytes of a pixel of each of [`SHADES`] in a swap chain's format.
fn colours(format: DXGI_FORMAT) -> Option<[Vec<u8>; 4]> {
    let byte = |v: f32| (v * 255.0 + 0.5) as u8;
    let of = |make: &dyn Fn([f32; 3]) -> Vec<u8>| SHADES.map(make);
    Some(match format {
        DXGI_FORMAT_R8G8B8A8_UNORM => of(&|[r, g, b]| vec![byte(r), byte(g), byte(b), 0xFF]),
        DXGI_FORMAT_B8G8R8A8_UNORM => of(&|[r, g, b]| vec![byte(b), byte(g), byte(r), 0xFF]),
        DXGI_FORMAT_R10G10B10A2_UNORM => of(&|[r, g, b]| {
            let ten = |v: f32| (v * 1023.0 + 0.5) as u32;
            (ten(r) | ten(g) << 10 | ten(b) << 20 | 3 << 30).to_le_bytes().to_vec()
        }),
        DXGI_FORMAT_R16G16B16A16_FLOAT => of(&|[r, g, b]| [r, g, b, 1.0].into_iter().flat_map(|v| half(v).to_le_bytes()).collect()),
        _ => return None,
    })
}

/// The box as rows of `pitch` bytes.
fn painted(rows: &[Row], scale: u32, colours: &[Vec<u8>; 4], pitch: usize) -> Vec<u8> {
    let (width, height) = size(scale);
    let bytes = colours[0].len();
    let dots_wide = dots().0;
    let picture = drawn(rows);
    let mut out = vec![0; pitch * height as usize];
    for y in 0..height / scale {
        let top = pitch * (y * scale) as usize;
        for x in 0..width / scale {
            let colour = &colours[usize::from(picture[(y * dots_wide + x) as usize])];
            for i in 0..scale {
                let at = top + (x * scale + i) as usize * bytes;
                out[at..at + bytes].copy_from_slice(colour);
            }
        }
        for i in 1..scale as usize {
            out.copy_within(top..top + pitch, top + i * pitch);
        }
    }
    out
}

/// The counters read for it. They grow, and go back to 0 with the frame-rate
/// line (right behind a look of ours).
#[derive(Clone, Copy, Default)]
struct Counters([u64; COUNTERS]);

const COUNTERS: usize = 12;

impl Counters {
    fn now() -> Counters {
        use super::{CARD_CLOCK_US, COPY_BACK, PREPARING};
        use crate::copystat::{C, peek};
        let get = |a: &std::sync::atomic::AtomicU64| a.load(Ordering::Relaxed);
        Counters([
            super::worked(),
            get(&CARD_CLOCK_US[0]),
            get(&CARD_CLOCK_US[1]),
            get(&CARD_CLOCK_US[2]),
            get(&PREPARING[3]),
            get(&COPY_BACK[0]),
            get(&COPY_BACK[1]),
            get(&crate::shadow::CARD_STATS[0]),
            crate::draw::dropped_now(),
            peek(C::RecordNs),
            crate::draw::drawn(),
            crate::framegen::shown_total(),
        ])
    }

    /// What came since `before`, per frame.
    fn since(self, before: Counters, frames: f64) -> [f64; COUNTERS] {
        let mut out = [0.0; COUNTERS];
        for (i, out) in out.iter_mut().enumerate() {
            let (was, is) = (before.0[i], self.0[i]);
            *out = if is >= was { is - was } else { is } as f64 / frames;
        }
        out
    }
}

/// The rates of the last 30 seconds, for [`text`].
#[derive(Default)]
struct Rates(std::collections::VecDeque<f64>);

impl Rates {
    /// Notes `rate` (one every [`EVERY`]); the highest, lowest and mean of
    /// the last 30 s.
    fn note(&mut self, rate: f64) -> (f64, f64, f64) {
        let most = (30_000 / EVERY.as_millis()) as usize;
        if self.0.len() == most {
            self.0.pop_front();
        }
        self.0.push_back(rate);
        let high = self.0.iter().copied().fold(0.0, f64::max);
        let low = self.0.iter().copied().fold(f64::INFINITY, f64::min);
        (high, low, self.0.iter().sum::<f64>() / self.0.len() as f64)
    }
}

/// The share of all processors busy since the last call, in percent.
fn processors_busy(last: &mut [u64; 2]) -> f64 {
    use windows_sys::Win32::Foundation::FILETIME;
    use windows_sys::Win32::System::Threading::GetSystemTimes;
    let (mut idle, mut kernel, mut user) = (FILETIME::default(), FILETIME::default(), FILETIME::default());
    // SAFETY: plain data the call fills in.
    if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
        return 0.0;
    }
    let of = |t: FILETIME| u64::from(t.dwHighDateTime) << 32 | u64::from(t.dwLowDateTime);
    // (The kernel's time holds the idle time.)
    let now = [of(idle), of(kernel) + of(user)];
    let (was, all) = (now[0].saturating_sub(last[0]), now[1].saturating_sub(last[1]));
    *last = now;
    if all == 0 { 0.0 } else { 100.0 * (1.0 - was as f64 / all as f64).clamp(0.0, 1.0) }
}

/// The PC's own lines: its processor, its card, its memory and this build.
fn pc(host: &Host) -> &'static [String; 3] {
    static PC: std::sync::OnceLock<[String; 3]> = std::sync::OnceLock::new();
    PC.get_or_init(|| {
        use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory2, DXGI_CREATE_FACTORY_FLAGS, IDXGIAdapter1, IDXGIFactory4};
        use windows_sys::Win32::System::Registry::{HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ, RegGetValueW};
        use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
        let wide = |s: &str| s.encode_utf16().chain([0]).collect::<Vec<u16>>();
        let mut name = [0u16; 128];
        let mut bytes = size_of_val(&name) as u32;
        // SAFETY: NUL-terminated names and a buffer of `bytes` bytes.
        let read = unsafe {
            RegGetValueW(
                HKEY_LOCAL_MACHINE,
                wide("HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0").as_ptr(),
                wide("ProcessorNameString").as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                name.as_mut_ptr().cast(),
                &mut bytes,
            )
        } == 0;
        let tidy = |s: String| s.trim_end_matches('\0').split(" (").next().unwrap_or("").split_whitespace().collect::<Vec<_>>().join(" ");
        let processor = if read { tidy(String::from_utf16_lossy(&name)) } else { String::new() };
        // SAFETY: plain DXGI queries.
        let card = unsafe {
            CreateDXGIFactory2::<IDXGIFactory4>(DXGI_CREATE_FACTORY_FLAGS(0))
                .and_then(|f| f.EnumAdapterByLuid::<IDXGIAdapter1>(host.device.GetAdapterLuid()))
                .and_then(|a| a.GetDesc1())
        }
        .map_or(String::new(), |d| tidy(String::from_utf16_lossy(&d.Description)));
        // SAFETY: plain data the call fills in.
        let mut m: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
        m.dwLength = size_of::<MEMORYSTATUSEX>() as u32;
        // SAFETY: a sized structure.
        unsafe { GlobalMemoryStatusEx(&mut m) };
        let gb = (m.ullTotalPhys as f64 / (1u64 << 30) as f64).round();
        let fit = |s: String| s.chars().take(COLUMNS as usize).collect::<String>();
        [
            fit(processor),
            fit(card),
            fit(format!("{gb:.0} GB RAM - BUILD {} T{}", env!("CARGO_PKG_VERSION"), victory_shader::TRANSLATOR_VERSION)),
        ]
    })
}

/// The PC's memory in use and in all, in bytes.
fn pc_memory() -> (u64, u64) {
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
    // SAFETY: plain data the call fills in.
    let mut m: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
    m.dwLength = size_of::<MEMORYSTATUSEX>() as u32;
    // SAFETY: a sized structure.
    unsafe { GlobalMemoryStatusEx(&mut m) };
    (m.ullTotalPhys.saturating_sub(m.ullAvailPhys), m.ullTotalPhys)
}

/// The rows. `c`: [`Counters`] per frame, `ms` a real frame's time; `rates`
/// the last 30 s (high, low, mean) of the rate shown, `busy` the processors'
/// share. Only the rate on the screen is shown (with frame generation's
/// frames, where it runs): the log has the real one beside it, as before.
fn text(c: &[f64; COUNTERS], ms: f64, rates: (f64, f64, f64), busy: f64, host: &Host) -> Vec<Row> {
    let gb = |bytes: u64| bytes as f64 / (1u64 << 30) as f64;
    let (_, private) = crate::copystat::process_memory();
    let [budget, used, ..] = host.video_memory().unwrap_or_default();
    let (pc_used, pc_all) = pc_memory();
    let idle = (c[1] / 1e3).min(ms);
    let shown = final_fps(c[11], ms);
    let pair = |name: &str, value: String| Row::Pair(name.to_owned(), value);
    let mut rows = vec![
        Row::Head(format!("{shown:.0}"), format!("{:.1} MS OUTPUT", 1e3 / shown.max(0.001))),
        pair("30 S HIGH", format!("{:.1} FPS", rates.0)),
        pair("30 S LOW", format!("{:.1} FPS", rates.1)),
        pair("30 S AVG", format!("{:.1} FPS", rates.2)),
        pair("NATIVE PRESENT", format!("{:.1} FPS", 1e3 / ms.max(0.001))),
        pair("DRAWS", format!("{:.0} / FRAME", c[10])),
        pair("CPU / QUEUE EST.", format!("{:.1} / {:.1} MS", c[0] / 1e3, ms - idle)),
        pair("CPU", format!("{busy:.0}%")),
        pair("QUEUE BUSY EST.", format!("{:.0}%", 100.0 * (ms - idle) / ms.max(0.001))),
        pair("RAM", format!("{:.1} GB - {:.1} / {:.0} GB", gb(private), gb(pc_used), gb(pc_all))),
        pair("VRAM", format!("{:.1} / {:.1} GB", gb(used), gb(budget))),
        Row::Rule,
    ];
    rows.extend(pc(host).iter().cloned().map(Row::Note));
    rows
}

/// The frame rate on the screen: `shown` frames a real frame of `ms` (with
/// frame generation's), or the real one without them.
fn final_fps(shown: f64, ms: f64) -> f64 {
    let shown = if shown > 0.0 { shown } else { 1.0 };
    shown * 1e3 / ms.max(0.001)
}

/// The buffers of a box that size and format.
struct Buffers {
    format: DXGI_FORMAT,
    scale: u32,
    pitch: u32,
    uploads: Vec<(ID3D12Resource, *mut u8)>,
    card: ID3D12Resource,
    /// The upload written last.
    turn: usize,
    /// It is not in the card's buffer yet.
    fresh: bool,
    /// `card` has already been copied into once and now sits in `COPY_SOURCE`.
    uploaded: bool,
}

// SAFETY: free-threaded resources, and memory only written under the presenter's lock.
unsafe impl Send for Buffers {}

impl Buffers {
    fn new(host: &Host, format: DXGI_FORMAT, scale: u32) -> Option<Buffers> {
        let (width, height) = size(scale);
        let pitch = (width * colours(format)?[0].len() as u32).next_multiple_of(256);
        let bytes = u64::from(pitch) * u64::from(height);
        let uploads = (0..UPLOADS).map(|_| host.upload_buffer(bytes)).collect::<Option<Vec<_>>>()?;
        let heap = D3D12_HEAP_PROPERTIES {
            Type: D3D12_HEAP_TYPE_DEFAULT,
            ..Default::default()
        };
        let desc = D3D12_RESOURCE_DESC {
            Dimension: D3D12_RESOURCE_DIMENSION_BUFFER,
            Width: bytes,
            Height: 1,
            DepthOrArraySize: 1,
            MipLevels: 1,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            Layout: D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
            ..Default::default()
        };
        let mut card: Option<ID3D12Resource> = None;
        // SAFETY: plain creation of a buffer.
        unsafe {
            host.device
                .CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc, D3D12_RESOURCE_STATE_COMMON, None, &mut card)
                .ok()?;
        }
        Some(Buffers { format, scale, pitch, uploads, card: card?, turn: 0, fresh: false, uploaded: false })
    }

    fn write(&mut self, lines: &[Row]) {
        let Some(colours) = colours(self.format) else { return };
        let picture = painted(lines, self.scale, &colours, self.pitch as usize);
        self.turn = (self.turn + 1) % UPLOADS;
        // SAFETY: a mapped upload buffer of the picture's size, which the card read a second or more ago.
        unsafe { std::ptr::copy_nonoverlapping(picture.as_ptr(), self.uploads[self.turn].1, picture.len()) };
        self.fresh = true;
    }
}

/// The panel of a presenter.
pub(super) struct Panel {
    since: Instant,
    frames: u32,
    last: Instant,
    before: Counters,
    rates: Rates,
    busy: [u64; 2],
    lines: Vec<Row>,
    changed: bool,
    buffers: Option<Buffers>,
    failed: bool,
    /// For the title's UI layer under frame generation.
    over: Option<Buffers>,
    over_failed: bool,
    over_changed: bool,
}

impl Panel {
    pub(super) fn new() -> Panel {
        let now = Instant::now();
        Panel {
            since: now,
            frames: 0,
            last: now,
            before: Counters::default(),
            rates: Rates::default(),
            busy: [0; 2],
            lines: Vec::new(),
            changed: false,
            buffers: None,
            failed: false,
            over: None,
            over_failed: false,
            over_changed: false,
        }
    }

    /// A frame was presented; `line`: the counters go back to 0 right behind
    /// this call. (Not shown it asks for the key's state and no more.)
    pub(super) fn frame(&mut self, line: bool) {
        // F4 (with `VICTORY_CAPTURE`): the next frame saved and listed, as
        // `VICTORY_CAPTURE_FRAMES` would (its targets too, with
        // `VICTORY_DUMP_TARGETS`): what is on the screen when it is seen.
        static F4: AtomicBool = AtomicBool::new(false);
        if key_pressed(VK_F4, &F4) {
            crate::capture::ask(crate::queue::presented() + 2);
        }
        if pressed() {
            shown().fetch_xor(true, Ordering::Relaxed);
        }
        if !shown().load(Ordering::Relaxed) {
            self.frames = 0;
            return;
        }
        let now = Instant::now();
        if self.frames == 0 && now - self.last > 2 * EVERY {
            // (Shown from here on.)
            (self.since, self.last, self.before) = (now, now, Counters::now());
            self.frames = 0;
        }
        self.frames += 1;
        self.last = now;
        let elapsed = now - self.since;
        if elapsed < EVERY && !(line && elapsed * 2 >= EVERY) {
            return;
        }
        let Some(host) = super::host() else { return };
        let frames = f64::from(self.frames);
        let counters = Counters::now();
        let c = counters.since(self.before, frames);
        self.before = counters;
        let ms = elapsed.as_secs_f64() * 1e3 / frames;
        let rates = self.rates.note(final_fps(c[11], ms));
        let busy = processors_busy(&mut self.busy);
        self.lines = text(&c, ms, rates, busy, host);
        self.changed = true;
        self.over_changed = true;
        (self.since, self.frames) = (now, 0);
    }

    /// Records the copy of the box onto `back`, a buffer of the swap chain of
    /// that size and format in the state to be copied into. With frame
    /// generation the title's UI layer is laid over every frame shown, after
    /// this (its black bars in a cutscene hid the top of the box): then the
    /// box goes onto that layer too.
    pub(super) fn draw(
        &mut self,
        host: &Host,
        list: &HostList,
        back: &ID3D12Resource,
        (width, height): (u32, u32),
        format: DXGI_FORMAT,
    ) {
        if !shown().load(Ordering::Relaxed) || self.lines.is_empty() {
            return;
        }
        // Not the title's: the frame recorder leaves it out.
        let _ours = crate::replay::record::unrecorded();
        let changed = std::mem::take(&mut self.changed);
        if !self.failed {
            self.failed = !Self::onto(&mut self.buffers, &self.lines, changed, host, list, back, (width, height), format);
        }
        #[cfg(feature = "diagnostics")]
        if !self.over_failed
            && let Some((layer, read)) = crate::framegen::ui_layer_ahead([width, height])
        {
            // SAFETY: an open list; the layer is live and in `read` between
            // the lists that use it.
            unsafe { list.list.ResourceBarrier(&[transition(&layer, read, D3D12_RESOURCE_STATE_COPY_DEST)]) };
            self.over_failed =
                !Self::onto(&mut self.over, &self.lines, std::mem::take(&mut self.over_changed), host, list, &layer, (width, height), DXGI_FORMAT_R8G8B8A8_UNORM);
            // SAFETY: as above.
            unsafe { list.list.ResourceBarrier(&[transition(&layer, D3D12_RESOURCE_STATE_COPY_DEST, read)]) };
            list.keep(&layer);
        }
    }

    /// The box onto `back` from `buffers` (made and filled as needed); false
    /// if they could not be made.
    #[allow(clippy::too_many_arguments)]
    fn onto(
        buffers: &mut Option<Buffers>,
        lines: &[Row],
        mut changed: bool,
        host: &Host,
        list: &HostList,
        back: &ID3D12Resource,
        (width, height): (u32, u32),
        format: DXGI_FORMAT,
    ) -> bool {
        let scale = scale(height);
        if buffers.as_ref().is_none_or(|b| (b.format, b.scale) != (format, scale)) {
            // (What the card may still read stays: a picture's size changes once or twice in a run.)
            std::mem::forget(buffers.take());
            *buffers = Buffers::new(host, format, scale);
            changed = true;
        }
        let Some(buffers) = buffers else { return false };
        if changed {
            buffers.write(lines);
        }
        let (w, h) = size(scale);
        let list = &list.list;
        // SAFETY: an open list and live resources; the box lies within both.
        unsafe {
            if std::mem::take(&mut buffers.fresh) {
                if buffers.uploaded {
                    list.ResourceBarrier(&[transition(
                        &buffers.card,
                        D3D12_RESOURCE_STATE_COPY_SOURCE,
                        D3D12_RESOURCE_STATE_COPY_DEST,
                    )]);
                }
                list.CopyResource(&buffers.card, &buffers.uploads[buffers.turn].0);
                list.ResourceBarrier(&[transition(
                    &buffers.card,
                    D3D12_RESOURCE_STATE_COPY_DEST,
                    D3D12_RESOURCE_STATE_COPY_SOURCE,
                )]);
                buffers.uploaded = true;
            }
            let from = D3D12_TEXTURE_COPY_LOCATION {
                pResource: std::mem::transmute_copy(&buffers.card),
                Type: D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT,
                Anonymous: D3D12_TEXTURE_COPY_LOCATION_0 {
                    PlacedFootprint: D3D12_PLACED_SUBRESOURCE_FOOTPRINT {
                        Offset: 0,
                        Footprint: D3D12_SUBRESOURCE_FOOTPRINT {
                            Format: format,
                            Width: w,
                            Height: h,
                            Depth: 1,
                            RowPitch: buffers.pitch,
                        },
                    },
                },
            };
            let to = D3D12_TEXTURE_COPY_LOCATION {
                pResource: std::mem::transmute_copy(back),
                Type: D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,
                Anonymous: D3D12_TEXTURE_COPY_LOCATION_0 { SubresourceIndex: 0 },
            };
            let part = D3D12_BOX {
                left: 0,
                top: 0,
                front: 0,
                right: w.min(width),
                bottom: h.min(height),
                back: 1,
            };
            list.CopyTextureRegion(&to, 0, 0, 0, &from, Some(&part));
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every character the lines use has its own picture, and no two look
    /// alike.
    #[test]
    fn characters_differ() {
        let used: Vec<u8> = (b'0'..=b'9').chain(b'A'..=b'Z').chain(*b"./-%:+ ").collect();
        for (i, a) in used.iter().enumerate() {
            assert_ne!(glyph(*a), glyph(b'#'), "{} has a picture", *a as char);
            assert!(glyph(*a).iter().all(|row| row >> GLYPH.0 == 0));
            for b in &used[i + 1..] {
                assert_ne!(glyph(*a), glyph(*b), "{} {}", *a as char, *b as char);
            }
        }
    }

    fn rows() -> Vec<Row> {
        let mut rows = vec![Row::Head("48".into(), "20.8 MS".into())];
        rows.extend((0..PAIRS).map(|i| Row::Pair(format!("NAME {i}"), "1.0 FPS".into())));
        rows.push(Row::Rule);
        rows.extend((0..NOTES).map(|_| Row::Note("a processor".into())));
        rows
    }

    /// The rate is large and in its own colour at the upper left, names dim
    /// at the left, values at the right edge, the rule a line, and nothing
    /// is drawn into the rim.
    #[test]
    fn the_rows_are_drawn_where_they_belong() {
        let (width, height) = dots();
        let picture = drawn(&rows());
        assert_eq!(picture.len(), (width * height) as usize);
        let at = |x: u32, y: u32| picture[(y * width + x) as usize];
        // The rim.
        assert!((0..width).all(|x| at(x, 0) == BOX && at(x, height - 1) == BOX));
        assert!((0..height).all(|y| at(0, y) == BOX && at(width - 1, y) == BOX));
        // "4" at twice the size: its upper right stroke, two dots a dot.
        assert_eq!(at(RIM + 3 * 2, RIM), RATE);
        assert_eq!(at(RIM + 3 * 2 + 1, RIM + 1), RATE);
        // No other row has the rate's colour.
        assert!((RIM + HEAD..height).all(|y| (0..width).all(|x| at(x, y) != RATE)));
        // The first pair: "N" dim at the left, "S" of FPS ends at the right edge.
        let y = RIM + HEAD;
        assert_eq!(at(RIM, y), NAME);
        assert!((y..y + GLYPH.1).any(|y| at(width - RIM - 1, y) == VALUE));
        // The rule's line.
        let rule = RIM + HEAD + PAIRS * CELL.1 + 1;
        assert!((RIM..width - RIM).all(|x| at(x, rule) == NAME));
    }

    #[test]
    fn a_box_is_painted() {
        for format in [DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R10G10B10A2_UNORM, DXGI_FORMAT_R16G16B16A16_FLOAT] {
            let colours = colours(format).unwrap();
            let bytes = colours[0].len();
            assert!(colours.iter().all(|c| c.len() == bytes));
            for scale in [1, 2, 4] {
                let (width, height) = size(scale);
                let pitch = (width as usize * bytes).next_multiple_of(256);
                let picture = painted(&rows(), scale, &colours, pitch);
                assert_eq!(picture.len(), pitch * height as usize);
                let at = |x: u32, y: u32| &picture[y as usize * pitch + x as usize * bytes..][..bytes];
                assert_eq!(at(0, 0), &colours[usize::from(BOX)][..]);
                // The rate's first dot, every pixel of it.
                assert_eq!(at((RIM + 6) * scale, RIM * scale), &colours[usize::from(RATE)][..]);
                assert_eq!(at((RIM + 6) * scale + scale - 1, RIM * scale + scale - 1), &colours[usize::from(RATE)][..]);
            }
        }
        // Red is red in both byte orders.
        assert_eq!(colours(DXGI_FORMAT_R8G8B8A8_UNORM).unwrap()[3][0], colours(DXGI_FORMAT_B8G8R8A8_UNORM).unwrap()[3][2]);
        assert_eq!((half(1.0), half(0.5), half(0.0)), (0x3C00, 0x3800, 0));
    }

    #[test]
    fn the_last_thirty_seconds_are_kept() {
        let mut rates = Rates::default();
        assert_eq!(rates.note(40.0), (40.0, 40.0, 40.0));
        assert_eq!(rates.note(20.0), (40.0, 20.0, 30.0));
        for _ in 0..60 {
            rates.note(50.0);
        }
        assert_eq!(rates.note(50.0), (50.0, 50.0, 50.0));
    }

    #[test]
    fn the_final_rate_counts_the_generated_frames() {
        assert_eq!(final_fps(2.0, 40.0), 50.0);
        assert_eq!(final_fps(0.0, 40.0), 25.0);
    }

    #[test]
    fn counters_that_went_back_to_nothing_count_from_there() {
        let (mut a, mut b) = (Counters::default(), Counters::default());
        (a.0[0], b.0[0]) = (10, 30);
        (a.0[1], b.0[1]) = (50, 20);
        let c = b.since(a, 10.0);
        assert_eq!((c[0], c[1]), (2.0, 2.0));
    }
}

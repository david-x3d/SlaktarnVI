//! Optional accepted-present intervals. Swap-chain counters are not scanout
//! timestamps; draw/cache counters are process-wide, not per-render-pass.
use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::path::Path;
use std::time::{Duration, Instant};

const COUNTERS: usize = 7;

struct Recorder {
    output: BufWriter<File>,
    start: Instant,
    previous: Option<(Instant, Option<u32>, [u64; COUNTERS])>,
    flushed: Instant,
    failed: bool,
}

impl Recorder {
    #[cfg(windows)]
    fn from_env() -> Option<Self> {
        let path = std::env::var_os("VICTORY_FRAME_CSV")?;
        match Self::open(Path::new(&path)) {
            Ok(recorder) => Some(recorder),
            Err(error) => {
                tracing::warn!(%error, "frame CSV unavailable");
                None
            }
        }
    }

    fn open(path: &Path) -> anyhow::Result<Self> {
        victory_core::ensure_outside_repo(path)?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut output = BufWriter::with_capacity(64 * 1024, File::create(path)?);
        writeln!(
            output,
            "frame,elapsed_ns,interval_ns,swapchain_present_delta,draw_delta,shader_hit_delta,shader_miss_delta,dropped_work_delta,dispatch_delta,pipeline_load_delta,pipeline_make_delta"
        )?;
        let now = Instant::now();
        Ok(Self {
            output,
            start: now,
            previous: None,
            flushed: now,
            failed: false,
        })
    }

    fn present(&mut self, frame: u64, count: Option<u32>, counters: [u64; COUNTERS]) {
        if self.failed {
            return;
        }
        if let Err(error) = self.record(Instant::now(), frame, count, counters) {
            tracing::warn!(%error, "frame CSV write failed; recording stopped");
            self.failed = true;
        }
    }

    fn record(
        &mut self,
        now: Instant,
        frame: u64,
        count: Option<u32>,
        counters: [u64; COUNTERS],
    ) -> io::Result<()> {
        let previous = self.previous.replace((now, count, counters));
        if let Some((at, old_count, old)) = previous {
            let interval = now.duration_since(at).as_nanos();
            if interval > 0 {
                let delta = count.zip(old_count).and_then(|(a, b)| a.checked_sub(b));
                let delta = delta.map(|n| n.to_string()).unwrap_or_default();
                write!(
                    self.output,
                    "{frame},{},{interval},{delta}",
                    now.duration_since(self.start).as_nanos()
                )?;
                for (a, b) in counters.into_iter().zip(old) {
                    write!(self.output, ",{}", a.saturating_sub(b))?;
                }
                writeln!(self.output)?;
            }
        }
        if now.duration_since(self.flushed) >= Duration::from_secs(1) {
            self.output.flush()?;
            self.flushed = now;
        }
        Ok(())
    }
}

// A presenter can be recreated on a resize. Keep the file open for the process
// rather than truncating the run each time a swap chain is recreated.
#[cfg(windows)]
pub(crate) fn present(frame: u64, counters: impl FnOnce() -> (Option<u32>, [u64; COUNTERS])) {
    use std::sync::{Mutex, OnceLock};
    static RECORDER: OnceLock<Option<Mutex<Recorder>>> = OnceLock::new();
    if let Some(recorder) = RECORDER.get_or_init(|| Recorder::from_env().map(Mutex::new)) {
        let (count, counters) = counters();
        recorder
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .present(frame, count, counters);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_intervals_and_marks_counter_reset_missing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("frames.csv");
        let mut recorder = Recorder::open(&path).unwrap();
        let start = recorder.start;
        recorder.present(0, Some(9), [10, 1, 1, 0, 0, 0, 0]);
        // Deterministic time and counters, independent of hardware.
        recorder.previous = Some((start, Some(9), [10, 1, 1, 0, 0, 0, 0]));
        recorder
            .record(
                start + Duration::from_millis(20),
                1,
                Some(11),
                [20, 4, 2, 1, 3, 2, 1],
            )
            .unwrap();
        recorder
            .record(
                start + Duration::from_millis(50),
                2,
                Some(1),
                [25, 4, 2, 3, 4, 2, 1],
            )
            .unwrap();
        drop(recorder);
        let text = std::fs::read_to_string(path).unwrap();
        assert!(text.contains("1,20000000,20000000,2,10,3,1,1,3,2,1"));
        assert!(text.contains("2,50000000,30000000,,5,0,0,2,1,0,0"));
    }
}

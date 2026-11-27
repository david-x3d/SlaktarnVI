//! Coalesce concurrent mapping barriers without publishing a resource before
//! a successfully completed queue fence. Register only AFTER MakeResident.
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub(crate) struct Requests(AtomicU64);
impl Requests {
    pub(crate) fn register(&self) -> u64 {
        self.0.fetch_add(1, Ordering::AcqRel) + 1
    }
    pub(crate) fn snapshot(&self) -> u64 {
        self.0.load(Ordering::Acquire)
    }
}

#[derive(Default)]
pub(crate) struct Completed(u64);
impl Completed {
    pub(crate) fn covers(&self, ticket: u64) -> bool {
        ticket <= self.0
    }
    // Called only once the fence covering this snapshot has completed.
    pub(crate) fn complete(&mut self, snapshot: u64) {
        self.0 = self.0.max(snapshot);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn concurrent_requests_share_only_a_completed_snapshot() {
        let requests = Requests::default();
        let mut completed = Completed::default();
        let first = requests.register();
        let second = requests.register();
        let submitted = requests.snapshot();
        let late = requests.register();
        assert!(!completed.covers(first));
        assert!(!completed.covers(second));
        // Failure leaves the watermark unchanged; only success publishes it.
        completed.complete(submitted);
        assert!(completed.covers(first));
        assert!(completed.covers(second));
        assert!(!completed.covers(late));
        completed.complete(requests.snapshot());
        assert!(completed.covers(late));
    }

    #[test]
    fn racing_registrations_have_unique_tickets() {
        let requests = Requests::default();
        std::thread::scope(|scope| {
            let handles: Vec<_> = (0..8).map(|_| scope.spawn(|| requests.register())).collect();
            let mut tickets: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
            tickets.sort_unstable();
            assert_eq!(tickets, (1..=8).collect::<Vec<_>>());
            assert_eq!(requests.snapshot(), 8);
        });
    }
}

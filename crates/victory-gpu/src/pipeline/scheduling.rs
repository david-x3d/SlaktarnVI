//! Compiler pool sizing and per-key serialization, independent of D3D12.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, Weak};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct WorkerConfig {
    pub general: usize,
    pub screen: usize,
}

impl WorkerConfig {
    pub fn new(asked: Option<usize>, processors: usize, wine: bool, screens: bool) -> Self {
        // vkd3d-proton translates DXIL and the Vulkan driver compiles it on
        // the calling thread. Leave more CPU time for the title and recording
        // on Wine; the native Windows default and explicit override remain.
        if wine && asked.is_none() {
            let total = (processors / 3).clamp(2, 8);
            let screen = usize::from(screens);
            return Self {
                general: total - screen,
                screen,
            };
        }
        Self {
            general: asked.map_or((processors.saturating_mul(2) / 3).clamp(2, 16), |n| {
                n.clamp(1, 64)
            }),
            screen: if screens { 2 } else { 0 },
        }
    }
}

/// A lock for each active pipeline key. A weak reference does not retain
/// completed compilations or their D3D12 objects. The map is pruned on new
/// keys, so the number of historical keys retained is bounded.
#[derive(Default)]
pub(super) struct KeyGates {
    gates: Mutex<HashMap<String, Weak<Mutex<()>>>>,
}

impl KeyGates {
    pub fn get(&self, key: &str) -> Arc<Mutex<()>> {
        let mut gates = self.gates.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(gate) = gates.get(key).and_then(Weak::upgrade) {
            return gate;
        }
        if gates.len() >= 64 {
            gates.retain(|_, gate| gate.strong_count() != 0);
        }
        let gate = Arc::new(Mutex::new(()));
        gates.insert(key.to_owned(), Arc::downgrade(&gate));
        gate
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wine_default_includes_screen_workers_in_budget() {
        assert_eq!(
            WorkerConfig::new(None, 16, true, true),
            WorkerConfig {
                general: 4,
                screen: 1
            }
        );
        assert_eq!(
            WorkerConfig::new(None, 16, true, false),
            WorkerConfig {
                general: 5,
                screen: 0
            }
        );
        assert_eq!(
            WorkerConfig::new(None, 1, true, true),
            WorkerConfig {
                general: 1,
                screen: 1
            }
        );
        assert_eq!(
            WorkerConfig::new(None, 64, true, true),
            WorkerConfig {
                general: 7,
                screen: 1
            }
        );
    }

    #[test]
    fn native_and_explicit_worker_counts_preserve_previous_behavior() {
        assert_eq!(
            WorkerConfig::new(None, 24, false, true),
            WorkerConfig {
                general: 16,
                screen: 2
            }
        );
        assert_eq!(
            WorkerConfig::new(Some(10), 16, true, true),
            WorkerConfig {
                general: 10,
                screen: 2
            }
        );
        assert_eq!(
            WorkerConfig::new(Some(0), 16, true, false),
            WorkerConfig {
                general: 1,
                screen: 0
            }
        );
        assert_eq!(WorkerConfig::new(Some(100), 16, true, true).general, 64);
    }

    #[test]
    fn only_identical_active_keys_share_a_lock() {
        let gates = KeyGates::default();
        let a = gates.get("a");
        let locked = a.lock().unwrap();
        let same = gates.get("a");
        assert!(Arc::ptr_eq(&a, &same));
        assert!(same.try_lock().is_err());
        assert!(gates.get("b").try_lock().is_ok());
        drop(locked);
        assert!(same.try_lock().is_ok());
    }

    #[test]
    fn completed_keys_are_pruned_without_splitting_live_keys() {
        let gates = KeyGates::default();
        let active = gates.get("active");
        for i in 0..1000 {
            drop(gates.get(&i.to_string()));
        }
        assert!(gates.gates.lock().unwrap().len() <= 64);
        assert!(Arc::ptr_eq(&active, &gates.get("active")));
    }
}

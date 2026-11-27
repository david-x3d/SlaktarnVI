//! Synthetic cache-key benchmark; no game assets or graphics device.
use std::{hint::black_box, time::Instant};
use victory_pipeline::{CacheKey, Converter};

struct Shader;
impl Converter for Shader {
    fn id(&self) -> &'static str { "synthetic.shader" }
    fn version(&self) -> u32 { 1 }
    fn convert(&self, input: &[u8]) -> anyhow::Result<Vec<u8>> { Ok(input.to_vec()) }
}

fn main() {
    for size in [4096, 65536, 1048576] {
        let prefix = [7u8; 32];
        let data = vec![42u8; size];
        let count = (32 * 1024 * 1024 / size).max(32);
        assert_eq!(CacheKey::new(&Shader, &[&prefix[..], &data].concat()),
                   CacheKey::from_parts(&Shader, &[&prefix, &data]));
        let mut samples = [[0u128; 7]; 2];
        for sample in 0..7 {
            // Alternate order to reduce systematic warm-cache bias.
            for mode in [sample % 2, 1 - sample % 2] {
                let start = Instant::now();
                for _ in 0..count {
                    let result = if mode == 0 {
                        CacheKey::new(&Shader, &[&prefix[..], black_box(&data)].concat())
                    } else {
                        CacheKey::from_parts(&Shader, &[&prefix, black_box(&data)])
                    };
                    black_box(result);
                }
                samples[mode][sample] = start.elapsed().as_nanos() / count as u128;
            }
        }
        samples[0].sort_unstable();
        samples[1].sort_unstable();
        println!("{} bytes: concat={} ns/key, segmented={} ns/key (median)", size, samples[0][3], samples[1][3]);
    }
}

# Valhalla — NVIDIA / Linux performance

## Baseline and evidence

Initial user-provided RTX 4090 / 7800X3D / 32 GiB / GE-Proton11-14-viewtree /
vkd3d-proton 3.2.0 run: Series S, 1920x1080, no frame generation, prologue.
2.8 average FPS (2–5.5 range), 364.1 ms frame time, reported 69.9 ms CPU and
331.3 ms GPU, 1,647 draws/frame, 23.5 GiB RAM, 11.6 GiB VRAM of reported
20.9 GiB budget. These are supplied observations, not reproduced benchmarks.
The upstream AMD 19–25 FPS claim is not a controlled cross-vendor comparison.
The user's earlier Vice-Tay run dropped 2,407 of 3,407 draws; it cannot serve
as a correctness-equivalent performance target.

The overlay used elapsed present time minus time without our GPU work
queued as an estimate of GPU execution. It is not a timestamp measurement.
CPU preparation and queue/fence waits may overlap. Do not add the reported
CPU and GPU values. The new overlay labels them as estimates and displays
accepted native presents separately.

## Implemented

| Change | Evidence | Expected effect / limits |
| --- | --- | --- |
| Wave32 single-wave groups | Synthetic lowering checks for 1, 16, 32, 8x4, 33, 64, 128 threads. For <=32, the old artificial group counter/exchange storage is unnecessary. | Removes counter LDS, atomic and group barrier sequence for eligible groups; no change to multi-wave exchange. |
| Wave32 guarded first-lane reads | IR instruction census and serialization; low-word path only when required WaveSize(32) is known. | Fewer extracts, FirstbitLo calls, selects and integer operations. Empty guard and Wave64 path preserved. Translator cache version 8. |
| Descriptor texture queue | A single traversal collects due slots and census; retries that fill the batch avoid fresh sort. Based on Vice-Tay 369f940ce283 (timorjay, PR #248). | Less CPU work and time holding queue mutex. Original ordering retained. |
| Capped descriptor mirroring | Clip consumed write-watch ranges to mirrored host capacity, including marked/fallback port ranges. | Avoids reads and page jobs for unbacked NVIDIA slots; no changes to dynamic slot ownership. Does not fix all descriptors outside the cap. |
| Content-addressed shader cache | Identical segmented hash keys, eight concurrent requests result in one conversion, all nine cache tests pass. | Removes per-request container copy, reduces duplicate compilation on misses sharing a cache instance. 256 lock stripes bound memory. Collisions can serialize unrelated misses; not interprocess locking. |
| Native frame CSV and NVIDIA hardware log | Synthetic timing and parser tests; no device required. | Accepted-present interval percentiles, swap-chain present count (not physical scanout), global draw and shader cache deltas, NVIDIA board-wide telemetry. |
| Linux launcher, profiles and CI | Synthetic fake Proton directories, launcher tests, Python reporter tests, shell syntax checks and real cross-builds. | Separate runtime files from Git, retain view-tree Proton, release vs diagnostics binaries, avoid AMD FSR4 on NVIDIA. |

No draw skipping, shader approximation, DRM bypass, synchronization bypass,
or vendor-exclusive generated-frame claims were added. Existing upstream
rendering shortcuts/limitations remain. Source-level correctness is not proof
that the entire game renders correctly.

### Reproducible CPU-only synthetic microbenchmark

Run `cargo bench --locked -p victory-pipeline --bench cache_keys`. One run
on the build host, 7 alternating-order samples per input, median ns/key:

| Synthetic shader size | Concatenate + hash | Segmented hash |
| --- | ---: | ---: |
| 4 KiB | 3177 | 3133 |
| 64 KiB | 14048 | 13029 |
| 1 MiB | 170323 | 148173 |

This isolates key creation, not shader compilation, game frame time or disk I/O.
Results vary with CPU, clock, compiler, contention and cache state. The
concurrent-cache regression checks work counts rather than wall time.

## Prior research and integration decisions

- [Marvisak/VIctory](https://github.com/Marvisak/VIctory) is the base loader,
  GPU/HLE implementation and shader translator. The
  [NVIDIA/Wine rendering issue #1528](https://github.com/Marvisak/VIctory/issues/1528)
  illustrates why a visually incomplete scene is not a performance baseline.
- [Slaktarn700/SlaktarnVI](https://github.com/Slaktarn700/SlaktarnVI) is the
  direct parent (e5b0dc8). It already includes Linux fence/guard work, shader
  optimizers, descriptor cap handling, pipeline library mmap and a patched
  wineserver view tree. Preserve them; do not duplicate those optimizations.
- [TaynanGT/Vice-Tay](https://github.com/TaynanGT/Vice-Tay): reviewed source
  changes 369f940ce283 (queue census, selectively integrated),
  5e588e8 (paired wave implementation spans translator/runtime),
  8058344 (DXIL loop latch canonicalization),
  fa8fc1d (static sampler library reuse and binding correctness),
  447dbb1 (SIMD detiling), 0c9600f (command-list reuse).
  The latter five are candidates for targeted ports and regression work,
  not merged wholesale. They have different source assumptions and need
  driver validation.
- [fxceless/victory-fork](https://github.com/fxceless/victory-fork):
  paired-wave work and masked patching of shader-written resources are
  relevant to the bottleneck. Test equivalence before integrating.
- [avatarmanz/VIctory-7900XTX](https://github.com/avatarmanz/VIctory-7900XTX):
  upstream already incorporates several renderer and memory work paths;
  do not assume RDNA measurements transfer to Ada.
- [readmet3xt-lab/foxhound](https://github.com/readmet3xt-lab/foxhound) and
  [trustno1-coder/VIctory-NVIDIA](https://github.com/trustno1-coder/VIctory-NVIDIA):
  NVIDIA Streamline integration and live graphics controls are candidates.
  The published configurations are Windows-focused. A Wine-compatible
  Streamline integration requires SDK/runtime loading, device/queue hooks,
  motion vectors, depth, jitter, resource lifetime, and presentation
  validation. No NVIDIA SDK binaries included. DLSS SR, RR, FG and Reflex
  are **not implemented in this branch**. Quality uses the existing game
  upscaler, not DLSS. Ada's conventional 2x FG must remain distinct from
  native FPS and RTX 50-only MFG.
- [vkd3d-proton changelog](https://github.com/HansKristian-Work/vkd3d-proton/blob/master/CHANGELOG.md):
  descriptor buffers/heaps, pipeline cache and driver features are important
  external dependencies. Do not force undocumented Vulkan flags. Diagnose
  actual extension availability and hot paths before proposing a separate
  Wine/vkd3d patch.

## Build, launch and measure

Rust is pinned by rust-toolchain.toml; local cargo-xwin 0.24.2.
Build from source (no game needed):

```sh
cargo xwin build --locked --release -p victory --target x86_64-pc-windows-msvc
cargo xwin build --locked --profile diag -p victory --features diagnostics --target x86_64-pc-windows-msvc
cargo test --locked -p victory-core -p victory-formats -p victory-pipeline -p victory-shader -p victory-gpu --lib
python3 -I linux/launcher/valhalla.py --selftest
python3 -I linux/hw-log.py --selftest
python3 -I linux/benchmark.py selftest
bash linux/test-runtime-paths.sh
cargo xtask policy
```

The two binaries are under target/x86_64-pc-windows-msvc/{release,diag}/valhalla.exe.
CI builds both and uploads renamed executables with checksums. Full Linux
workspace tests currently fail in existing victory-loader crash code, which
references Windows APIs without a platform guard; the five selected crates
pass (353 tests, 9 ignored). Windows GPU integration tests are not executed
in this build-only session.

Launch GUI with `python3 linux/launcher/valhalla.py` (PySide6 required).
The Performance preset is Series S, 1080p, 120 FPS pacing target, no FSR4 or
frame generation. Compatibility uses safer existing switches and longer
pipeline wait; Balanced is Series X/1080p; Quality Series X/1440p;
Experimental enables a 50% own-upscaler scale and opt-in wider raw loads.
These presets are starting points, not validated hardware profiles.
Existing AMD presets remain available.

Set GAME_DIR and GAME_EXE to your legally obtained game files, outside Git.
The shell example uses env and bash so it works from fish too:

```sh
env PROTON="$HOME/.steam/root/compatibilitytools.d/GE-Proton11-14-viewtree" \
    GAME_DIR="/absolute/path/to/your/game" GAME_EXE=game.exe \
    CONSOLE=series-s VICTORY_OUTPUT=1920x1080 \
    VICTORY_FRAME_INTERVAL_MS=8.333 VICTORY_FIDELITYFX_FRAMEGEN=0 \
    VALHALLA_GPU_VENDOR=nvidia VICTORY_DIAGNOSTICS=1 \
    bash linux/run-7900.sh
```

Use the actual executable name. PROTON can be omitted for autodetection;
explicit PROTON is authoritative. Set MSD3D to the folder of your own
Microsoft shader compiler DLLs if needed. No Steam or system installation
is patched by the launcher. VICTORY_DIAGNOSTICS=0 selects the release build.
Custom PREFIX and EXE override defaults. The default paths are:

- State/logs/captures: ~/.local/state/valhalla
- Cache: ~/.cache/valhalla (translated shaders, DXVK, vkd3d-proton)
- Saves/data/prefix: ~/.local/share/valhalla

XDG variables and VALHALLA_STATE_DIR, VALHALLA_CACHE_DIR, VALHALLA_DATA_DIR,
VICTORY_DATA_DIR, VICTORY_CACHE_DIR, PREFIX override them. Existing caches
are not moved or deleted. Point these variables at a previously external
cache if you want to reuse it; paths inside a marked source tree are refused.
Proton view-tree versions are preferred; Steam is not modified.

### Benchmark protocol

Use the same save/location, resolution, console, settings, warmed cache and
driver for both tests. Record a sufficiently long comparable segment beyond
initial loading. Do not compare a cold startup with a warmed run. Capture
images manually for correctness and do not publish game-derived output.

```sh
env GAME_DIR="/absolute/path/to/your/game" GAME_EXE=game.exe \
    CONSOLE=series-s VICTORY_OUTPUT=1920x1080 \
    VICTORY_FRAME_INTERVAL_MS=8.333 VALHALLA_GPU_VENDOR=nvidia \
    python3 linux/benchmark.py run --duration 180 --warmup 30 \
      --a VICTORY_SYNC_IN_PLACE=0 --b VICTORY_SYNC_IN_PLACE=1
```

This is an example A/B of an existing runtime switch, not a measurement of
the new shader optimization. Run old and new builds with identical settings
to isolate the branch's changes. Runner outputs separate CSV, JSON, logs and
hardware TSV under state/benchmarks; it disables FidelityFX frame generation
and the overlay. Exit 124 means timeout, not successful game completion.
Warmup is relative to meter initialization, and duration includes startup;
verify comparable scene coverage in the log. A/B runs are sequential and
share caches by default; repeat in reverse order to control warm-cache bias.

```sh
python3 linux/benchmark.py report /absolute/path/frames.csv --warmup 30
```

Frame CSV is opt-in (diagnostics builds, VICTORY_FRAME_CSV=/external/file.csv).
Native FPS = accepted intervals / total seconds, median/p95/p99 in ms.
Swap-chain present count is an API counter, not display scanout. Missing
or reset counters yield null. Draw/shader counters are global deltas,
not attribution to a specific pass. Hardware TSV is board-wide on NVIDIA
(nvidia-smi; VALHALLA_NVIDIA_GPU selects device), CPU from /proc, unknown
fields are ?. No polling is required in release builds (NO_HW_LOG=1).

For bottleneck diagnosis, separately enable existing diagnostics:
VICTORY_TIMELINE=/external/trace.json, VICTORY_TIMELINE_AT=60,
VICTORY_TIMELINE_FRAMES=3 (CPU waits and GPU submission timestamps, Perfetto);
VICTORY_GPU_TIMING=1 and VICTORY_D3D12_DRED=light (times individual lists and
marked commands, changes submission behavior, so not a clean FPS benchmark).
Existing detailed logs report submission preparation, fence waits, copied
bytes, descriptors, dropped pipeline causes, pipeline loads and compilation.
These are sampled runtime logs, not yet aligned to each CSV row. Physical
GPU execution time, exact scanout FPS, per-pass aggregation and per-frame
VRAM/transfer accounting remain follow-up work.

## Road to 120 native FPS

The supplied frame time must improve by roughly 43.7x to reach 8.33 ms.
There is no demonstrated native gameplay improvement yet.

| Milestone | Frame budget | Priority |
| --- | ---: | --- |
| 10 FPS | 100 ms | Reproduce and identify actual GPU list costs, missing draws, CPU stalls; remove redundant work without changing output. |
| 20 FPS | 50 ms | Profile long compute shaders, port paired Wave64 selectively with DXIL/SPIR-V and golden-output tests. |
| 30 FPS | 33.33 ms | Optimize descriptor heaps, residency, texture detiling, pipeline reuse and memory transfers where measured. |
| 60 FPS | 16.67 ms | Reduce queue serialization and guest readback with validated dependencies; investigate exact GPU detiling and async compute. |
| 120 FPS | 8.33 ms | Re-evaluate after measured 60 FPS. Shader occupancy/register pressure, render scale, native compiler integration; no promise that this is achievable. |

Next test on RTX 4090: compare diagnostic build to upstream on matched
save and warmed cache, require no increase in dropped draw count or visible
defects, compare native p95/p99, inspect the longest actual GPU lists with
timestamps. Use the 32 GiB RAM and actual DXGI/Vulkan budget as constraints
rather than assuming all 24 GiB of VRAM is available. Do not use a Windows
VM or modify Steam. Only propose an external runtime patch with evidence
that the bottleneck resides there.

GPL and existing attribution remain intact. No game assets, game-derived
caches, NVIDIA libraries or other proprietary binaries are committed.

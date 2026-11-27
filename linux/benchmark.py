#!/usr/bin/env python3
"""Accepted native frame benchmark. No game execution unless 'run' is requested."""
import argparse
import csv
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent


def external(path):
    path = Path(path).expanduser().resolve()
    if any((p / ".victory-root").is_file() for p in (path, *path.parents)):
        raise ValueError("benchmark output must be outside the source checkout")
    return path


def report(path, warmup=0, until=None):
    rows = []
    with open(path, newline="") as file:
        for row in csv.DictReader(file):
            try:
                end, interval = int(row["elapsed_ns"]), int(row["interval_ns"])
                if (interval > 0 and end - interval >= warmup * 1e9
                        and (until is None or end <= until * 1e9)):
                    rows.append((interval, row))
            except (ValueError, TypeError, KeyError):
                # Interrupted process may leave the final row incomplete.
                continue
    if not rows:
        raise ValueError("no complete accepted native frame intervals after warmup")
    intervals = sorted(n / 1e6 for n, _ in rows)
    total = sum(n for n, _ in rows) / 1e9
    def percentile(p):
        return intervals[math.ceil(p * len(intervals)) - 1]
    def sum_field(key):
        try:
            return sum(int(row[key]) for _, row in rows)
        except (ValueError, TypeError, KeyError):
            return None
    presents = sum_field("swapchain_present_delta")
    hits, misses = sum_field("shader_hit_delta"), sum_field("shader_miss_delta")
    loaded, made = sum_field("pipeline_load_delta"), sum_field("pipeline_make_delta")
    draws, dropped = sum_field("draw_delta"), sum_field("dropped_work_delta")
    dispatches = sum_field("dispatch_delta")
    return {
        "window_seconds": {"from": warmup, "until": until},
        "rendering_complete": False if dropped else None,
        "accepted_native_fps": len(rows) / total,
        "swapchain_present_rate": presents / total if presents is not None else None,
        "scanout_fps": None,
        "intervals": len(rows), "sample_seconds": total,
        "frame_ms": {"median": (intervals[(len(intervals)-1)//2] + intervals[len(intervals)//2])/2,
                     "p95": percentile(.95), "p99": percentile(.99)},
        "draw_counter_delta": draws,
        "dispatch_counter_delta": dispatches,
        "dropped_work_delta": dropped,
        "draws_per_accepted_present": draws / len(rows) if draws is not None else None,
        "dispatches_per_accepted_present": dispatches / len(rows) if dispatches is not None else None,
        "dropped_work_per_accepted_present": dropped / len(rows) if dropped is not None else None,
        "pipeline_library_loads": loaded,
        "pipeline_make_counter": made,
        "pipeline_library_load_fraction": loaded / (loaded + made) if loaded is not None and made is not None and loaded + made else None,
        "shader_cache_hits": hits, "shader_cache_misses": misses,
        "shader_cache_hit_rate": hits / (hits + misses) if hits is not None and misses is not None and hits + misses else None,
    }


# These are upstream periodic samples, not time-aligned to the frame CSV.
# Preserve the original aggregate values; never treat them as GPU timestamps.
LOG_SAMPLE = re.compile(
    r"(?P<native_fps>[\d.]+) fps, (?P<frame_ms>[\d.]+) ms/frame "
    r"\((?P<cpu_prepare_ms>[\d.]+) preparing: copies (?P<copy_ms>[\d.]+) ms "
    r"(?P<copy_mb>[\d.]+) MB, textures (?P<texture_ms>[\d.]+) ms "
    r"(?P<texture_mb>[\d.]+) MB; (?P<gpu_wait_ms>[\d.]+) (?:on the|waiting for the) "
    r"GPU; (?P<copyback_ms>[\d.]+) copying back (?P<copyback_mb>[\d.]+) MB\), "
    r"(?P<draws>[\d.]+) draws/frame, dropped (?P<dropped>[\d.]+)"
    r"(?: without a host pipeline \((?P<pending_pipelines>[\d.]+) being made\)"
    r" and (?P<dropped_indirect>[\d.]+) indirect)?")


def log_samples(path):
    samples, reasons, mapping, passes, budgets, gpu_spans, coalesced = [], [], [], [], [], [], []
    with open(path, errors="replace") as file:
        for line in file:
            if match := LOG_SAMPLE.search(line):
                samples.append({k: float(v) if v is not None else None
                                for k, v in match.groupdict().items()})
            if match := re.search(r"dropped ([\d.]+)/frame without a host pipeline: (.*)", line):
                reasons.append({"per_frame": float(match[1]), "reason": match[2].strip()})
            if match := re.search(r"new card memory waited for until it is mapped .*since the start: (\d+) times, ([\d.]+) ms, the longest ([\d.]+) ms", line):
                mapping.append({"barriers": int(match[1]), "cumulative_ms": float(match[2]), "longest_ms": float(match[3])})
            if match := re.search(r"the card's own: budget (\d+) MB, in use (\d+) MB, \d+ MB could still be reserved, \d+ MB reserved; the PC's shared with the card: budget (\d+) MB, in use (\d+) MB", line):
                budgets.append({"local_used_mb": int(match[2]), "local_budget_mb": int(match[1]), "nonlocal_used_mb": int(match[4]), "nonlocal_budget_mb": int(match[3])})
            if "mapping barrier requests coalesced" in line:
                if match := re.search(r"\bn=(\d+)", line):
                    coalesced.append(int(match[1]))
            if match := re.search(r"the card's time by command \(timing run\): ([\d.]+) ms a frame in ([\d.]+) commands a frame, the longest ([\d.]+) ms: (.*)", line):
                passes.append({"gpu_timestamp_ms_per_frame": float(match[1]), "commands_per_frame": float(match[2]), "longest_ms": float(match[3]), "command": match[4].strip()})
            if match := re.search(r"the card's own stamps per frame: ([\d.]+) submissions, working ([\d.]+) ms, idle between them ([\d.]+) ms, the longest ([\d.]+) ms", line):
                gpu_spans.append({"submissions_per_frame": float(match[1]), "gpu_busy_ms_per_frame": float(match[2]),
                                  "gpu_between_ms_per_frame": float(match[3]), "longest_submission_ms": float(match[4])})
    return {"periodic_frame_samples": samples, "periodic_drop_reasons": reasons,
            "gpu_queue_timestamp_samples_delayed": gpu_spans,
            "mapping_barriers_last": mapping[-1] if mapping else None,
            "coalesced_mapping_requests_last_reported": coalesced[-1] if coalesced else None,
            "gpu_memory_budget_samples": budgets,
            "gpu_timestamp_command_samples": passes,
            "steady_state_established": False if any(
                s["pending_pipelines"] or s["dropped"] or s["dropped_indirect"] for s in samples[-3:]) else None}


def hardware_summary(path):
    with open(path, newline="") as file:
        rows = list(csv.DictReader(file, delimiter="\t"))
    result = {"samples": len(rows)}
    for key in ("gpu%", "vram_MiB", "gpu_W", "cpu%", "game%", "ram_used_MiB"):
        values = []
        for row in rows:
            try:
                value = float(row[key])
                if math.isfinite(value):
                    values.append(value)
            except (KeyError, ValueError, TypeError):
                continue
        result[key] = {"mean": sum(values) / len(values), "max": max(values)} if values else None
    return result

def run(args):
    root = external(args.output) if args.output else external(Path(
        os.environ.get("VALHALLA_STATE_DIR") or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "valhalla")
        / "benchmarks" / time.strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True, exist_ok=False)
    variants = [("A", args.a)] + ([("B", args.b)] if args.b is not None else [])
    for name, overrides in variants:
        directory = root / name
        directory.mkdir()
        env = os.environ.copy()
        for item in overrides:
            key, sep, value = item.partition("=")
            if not sep or not key.startswith(("VICTORY_", "VKD3D_", "VALHALLA_")):
                raise ValueError(f"invalid benchmark override: {key}")
            env[key] = value
        env.update(VICTORY_DIAGNOSTICS="1", VICTORY_PANEL="0", VICTORY_FIDELITYFX_FRAMEGEN="0",
                   SECS=str(args.duration), LOG=str(directory / "run.log"),
                   VICTORY_FRAME_CSV="Z:" + str(directory / "frames.csv").replace("/", "\\"),
                   VK_LOADER_LAYERS_DISABLE="VK_LAYER_LSFGVK_frame_generation")
        env.pop("VK_INSTANCE_LAYERS", None)
        (directory / "config.json").write_text(json.dumps({
            "overrides": overrides, "duration": args.duration, "warmup": args.warmup,
            "frame_generation_requested": False}, indent=2) + "\n")
        result = subprocess.run(["bash", str(HERE / "run-7900.sh")], env=env)
        try:
            stats = report(directory / "frames.csv", args.warmup)
        except (ValueError, OSError) as exc:
            stats = {"error": str(exc)}
        stats["launcher_exit"] = result.returncode
        if (directory / "run.log").is_file():
            stats["runtime_log"] = log_samples(directory / "run.log")
        if (directory / "run-hw.tsv").is_file():
            stats["hardware_all_samples"] = hardware_summary(directory / "run-hw.tsv")
        (directory / "report.json").write_text(json.dumps(stats, indent=2) + "\n")
        print(name, json.dumps(stats, indent=2))
        if result.returncode not in (0, 124) or "error" in stats:
            return 1
    return 0


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "frames.csv"
        path.write_text("frame,elapsed_ns,interval_ns,swapchain_present_delta,draw_delta,shader_hit_delta,shader_miss_delta\n"
                        "1,10000000,10000000,2,10,3,1\n"
                        "2,30000000,20000000,2,15,1,0\n"
                        "3,60000000,30000000,,20,0,1\n")
        stats = report(path)
        assert stats["accepted_native_fps"] == 50
        assert stats["frame_ms"] == {"median": 20, "p95": 30, "p99": 30}
        assert stats["swapchain_present_rate"] is None
        assert stats["shader_cache_hit_rate"] == 4 / 6
        assert report(path, .01)["intervals"] == 2
        assert report(path, .01, .03)["intervals"] == 1
        assert report(path)["rendering_complete"] is None
        assert stats["pipeline_library_load_fraction"] is None
        log = Path(tmp) / "run.log"
        log.write_text("10.0 fps, 100.0 ms/frame (2.0 preparing: copies 1.0 ms 2.0 MB, textures 3.0 ms 4.0 MB; 20.0 waiting for the GPU; 1.0 copying back 0.5 MB), 25 draws/frame, dropped 4\n")
        with log.open("a") as f:
            f.write("10.0 fps, 100.0 ms/frame (2.0 preparing: copies 1.0 ms 2.0 MB, textures 3.0 ms 4.0 MB; 20.0 waiting for the GPU; 1.0 copying back 0.5 MB), 25 draws/frame, dropped 4 without a host pipeline (7 being made) and 2 indirect\n")
            f.write("new card memory waited for until it is mapped (test), since the start: 100 times, 70.0 ms, the longest 2.0 ms\n")
            f.write("the card's time by command (timing run): 1.25 ms a frame in 2.0 commands a frame, the longest 0.8 ms: draw [test]\n")
        with log.open("a") as f:
            f.write("the card's own stamps per frame: 41.0 submissions, working 235.0 ms, idle between them 27.0 ms, the longest 90.0 ms\n")
            f.write("video memory as the system counts it, here: the card's own: budget 21000 MB, in use 17000 MB, 4000 MB could still be reserved, 0 MB reserved; the PC's shared with the card: budget 14000 MB, in use 3000 MB, 11000 MB could still be reserved, 0 MB reserved\n")
            f.write("mapping barrier requests coalesced n=1000\n")
        logs = log_samples(log)
        assert logs["gpu_queue_timestamp_samples_delayed"][0]["gpu_busy_ms_per_frame"] == 235
        assert logs["gpu_memory_budget_samples"][0]["local_budget_mb"] == 21000
        assert logs["gpu_memory_budget_samples"][0]["nonlocal_used_mb"] == 3000
        assert logs["coalesced_mapping_requests_last_reported"] == 1000
        assert logs["periodic_frame_samples"][0]["gpu_wait_ms"] == 20
        assert logs["periodic_frame_samples"][1]["pending_pipelines"] == 7
        assert logs["steady_state_established"] is False
        assert logs["mapping_barriers_last"]["barriers"] == 100
        assert logs["gpu_timestamp_command_samples"][0]["gpu_timestamp_ms_per_frame"] == 1.25
        hw = Path(tmp) / "hw.tsv"
        hw.write_text("gpu%\tvram_MiB\n50\t1000\n?\t2000\n")
        assert hardware_summary(hw)["vram_MiB"] == {"mean": 1500, "max": 2000}
        assert hardware_summary(hw)["gpu%"]["mean"] == 50
    print("benchmark selftest ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    reader = sub.add_parser("report")
    reader.add_argument("csv", type=Path)
    reader.add_argument("--warmup", type=float, default=0)
    reader.add_argument("--until", type=float)
    reader.add_argument("--log", type=Path)
    reader.add_argument("--hardware", type=Path)
    runner = sub.add_parser("run")
    runner.add_argument("--output", type=Path)
    runner.add_argument("--duration", type=int, default=120)
    runner.add_argument("--warmup", type=float, default=30)
    runner.add_argument("--a", nargs="*", default=[])
    runner.add_argument("--b", nargs="*")
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
    elif args.command == "report":
        stats = report(args.csv, args.warmup, args.until)
        if args.log:
            stats["runtime_log"] = log_samples(args.log)
        if args.hardware:
            stats["hardware_all_samples"] = hardware_summary(args.hardware)
        print(json.dumps(stats, indent=2))
    else:
        if not 0 <= args.warmup < args.duration:
            parser.error("require 0 <= warmup < duration")
        return run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

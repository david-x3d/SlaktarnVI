#!/usr/bin/env python3
"""Accepted native frame benchmark. No game execution unless 'run' is requested."""
import argparse
import csv
import json
import math
import os
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


def report(path, warmup=0):
    rows = []
    with open(path, newline="") as file:
        for row in csv.DictReader(file):
            try:
                end, interval = int(row["elapsed_ns"]), int(row["interval_ns"])
                if interval > 0 and end - interval >= warmup * 1e9:
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
    return {
        "accepted_native_fps": len(rows) / total,
        "swapchain_present_rate": presents / total if presents is not None else None,
        "scanout_fps": None,
        "intervals": len(rows), "sample_seconds": total,
        "frame_ms": {"median": (intervals[(len(intervals)-1)//2] + intervals[len(intervals)//2])/2,
                     "p95": percentile(.95), "p99": percentile(.99)},
        "draw_counter_delta": sum_field("draw_delta"),
        "shader_cache_hits": hits, "shader_cache_misses": misses,
        "shader_cache_hit_rate": hits / (hits + misses) if hits is not None and misses is not None and hits + misses else None,
    }


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
                   VICTORY_FRAME_CSV=str(directory / "frames.csv"))
        (directory / "config.json").write_text(json.dumps({
            "overrides": overrides, "duration": args.duration, "warmup": args.warmup,
            "frame_generation_requested": False}, indent=2) + "\n")
        result = subprocess.run(["bash", str(HERE / "run-7900.sh")], env=env)
        try:
            stats = report(directory / "frames.csv", args.warmup)
        except (ValueError, OSError) as exc:
            stats = {"error": str(exc)}
        stats["launcher_exit"] = result.returncode
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
    print("benchmark selftest ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    reader = sub.add_parser("report")
    reader.add_argument("csv", type=Path)
    reader.add_argument("--warmup", type=float, default=0)
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
        print(json.dumps(report(args.csv, args.warmup), indent=2))
    else:
        if not 0 <= args.warmup < args.duration:
            parser.error("require 0 <= warmup < duration")
        return run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Logs GPU and CPU once a second while the game runs; read-only (sysfs, /proc).

    hw-log.py <out.tsv> [seconds to wait for the game]

Per line: time, GPU busy %, VRAM used and GTT used (MiB), GPU shader clock
(MHz), GPU edge/junction temperature, GPU power (W), CPU busy % (all cores),
CPU temperature (Tctl), the game's CPU % (100 = one core), the game's own
memory as the GPU driver counts it (/proc/<pid>/fdinfo: resident in VRAM, in
GTT, evicted from VRAM, requested in VRAM; MiB), and its busiest threads
(name:% ...). Ends when the game has gone (or never came).
"""
import glob
import os
import sys
import csv
import shutil
import subprocess
import time



def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def parse_nvidia(line):
    values = next(csv.reader([line], skipinitialspace=True))
    if len(values) != 5:
        raise ValueError("expected five NVIDIA metrics")
    def number(value):
        try:
            return str(float(value))
        except ValueError:
            return "?"
    busy, vram, clock, temp, power = map(number, values)
    return busy, vram, "?", clock, temp, "?", power


def gpu_sample(vendor, dev, hwmon):
    if vendor == "nvidia":
        try:
            result = subprocess.run(
                ["nvidia-smi", "-i", os.environ.get("VALHALLA_NVIDIA_GPU", "0"),
                 "--query-gpu=utilization.gpu,memory.used,clocks.gr,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=True)
            return parse_nvidia(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return ("?",) * 7
    if not dev:
        return ("?",) * 7
    def mib(name):
        value = read(f"{dev}/{name}")
        return str(int(value) >> 20) if value.isdigit() else "?"
    power = read(f"{hwmon}/power1_average", read(f"{hwmon}/power1_input"))
    return (read(f"{dev}/gpu_busy_percent", "?"), mib("mem_info_vram_used"),
            mib("mem_info_gtt_used"), sclk(dev), str(temp_by_label(hwmon, "edge")),
            str(temp_by_label(hwmon, "junction")), str(int(power) / 1e6) if power.isdigit() else "?")


def gpu_device():
    vendor = os.environ.get("VALHALLA_GPU_VENDOR", "auto")
    if vendor == "auto":
        vendor = "nvidia" if shutil.which("nvidia-smi") else "amd"
    cards = [c for c in glob.glob("/sys/class/drm/card*")
             if os.path.basename(c)[4:].isdigit() and read(f"{c}/device/vendor") == "0x1002"]
    card = max(cards, key=lambda c: int(read(f"{c}/device/mem_info_vram_total", "0") or 0), default=None)
    dev = f"{card}/device" if card and vendor == "amd" else None
    return vendor, dev, (glob.glob(f"{dev}/hwmon/hwmon*") or [""])[0] if dev else ""


def ram_used():
    values = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.split()[0])
    return str((values["MemTotal"] - values["MemAvailable"]) // 1024) if len(values) == 2 else "?"


def temp_by_label(hwmon, label):
    for f in glob.glob(f"{hwmon}/temp*_label"):
        if read(f) == label:
            return int(read(f.replace("_label", "_input"), "0")) / 1000
    return "?"


def sclk(dev):
    for line in read(f"{dev}/pp_dpm_sclk").splitlines():
        if line.endswith("*"):
            return line.split()[1]
    return "?"


def cpu_times():
    f = read("/proc/stat").splitlines()[0].split()[1:]
    v = list(map(int, f))
    idle = v[3] + v[4]
    return sum(v), idle


def game_pid():
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open(f"/proc/{p}/cmdline", "rb") as f:
                    cmd = f.read()
            except OSError:
                continue
            if (b"valhalla.exe" in cmd or b"valhalla-diagnostics.exe" in cmd or b"seger.exe" in cmd or b"laurel.exe" in cmd or b"victory.exe" in cmd) and b"run" in cmd and read(f"/proc/{p}/comm") == "main":
                return int(p)
    return None


def threads(pid):
    t = {}
    for task in glob.glob(f"/proc/{pid}/task/*"):
        stat = read(f"{task}/stat")
        if not stat:
            continue
        name = stat[stat.index("(") + 1 : stat.rindex(")")]
        rest = stat[stat.rindex(")") + 2 :].split()
        t[task] = (name, int(rest[11]) + int(rest[12]))
    return t


def drm_memory(pid):
    """The game's GPU memory by the driver's fdinfo, per DRM client once:
    (resident VRAM, resident GTT, evicted VRAM, requested VRAM) in MiB;
    '?' where the kernel does not tell."""
    clients = {}
    for fd in glob.glob(f"/proc/{pid}/fdinfo/*"):
        text = read(fd)
        if "drm-driver:" not in text:
            continue
        keys = {}
        for line in text.splitlines():
            k, _, v = line.partition(":")
            keys[k.strip()] = v.strip()
        clients[keys.get("drm-client-id", fd)] = keys

    def mib(*names):
        total, seen = 0, False
        for keys in clients.values():
            for name in names:
                if name in keys:
                    parts = keys[name].split()
                    unit = {"KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30}.get(parts[1] if len(parts) > 1 else "", 1)
                    total += int(parts[0]) * unit
                    seen = True
                    break
        return str(total >> 20) if seen else "?"

    return (mib("drm-resident-vram", "drm-memory-vram"), mib("drm-resident-gtt", "drm-memory-gtt"),
            mib("amd-evicted-vram"), mib("amd-requested-vram"))


def main():
    if sys.argv[1:] == ["--selftest"]:
        assert parse_nvidia("91, 11800, 2520, 65, 210.5") == ("91.0", "11800.0", "?", "2520.0", "65.0", "?", "210.5")
        assert parse_nvidia("0, 10, [N/A], 55, [Not Supported]")[2:] == ("?", "?", "55.0", "?", "?")
        print("hardware parser selftest ok")
        return
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    vendor, dev, hwmon = gpu_device()
    cpu_hwmon = next((h for h in glob.glob("/sys/class/hwmon/hwmon*") if read(f"{h}/name") == "k10temp"), "")
    hz = os.sysconf("SC_CLK_TCK")
    start = time.monotonic()
    pid = None
    while pid is None and time.monotonic() - start < wait:
        pid = game_pid()
        time.sleep(1)
    if pid is None:
        sys.exit("no game process")
    with open(sys.argv[1], "w", buffering=1) as out:
        out.write("t\tgpu%\tvram_MiB\tgtt_MiB\tsclk\tgpu_edge\tgpu_junction\tgpu_W\tcpu%\tcpu_C\tgame%\tgame_vram\tgame_gtt\tevicted_vram\trequested_vram\tthreads\tram_used_MiB\n")
        prev_cpu, prev_t, prev_time = cpu_times(), threads(pid), time.monotonic()
        while os.path.exists(f"/proc/{pid}"):
            time.sleep(1)
            now = time.monotonic()
            dt = now - prev_time
            total, idle = cpu_times()
            cpu = 100 * (1 - (idle - prev_cpu[1]) / max(1, total - prev_cpu[0]))
            t = threads(pid)
            use = sorted(((100 * (ticks - prev_t[k][1]) / hz / dt, name)
                          for k, (name, ticks) in t.items() if k in prev_t), reverse=True)
            game = sum(u for u, _ in use)
            top = " ".join(f"{n}:{u:.0f}" for u, n in use[:8] if u >= 5)
            row = [f"{now - start:.1f}", *gpu_sample(vendor, dev, hwmon), f"{cpu:.0f}",
                   str(temp_by_label(cpu_hwmon, "Tctl")), f"{game:.0f}", *drm_memory(pid), top, ram_used()]
            out.write("\t".join(row) + "\n")
            prev_cpu, prev_t, prev_time = (total, idle), t, now


if __name__ == "__main__":
    main()

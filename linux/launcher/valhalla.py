#!/usr/bin/env python3
"""Valhalla launcher, by Slaktarn.

Settings before the game starts (graphics, AMD FSR upscaling and frame
generation, Series X or S profile, performance), a system check, shader
preparation, and Play / Stop. It starts the game through run-7900.sh with
the settings as its environment, so every run is the same as the tested ones.

    python3 launcher/valhalla.py            the window
    python3 launcher/valhalla.py --dry-run  print what Play would run
    python3 launcher/valhalla.py --selftest check the settings and presets
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent  # the folder with run-7900.sh, game/, the Proton copy
def runtime_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if any((p / ".victory-root").is_file() for p in (path, *path.parents)):
        raise ValueError(f"Runtime data cannot be stored in the source tree: {path}")
    return path


def location(override: str, xdg: str, default: Path) -> Path:
    return runtime_path(Path(os.environ.get(override) or Path(os.environ.get(xdg) or default) / "valhalla"))


STATE = location("VALHALLA_STATE_DIR", "XDG_STATE_HOME", Path.home() / ".local/state")
CACHE = location("VALHALLA_CACHE_DIR", "XDG_CACHE_HOME", Path.home() / ".cache")
DATA = location("VALHALLA_DATA_DIR", "XDG_DATA_HOME", Path.home() / ".local/share")
SETTINGS_FILE = STATE / "launcher.json"
LOG_FILE = STATE / "valhalla.log"


def executable(diagnostics: bool) -> Path:
    if override := os.environ.get("EXE"):
        return Path(override).expanduser().resolve()
    profile = "diag" if diagnostics else "release"
    built = BASE.parent / "target" / "x86_64-pc-windows-msvc" / profile / "valhalla.exe"
    packaged = BASE / "bin" / ("valhalla-diagnostics.exe" if diagnostics else "valhalla.exe")
    return next((p for p in (built, packaged) if p.is_file()), built)


def gpu_vendor() -> str:
    vendors = set()
    for path in Path("/sys/class/drm").glob("card*/device/vendor"):
        try:
            vendors.add(path.read_text().strip())
        except OSError:
            continue
    return "nvidia" if "0x10de" in vendors else "amd" if "0x1002" in vendors else "other"


GAME_EXE_NAME = os.environ.get("GAME_EXE", "game.exe")
FIDELITYFX = BASE / "keep" / "fidelityfx"
CAPTURES = STATE / "captures"
SHADER_CACHE = runtime_path(Path(os.environ.get("VICTORY_CACHE_DIR") or CACHE / "cache")) / "shaders"


def shaders_prepared() -> bool:
    """Whether this PC has translated the game's shaders (Tools > Prepare)."""
    try:
        return any(SHADER_CACHE.iterdir())
    except OSError:
        return False
# The title's own key art and logo, read from its folder when the window opens.
def key_art(game_dir: Path) -> list[Path]:
    return [game_dir / "xbs_splash.png", game_dir / "xbs_logo_480.png"]

RESOLUTIONS = ["1280x720", "1664x936", "1920x1080", "2560x1440", "3840x2160"]
# Name, render scale (of the output) for AMD FSR.
FSR_QUALITY = [
    ("Native AA", 1.0),
    ("Quality", 0.6667),
    ("Balanced", 0.5882),
    ("Performance", 0.5),
]
RENDER_SCALES = [("Default", ""), ("50% (experimental)", "0.5"), ("67%", "0.6667"), ("100%", "1")]
# Name, frame interval the title is paced at (ms): whole vblanks of 60 Hz, as
# on the console. (16 ms ticks put 30 fps video on a 2-2-2-3 tick cadence:
# Bink's logo video juddered, 2026-11-26.)
FRAME_CAPS = [("30 fps", "33.333"), ("60 fps", "16.667"), ("120 fps", "8.333"), ("Unlimited", "0")]


@dataclass
class Settings:
    preset: str = "High"
    vendor: str = "auto"
    diagnostics: bool = True
    render_scale: str = "Default"
    resolution: str = "2560x1440"
    upscaler: str = "fsr"  # "game": the title's own; "fsr": AMD FSR on the host
    fsr_quality: str = "Quality"
    fsr4: bool = True  # FSR 4 (machine learning, RDNA 4) in place of FSR 3.1
    frame_generation: bool = True
    frame_cap: str = "60 fps"
    console: str = "series-x"  # or "series-s": lighter settings, lower resolution
    reflections_hold: bool = True
    ambient: float = 0.8
    ray_tracing: bool = False  # experimental, about a third slower
    safe_mode: bool = False  # without the newest, faster ways
    low_memory: bool = False  # for PCs with 16 GB
    pipeline_wait_ms: int = 30
    stats_overlay: bool = True
    skip_intro: bool = True
    f4_capture: bool = False  # F4 saves the next frame (for bug reports)
    hw_log: bool = True
    game_dir: str = ""  # the game's folder (its own files); empty: game/ beside Valhalla

    def game_path(self) -> Path:
        return Path(self.game_dir or os.environ.get("GAME_DIR") or DATA / "game").expanduser().resolve()

    @staticmethod
    def load() -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return apply_preset(Settings(), "Performance" if gpu_vendor() == "nvidia" else "High")
        if not isinstance(data, dict):
            return apply_preset(Settings(), "Performance" if gpu_vendor() == "nvidia" else "High")
        known = {f.name for f in fields(Settings)}
        settings = Settings(**{k: v for k, v in data.items() if k in known})
        if settings.resolution == "1600x900":
            settings.resolution = "1664x936"
        return settings

    def save(self) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2))


PRESETS: dict[str, dict] = {
    "Compatibility": dict(resolution="1920x1080", upscaler="game", render_scale="Default", fsr4=False,
                          frame_generation=False, frame_cap="60 fps", console="series-s", low_memory=False,
                          ray_tracing=False, safe_mode=True, pipeline_wait_ms=500),
    "Performance": dict(resolution="1920x1080", upscaler="game", render_scale="Default", fsr4=False,
                        frame_generation=False, frame_cap="120 fps", console="series-s", low_memory=False,
                        ray_tracing=False, safe_mode=False, pipeline_wait_ms=30),
    "Balanced": dict(resolution="1920x1080", upscaler="game", render_scale="Default", fsr4=False,
                     frame_generation=False, frame_cap="60 fps", console="series-x", low_memory=False,
                     ray_tracing=False, safe_mode=False, pipeline_wait_ms=100),
    "Quality": dict(resolution="2560x1440", upscaler="game", render_scale="67%", fsr4=False,
                    frame_generation=False, frame_cap="60 fps", console="series-x", low_memory=False,
                    ray_tracing=False, safe_mode=False, pipeline_wait_ms=100),
    "Experimental": dict(resolution="1920x1080", upscaler="game", render_scale="50% (experimental)", fsr4=False,
                         frame_generation=False, frame_cap="Unlimited", console="series-s", low_memory=False,
                         ray_tracing=False, safe_mode=False, pipeline_wait_ms=30),
    # A lower-spec PC: the title's Series S settings, 1080p, FSR Performance,
    # frame generation for smoothness, 30 fps pacing. (Not low memory mode:
    # under Wine it made every window wait for card memory, 4-5 fps.)
    "Low": dict(resolution="1920x1080", upscaler="fsr", fsr_quality="Performance", fsr4=False,
                frame_generation=True, frame_cap="30 fps", console="series-s", low_memory=False,
                ray_tracing=False, safe_mode=False),
    "Medium": dict(resolution="1920x1080", upscaler="fsr", fsr_quality="Quality", fsr4=True,
                   frame_generation=False, frame_cap="60 fps", console="series-x", low_memory=False,
                   ray_tracing=False, safe_mode=False),
    "High": dict(resolution="2560x1440", upscaler="fsr", fsr_quality="Quality", fsr4=True,
                 frame_generation=True, frame_cap="60 fps", console="series-x", low_memory=False,
                 ray_tracing=False, safe_mode=False),
    # S70: exactly the tested runs' settings (Slaktarn's RX 9070 XT, 9800X3D).
    "S70": dict(resolution="2560x1440", upscaler="fsr", fsr_quality="Quality", fsr4=True,
                frame_generation=True, frame_cap="60 fps", console="series-x", low_memory=False,
                ray_tracing=False, safe_mode=False, reflections_hold=True, ambient=0.8,
                pipeline_wait_ms=30, stats_overlay=True, skip_intro=True),
    "Ultra": dict(resolution="3840x2160", upscaler="fsr", fsr_quality="Quality", fsr4=True,
                  frame_generation=True, frame_cap="60 fps", console="series-x", low_memory=False,
                  ray_tracing=False, safe_mode=False),
}


def apply_preset(s: Settings, name: str) -> Settings:
    values = PRESETS.get(name)
    if values is None:
        s.preset = "Custom"
        return s
    for k, v in values.items():
        setattr(s, k, v)
    s.preset = name
    return s


def windows_path(p: Path) -> str:
    """The Wine drive Z: path of a Linux path."""
    return "Z:" + str(p).replace("/", "\\")


def launch_env(s: Settings) -> dict[str, str]:
    """The settings as run-7900.sh's environment."""
    vendor = gpu_vendor() if s.vendor == "auto" else s.vendor
    env: dict[str, str] = {
        "LOG": str(LOG_FILE),
        "EXE": str(executable(s.diagnostics)),
        "VICTORY_DIAGNOSTICS": "1" if s.diagnostics else "0",
        "VALHALLA_GPU_VENDOR": vendor,
        "VICTORY_OUTPUT": s.resolution,
        "VICTORY_FRAME_INTERVAL_MS": dict(FRAME_CAPS).get(s.frame_cap, "16.667"),
        "VICTORY_AMBIENT": f"{s.ambient:g}",
        "VICTORY_AMBIENT_REFLECT": "1" if s.reflections_hold else "0",
        "VICTORY_PIPELINE_WAIT_MS": str(int(s.pipeline_wait_ms)),
        "VICTORY_PANEL": "1" if s.stats_overlay and s.diagnostics else "0",
        "SKIP_INTRO": "1" if s.skip_intro else "0",
        "VICTORY_CARD_BUFFERS_MIN_KB": "64",
        "GAME_DIR": str(s.game_path()),
        "GAME_EXE": GAME_EXE_NAME,
    }
    if s.console == "series-s":
        env["CONSOLE"] = "series-s"
    if s.upscaler == "fsr":
        env["VICTORY_FIDELITYFX_DIR"] = windows_path(FIDELITYFX)
        env["VICTORY_UPSCALER"] = "7"
        env["VICTORY_FFX_SCALE"] = f"{dict(FSR_QUALITY).get(s.fsr_quality, 0.6667):g}"
        if s.fsr4 and vendor == "amd":
            env["FSR4_UPGRADE"] = "1"
            env["PROTON_FSR4_UPGRADE"] = "1"
        if s.frame_generation and s.diagnostics:
            env["VICTORY_FIDELITYFX_FRAMEGEN"] = "1"
    elif scale := dict(RENDER_SCALES).get(s.render_scale):
        env["VICTORY_RENDER_SCALE"] = scale
    if s.ray_tracing:
        env["RT"] = "1"
    if s.safe_mode:
        env["SAFE"] = "1"
        env.update({k: "0" for k in ("VICTORY_TEXTURE_NO_WAIT", "VICTORY_THIN", "VICTORY_UPLOAD_AHEAD",
                                        "VICTORY_PORT_DESCRIPTORS", "VICTORY_PORT_CONSTANTS", "VICTORY_PORT_PRIVATE",
                                        "VICTORY_PORT_CBV")})
    if s.preset == "Experimental":
        env["VICTORY_SHADER_OPT"] = "127"  # opt-in vectorized raw loads; needs NVIDIA A/B
    if s.low_memory:
        env["VICTORY_WINDOW_PLAIN"] = "1"
    if s.f4_capture and s.diagnostics:
        env["VICTORY_CAPTURE"] = str(CAPTURES)
        env["VICTORY_DUMP_TARGETS"] = "1"
    return env


def process_env(s: Settings) -> dict[str, str]:
    env = dict(os.environ)
    # Switching profiles must clear settings inherited from an earlier preset.
    for key in ("FSR4_UPGRADE", "PROTON_FSR4_UPGRADE", "VICTORY_FIDELITYFX_FRAMEGEN", "VICTORY_UPSCALER",
                "VICTORY_FFX_SCALE", "VICTORY_RENDER_SCALE", "VICTORY_FIDELITYFX_DIR", "SAFE", "RT", "CONSOLE"):
        env.pop(key, None)
    env.update(launch_env(s))
    return env


def game_pids() -> list[int]:
    """The game's process: comm `main`, args with `valhalla.exe run`."""
    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip()
            args = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if comm == "main" and (b"valhalla.exe" in args or b"valhalla-diagnostics.exe" in args) and b" run" in args:
            out.append(int(proc.name))
    return out


def last_fps() -> str | None:
    """The newest frame-rate line of the game's log, in short."""
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            tail = f.read().decode(errors="replace")
    except OSError:
        return None
    lines = re.findall(r"victory_gpu::host: ([\d.]+) fps, ([\d.]+) ms/frame.*?(\d+) draws/frame", tail)
    if not lines:
        return None
    fps, ms, draws = lines[-1]
    return f"{float(fps):.1f} fps real · {float(ms):.1f} ms a frame · {draws} draws"


def system_info(game_dir: Path | None = None, settings: Settings | None = None) -> list[tuple[str, str, bool]]:
    """(what, found, all right) for the system check."""
    rows = []
    cpu = "?"
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    rows.append(("Processor", cpu, cpu != "?"))
    ram_gb = 0.0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                ram_gb = int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    rows.append(("Memory", f"{ram_gb:.0f} GB" + ("" if ram_gb >= 30 else " (use Low memory mode)"), ram_gb >= 15))
    gpu = "?"
    if shutil.which("lspci"):
        try:
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if re.search(r"VGA|3D controller|Display controller", line):
                    gpu = line.split(": ", 1)[-1]
                    if "NVIDIA" in gpu or "AMD" in gpu or "ATI" in gpu:
                        break
        except (OSError, subprocess.SubprocessError):
            pass
    rows.append(("Graphics card", gpu, gpu != "?"))
    try:
        proton = subprocess.run(["bash", str(BASE / "run-victory.sh"), "--print-proton"], capture_output=True, text=True, timeout=5)
        rows.append(("Proton", proton.stdout.strip() or "missing", proton.returncode == 0))
    except (OSError, subprocess.SubprocessError):
        rows.append(("Proton", "missing", False))
    settings = settings or Settings.load()
    game = (game_dir or settings.game_path()) / GAME_EXE_NAME
    rows.append(("Game files", str(game.parent) if game.exists() else f"missing in {game.parent}", game.exists()))
    exe = executable(settings.diagnostics)
    rows.append(("Valhalla build", "found" if exe.exists() else "missing (build it first)", exe.exists()))
    fsr = (FIDELITYFX / "amd_fidelityfx_upscaler_dx12.dll").exists()
    rows.append(("AMD FidelityFX", "found" if fsr else "missing (FSR off)", fsr or settings.upscaler != "fsr"))
    rows.append(("Launch script", "found" if (BASE / "run-7900.sh").exists() else "missing", (BASE / "run-7900.sh").exists()))
    return rows


# ---------------------------------------------------------------- the window

ACCENT = "#ff5c8a"
ACCENT2 = "#ffa14d"

STYLE = """
* { font-family: "Inter", "Noto Sans", sans-serif; font-size: 10pt; color: #eef0f3; }
QLabel#wordmark { font-size: 15pt; font-weight: 700; letter-spacing: 3px; color: #ffffff; }
QLabel#credit { font-size: 9pt; color: rgba(255,255,255,0.62); }
QPushButton#nav { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 2px;
    margin: 0 12px; color: rgba(255,255,255,0.66); font-size: 10pt; font-weight: 600; letter-spacing: 1px; }
QPushButton#nav:hover { color: #ffffff; }
QPushButton#nav:checked { color: #ffffff; border-bottom: 2px solid """ + ACCENT + """; }
QLabel#herotitle { font-size: 30pt; font-weight: 800; color: #ffffff; }
QLabel#herosub { font-size: 11pt; color: rgba(255,255,255,0.78); }
QLabel#pagetitle { font-size: 20pt; font-weight: 700; color: #ffffff; }
QLabel#pagesub { color: rgba(255,255,255,0.65); }
QLabel#group { font-size: 8.5pt; font-weight: 700; color: rgba(255,255,255,0.55); letter-spacing: 2px; }
QLabel#rowtitle { font-size: 10.5pt; color: #f1f2f4; font-weight: 500; }
QLabel#rowdesc { font-size: 9pt; color: rgba(255,255,255,0.55); }
QFrame#rows { background: rgba(14,15,20,0.80); border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; }
QFrame#divider { background: rgba(255,255,255,0.07); max-height: 1px; min-height: 1px; border: none; }
QComboBox, QSpinBox { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.14); border-radius: 3px;
    padding: 5px 10px; min-height: 22px; min-width: 180px; }
QComboBox:hover, QSpinBox:hover { border-color: rgba(255,255,255,0.32); }
QComboBox:focus, QSpinBox:focus { border-color: """ + ACCENT + """; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #17181e; border: 1px solid #2c2e37; selection-background-color: #3a2230;
    outline: none; padding: 4px; }
QRadioButton { spacing: 10px; }
QRadioButton::indicator { width: 14px; height: 14px; border-radius: 8px; border: 2px solid rgba(255,255,255,0.4); background: transparent; }
QRadioButton::indicator:checked { border: 5px solid """ + ACCENT + """; background: #ffffff; }
QPushButton#seg, QPushButton#segfirst, QPushButton#seglast { background: rgba(10,10,14,0.55); border: 1px solid rgba(255,255,255,0.16);
    padding: 7px 18px; color: rgba(255,255,255,0.8); font-weight: 600; border-radius: 0; }
QPushButton#segfirst { border-top-left-radius: 3px; border-bottom-left-radius: 3px; }
QPushButton#seglast { border-top-right-radius: 3px; border-bottom-right-radius: 3px; }
QPushButton#seg:checked, QPushButton#segfirst:checked, QPushButton#seglast:checked {
    background: rgba(255,92,138,0.22); border-color: """ + ACCENT + """; color: #ffffff; }
QPushButton#ghost { background: rgba(10,10,14,0.55); border: 1px solid rgba(255,255,255,0.22); border-radius: 3px;
    padding: 9px 20px; color: #eef0f3; font-weight: 600; }
QPushButton#ghost:hover { border-color: rgba(255,255,255,0.5); }
QPushButton#ghost:disabled { color: rgba(255,255,255,0.3); border-color: rgba(255,255,255,0.1); }
QPushButton#play { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 """ + ACCENT + """, stop:1 """ + ACCENT2 + """);
    color: #1a0a10; border: none; border-radius: 3px; font-size: 15pt; font-weight: 800; letter-spacing: 4px; padding: 14px 64px; }
QPushButton#play:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff7aa0, stop:1 #ffb56e); }
QPushButton#play:disabled { background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }
QLabel#status { color: rgba(255,255,255,0.72); }
QLabel#chip { background: rgba(10,10,14,0.6); border: 1px solid rgba(255,255,255,0.16); border-radius: 3px;
    padding: 4px 10px; color: #f1f2f4; font-size: 9pt; font-weight: 600; }
QLabel#ok { color: #6fdc9b; } QLabel#bad { color: #ff7d6e; }
QPlainTextEdit { background: rgba(8,8,11,0.85); border: 1px solid rgba(255,255,255,0.08); border-radius: 4px;
    font-family: "JetBrains Mono", "Noto Sans Mono", monospace; font-size: 9pt; color: #c8ccd2; padding: 6px; }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.18); border-radius: 2px; }
QSlider::sub-page:horizontal { background: """ + ACCENT + """; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffffff; width: 14px; margin: -6px 0; border-radius: 7px; }
QScrollArea, QScrollArea > QWidget > QWidget { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.2); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QStackedWidget, QWidget#page { background: transparent; }
"""


def run_window() -> int:
    from PySide6.QtCore import QProcess, QProcessEnvironment, QRectF, QSize, Qt, QTimer
    from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QAbstractButton, QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
        QStackedWidget, QVBoxLayout, QWidget,
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Valhalla")
    app.setStyleSheet(STYLE)

    class Backdrop(QWidget):
        """The title's key art over the whole window in two layers: a soft,
        blurred one deep behind and the sharp art in front. Both follow the
        mouse a little (the front one more: parallax) and drift slowly. The
        Play page shows the art; the settings pages the soft layer, darker."""

        DEPTH_FRONT = 22.0  # pixels the front layer moves, at the window's edge
        DEPTH_BACK = 9.0

        def __init__(self) -> None:
            super().__init__()
            self.art = next((QPixmap(str(p)) for p in key_art(Settings.load().game_path()) if p.exists()),
                            QPixmap())
            self.dim = 0.0
            self.front = QPixmap()
            self.back = QPixmap()
            self.size_cached = (0, 0)
            self.offset = [0.0, 0.0]
            self.started = time.time()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.step)
            self.timer.start(16)

        def layers(self) -> None:
            w, h = self.width(), self.height()
            if self.art.isNull() or self.size_cached == (w, h):
                return
            self.size_cached = (w, h)
            # Room for the layers to move: a little larger than the window.
            margin = 1.0 + 2.6 * self.DEPTH_FRONT / max(1, min(w, h))
            cw, ch = int(w * margin), int(h * margin)
            self.front = self.art.scaled(cw, ch, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.back = self.soft().scaled(int(cw * 1.04), int(ch * 1.04), Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)

        def soft(self) -> "QPixmap":
            """The art blurred (Gaussian, once), for the deep layer."""
            if getattr(self, "_soft", None) is not None:
                return self._soft
            pix = self.art.scaled(640, 360, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            try:
                import io
                from PIL import Image, ImageFilter
                from PySide6.QtCore import QBuffer, QByteArray, QIODevice
                data = QByteArray()
                buf = QBuffer(data)
                buf.open(QIODevice.WriteOnly)
                pix.save(buf, "PNG")
                im = Image.open(io.BytesIO(bytes(data))).convert("RGB").filter(ImageFilter.GaussianBlur(14))
                out = io.BytesIO()
                im.save(out, "PNG")
                blurred = QPixmap()
                blurred.loadFromData(out.getvalue())
                self._soft = blurred
            except Exception:
                self._soft = pix.scaled(40, 22, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return self._soft

        def step(self) -> None:
            if not self.isVisible():
                return
            from PySide6.QtGui import QCursor
            pos = self.mapFromGlobal(QCursor.pos())
            w, h = max(1, self.width()), max(1, self.height())
            target = [max(-1.0, min(1.0, (pos.x() - w / 2) / (w / 2))),
                      max(-1.0, min(1.0, (pos.y() - h / 2) / (h / 2)))]
            t = time.time() - self.started
            target[0] += 0.18 * math.sin(t * 0.11)
            target[1] += 0.12 * math.cos(t * 0.08)
            moved = False
            for i in range(2):
                new = self.offset[i] + (target[i] - self.offset[i]) * 0.06
                moved |= abs(new - self.offset[i]) > 0.002
                self.offset[i] = new
            if moved:
                self.update()

        def draw(self, p: "QPainter", pix: "QPixmap", depth: float) -> None:
            w, h = self.width(), self.height()
            x = (w - pix.width()) / 2 - self.offset[0] * depth
            y = (h - pix.height()) / 2 - self.offset[1] * depth
            p.drawPixmap(int(x), int(y), pix)

        def paintEvent(self, _event: object) -> None:
            self.layers()
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor("#0b0b10"))
            if not self.back.isNull():
                self.draw(p, self.back, self.DEPTH_BACK)
            if not self.front.isNull() and self.dim < 0.3:
                p.setOpacity(1.0 - self.dim / 0.3)
                self.draw(p, self.front, self.DEPTH_FRONT)
                p.setOpacity(1.0)
            top = QLinearGradient(0, 0, 0, 130)
            top.setColorAt(0, QColor(6, 6, 10, 220))
            top.setColorAt(1, QColor(6, 6, 10, 0))
            p.fillRect(0, 0, w, 130, top)
            bottom = QLinearGradient(0, h * 0.42, 0, h)
            bottom.setColorAt(0, QColor(6, 6, 10, 0))
            bottom.setColorAt(1, QColor(6, 6, 10, 240))
            p.fillRect(0, int(h * 0.42), w, h - int(h * 0.42), bottom)
            left = QLinearGradient(0, 0, w * 0.6, 0)
            left.setColorAt(0, QColor(6, 6, 10, 180))
            left.setColorAt(1, QColor(6, 6, 10, 0))
            p.fillRect(0, 0, int(w * 0.6), h, left)
            if self.dim:
                p.fillRect(self.rect(), QColor(8, 8, 12, int(255 * self.dim)))

    class Toggle(QAbstractButton):
        """A switch."""

        def __init__(self) -> None:
            super().__init__()
            self.setCheckable(True)
            self.setCursor(Qt.PointingHandCursor)

        def sizeHint(self) -> QSize:
            return QSize(40, 22)

        def paintEvent(self, _event: object) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            on = self.isChecked()
            track = QColor(ACCENT) if on else QColor("#3a3f47")
            if not self.isEnabled():
                track = QColor("#2a2e34")
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRectF(0, 2, 40, 18), 9, 9)
            p.setBrush(QColor("#ffffff" if self.isEnabled() else "#6b7079"))
            x = 22 if on else 2
            p.drawEllipse(QRectF(x, 4, 14, 14))

    def label(text: str, name: str, wrap: bool = False) -> QLabel:
        l = QLabel(text)
        l.setObjectName(name)
        l.setWordWrap(wrap)
        return l

    class Rows(QFrame):
        """A group of settings rows: title and description left, control right."""

        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("rows")
            self.box = QVBoxLayout(self)
            self.box.setContentsMargins(0, 0, 0, 0)
            self.box.setSpacing(0)
            self.count = 0

        def add(self, title: str, desc: str, control: QWidget) -> QWidget:
            if self.count:
                d = QFrame()
                d.setObjectName("divider")
                self.box.addWidget(d)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(18, 12, 18, 12)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(title, "rowtitle"))
            if desc:
                text.addWidget(label(desc, "rowdesc", True))
            h.addLayout(text, 1)
            h.addSpacing(24)
            h.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
            self.box.addWidget(row)
            self.count += 1
            return row

    def page(title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        inner = QWidget()
        inner.setObjectName("page")
        inner.setMaximumWidth(900)
        v = QVBoxLayout(inner)
        v.setContentsMargins(48, 26, 48, 30)
        v.setSpacing(8)
        v.addWidget(label(title, "pagetitle"))
        v.addWidget(label(subtitle, "pagesub", True))
        v.addSpacing(14)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        return area, v

    def group(v: QVBoxLayout, name: str) -> Rows:
        v.addSpacing(8)
        v.addWidget(label(name.upper(), "group"))
        v.addSpacing(2)
        rows = Rows()
        v.addWidget(rows)
        return rows

    class Window(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.s = Settings.load()
            self.loading = True
            self.started_at = 0.0
            self.game: subprocess.Popen | None = None
            self.tool: QProcess | None = None
            self.setWindowTitle("Valhalla")
            self.resize(1120, 740)
            self.setMinimumSize(980, 640)
            self.backdrop = Backdrop()
            self.setCentralWidget(self.backdrop)
            v = QVBoxLayout(self.backdrop)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)

            # The top bar.
            bar = QHBoxLayout()
            bar.setContentsMargins(40, 18, 40, 0)
            bar.addWidget(label("VALHALLA", "wordmark"))
            bar.addSpacing(34)
            self.stack = QStackedWidget()
            self.nav = QButtonGroup(self)
            for i, name in enumerate(["PLAY", "GRAPHICS", "PERFORMANCE", "SYSTEM", "TOOLS"]):
                b = QPushButton(name)
                b.setObjectName("nav")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, i=i: self.go(i))
                self.nav.addButton(b, i)
                bar.addWidget(b)
            bar.addStretch(1)
            self.side_status = label("", "credit")
            bar.addWidget(self.side_status)
            bar.addSpacing(18)
            bar.addWidget(label("by Slaktarn", "credit"))
            v.addLayout(bar)
            v.addWidget(self.stack, 1)

            self.build_play()
            self.build_graphics()
            self.build_performance()
            self.build_system()
            self.build_tools()

            self.nav.button(0).setChecked(True)
            self.show_settings()
            self.connect_changes()
            self.loading = False
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(2000)
            self.tick()

        # Pages.
        def go(self, i: int) -> None:
            self.stack.setCurrentIndex(i)
            self.backdrop.dim = 0.0 if i == 0 else 0.5
            self.backdrop.update()

        def build_play(self) -> None:
            hero = QWidget()
            hero.setObjectName("page")
            v = QVBoxLayout(hero)
            v.setContentsMargins(48, 20, 48, 40)
            v.setSpacing(10)
            v.addStretch(1)
            presets = QHBoxLayout()
            presets.setSpacing(0)
            self.preset = QComboBox()
            self.preset.addItems([*PRESETS, "Custom"])
            self.preset.activated.connect(lambda _: self.choose_preset(self.preset.currentText()))
            presets.addWidget(self.preset)
            presets.addStretch(1)
            v.addWidget(label("PRESET", "group"))
            v.addLayout(presets)
            v.addSpacing(6)
            self.chips = QHBoxLayout()
            self.chips.setSpacing(8)
            self.chip_labels = [label("", "chip") for _ in range(5)]
            for c in self.chip_labels:
                self.chips.addWidget(c)
            self.chips.addStretch(1)
            v.addLayout(self.chips)
            v.addSpacing(14)
            row = QHBoxLayout()
            row.setSpacing(12)
            self.play_button = QPushButton("PLAY")
            self.play_button.setObjectName("play")
            self.play_button.setCursor(Qt.PointingHandCursor)
            self.play_button.clicked.connect(self.play)
            row.addWidget(self.play_button)
            self.stop_button = QPushButton("STOP")
            self.stop_button.setObjectName("ghost")
            self.stop_button.clicked.connect(self.stop)
            row.addWidget(self.stop_button)
            row.addSpacing(10)
            self.status = label("", "status")
            row.addWidget(self.status, 1)
            v.addLayout(row)
            self.checks_line = label("", "rowdesc", True)
            v.addWidget(self.checks_line)
            self.summary_labels = {}
            self.stack.addWidget(hero)

        def build_graphics(self) -> None:
            area, v = page("Graphics", "Resolution, upscaling and GPU settings. Changes apply on the next launch.")
            rows = group(v, "Display")
            self.vendor = QComboBox()
            for name, value in [("Automatic", "auto"), ("NVIDIA", "nvidia"), ("AMD", "amd"), ("Other", "other")]:
                self.vendor.addItem(name, value)
            rows.add("GPU vendor", "FSR 4 upgrades are only requested for AMD. Other vendors can use the game's upscaler.", self.vendor)
            self.resolution = QComboBox()
            self.resolution.addItems(RESOLUTIONS)
            rows.add("Output resolution", "The size the game renders its frame for.", self.resolution)
            self.frame_cap = QComboBox()
            self.frame_cap.addItems([n for n, _ in FRAME_CAPS])
            rows.add("Frame rate cap", "The pace the game runs its frames at.", self.frame_cap)
            rows = group(v, "AMD FidelityFX")
            self.upscaler = QComboBox()
            self.upscaler.addItem("AMD FSR", "fsr")
            self.upscaler.addItem("The game's own", "game")
            rows.add("Upscaler", "AMD FSR on the PC, or the game's own upscaling.", self.upscaler)
            self.render_scale = QComboBox()
            self.render_scale.addItems([name for name, _ in RENDER_SCALES])
            rows.add("Game render scale", "Internal scale for the game's own upscaler. 50% is experimental.", self.render_scale)
            self.fsr_quality = QComboBox()
            for name, scale in FSR_QUALITY:
                self.fsr_quality.addItem(f"{name} ({scale * 100:.0f}%)", name)
            rows.add("FSR quality", "The share of the output the game renders before upscaling.", self.fsr_quality)
            self.fsr4 = Toggle()
            rows.add("FSR 4", "Machine-learning upscaling on Radeon RX 9000 cards.", self.fsr4)
            self.frame_generation = Toggle()
            rows.add("Frame generation", "About twice the frames on screen. Smoother motion; the game still reacts "
                     "at its real frame rate. Best from 30 real fps up.", self.frame_generation)
            rows = group(v, "Lighting")
            slider = QWidget()
            sh = QHBoxLayout(slider)
            sh.setContentsMargins(0, 0, 0, 0)
            self.ambient = QSlider(Qt.Horizontal)
            self.ambient.setRange(40, 140)
            self.ambient.setFixedWidth(150)
            self.ambient_label = label("", "rowdesc")
            self.ambient_label.setFixedWidth(34)
            sh.addWidget(self.ambient)
            sh.addWidget(self.ambient_label)
            rows.add("Indirect light", "The strength of the light that stands in for ray-traced bounce light.", slider)
            self.reflections_hold = Toggle()
            rows.add("Hold reflections", "Keeps reflections to the light around them: no open-sky glow indoors.",
                     self.reflections_hold)
            self.ray_tracing = Toggle()
            rows.add("Ray tracing", "Experimental. The game's ray-traced indirect light, about a third slower.",
                     self.ray_tracing)
            v.addStretch(1)
            self.stack.addWidget(area)

        def build_performance(self) -> None:
            area, v = page("Performance", "The console the game believes it runs on, and how Valhalla works.")
            rows = group(v, "Console profile")
            pick = QWidget()
            ph = QVBoxLayout(pick)
            ph.setContentsMargins(0, 0, 0, 0)
            self.series_x = QRadioButton("Xbox Series X")
            self.series_s = QRadioButton("Xbox Series S")
            g = QButtonGroup(self)
            g.addButton(self.series_x)
            g.addButton(self.series_s)
            ph.addWidget(self.series_x)
            ph.addWidget(self.series_s)
            rows.add("Console", "The game picks its resolution, memory budgets and effects by it. "
                     "Series S suits weaker graphics cards.", pick)
            rows = group(v, "Engine")
            self.safe_mode = Toggle()
            rows.add("Safe mode", "Without the newest, faster ways, if something looks wrong.", self.safe_mode)
            self.low_memory = Toggle()
            rows.add("Low memory mode", "For PCs with 16 GB: less memory shared with the graphics card. "
                     "Experimental under Linux: it can make the game much slower.", self.low_memory)
            self.pipeline_wait = QSpinBox()
            self.pipeline_wait.setRange(0, 500)
            self.pipeline_wait.setSuffix(" ms")
            rows.add("Pipeline wait", "How long a frame waits for a pipeline still being built. "
                     "Higher: fewer pop-ins, more stutter.", self.pipeline_wait)
            rows = group(v, "While playing")
            self.diagnostics = Toggle()
            rows.add("Diagnostics build", "Enables profiling, the overlay, captures and FidelityFX frame generation. Disable for normal builds.", self.diagnostics)
            self.stats_overlay = Toggle()
            rows.add("Stats overlay", "Real and final fps and the frame's parts. F3 shows and hides it.",
                     self.stats_overlay)
            self.skip_intro = Toggle()
            rows.add("Skip the intro", "Presses Enter through the opening screens.", self.skip_intro)
            self.f4_capture = Toggle()
            rows.add("F4 frame capture", "F4 saves the next frame with all its draws, for bug reports.",
                     self.f4_capture)
            self.hw_log = Toggle()
            rows.add("Hardware log", "GPU and CPU once a second, beside the game's log.", self.hw_log)
            v.addStretch(1)
            self.stack.addWidget(area)

        def build_system(self) -> None:
            area, v = page("System", "What Valhalla found on this PC.")
            rows = group(v, "The game")
            self.game_label = label(str(self.s.game_path()), "rowdesc")
            cell = QWidget()
            ch = QHBoxLayout(cell)
            ch.setContentsMargins(0, 0, 0, 0)
            ch.addWidget(self.game_label)
            change = QPushButton("Change…")
            change.setObjectName("ghost")
            change.clicked.connect(self.choose_game_dir)
            ch.addWidget(change)
            rows.add("Game folder", "Your own copy of the game (the folder with its executable; set GAME_EXE to that file's name). "
                     "Valhalla ships without any game files.", cell)
            rows = group(v, "This PC")
            for what, found, ok in system_info():
                value = label(found, "rowdesc")
                value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell = QWidget()
                ch = QHBoxLayout(cell)
                ch.setContentsMargins(0, 0, 0, 0)
                ch.addWidget(value)
                ch.addWidget(label("●", "ok" if ok else "bad"))
                rows.add(what, "", cell)
            v.addStretch(1)
            self.stack.addWidget(area)

        def build_tools(self) -> None:
            area, v = page("Tools", "Preparation and files.")
            rows = group(v, "Preparation")
            self.prepare_button = QPushButton("Prepare")
            self.prepare_button.setObjectName("ghost")
            self.prepare_button.clicked.connect(self.prepare)
            rows.add("Shaders and pipelines", "After an update: translates every shader and builds the pipelines "
                     "the game used ahead, so the first minutes stutter less.", self.prepare_button)
            rows = group(v, "Files")
            b = QPushButton("Open")
            b.setObjectName("ghost")
            b.clicked.connect(lambda: self.open_path(LOG_FILE))
            rows.add("The game's log", str(LOG_FILE), b)
            b = QPushButton("Open")
            b.setObjectName("ghost")
            b.clicked.connect(lambda: self.open_path(CAPTURES))
            rows.add("Captures", str(CAPTURES), b)
            v.addSpacing(10)
            v.addWidget(label("OUTPUT", "group"))
            self.console_out = QPlainTextEdit()
            self.console_out.setReadOnly(True)
            self.console_out.setMinimumHeight(220)
            v.addWidget(self.console_out, 1)
            self.stack.addWidget(area)

        # Settings <-> widgets.
        def toggles(self) -> list:
            return [self.fsr4, self.frame_generation, self.reflections_hold, self.ray_tracing, self.safe_mode,
                    self.low_memory, self.stats_overlay, self.skip_intro, self.f4_capture, self.hw_log, self.diagnostics]

        def connect_changes(self) -> None:
            for w in (self.resolution, self.frame_cap, self.upscaler, self.fsr_quality, self.vendor, self.render_scale):
                w.currentIndexChanged.connect(self.changed)
            for w in self.toggles() + [self.series_x, self.series_s]:
                w.toggled.connect(self.changed)
            self.ambient.valueChanged.connect(self.changed)
            self.pipeline_wait.valueChanged.connect(self.changed)

        def show_settings(self) -> None:
            s = self.s
            self.loading = True
            self.resolution.setCurrentText(s.resolution)
            self.frame_cap.setCurrentText(s.frame_cap)
            self.ambient.setValue(int(round(s.ambient * 100)))
            self.reflections_hold.setChecked(s.reflections_hold)
            self.ray_tracing.setChecked(s.ray_tracing)
            self.upscaler.setCurrentIndex(max(0, self.upscaler.findData(s.upscaler)))
            self.vendor.setCurrentIndex(max(0, self.vendor.findData(s.vendor)))
            self.render_scale.setCurrentText(s.render_scale)
            self.diagnostics.setChecked(s.diagnostics)
            self.fsr_quality.setCurrentIndex(max(0, self.fsr_quality.findData(s.fsr_quality)))
            self.fsr4.setChecked(s.fsr4)
            self.frame_generation.setChecked(s.frame_generation)
            (self.series_s if s.console == "series-s" else self.series_x).setChecked(True)
            self.safe_mode.setChecked(s.safe_mode)
            self.low_memory.setChecked(s.low_memory)
            self.pipeline_wait.setValue(int(s.pipeline_wait_ms))
            self.stats_overlay.setChecked(s.stats_overlay)
            self.skip_intro.setChecked(s.skip_intro)
            self.f4_capture.setChecked(s.f4_capture)
            self.hw_log.setChecked(s.hw_log)
            self.preset.setCurrentText(s.preset)
            self.refresh()
            self.loading = False

        def read_settings(self) -> None:
            s = self.s
            s.resolution = self.resolution.currentText()
            s.frame_cap = self.frame_cap.currentText()
            s.ambient = self.ambient.value() / 100
            s.reflections_hold = self.reflections_hold.isChecked()
            s.ray_tracing = self.ray_tracing.isChecked()
            s.upscaler = self.upscaler.currentData()
            s.vendor = self.vendor.currentData()
            s.render_scale = self.render_scale.currentText()
            s.diagnostics = self.diagnostics.isChecked()
            s.fsr_quality = self.fsr_quality.currentData()
            s.fsr4 = self.fsr4.isChecked()
            s.frame_generation = self.frame_generation.isChecked()
            s.console = "series-s" if self.series_s.isChecked() else "series-x"
            s.safe_mode = self.safe_mode.isChecked()
            s.low_memory = self.low_memory.isChecked()
            s.pipeline_wait_ms = self.pipeline_wait.value()
            s.stats_overlay = self.stats_overlay.isChecked()
            s.skip_intro = self.skip_intro.isChecked()
            s.f4_capture = self.f4_capture.isChecked()
            s.hw_log = self.hw_log.isChecked()

        def refresh(self) -> None:
            s = self.s
            fsr = self.upscaler.currentData() == "fsr" and (FIDELITYFX / "amd_fidelityfx_upscaler_dx12.dll").exists()
            vendor = gpu_vendor() if s.vendor == "auto" else s.vendor
            self.fsr_quality.setEnabled(fsr)
            self.fsr4.setEnabled(fsr and vendor == "amd")
            self.frame_generation.setEnabled(fsr and s.diagnostics)
            self.render_scale.setEnabled(s.upscaler == "game")
            self.stats_overlay.setEnabled(s.diagnostics)
            self.f4_capture.setEnabled(s.diagnostics)
            self.ambient_label.setText(f"{self.ambient.value() / 100:.2f}")
            up = f"FSR {'4' if s.fsr4 and vendor == 'amd' else '3.1'} {s.fsr_quality}" if s.upscaler == "fsr" else "GAME UPSCALER"
            chips = [s.resolution, up.upper(),
                     "FRAME GENERATION" if s.upscaler == "fsr" and s.frame_generation and s.diagnostics else "NO FRAME GENERATION",
                     "SERIES S" if s.console == "series-s" else "SERIES X", s.frame_cap.upper()]
            for c, text in zip(self.chip_labels, chips):
                c.setText(text)
            bad = [what for what, _, ok in system_info(settings=s) if not ok]
            self.checks_line.setText("Everything Valhalla needs is in place." if not bad
                                     else "Missing: " + ", ".join(bad) + "  (see System)")
            for w in self.toggles():
                w.update()

        def changed(self, *_: object) -> None:
            if self.loading:
                return
            self.read_settings()
            if self.s.preset != "Custom" and any(
                getattr(self.s, k) != v for k, v in PRESETS.get(self.s.preset, {}).items()
            ):
                self.s.preset = "Custom"
                self.preset.setCurrentText("Custom")
            self.refresh()
            self.s.save()
            self.tick()

        def choose_preset(self, name: str) -> None:
            apply_preset(self.s, name)
            self.show_settings()
            self.s.save()
            self.tick()

        # Play, stop and the status.
        def play(self) -> None:
            if game_pids():
                QMessageBox.information(self, "Valhalla", "The game is running already.")
                return
            missing = [what for what, _, ok in system_info(settings=self.s) if not ok and what in
                       ("Proton", "Game files", "Valhalla build", "Launch script")]
            if missing:
                QMessageBox.warning(self, "Valhalla", "Missing: " + ", ".join(missing))
                return
            self.read_settings()
            self.s.save()
            # The first start on a PC: the shaders are translated and the
            # pipelines built first (else the world is black while they are
            # made in play), then the game starts by itself.
            if not shaders_prepared():
                self.play_after_prepare = True
                self.status.setText("First start: preparing the shaders (a few minutes, once)…")
                self.go(4)
                self.nav.button(4).setChecked(True)
                self.prepare()
                return
            env = process_env(self.s)
            if not self.s.hw_log:
                env["NO_HW_LOG"] = "1"
            self.game = subprocess.Popen(["bash", str(BASE / "run-7900.sh")], cwd=BASE, env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
            self.started_at = time.time()
            self.tick()

        def stop(self) -> None:
            for pid in game_pids():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            self.tick()

        def tick(self) -> None:
            running = bool(game_pids()) or (self.game is not None and self.game.poll() is None)
            self.play_button.setEnabled(not running)
            self.stop_button.setEnabled(running)
            if running:
                secs = int(time.time() - self.started_at) if self.started_at else 0
                fps = last_fps() if secs > 5 or not self.started_at else None
                text = "Running" + (f"  ·  {secs // 60}:{secs % 60:02d}" if self.started_at else "")
                if fps:
                    text += f"  ·  {fps}"
                self.status.setText(text)
                self.side_status.setText("●  GAME RUNNING")
            else:
                s = self.s
                parts = [s.resolution,
                         (f"FSR {'4' if s.fsr4 else '3.1'} {s.fsr_quality}" if s.upscaler == "fsr" else "Game upscaler"),
                         ("Frame generation" if s.upscaler == "fsr" and s.frame_generation else None),
                         ("Series S" if s.console == "series-s" else "Series X"), s.frame_cap]
                self.status.setText("Ready")
                self.side_status.setText("")

        # Tools.
        def prepare(self) -> None:
            if self.tool is not None:
                return
            self.console_out.clear()
            steps = [["bash", str(BASE / "run-victory.sh"), "shaders", str(self.s.game_path())],
                     ["bash", str(BASE / "run-victory.sh"), "warm", "--used"]]
            env = process_env(self.s)
            env.update({"VICTORY_EXE": str(executable(self.s.diagnostics)), "VICTORY_PIPELINE_BOOK": "1"})
            env.setdefault("VICTORY_PORT_CBV", "1")
            self.prepare_button.setEnabled(False)
            self.prepare_button.setText("Preparing…")
            self.run_steps(steps, env)

        def run_steps(self, steps: list[list[str]], env: dict[str, str]) -> None:
            if not steps:
                self.tool = None
                self.prepare_button.setEnabled(True)
                self.prepare_button.setText("Prepare")
                self.console_out.appendPlainText("\nDone.")
                if getattr(self, "play_after_prepare", False):
                    self.play_after_prepare = False
                    if shaders_prepared():
                        self.play()
                    else:
                        QMessageBox.warning(self, "Valhalla", "The shaders were not prepared: see Tools.")
                return
            proc = QProcess(self)
            STATE.mkdir(parents=True, exist_ok=True)
            proc.setWorkingDirectory(str(STATE))
            penv = QProcessEnvironment()
            for k, v in env.items():
                penv.insert(k, v)
            proc.setProcessEnvironment(penv)
            proc.setProcessChannelMode(QProcess.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda: self.console_out.appendPlainText(
                    re.sub(r"\x1b\[[0-9;]*m", "", bytes(proc.readAllStandardOutput()).decode(errors="replace")).rstrip()))
            def finished(code, status):
                if code == 0 and status == QProcess.NormalExit:
                    self.run_steps(steps[1:], env)
                else:
                    self.tool = None
                    self.play_after_prepare = False
                    self.prepare_button.setEnabled(True)
                    self.prepare_button.setText("Prepare")
                    self.console_out.appendPlainText(f"\nPreparation failed (exit {code}).")
            proc.finished.connect(finished)
            self.console_out.appendPlainText("$ " + " ".join(steps[0][1:]))
            self.tool = proc
            proc.start(steps[0][0], steps[0][1:])

        def choose_game_dir(self) -> None:
            from PySide6.QtWidgets import QFileDialog
            chosen = QFileDialog.getExistingDirectory(self, "The game's folder", str(self.s.game_path()))
            if not chosen:
                return
            if not (Path(chosen) / GAME_EXE_NAME).exists():
                QMessageBox.warning(self, "Valhalla", f"{GAME_EXE_NAME} is not in that folder.")
                return
            self.s.game_dir = chosen
            self.s.save()
            self.game_label.setText(chosen)
            QMessageBox.information(self, "Valhalla", "Game folder set. Restart the launcher to see its art.")
            self.refresh()

        def open_path(self, path: Path) -> None:
            if not path.exists():
                QMessageBox.information(self, "Valhalla", f"{path} does not exist yet.")
                return
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)

    w = Window()
    w.show()
    return app.exec()


def selftest() -> int:
    """The presets and settings give the environments the runs were tested with."""
    s = apply_preset(Settings(vendor="amd"), "High")
    env = launch_env(s)
    assert env["VICTORY_OUTPUT"] == "2560x1440" and env["VICTORY_UPSCALER"] == "7", env
    assert env["VICTORY_FFX_SCALE"] == "0.6667" and env["VICTORY_FIDELITYFX_FRAMEGEN"] == "1", env
    assert env["FSR4_UPGRADE"] == "1" and env["VICTORY_FRAME_INTERVAL_MS"] == "16.667", env
    assert "CONSOLE" not in env and "RT" not in env, env
    low = launch_env(apply_preset(Settings(), "Low"))
    assert low["CONSOLE"] == "series-s" and "VICTORY_WINDOW_PLAIN" not in low, low
    assert low["VICTORY_FRAME_INTERVAL_MS"] == "33.333" and low["VICTORY_FFX_SCALE"] == "0.5", low
    own = Settings(upscaler="game", frame_generation=True)
    assert "VICTORY_UPSCALER" not in launch_env(own) and "VICTORY_FIDELITYFX_FRAMEGEN" not in launch_env(own)
    # A changed setting is no longer the preset.
    s2 = apply_preset(Settings(), "Medium")
    assert s2.preset == "Medium" and apply_preset(s2, "nope").preset == "Custom"
    assert windows_path(Path("/a/b")) == "Z:\\a\\b"
    s70 = launch_env(apply_preset(Settings(vendor="amd", low_memory=True, console="series-s"), "S70"))
    assert s70["VICTORY_OUTPUT"] == "2560x1440" and s70["VICTORY_FFX_SCALE"] == "0.6667", s70
    assert s70["FSR4_UPGRADE"] == "1" and s70["VICTORY_FIDELITYFX_FRAMEGEN"] == "1", s70
    assert s70["VICTORY_FRAME_INTERVAL_MS"] == "16.667" and "CONSOLE" not in s70 and "VICTORY_WINDOW_PLAIN" not in s70, s70
    nvidia = launch_env(apply_preset(Settings(vendor="nvidia"), "Performance"))
    assert nvidia["CONSOLE"] == "series-s" and nvidia["VICTORY_FRAME_INTERVAL_MS"] == "8.333"
    assert not {"FSR4_UPGRADE", "PROTON_FSR4_UPGRADE", "VICTORY_FIDELITYFX_FRAMEGEN"} & nvidia.keys()
    nvidia_fsr = launch_env(apply_preset(Settings(vendor="nvidia"), "High"))
    assert "FSR4_UPGRADE" not in nvidia_fsr and "PROTON_FSR4_UPGRADE" not in nvidia_fsr
    assert launch_env(Settings(diagnostics=False))["VICTORY_PANEL"] == "0"
    assert "VICTORY_FIDELITYFX_FRAMEGEN" not in launch_env(Settings(diagnostics=False))
    assert launch_env(apply_preset(Settings(), "Experimental"))["VICTORY_SHADER_OPT"] == "127"
    assert launch_env(apply_preset(Settings(), "Compatibility"))["VICTORY_TEXTURE_NO_WAIT"] == "0"
    assert all(BASE.parent not in p.parents for p in (SETTINGS_FILE, LOG_FILE, SHADER_CACHE, CAPTURES))
    # Every variable any setting sets is read: by the runtime's sources, the
    # launch scripts, or Proton (the FSR 4 upgrade).
    every = Settings(vendor="amd", upscaler="fsr", fsr4=True, frame_generation=True, console="series-s", ray_tracing=True,
                     safe_mode=True, low_memory=True, f4_capture=False, stats_overlay=False, skip_intro=False)
    names = set(launch_env(every)) | {"NO_HW_LOG", "VICTORY_CAPTURE", "VICTORY_DUMP_TARGETS"}
    sources = [BASE / "run-7900.sh", BASE / "run-victory.sh"] + list((BASE / "victory7900" / "crates").rglob("*.rs")) \
        + list((BASE / "victory7900" / "apps").rglob("*.rs"))
    text = "\n".join(p.read_text(errors="replace") for p in sources if p.exists())
    proton_read = {"FSR4_UPGRADE", "PROTON_FSR4_UPGRADE"}
    if (BASE / "victory7900" / "crates").exists():  # (a package has no sources)
        unread = sorted(n for n in names - proton_read if n not in text)
        assert not unread, f"set by the launcher, read nowhere: {unread}"
    assert "VICTORY_FRAME_INTERVAL_MS" in launch_env(Settings(frame_cap="Unlimited")) and \
        launch_env(Settings(frame_cap="Unlimited"))["VICTORY_FRAME_INTERVAL_MS"] == "0"
    print("selftest ok")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--dry-run" in sys.argv:
        settings = Settings.load()
        for k, v in sorted(launch_env(settings).items()):
            print(f"{k}={v}")
        print(f"bash {BASE / 'run-7900.sh'}")
        sys.exit(0)
    sys.exit(run_window())

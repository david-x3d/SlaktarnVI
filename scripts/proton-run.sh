#!/usr/bin/env bash
# Runs valhalla.exe (or another Windows exe) under Proton's Wine on Linux,
# with vkd3d-proton (D3D12) and DXVK (DXGI) from the Proton build.
# See docs/linux-proton.md.
#
#   scripts/proton-run.sh config
#   scripts/proton-run.sh run /path/to/game/<the title's exe> --execute
#
# As cargo's runner (its first argument is the test exe):
#   CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUNNER=$PWD/scripts/proton-run.sh \
#       cargo xwin test --workspace --target x86_64-pc-windows-msvc
#
# Environment:
#   PROTON       a Proton folder (default: view-tree GE, GE, then Experimental)
#   PREFIX       the Proton prefix to use (default: ~/.local/share/valhalla/proton)
#   VICTORY_EXE  the exe to run (default: this checkout's release build)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec "$ROOT/linux/run-victory.sh" "$@"

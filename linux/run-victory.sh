#!/usr/bin/env bash
# Runs a Windows exe (victory.exe by default) under GE-Proton's Wine, with
# vkd3d-proton (D3D12) and DXVK (DXGI) from the Proton build.
#
#   ./run-victory.sh config
#   ./run-victory.sh run "$PWD/game/<title>.exe" --execute
#   VICTORY_EXE=/path/to/other.exe ./run-victory.sh ...     (used as cargo runner)
#
# Linux paths in arguments are fine: Wine maps / to Z:\.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/runtime-paths.sh"
PROTON=$(find_proton "$HERE")
if [[ ${1:-} == --print-proton ]]; then printf '%s\n' "$PROTON"; exit 0; fi
runtime_paths

# First run: let Proton build the prefix (installs vkd3d-proton / DXVK DLLs).
if [[ ! -f "$PREFIX/pfx/system.reg" ]]; then
    mkdir -p "$PREFIX"
    STEAM_COMPAT_DATA_PATH="$PREFIX" \
    STEAM_COMPAT_CLIENT_INSTALL_PATH="${STEAM_COMPAT_CLIENT_INSTALL_PATH:-$HOME/.local/share/Steam}" \
    SteamAppId=0 SteamGameId=0 \
        python3 "$PROTON/proton" run cmd /c exit
    [[ -f "$PREFIX/pfx/system.reg" ]] || { echo 'Proton did not initialize the prefix' >&2; exit 1; }
fi

export WINEPREFIX="$PREFIX/pfx"
export WINEDLLOVERRIDES="d3d12,d3d12core,dxgi,d3d11,d3d10core=n,b;${WINEDLLOVERRIDES:-}"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEFSYNC="${WINEFSYNC:-1}"
# Keep vkd3d-proton's pipeline cache out of the working directory.
mkdir -p "$VKD3D_SHADER_CACHE_PATH" "$DXVK_STATE_CACHE_PATH" "$VICTORY_DATA_DIR" "$VICTORY_CACHE_DIR" "$VALHALLA_STATE_DIR"

# Microsoft's own shader compiler (keep/ms-d3d, from the Windows SDK package on
# nuget.org): Wine's lacks [RootSignature], ByteAddressBuffer methods and more,
# which the runtime's own shaders use. Copied next to the exe that runs.
MSD3D="${MSD3D:-$HERE/keep/ms-d3d}"
with_ms_d3d() {
    local dir; dir="$(dirname "$1")"
    for f in d3dcompiler_47.dll dxil.dll; do
        if [[ ! -f "$dir/$f" ]]; then
            if [[ -f "$MSD3D/$f" ]]; then
                cp -- "$MSD3D/$f" "$dir/$f"
            else
                printf 'Warning: Microsoft %s not found in %s or %s\n' "$f" "$dir" "$MSD3D" >&2
            fi
        fi
    done
}
export WINEDLLOVERRIDES="d3dcompiler_47,dxil=n,b;${WINEDLLOVERRIDES:-}"
if [[ "${1:-}" == *.exe && -f "${1:-}" ]]; then first=$(realpath -- "$1"); shift; set -- "$first" "$@"; fi
if [[ -n "${VICTORY_EXE:-}" ]]; then VICTORY_EXE=$(realpath -- "$VICTORY_EXE"); fi
cd "$VALHALLA_STATE_DIR"
if [[ -n "${VICTORY_EXE:-}" ]]; then
    with_ms_d3d "$VICTORY_EXE"
    exec "$PROTON/files/bin/wine" "$VICTORY_EXE" "$@"
elif [[ "${1:-}" == *.exe && -f "${1:-}" ]]; then
    with_ms_d3d "$1"
    exec "$PROTON/files/bin/wine" "$@"          # cargo runner: first arg is the exe
else
    VICTORY_EXE="$HERE/../target/x86_64-pc-windows-msvc/release/valhalla.exe"
    [[ -f "$VICTORY_EXE" ]] || { echo "Missing $VICTORY_EXE; build it or set VICTORY_EXE" >&2; exit 1; }
    with_ms_d3d "$VICTORY_EXE"
    exec "$PROTON/files/bin/wine" "$VICTORY_EXE" "$@"
fi

#!/usr/bin/env bash
# Synthetic directories only: no Wine invocation, no game files.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
export HOME="$tmp/home"
export XDG_DATA_HOME="$tmp/data"
export XDG_CACHE_HOME="$tmp/cache"
export XDG_STATE_HOME="$tmp/state"
unset PROTON PREFIX VICTORY_DATA_DIR VICTORY_CACHE_DIR VALHALLA_DATA_DIR VALHALLA_CACHE_DIR VALHALLA_STATE_DIR VKD3D_SHADER_CACHE_PATH DXVK_STATE_CACHE_PATH
source "$HERE/runtime-paths.sh"
runtime_paths
[[ $PREFIX == "$tmp/data/valhalla/proton" ]]
[[ $VICTORY_CACHE_DIR == "$tmp/cache/valhalla/cache" ]]
[[ $VKD3D_SHADER_CACHE_PATH == "$tmp/cache/valhalla/vkd3d" ]]
mkdir -p "$tmp/checkout"
touch "$tmp/checkout/.victory-root"
ln -s "$tmp/checkout" "$tmp/link"
if outside_repo "$tmp/link/cache" >/dev/null 2>&1; then
    echo 'source path was incorrectly allowed' >&2
    exit 1
fi
for name in GE-Proton11-13 GE-Proton11-14-viewtree; do
    dir="$HOME/.steam/root/compatibilitytools.d/$name"
    mkdir -p "$dir/files/bin"
    touch "$dir/proton" "$dir/files/bin/wine"
    chmod +x "$dir/files/bin/wine"
done
[[ $(find_proton "$tmp") == "$HOME/.steam/root/compatibilitytools.d/GE-Proton11-14-viewtree" ]]
PROTON="$HOME/.steam/root/compatibilitytools.d/GE-Proton11-13"
[[ $(find_proton "$tmp") == "$PROTON" ]]
echo 'runtime paths selftest ok'

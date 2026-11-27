#!/usr/bin/env bash
# Shared launcher paths. Sourcing this file does not create directories.
VALHALLA_STATE_DIR=${VALHALLA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/valhalla}
VALHALLA_CACHE_DIR=${VALHALLA_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/valhalla}
VALHALLA_DATA_DIR=${VALHALLA_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/valhalla}

# Match the runtime's asset policy, including symlinked directories and other
# source clones. realpath -m also handles a path that has not been made yet.
outside_repo() {
    local path parent
    path=$(realpath -m -- "$1") || return 1
    parent=$path
    while :; do
        if [[ -f "$parent/.victory-root" ]]; then
            printf 'Refusing runtime path inside source tree: %s\n' "$path" >&2
            return 1
        fi
        [[ $parent == / ]] && break
        parent=$(dirname -- "$parent")
    done
    printf '%s\n' "$path"
}

runtime_paths() {
    VALHALLA_STATE_DIR=$(outside_repo "$VALHALLA_STATE_DIR") || return 1
    VALHALLA_CACHE_DIR=$(outside_repo "$VALHALLA_CACHE_DIR") || return 1
    VALHALLA_DATA_DIR=$(outside_repo "$VALHALLA_DATA_DIR") || return 1
    PREFIX=$(outside_repo "${PREFIX:-$VALHALLA_DATA_DIR/proton}") || return 1
    VICTORY_DATA_DIR=$(outside_repo "${VICTORY_DATA_DIR:-$VALHALLA_DATA_DIR/data}") || return 1
    VICTORY_CACHE_DIR=$(outside_repo "${VICTORY_CACHE_DIR:-$VALHALLA_CACHE_DIR/cache}") || return 1
    VKD3D_SHADER_CACHE_PATH=$(outside_repo "${VKD3D_SHADER_CACHE_PATH:-$VALHALLA_CACHE_DIR/vkd3d}") || return 1
    DXVK_STATE_CACHE_PATH=$(outside_repo "${DXVK_STATE_CACHE_PATH:-$VALHALLA_CACHE_DIR/dxvk}") || return 1
    export VALHALLA_STATE_DIR VALHALLA_CACHE_DIR VALHALLA_DATA_DIR PREFIX VICTORY_DATA_DIR VICTORY_CACHE_DIR
    export VKD3D_SHADER_CACHE_PATH DXVK_STATE_CACHE_PATH
}

find_proton() {
    local here=$1 candidate root
    if [[ -n ${PROTON:-} ]]; then
        [[ -x "$PROTON/files/bin/wine" && -f "$PROTON/proton" ]] || {
            printf 'Invalid PROTON: %s\n' "$PROTON" >&2; return 1;
        }
        realpath -- "$PROTON"
        return
    fi
    if [[ ${PATCHED:-1} == 1 && -x "$here/proton-patched/files/bin/wine" && -f "$here/proton-patched/proton" ]]; then
        realpath -- "$here/proton-patched"; return
    fi
    local roots=("$HOME/.steam/root" "${XDG_DATA_HOME:-$HOME/.local/share}/Steam" "$HOME/.var/app/com.valvesoftware.Steam/data/Steam")
    local pattern
    for pattern in '*viewtree*' 'GE-Proton*'; do
        for root in "${roots[@]}"; do
            while IFS= read -r candidate; do
                if [[ -x "$candidate/files/bin/wine" && -f "$candidate/proton" ]]; then
                    realpath -- "$candidate"; return
                fi
            done < <(find "$root/compatibilitytools.d/" -mindepth 1 -maxdepth 1 -name "$pattern" -print 2>/dev/null | sort -Vr)
        done
    done
    for root in "${roots[@]}"; do
        candidate="$root/steamapps/common/Proton - Experimental"
        if [[ -x "$candidate/files/bin/wine" && -f "$candidate/proton" ]]; then
            realpath -- "$candidate"; return
        fi
    done
    printf 'Proton not found; set PROTON to its installation directory.\n' >&2
    return 1
}

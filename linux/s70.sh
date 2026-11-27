#!/bin/bash
# A run with the launcher's S70 preset (PRESET=Low etc. for another; F4=1: F4 capture) (the user's own settings), for tests:
# the same environment Play gives run-7900.sh, plus what this run adds
# (SECS, WALK, LOG, VICTORY_CAPTURE...). Runs a copy of run-7900.sh.
cd "$(dirname "$0")"
eval "$(python3 -I - <<'PY'
import shlex, sys
sys.path.insert(0, "launcher")
import valhalla
import os
settings = valhalla.apply_preset(valhalla.Settings(), os.environ.get("PRESET", "S70"))
if os.environ.get("F4") == "1":
    settings.f4_capture = True
env = valhalla.launch_env(settings)
for k in ("LOG",):
    env.pop(k, None)
for k, v in env.items():
    print(f"export {k}={shlex.quote(v)}")
PY
)"
# OVERRIDE="VICTORY_X=1 VICTORY_Y=2": what this run changes after the preset
# (e.g. one setting measured against S70).
for kv in ${OVERRIDE:-}; do export "$kv"; done
exec ./run-7900.sh

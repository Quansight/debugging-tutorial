#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export UV_PYTHON_INSTALL_DIR="$PWD/.python-builds"
uv python install 3.11.16+debug --no-bin
debug_python="$(uv python find 3.11.16+debug)"

# Keep installed extensions when setup is run again.
if [[ ! -f .venv/pyvenv.cfg ]]; then
    # Keep LLDB's resolved executable inside the virtual environment.
    "$debug_python" -m venv --copies --without-pip .venv
fi

# Convert aliases in existing environments too: LLDB and build scripts may
# launch any of these names, and the executable must stay inside the venv.
for executable in .venv/bin/python .venv/bin/python3 .venv/bin/python3.11 .venv/bin/python3.11d; do
    if [[ -L "$executable" ]]; then
        copied_executable="$(mktemp "$executable.XXXXXX")"
        cp -pL "$executable" "$copied_executable"
        mv "$copied_executable" "$executable"
    fi
done

.venv/bin/python - <<'PY'
from pathlib import Path
import sys
import sysconfig

if sys.version_info[:3] != (3, 11, 16) or not sysconfig.get_config_var("Py_DEBUG"):
    raise SystemExit(
        "Expected a Python 3.11.16 debug build in .venv. "
        "Move that environment aside and rerun pixi run setup-python."
    )
print(f"Debug interpreter: {Path(sys.executable).resolve()}")
print(f"Total references: {sys.gettotalrefcount()}")
PY

uv pip install --python .venv/bin/python -r requirements-build.txt

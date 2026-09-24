# Set up the LLDB tutorial

Use Linux or macOS, on x86-64 or ARM64. Windows users can use WSL2 and follow
the Linux instructions. You need [pixi](https://pixi.sh); on macOS, also
install Apple's Command Line Tools (`xcode-select --install`) for the SDK.
Allow time and several GB of disk space for tools and source builds.

From the root of this repository:

```bash
cd debugger-tutorial
pixi run --locked setup-python
pixi shell
source .venv/bin/activate
```

The setup command installs the build tools and downloads a debug Python
interpreter with uv. It prepares `.venv` with the Python build dependencies
and uses `venv --copies` to put a real Python executable in `.venv/bin/python`.
We give LLDB the resolved path to that executable. Keeping the executable
inside `.venv` preserves access to the packages installed there, even after
resolving its path. You can rerun setup without removing built extensions.

Stay in this shell for the tutorial. On Linux, pixi supplies LLDB; on macOS,
use Apple's LLDB from the Command Line Tools. Run the following commands
at your terminal prompt; they use bash/zsh syntax.

## Check the debug interpreter

We need a **debug build of Python itself**, as well as debug symbols in
NumPy. An ordinary interpreter cannot report `sys.gettotalrefcount()`.
Check which interpreter is active:

```bash
python - <<'PY'
from pathlib import Path
import sys
import sysconfig

executable = Path(sys.executable)
print("Executable:", executable)
print("Resolved path:", executable.resolve())
print("Symlink:", executable.is_symlink())
print("Py_DEBUG:", sysconfig.get_config_var("Py_DEBUG"))
print("Total references:", sys.gettotalrefcount())
PY
```

Check that the resolved executable is inside this tutorial's `.venv`,
`Symlink` is `False`, `Py_DEBUG` is `1`, and a total reference count is printed.
If the executable resolves outside `.venv`, follow the
[symlink troubleshooting instructions](README.md#troubleshooting) before
launching LLDB. We pair Python 3.11 with NumPy 2.4.6: this NumPy release
supports spin and Meson, and this interpreter keeps its reference-count
accumulator in the global `_Py_RefTotal`. Python 3.12 and newer use a
per-interpreter counter, which requires more work to locate in a debugger.

## Build NumPy with the bug restored

NumPy 2.4.6 already includes the 2023 fix. Apply the supplied one-line
patch to recreate the duplicate initialization for this exercise:

```bash
git clone --branch v2.4.6 --depth 1 https://github.com/numpy/numpy.git numpy-src
git -C numpy-src submodule update --init --recursive --depth 1
git -C numpy-src apply ../reintroduce-refcount-bug.patch

cd numpy-src
spin build -- -Dbuildtype=debug
cd ..
export PYTHONPATH="$PWD/numpy-src/build-install/usr/lib/python3.11/site-packages"

python -c 'import numpy; print(numpy.__version__); print(numpy.__file__)'
```

The printed version must be `2.4.6` and the path must point into
`numpy-src/build-install`. spin drives NumPy's Meson build and installs it
into that staging directory; `PYTHONPATH` makes it available to ordinary
Python and LLDB commands. The build tools are pinned in
`requirements-build.txt`.

`-Dbuildtype=debug` enables debug information and disables compiler
optimization. `-Dcpu-dispatch=none` skips additional CPU-specific variants
to reduce build time. Lower `-j 4` if memory is tight. The default Meson
configuration can use available BLAS/LAPACK libraries or NumPy's bundled
fallback routines; this constructor bug does not depend on them.
Keep the source and build directories: LLDB needs them for source lines
and, on macOS, debug information in object files.

## Check the example

The example uses NumPy's built-in `StringDType`. Its constructor delegates
allocation to `arraydescr_new`, where we restored the faulty line. No
additional extension is needed:

```bash
python measure.py
python reproduce.py
```

NumPy is loaded from `numpy-src/build-install` via `PYTHONPATH`.
`reproduce.py` should exit normally outside LLDB: it installs a harmless
Python signal handler.

Continue with the [walkthrough](README.md#establish-the-symptom).

## Return in a new terminal

From the repository root, reactivate and restore the import path.
Do not clone or create the virtual environment again.

```bash
cd gdb-tutorial
pixi shell
source .venv/bin/activate
export PYTHONPATH="$PWD/numpy-src/build-install/usr/lib/python3.11/site-packages"
```

Run `deactivate` and then `exit` when finished to leave the Python environment
and the pixi shell.

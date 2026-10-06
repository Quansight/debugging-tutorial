# Set up the debugging tutorials

Use an x86-64 or ARM64 machine. Windows users need WSL2.
Install [Pixi](https://pixi.sh) 0.81 or newer and, on macOS, Apple's Command
Line Tools (`xcode-select --install`). Source builds require several GB of
disk space. Run these commands from the **repository root** in bash or zsh.

The root `pixi.toml` and `pixi.lock` define both tutorial environments:

| Exercise | Environment | Python and NumPy |
| --- | --- | --- |
| Reference-counting with LLDB | `default` | GIL-enabled debug Python; NumPy built locally with debug information |
| Profiling with Samply | `profiling` | Prebuilt free-threaded Python; installed NumPy and scientific packages |

Both use Python 3.15.0rc2 and NumPy 2.5.3. Pixi installs all dependencies;
there is no separate pip installation step. Follow the section for the
exercise you want to run. Run `exit` before switching Pixi shells.

## Reference-counting environment

`sys.gettotalrefcount()` requires a **debug build of Python**, not just
debug symbols in NumPy. The [recipe](python-debug/recipe.yaml) builds a
GIL-enabled interpreter with `--with-pydebug` and `-O0 -g`, enabling
reference-count tracking and keeping the interpreter easy to step through.

Lucas Colley's ongoing work on instrumented builds includes CPython's
[Pixi recipes](https://github.com/python/cpython/tree/v3.15.0rc2/Tools/pixi-packages).
See his EuroPython 2026 Packaging Summit talk,
[Instrumented CPython builds with Pixi](https://lucascolley.github.io/talks/europython-26-instrumented/index.html).

```bash
pixi run --locked build-python
pixi shell --locked
```

Pixi builds CPython from source with four jobs and installs it alongside
the NumPy build tools in `.pixi/envs/default`. Later runs reuse the build.
Keep `.pixi`: LLDB needs the source and object files in its build directories.
If you move the checkout, rebuild it at the new location.

### Check the debug interpreter

```bash
python -VV
python -c 'import sys, sysconfig; assert hasattr(sys, "gettotalrefcount"); assert sysconfig.get_config_var("Py_DEBUG") == 1; assert not sysconfig.get_config_var("Py_GIL_DISABLED")'
```

Expect version `3.15.0rc2` and no assertion error.

### Build NumPy

```bash
git clone --branch v2.5.3 --depth 1 https://github.com/numpy/numpy.git numpy-src
git -C numpy-src submodule update --init --recursive --depth 1

cd numpy-src
spin build -- -Dbuildtype=debug
cd ..

python -c 'import numpy; print(numpy.__version__); print(numpy.__file__)'
```

Expect version `2.5.3` and a path under `numpy-src/build-install`.
spin builds NumPy with Meson and installs it there. The default environment
sets `PYTHONPATH` to select that build. `-Dbuildtype=debug` enables debug
information and disables optimization. Keep NumPy's source and build
directories too. The reference-counting exercise works with either
BLAS/LAPACK or NumPy's bundled fallback routines.

### Check LLDB

```bash
pixi run --locked -e debugger lldb --version
```

LLDB's `debugger` environment has its own Python for scripting. The
[launch commands](debugger-tutorial/README.md#follow-the-writes) tell LLDB to run
the Python program from the tutorial's `default` environment.

On macOS, configure conda-forge's LLDB to use Apple's signed debugserver.
Run this in each shell where you launch LLDB:

```bash
export LLDB_DEBUGSERVER_PATH="$(xcode-select -p)/../SharedFrameworks/LLDB.framework/Versions/A/Resources/debugserver"
# Command Line Tools installations use a different location:
if [ ! -x "$LLDB_DEBUGSERVER_PATH" ]; then
    export LLDB_DEBUGSERVER_PATH="$(xcode-select -p)/Library/PrivateFrameworks/LLDB.framework/Versions/A/Resources/debugserver"
fi
if [ ! -x "$LLDB_DEBUGSERVER_PATH" ]; then
    echo "debugserver not found" >&2
    unset LLDB_DEBUGSERVER_PATH
fi
```

Continue with the [reference-counting walkthrough](debugger-tutorial/README.md).

## Profiling environment

From the repository root, outside any other Pixi shell:

```bash
pixi shell --locked -e profiling
```

Pixi installs free-threaded Python, Samply, and the scientific packages in
`.pixi/envs/profiling`. This exercise needs no source builds. It uses its own
NumPy wheel, so changes to the reference-counting exercise's local NumPy
build do not affect profiling.

Check that importing the scientific packages leaves the GIL disabled:

```pycon
>>> import sys
>>> import sysconfig
>>> import numpy, pandas, sklearn
>>> print(sys.version)
3.15.0rc2 free-threading build | packaged by conda-forge | (main, Oct  5 2026, 16:42:21) [Clang 21.1.8 ]
>>> print("NumPy:", numpy.__version__, numpy.__file__)
NumPy: 2.5.3 /Users/goldbaum/Documents/debugging-tutorial/.pixi/envs/profiling/lib/python3.15t/site-packages/numpy/__init__.py
>>> assert sysconfig.get_config_var("Py_GIL_DISABLED") == 1
>>> assert not sysconfig.get_config_var("Py_DEBUG")
>>> assert not sys._is_gil_enabled()
```

Also check that samply is working:

```bash
samply --version
```

Expect `samply 0.13.1`.

Continue with the [profiling tutorial](samply-tutorial/README.md).

## Troubleshooting

- **Wrong Python or missing `sys.gettotalrefcount`:** leave any previously
  activated environment and select the appropriate Pixi shell above.
  Check `python -c 'import sys; print(sys.executable)'`: reference counting
  uses `.pixi/envs/default/bin`, and profiling uses `.pixi/envs/profiling/bin`.
- **Wrong NumPy or import failure:** in the default environment,
  `numpy.__file__` should point into `numpy-src/build-install`; if that
  directory is missing, [build NumPy](#build-numpy). In `profiling`, it
  should point inside `.pixi/envs/profiling`. Avoid setting `PYTHONPATH`
  manually after activation.
- **Missing C source lines or types:** LLDB needs the source and build
  artifacts for both Python and NumPy. If you moved or removed them,
  rebuild at the current location. For missing NumPy debug information,
  run `python vendored-meson/meson/meson.py configure build -Dbuildtype=debug`
  and `spin build -j 4` from `numpy-src`. Restart LLDB after rebuilding.
- **macOS startup appears stuck:** check for a folder-access dialog, or
  allow Python access under **System Settings → Privacy & Security →
  Files & Folders**. A checkout under `~/Developer` avoids the protected
  Documents, Desktop, and Downloads folders; choose its location before
  building.
- **Profiling imports enable the GIL:** check that you selected
  `pixi shell --locked -e profiling` and that `PYTHON_GIL=1` is not set in
  your shell. The locked packages support free-threaded Python.

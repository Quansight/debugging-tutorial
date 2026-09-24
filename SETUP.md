# Shared setup for the debugging tutorials

Use Linux or macOS on x86-64 or ARM64, or WSL2 on Windows.
Install [Pixi](https://pixi.sh) and, on macOS, Apple's Command Line Tools
(`xcode-select --install`). Source builds require several GB of disk space.
Run these commands from the **repository root** in bash or zsh.

## Build a debug interpreter from source

`sys.gettotalrefcount()` requires a **debug build of Python**, not just
debug symbols in NumPy. We use the GIL-enabled Python 3.15.0rc2 release
candidate and [NumPy 2.5.3](https://github.com/numpy/numpy/releases/tag/v2.5.3), which
supports Python 3.15. Free-threaded builds use different reference
bookkeeping and cannot be used for this watchpoint exercise.

Lucas Colley's ongoing work on instrumented builds includes CPython's
[Pixi recipes](https://github.com/python/cpython/tree/v3.15.0rc2/Tools/pixi-packages)
for ordinary, sanitizer, and free-threaded builds. See his EuroPython 2026
Packaging Summit talk,
[Instrumented CPython builds with Pixi](https://lucascolley.github.io/talks/europython-26-instrumented/index.html).
Our [recipe](python-debug/recipe.yaml) adds `--with-pydebug` and compiles
with `-O0 -g`; sanitizers or debug symbols alone do not enable
reference-count tracking.

```bash
pixi run --locked build-python
pixi shell --locked
```

Pixi builds CPython from source with four jobs and installs it in
`.pixi/envs/default`. Later runs reuse the build.

Stay in this shell for setup and the tutorial. Keep `.pixi`: LLDB needs
the source and object files in its build directories. If you move the
checkout, rebuild it at the new location.

## Check the debug interpreter

```bash
python -VV
python -c 'import sysconfig; assert sysconfig.get_config_var("Py_DEBUG") == 1'
```

Expect version `3.15.0rc2` and no assertion error.

Install the build dependencies:

```bash
python -m pip install -r requirements-build.txt
```

## Build NumPy

```bash
git clone --branch v2.5.3 --depth 1 https://github.com/numpy/numpy.git numpy-src
git -C numpy-src submodule update --init --recursive --depth 1

cd numpy-src
spin build -j 4 -- -Dbuildtype=debug
cd ..
export PYTHONPATH="$PWD/numpy-src/build-install/usr/lib/python3.15/site-packages"

python -c 'import numpy; print(numpy.__version__); print(numpy.__file__)'
```

Expect version `2.5.3` and a path under `numpy-src/build-install`.
spin builds NumPy with Meson and installs it there; `PYTHONPATH` selects
that build for Python and the debugger.

`-Dbuildtype=debug` enables debug information and disables optimization.
Keep NumPy's source and build directories too. The reference-counting
exercise works with either BLAS/LAPACK or NumPy's bundled fallback routines.
Profiling may require different optimization settings.

## Check the tools

Check Samply:

```bash
samply --version
```

On macOS, use Apple's LLDB:

```bash
lldb --version
```

On Linux, install and run LLDB in its separate Pixi environment:

```bash
pixi run --locked -e debugger lldb --version
```

Linux LLDB embeds a separate Python for scripting. The walkthrough passes
the debug interpreter's absolute path to LLDB to run the example.

Continue with the [reference-counting walkthrough](debugger-tutorial/README.md)
or the [profiling tutorial](samply-tutorial/README.md).

## Return in a new terminal

From the repository root, re-enter the shell and restore `PYTHONPATH`:

```bash
pixi shell --locked
export PYTHONPATH="$PWD/numpy-src/build-install/usr/lib/python3.15/site-packages"
```

Change into the tutorial directory. Run `exit` to leave the Pixi shell.

## Troubleshooting

- **Wrong Python or missing `sys.gettotalrefcount`:** leave any previously
  activated virtual environment and [re-enter the Pixi shell](#return-in-a-new-terminal).
  Check `python -c 'import sys; print(sys.executable)'` points into
  `.pixi/envs/default/bin`, then repeat the [debug-build check](#check-the-debug-interpreter).
- **Wrong NumPy or import failure:** [restore `PYTHONPATH`](#return-in-a-new-terminal)
  and check that `numpy.__file__` points into `numpy-src/build-install`.
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

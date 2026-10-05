# Finding a NumPy reference-counting bug with LLDB

Complete the [reference-counting setup](../SETUP.md#reference-counting-environment).
This recreates [NumPy bug #23318](https://github.com/numpy/numpy/pull/23318) using `StringDType`.
From the repository root, outside any other Pixi shell:

```bash
pixi shell --locked
git -C numpy-src apply ../debugger-tutorial/reintroduce-refcount-bug.patch
cd numpy-src
spin build -j 4
cd ../debugger-tutorial
python measure.py
```

[measure.py](measure.py) should show about 100 extra references for 100 dtype
constructions and 1000 for 1000, plus a small constant measurement offset.

## Follow the writes

[reproduce.py](reproduce.py) uses [SIGUSR1](https://numpy.org/devdocs/dev/development_advanced_debugging.html#running-a-test-script)
to stop after imports; its no-op handler lets us resume without terminating Python.
On macOS, [configure debugserver](../SETUP.md#check-lldb) in this shell.
Resolve Python's **real executable**: passing a symlink to LLDB can hang on macOS.

```bash
python_executable="$(python -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
pixi run --locked -e debugger lldb -- "$python_executable" "$PWD/reproduce.py"
```

Stop at the signal, then advance to the dtype allocation in the pinned NumPy source:

```text
(lldb) process handle SIGUSR1 --stop true --notify true --pass true
(lldb) run
(lldb) breakpoint set --name arraydescr_new
(lldb) continue
(lldb) breakpoint delete 1
(lldb) thread until 2552
(lldb) watchpoint set expression -w write -s 8 -- &_PyRuntime.interpreters.main->object_state.reftotal
(lldb) watchpoint list -v
(lldb) continue
(lldb) bt
(lldb) continue
(lldb) bt
```

A hardware watchpoint uses CPU debug registers to catch writes without
single-stepping. Here, `-w write` selects writes to the reference total, `-s 8` sets the byte
count, and `&` supplies the address. Check the hardware resources with
`watchpoint list -v`; use the breakpoint/watchpoint IDs LLDB prints.

Compare the stacks: `tp_alloc` initializes the object through `PyType_GenericAlloc`,
then `PyObject_Init` initializes it again. Both increment the debug total,
but the object's reference count is reset to 1, so deleting it subtracts only once.
Run `watchpoint disable 1`, `continue` twice (past the second signal), then `quit`.

## Fix and verify

Remove `PyObject_Init((PyObject *)descr, subtype);` from `arraydescr_new` in
`../numpy-src/numpy/_core/src/multiarray/descriptor.c`, then rebuild:

```bash
cd ../numpy-src
spin build -j 4
cd ../debugger-tutorial
python measure.py
```

All batch sizes should now give the same small offset (`[1, 1, 1, 1, 1]` here).
For a Python API to hardware watchpoints on Linux, try [libdebug](https://docs.libdebug.org/latest/).

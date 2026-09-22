# Debugging a NumPy segfault with gdb

This workspace rebuilds NumPy at a historical revision that segfaults on a
one-line Python snippet. The build is a debug build (`-O0 -g`), so you can
work out why it crashes with gdb.

The bug is numpy/numpy#27812: `divmod` of two `timedelta64` values with
incompatible units (years vs. seconds) crashes instead of raising
`TypeError`.

```python
import numpy as np
divmod(np.timedelta64(1, "Y"), np.timedelta64(1, "s"))   # Segmentation fault
```

## Setup

You need [pixi](https://pixi.sh). Everything else (Python 3.12, compilers,
meson, gdb/lldb) comes from conda-forge and is pinned in `pixi.lock`.

```bash
pixi install --locked    # create the environment exactly as locked
pixi run build           # clone numpy/numpy at the pinned revision, then do a debug build (~2 min)
pixi run crash           # Segmentation fault
```

| Task             | What it does                                                           |
|------------------|------------------------------------------------------------------------|
| `clone`          | Clone NumPy into `numpy-src/` at `NUMPY_REV` (the parent of the fix)   |
| `build`          | Editable debug build (`buildtype=debug`, `disable-optimization=true`)  |
| `crash`          | `python crash.py`                                                      |
| `debug`          | `gdb --args python crash.py` (`lldb -- python crash.py` on macOS)      |
| `apply-fix`      | Cherry-pick the upstream fix into `numpy-src/`                         |
| `reset`          | Drop local changes and go back to the buggy revision                   |

NumPy is installed in editable mode. After you edit C code in `numpy-src/`,
the next `import numpy` rebuilds the changed files automatically.

## Walkthrough (Linux, gdb)

```
$ pixi run debug
(gdb) run
Program received signal SIGSEGV, Segmentation fault.
0x... in Py_INCREF (op=0x0) at .../include/python3.12/object.h:641
641	    PY_UINT32_T cur_refcnt = op->ob_refcnt_split[PY_BIG_ENDIAN];
```

`op=0x0` shows the crash: something called `Py_INCREF(NULL)`.

**1. Get the C stack trace.**

```
(gdb) bt
#0  Py_INCREF (op=0x0) at .../object.h:641
#1  PyUFunc_DivmodTypeResolver (...) at ../numpy/_core/src/umath/ufunc_type_resolution.c:2236
#2  resolve_descriptors (...) at ../numpy/_core/src/umath/ufunc_object.c:4158
#3  ufunc_generic_fastcall (...) at ../numpy/_core/src/umath/ufunc_object.c:4520
#4  ufunc_generic_vectorcall (...)
...
#8  PyArray_GenericBinaryFunction (...) at ../numpy/_core/src/multiarray/number.c:207
#9  gentype_divmod (...) at ../numpy/_core/src/multiarray/scalartypes.c.src:337
#10 binary_op1 (..., op_slot=32) at .../Objects/abstract.c:882
```

Read it from the bottom up. Python's `divmod()` dispatches to the scalar's
`nb_divmod` slot (`gentype_divmod`). That forwards to the `np.divmod` ufunc,
which asks its type resolver for the output dtypes, and the resolver crashes.

**2. Get the Python stack trace.** conda-forge's gdb loads CPython's gdb
helpers automatically, which gives you `py-bt`, `py-list`, `py-locals`
and related commands:

```
(gdb) py-bt
Traceback (most recent call first):
  File ".../crash.py", line 4, in <module>
    print(divmod(np.timedelta64(1, "Y"), np.timedelta64(1, "s")))
```

**3. Move up to NumPy's frame and look around.**

```
(gdb) up
#1  PyUFunc_DivmodTypeResolver (...) at ../numpy/_core/src/umath/ufunc_type_resolution.c:2236
2236	            Py_INCREF(out_dtypes[1]);
(gdb) list
2231	    if (type_num1 == NPY_TIMEDELTA) {
2232	        if (type_num2 == NPY_TIMEDELTA) {
2233	            out_dtypes[0] = PyArray_PromoteTypes(PyArray_DESCR(operands[0]),
2234	                                                PyArray_DESCR(operands[1]));
2235	            out_dtypes[1] = out_dtypes[0];
2236	            Py_INCREF(out_dtypes[1]);
(gdb) p out_dtypes[0]
$1 = (PyArray_Descr *) 0x0
(gdb) p *PyArray_DESCR(operands[0])
$2 = {..., typeobj = 0x... <PyTimedeltaArrType_Type>, kind = 109 'm', type_num = 22, elsize = 8, ...}
```

`PyArray_PromoteTypes` returned `NULL`. By CPython convention, a `NULL`
return means "an exception is set". Is one set?

**4. Inspect the pending exception by calling C functions from gdb.**

```
(gdb) p ((PyTypeObject *)PyErr_Occurred())->tp_name
$3 = 0x... "TypeError"
(gdb) call (void)PyErr_Print()
TypeError: Cannot get a common metadata divisor for Numpy datetime metadata [Y] and [s]
because they have incompatible nonlinear base time units.
```

**Diagnosis:** promotion correctly raised a `TypeError` and returned `NULL`.
The resolver never checked the return value and passed `NULL` straight to
`Py_INCREF`. The fix is a single missing error check:

```c
if (out_dtypes[0] == NULL) {
    return -1;
}
```

**5. Verify the fix.**

```bash
pixi run apply-fix    # cherry-pick the upstream commit (or type the fix in yourself)
pixi run crash        # rebuilds on import, then: TypeError: Cannot get a common metadata divisor ...
pixi run reset        # back to the crashing revision
```

### Other things worth trying

- `break PyUFunc_DivmodTypeResolver`, then `run`, `next`, `step`, and `finish`
  to watch the `NULL` come back from `PyArray_PromoteTypes`.
- Catch the exact moment the `TypeError` is raised. First stop in the
  resolver (`break PyUFunc_DivmodTypeResolver`, `run`), then
  `break PyErr_Format`, `continue`, `bt`. You end up in
  `compute_datetime_metadata_greatest_common_divisor` in
  `numpy/_core/src/multiarray/datetime.c`. (If you set the `PyErr_Format`
  breakpoint before `run`, it also triggers many times during
  `import numpy`.)
- `info frame`, `info args`, `info locals`, and `frame N` to move around the stack.
- Run without gdb and get a Python-level traceback on crash:
  `pixi run python -X faulthandler crash.py`.

## macOS notes

gdb cannot debug native arm64 processes on macOS, so on `osx-*` the
environment ships lldb, and `pixi run debug` launches it instead. The same
steps in lldb:

| gdb                                 | lldb                                           |
|-------------------------------------|------------------------------------------------|
| `run`                               | `run`                                          |
| `bt`                                | `bt`                                           |
| `up` / `frame 1`                    | `up` / `frame select 1`                        |
| `list`                              | `source list`                                  |
| `p out_dtypes[0]`                   | `p out_dtypes[0]`                              |
| `call (void)PyErr_Print()`          | `expr (void)PyErr_Print()`                     |
| `break PyErr_Format`                | `b PyErr_Format`                               |

lldb has no `py-bt`. Use `python -X faulthandler crash.py` to get the
Python-level traceback instead.

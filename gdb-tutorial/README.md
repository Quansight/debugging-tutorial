# Debugging a NumPy segfault with lldb

This workspace rebuilds NumPy at a historical revision that segfaults on a
one-line Python snippet. The build is a debug build (`-O0 -g`), so you can
work out why it crashes with a debugger. The tutorial uses
[lldb](https://lldb.llvm.org), which works the same way on Linux and macOS.

The bug is numpy/numpy#27812: `divmod` of two `timedelta64` values with
incompatible units (years vs. seconds) crashes instead of raising
`TypeError`.

```python
import numpy as np
divmod(np.timedelta64(1, "Y"), np.timedelta64(1, "s"))   # Segmentation fault
```

## Setup

You need [pixi](https://pixi.sh). Everything else (Python 3.12, compilers,
[spin](https://github.com/scientific-python/spin), lldb) comes from
conda-forge and is pinned in `pixi.lock`. Linux and macOS are supported.

```bash
pixi install --locked    # create the environment exactly as locked
pixi run build           # clone numpy/numpy at the pinned revision, then spin build in debug mode (~2 min)
pixi run crash           # Segmentation fault
```

| Task             | What it does                                                                   |
|------------------|--------------------------------------------------------------------------------|
| `clone`          | Clone NumPy into `numpy-src/` at `NUMPY_REV` (the parent of the fix)           |
| `build`          | `spin build -- -Dbuildtype=debug -Ddisable-optimization=true`                  |
| `crash`          | Rebuild if needed, then `spin python ../crash.py`                              |
| `debug`          | Rebuild if needed, then run `crash.py` under lldb                              |
| `backtrace`      | Same, non-interactive: run, print the stack trace at the crash, exit           |
| `apply-fix`      | Cherry-pick the upstream fix into `numpy-src/`                                 |
| `reset`          | Drop local changes and go back to the buggy revision                           |

`spin build` compiles into `numpy-src/build/` and installs into
`numpy-src/build-install/`. `spin python` and `spin lldb` point
`PYTHONPATH` at that install, so nothing is installed into the pixi
environment. `crash` and `debug` run `build` first, and the rebuild is
incremental, so after you edit C code in `numpy-src/` only the changed files
get recompiled.

### Passing options to lldb

`debug` takes one optional argument, which is inserted as lldb options before
`-- python ../crash.py`. Quote it as a single string:

```bash
pixi run debug                                   # plain interactive session
pixi run debug "-o run"                          # start running right away
pixi run debug "-o 'b PyUFunc_DivmodTypeResolver' -o run"
pixi run debug "--batch -o run -k 'bt 5' -k quit"  # scripted, then exit
```

In `--batch` mode, `-o` commands run in order, and `-k` commands run only if
the program crashes. `pixi run backtrace` is shorthand for
`pixi run debug "--batch -o run -k bt -k quit"`.

To use spin directly, run it from inside `numpy-src/` in the pixi environment
(`pixi shell`, then `cd numpy-src`). For example, `spin lldb -c 'import numpy'`
or `spin python -X faulthandler ../crash.py`.

## Walkthrough

```
$ pixi run debug
(lldb) run
Process 30873 stopped
* thread #1, name = 'python', stop reason = signal SIGSEGV: address not mapped to object (fault address=0x0)
    frame #0: ... _multiarray_umath...so`Py_INCREF(op=0x0000000000000000) at object.h:641:17 [inlined]
-> 641 	    PY_UINT32_T cur_refcnt = op->ob_refcnt_split[PY_BIG_ENDIAN];
Likely cause: PyArray_PromoteTypes()->ob_base.ob_refcnt accessed 0x0
```

`op=0x0000000000000000` shows the crash: something called `Py_INCREF(NULL)`.
lldb even guesses where the `NULL` came from (`Likely cause: ...`). Let's
confirm that guess.

**1. Get the C stack trace.**

```
(lldb) bt
* frame #0: _multiarray_umath...so`Py_INCREF(op=0x0000000000000000) at object.h:641:17 [inlined]
  frame #1: _multiarray_umath...so`PyUFunc_DivmodTypeResolver(...) at ufunc_type_resolution.c:2236:13
  frame #2: _multiarray_umath...so`resolve_descriptors(...) at ufunc_object.c:4158:18
  frame #3: _multiarray_umath...so`ufunc_generic_fastcall(...) at ufunc_object.c:4520:9
  frame #4: _multiarray_umath...so`ufunc_generic_vectorcall(...) at ufunc_object.c:4592:12
  ...
  frame #8: _multiarray_umath...so`PyArray_GenericBinaryFunction(...) at number.c:207:12
  frame #9: _multiarray_umath...so`gentype_divmod(...) at scalartypes.c.src:337:19
  frame #10: python`binary_op1(..., op_slot=32) at abstract.c:882:13
  ...
  frame #13: python`builtin_divmod_impl(...) at bltinmodule.c:881:12 [inlined]
```

Read it from the bottom up. Python's builtin `divmod()` dispatches to the
scalar's `nb_divmod` slot (`gentype_divmod`). That forwards to the
`np.divmod` ufunc, which asks its type resolver for the output dtypes, and
the resolver crashes. Each frame says which shared object it lives in:
`python` for the interpreter, `_multiarray_umath...so` for NumPy's C code.

**2. Move up to NumPy's frame and look around.**

```
(lldb) up
frame #1: ... PyUFunc_DivmodTypeResolver(...) at ufunc_type_resolution.c:2236:13
   2233	            out_dtypes[0] = PyArray_PromoteTypes(PyArray_DESCR(operands[0]),
   2234	                                                PyArray_DESCR(operands[1]));
   2235	            out_dtypes[1] = out_dtypes[0];
-> 2236	            Py_INCREF(out_dtypes[1]);
(lldb) p out_dtypes[0]
(PyArray_Descr *) NULL
(lldb) p *((PyArrayObject_fields *)operands[0])->descr
(PyArray_Descr) {
  ...
  kind = 'm'
  type_num = 22
  elsize = 8
  ...
}
```

`PyArray_PromoteTypes` returned `NULL`. By CPython convention, a `NULL`
return means "an exception is set". Is one set?

(`PyArray_DESCR(operands[0])` is a `static inline` helper, and lldb can't call
it here. It fails with "call to 'PyArray_DESCR' is ambiguous". Casting to
`PyArrayObject_fields *` and reading the `descr` field directly does the same
thing.)

**3. Inspect the pending exception by calling C functions from lldb.**

```
(lldb) p ((PyTypeObject *)PyErr_Occurred())->tp_name
(const char *) 0x... "TypeError"
(lldb) expr (void)PyErr_Print()
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

**4. Verify the fix.**

```bash
pixi run apply-fix    # cherry-pick the upstream commit (or type the fix in yourself)
pixi run crash        # incremental rebuild, then: TypeError: Cannot get a common metadata divisor ...
pixi run reset        # back to the crashing revision
```

### Other things worth trying

- `b PyUFunc_DivmodTypeResolver`, then `run`, `next`, `step`, and `finish`
  to watch the `NULL` come back from `PyArray_PromoteTypes`. The breakpoint
  shows as "pending" at first, because NumPy's extension module isn't loaded
  yet. lldb resolves it once `import numpy` loads the module.
- Catch the exact moment the `TypeError` is raised. First stop in the
  resolver (`b PyUFunc_DivmodTypeResolver`, `run`), then `b PyErr_Format`,
  `continue`, `bt`. You end up in
  `compute_datetime_metadata_greatest_common_divisor` in
  `numpy/_core/src/multiarray/datetime.c`. (If you set the `PyErr_Format`
  breakpoint before `run`, it also triggers many times during
  `import numpy`.)
- `frame info`, `frame variable`, and `frame select N` to move around the stack.
- Run without a debugger and get a Python-level traceback on crash:
  `spin python -X faulthandler ../crash.py` from `numpy-src/`.

### Coming from gdb?

| gdb                                 | lldb                                           |
|-------------------------------------|------------------------------------------------|
| `run`                               | `run`                                          |
| `bt`                                | `bt`                                           |
| `up` / `frame 1`                    | `up` / `frame select 1`                        |
| `list`                              | `list` / `source list`                         |
| `info locals`                       | `frame variable`                               |
| `p out_dtypes[0]`                   | `p out_dtypes[0]`                              |
| `call (void)PyErr_Print()`          | `expr (void)PyErr_Print()`                     |
| `break PyErr_Format`                | `b PyErr_Format`                               |
| `gdb -batch -ex run -ex bt`         | `lldb --batch -o run -k bt -k quit`            |

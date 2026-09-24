# Finding a NumPy reference-counting bug with LLDB

This exercise recreates
[numpy/numpy#23318](https://github.com/numpy/numpy/pull/23318), a
reference-counting bug that Nathan Goldbaum fixed using hardware
watchpoints. NumPy initialized a dtype twice: `tp_alloc` already
initialized it, then NumPy called `PyObject_Init` again. We will watch both
initializations inside CPython and follow their call stacks back into NumPy.

We use NumPy 2.5.3 with the faulty line restored and a Python 3.15.0rc2
debug interpreter.
NumPy's built-in `StringDType` exercises the affected constructor, so the
example needs no additional extension.

## Set up your machine

Complete [setup](../SETUP.md). From the repository root, inside the Pixi
shell, restore the faulty line and rebuild:

```bash
git -C numpy-src apply ../debugger-tutorial/reintroduce-refcount-bug.patch
cd numpy-src
spin build -j 4
cd ../debugger-tutorial
python measure.py
python reproduce.py
```

Run the remaining terminal commands from `debugger-tutorial/` in the Pixi shell.

## Establish the symptom

The program exits normally, but the total reference count grows.

Read [measure.py](measure.py), then run it:

```bash
python measure.py
```

It warms up the constructor and measurement code, then creates and destroys
batches of dtypes. It collects garbage and compares `sys.gettotalrefcount()`
before and after each batch, printing five samples per batch size and a
zero-construction control.

Look for growth proportional to the number of constructions: about 100
extra references for 100 constructions, and 1000 for 1000. A small constant
offset is not the bug. Warming caches and repeating samples distinguishes
steady growth from one-time initialization.

Example output:

```text
   0 constructions: [1, 1, 1, 1, 1]
 100 constructions: [101, 101, 101, 101, 101]
1000 constructions: [1001, 1001, 1001, 1001, 1001]
```

[reproduce.py](reproduce.py) constructs and deletes one dtype:

```python
from numpy.dtypes import StringDType

dtype = StringDType()
del dtype
```

Why does the reference total grow even though we delete the object?

## Stop after imports with SIGUSR1

A breakpoint on object allocation can fire thousands of times while Python
and NumPy import. The script sends itself `SIGUSR1` after imports, before
constructing the dtype. LLDB stops there so we can set breakpoints in the
loaded extension modules. See [NumPy's debugging guide](https://numpy.org/devdocs/dev/development_advanced_debugging.html#running-a-test-script).

The script registers a no-op handler before sending the signal:

```python
def do_nothing(signum, frame):
    pass

signal.signal(signal.SIGUSR1, do_nothing)
pid = os.getpid()
os.kill(pid, signal.SIGUSR1)
```

Capture the Python executable's absolute, resolved path for LLDB:

```bash
python_executable="$(python -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
```

The path should be inside the repository's `.pixi/envs/default/bin`.
Launch LLDB:

```bash
pixi run --locked -e debugger lldb -- "$python_executable" "$PWD/reproduce.py"
```

```text
(lldb) process handle SIGUSR1 --stop true --notify true --pass true
(lldb) run
(lldb) bt
```

Expect a stop in `kill`/`__kill` with reason `signal SIGUSR1`, not at the bug.
Passing the signal on continuation lets Python run its no-op handler.
Without the handler, SIGUSR1 would terminate the program outside LLDB.
A second SIGUSR1 after `del dtype` stops us before interpreter shutdown.

## Watch the total reference count

A breakpoint stops at a code location. A watchpoint stops on access to a
memory location, including from inlined code or another shared library.
We do not need to know which function accesses it.

Watchpoints can be implemented in hardware or software. A
[software watchpoint](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html)
may require single-stepping the program and checking the watched value after
every instruction. That is expensive even when the value never changes.
A **hardware watchpoint** uses the CPU's debug registers to detect accesses
to a memory region. The program runs normally until a matching access traps
into the debugger, avoiding instruction-by-instruction checking.

In this Python 3.15 build, the location is the main interpreter's
`object_state.reftotal`, a `Py_ssize_t` (8 bytes).
It tracks references in the interpreter and extensions built with its
debug headers. `_Py_RefTotal` still exists for compatibility
with older extensions, but it is no longer the counter to watch here.

At the first SIGUSR1 stop, try:

```text
(lldb) watchpoint set expression -w write -s 8 -- &_PyRuntime.interpreters.main->object_state.reftotal
(lldb) watchpoint list -v
(lldb) continue
(lldb) bt
```

The command specifies what to watch:

- `watchpoint set expression` evaluates an expression to obtain the address
  of the memory to watch. It does not reevaluate the expression after every
  instruction.
- `-w write` selects writes, not reads. It also stops on stores that leave
  the value unchanged; `-w modify` would stop only when the value changes.
- `-s 8` watches eight bytes, covering the whole `Py_ssize_t` counter.
- `--` ends the options. The following `&...reftotal` expression takes the
  counter's address, rather than its current value.

There is no separate hardware-enabling flag: LLDB allocates hardware
resources for this command. `-w` selects the access type, not hardware versus
software. `watchpoint list -v` reports the supported hardware watchpoint
count and the resources assigned to our watchpoint. Look for an 8-byte
entry under `watchpoint resources`; the available slot count depends on the
CPU.

In GDB, watch the same counter at the first SIGUSR1 stop with:

```text
(gdb) watch -location _PyRuntime.interpreters.main->object_state.reftotal
(gdb) info watchpoints
```

`-location` watches the counter's storage, taking its address and size from
the expression and its type, so omit the `&` and explicit byte count.
GDB attempts to use hardware automatically; check that creation reports
`Hardware watchpoint`, since it can fall back to software. `-location` does
not force hardware. Unlike LLDB's `-w write`, GDB's `watch` stops only when
the value changes, so it would miss a store of the same value.

LLDB needs CPython's debug type information to evaluate the address. The
address may change on each run, so do not reuse one from an earlier process.

When the watchpoint triggers, LLDB prints the old and new values. The
instruction that caused the stop has generally already executed; the
highlighted source line can be the next line. Read the stack and values
together. `watchpoint list` also shows the watchpoint's ID and hit count.

The first hit may be ordinary interpreter or signal-handler activity.
Hardware makes detecting a write cheap, but handling each hit still stops
the process and hands control to the debugger. This counter changes so often
that watching it through imports or shutdown would still be slow. SIGUSR1
skips the import-time hits; disable the watchpoint while moving closer to
the constructor:

```text
(lldb) watchpoint disable 1
(lldb) breakpoint set --name arraydescr_new
(lldb) continue
(lldb) source list
(lldb) frame variable subtype
```

Use the IDs LLDB prints if they differ. You should now be in
`numpy/_core/src/multiarray/descriptor.c`, called by
`new_stringdtype_instance`. Delete the function breakpoint and advance to
the allocation at line 2552 in the pinned source with the tutorial patch:

```text
(lldb) breakpoint delete 1
(lldb) thread until 2552
(lldb) watchpoint enable 1
(lldb) continue
(lldb) bt
```

After re-enabling a watchpoint, LLDB's displayed old value can be stale:
the counter changed while the watchpoint was disabled. Compare consecutive
hits with the watchpoint enabled when interpreting the numeric change.

The first initialization should have a stack containing these calls (other
frames may appear between them):

```text
_Py_NewReference
_PyObject_Init
_PyType_AllocNoTrack
PyType_GenericAlloc
arraydescr_new                    descriptor.c:2552
new_stringdtype_instance          stringdtype/dtype.c:30
```

Continue to the next write and inspect its stack:

```text
(lldb) continue
(lldb) bt
```

The second initialization reaches `_Py_NewReference` through the
`PyObject_Init` call in `arraydescr_new`. There is no intervening
`PyType_GenericAlloc` this time. Both initializations increment the total
for the same descriptor. If there are intermediate hits on your build,
continue and compare their stacks; normal reference operations also occur.

For example:

```text
_Py_NewReference
_PyObject_Init
PyObject_Init
arraydescr_new                    descriptor.c:2557
new_stringdtype_instance          stringdtype/dtype.c:30
```

Select the `arraydescr_new` frame with `up` or `frame select N`, using the
frame number from `bt`, and inspect the descriptor:

```text
(lldb) frame variable descr
(lldb) p ((PyObject *)descr)->ob_refcnt
(lldb) source list
```

The object already has a reference count of 1 after allocation.
`PyObject_Init` invokes `_Py_NewReference` again, incrementing the debug total
and resetting the object's count to 1. When that object dies, its reference
is subtracted only once. This leaves a spurious reference in the total;
an increasing total alone does not prove that the dtype's memory stayed
allocated.

Disable the watchpoint, continue to the second signal, then let the program
finish:

```text
(lldb) watchpoint disable 1
(lldb) continue
(lldb) continue
(lldb) quit
```

Hardware limits the number, size, and alignment of watchpoints; avoid
watching large structs. One aligned 8-byte counter fits here.
If you know which object is wrong, watching that object's `ob_refcnt` can
reduce noise; stop watching its address before it is freed and reused.
Watching `descr->ob_refcnt` with `-w modify` would miss the second
initialization's assignment of 1 to an already-1 field; `-w write` can catch
that store. We watch the total because it exposes the erroneous extra
increment directly.

## Make the one-line fix and verify it

In your editor, open `../numpy-src/numpy/_core/src/multiarray/descriptor.c` and
remove this line from `arraydescr_new`:

```diff
-            PyObject_Init((PyObject *)descr, subtype);
```

Inspect the change, rebuild, and rerun the measurement in a fresh process:

```bash
git -C ../numpy-src diff
cd ../numpy-src
spin build -j 4
cd ../debugger-tutorial
python measure.py
```

`git -C ../numpy-src diff` should be empty: removing the injected line
restores the upstream source. Growth proportional to batch size should
disappear, though a fixed measurement offset may remain. The example above
becomes `[1, 1, 1, 1, 1]` for all three batch sizes.
Restart LLDB and repeat the watchpoint experiment;
only the allocator's initialization should remain. Line numbers below the
deletion will have moved by one.

To repeat the exercise after fixing it, apply the patch again and rebuild:

```bash
git -C ../numpy-src apply ../debugger-tutorial/reintroduce-refcount-bug.patch
cd ../numpy-src
spin build -j 4
cd ../debugger-tutorial
```

## Things to try next

- Use `next`, `step`, and `thread step-out` to follow the constructor paths.
  Keep the watchpoint disabled when you want ordinary stepping.
- Move the first signal earlier and observe the additional import-time
  calls. Move it later to narrow a different operation.
- Compare the control with larger batches in `measure.py`. Why is a single
  before/after count less persuasive than the scaling trend?
- [libdebug](https://docs.libdebug.org/latest/) offers a Python API for
  debugging another process, including [signal catchers](https://docs.libdebug.org/latest/stopping_events/signals/)
  and [hardware watchpoints](https://docs.libdebug.org/latest/stopping_events/watchpoints/).
  It supports Linux. Try using it to collect stacks or count writes between
  the signals.

## Troubleshooting

For interpreter, import, or debug-symbol problems, see
[setup troubleshooting](../SETUP.md#troubleshooting).

- **Cannot evaluate the reference-counter expression:** at the first
  SIGUSR1 stop, try `image lookup -s _PyRuntime`. The expression needs
  both that symbol and CPython's struct definitions.
- **Cannot install a watchpoint:** a container/VM must allow debugging child
  processes and expose hardware watchpoints.

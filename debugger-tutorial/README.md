# Finding a NumPy reference-counting bug with LLDB

This exercise recreates [numpy/numpy#23318](https://github.com/numpy/numpy/pull/23318),
Nathan Goldbaum's March 2023 fix, merged by Sebastian Berg. NumPy initialized
a dtype twice: `tp_alloc` already initialized it, then NumPy called
`PyObject_Init` again. Deleting that second call fixed the reference-counting
error. We will use a hardware watchpoint to see both initializations inside
CPython and follow their call stacks back into NumPy.

We use NumPy 2.4.6 with the faulty line restored by a small patch, and a
Python 3.11 debug interpreter. This combination supports spin and Meson
while keeping the reference total in a single named global variable.
NumPy's built-in `StringDType` exercises the affected constructor, so the
example needs no additional extension.

The program completes normally. Our symptom is a growing total reference
count, so there is no crash to give us a useful starting stack trace.

## Set up your machine

Follow [SETUP.md](SETUP.md) before the session to install the build tools
and debug Python, then build the patched NumPy.
The walkthrough uses **LLDB on both Linux and macOS**. Run terminal commands from
`gdb-tutorial/`, with the environments activated as described in setup.

## Establish the symptom

Read [measure.py](measure.py), then run it:

```bash
python measure.py
```

It warms up the constructor and measurement code, creates and destroys
batches of dtypes, collects garbage, and compares `sys.gettotalrefcount()`
before and after each batch. It prints five samples for each batch size,
including a zero-construction control.

Look for growth proportional to the number of constructions: about 100
extra references for 100 constructions, and 1000 for 1000. A small constant
offset from the measurement itself is not the bug. Warming caches and
comparing repeated samples helps distinguish steady growth from one-time
initialization. Compare these results again after the fix.

For example, with the pinned builds on macOS ARM64:

```text
   0 constructions: [1, 1, 1, 1, 1]
 100 constructions: [101, 101, 101, 101, 101]
1000 constructions: [1001, 1001, 1001, 1001, 1001]
```

The relevant part of [reproduce.py](reproduce.py) is just:

```python
from numpy.dtypes import StringDType

dtype = StringDType()
del dtype
```

Why does the reference total grow even though we delete the object?

## Stop after imports with SIGUSR1

A breakpoint on object allocation can fire thousands of times while Python
and NumPy import. Instead, the script sends itself `SIGUSR1` **after imports**,
immediately before the interesting code. LLDB stops when the signal arrives;
we can then install breakpoints in already-loaded extension modules. This
is the [SIGUSR1 technique in NumPy's debugging guide](https://numpy.org/devdocs/dev/development_advanced_debugging.html#running-a-test-script).

The script registers a no-op handler before sending the signal:

```python
def do_nothing(signum, frame):
    pass

signal.signal(signal.SIGUSR1, do_nothing)
pid = os.getpid()
os.kill(pid, signal.SIGUSR1)
```

**Give LLDB an absolute, resolved path to a real Python executable.** A
virtual environment's `python` is often a symlink. The setup command uses
`venv --copies`, so this tutorial's `.venv/bin/python` is a real executable
that stays inside the environment when its path is resolved. Start LLDB
with that resolved path:

```bash
python_executable="$(python -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
lldb -- "$python_executable" reproduce.py
```

The resolved path should still be inside `.venv`. Resolving a symlink to
the base interpreter outside `.venv` also changes which Python packages
are available. For an existing environment with
symlinks, use the copy procedure in [Troubleshooting](#troubleshooting).

```text
(lldb) process handle SIGUSR1 --stop true --notify true --pass true
(lldb) run
(lldb) bt
```

Expect a stop in `kill`/`__kill` with reason `signal SIGUSR1`. This stack is
in the signal-sending code, not the bug. Passing the signal on continuation
lets Python run its harmless handler. Without the handler, SIGUSR1's default
action would terminate the program outside the debugger. A second SIGUSR1
after `del dtype` gives us a stopping point before interpreter shutdown.

## Watch the total reference count

A breakpoint stops at a code location. A **hardware watchpoint** asks the
CPU to stop when a particular memory location is written. It can catch an
inlined increment or a write in a different shared library without knowing
which function to break on in advance.

In Python 3.11, the location is the `_Py_RefTotal` global, a `Py_ssize_t`
(8 bytes on our supported platforms). It is CPython's debug bookkeeping
for total references. Watching it exposes reference activity across the
interpreter and extensions built with its debug headers.

At the first SIGUSR1 stop, try:

```text
(lldb) watchpoint set expression -w write -s 8 -- &_Py_RefTotal
(lldb) continue
(lldb) bt
```

`&_Py_RefTotal` evaluates to the counter's address in this process. The
`-s 8` option supplies the size, so LLDB can watch that address even without
CPython's variable type information. The address may change on each run;
using the symbol avoids copying a numeric address from an earlier process.

LLDB should report a hardware watchpoint, its address, and old/new values
when it triggers. The instruction that caused the stop has generally already
executed; the highlighted source line can be the next line. Read the stack
and values together. `watchpoint list` shows the watchpoint's ID and hit count.

The first hit may be ordinary interpreter or signal-handler activity.
SIGUSR1 removes import noise, but every Python operation still changes
references. Disable the watchpoint while moving closer to the constructor:

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
the allocation at line 2523 in the pinned source with the tutorial patch:

```text
(lldb) breakpoint delete 1
(lldb) thread until 2523
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
arraydescr_new                    descriptor.c:2523
new_stringdtype_instance          stringdtype/dtype.c:30
```

Continue to the next write and inspect its stack:

```text
(lldb) continue
(lldb) bt
```

The second initialization reaches `_Py_NewReference` through the **explicit
`PyObject_Init` call in `arraydescr_new`**. There is no intervening
`PyType_GenericAlloc` this time. Both initializations increment the total
for the same descriptor. If there are intermediate hits on your build,
continue and compare their stacks; normal reference operations also occur.

For example, on macOS ARM64:

```text
_Py_NewReference
_PyObject_Init
PyObject_Init
arraydescr_new                    descriptor.c:2528
new_stringdtype_instance          stringdtype/dtype.c:30
```

uv's macOS interpreter may give function names without CPython source lines
or local-variable types. NumPy's locally built debug information
lets us inspect its frames. Move up to `arraydescr_new` using `up` (or
`frame select N`, using the frame number from `bt`) and inspect:

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
allocated. The bug is NumPy's duplicate initialization of a CPython object.

Disable the watchpoint, continue to the second signal, then let the program
finish:

```text
(lldb) watchpoint disable 1
(lldb) continue
(lldb) continue
(lldb) quit
```

Hardware has only a few watchpoint slots and limits the size/alignment of
each watched region. A single aligned 8-byte counter fits this exercise.
Avoid watching large structs. This counter is extremely busy: leaving the
watchpoint enabled through imports or shutdown will be slow even with
hardware support. Each write stops the process and involves the debugger.
If you know which object is wrong, watching that object's `ob_refcnt` can
reduce noise; stop watching its address before it is freed and reused.
Here, watching only `descr->ob_refcnt` could miss the second initialization's
assignment of 1 to an already-1 field.

## Make the one-line fix and verify it

In your editor, open `numpy-src/numpy/_core/src/multiarray/descriptor.c` and
remove this line from `arraydescr_new`:

```diff
-            PyObject_Init((PyObject *)descr, subtype);
```

Inspect the change, rebuild, and rerun the measurement in a fresh process:

```bash
git -C numpy-src diff
cd numpy-src
spin build -j 4
cd ..
python measure.py
```

Removing the injected line returns NumPy to its upstream source, so
`git -C numpy-src diff` should now be empty. The growth proportional to
the batch size should disappear. Small fixed measurement offsets may remain:
the example above becomes `[1, 1, 1, 1, 1]`
for all three batch sizes. Restart LLDB and repeat the watchpoint experiment;
only the allocator's initialization should remain. Line numbers below the
deletion will have moved by one.

To repeat the exercise after fixing it, apply the patch again and rebuild:

```bash
git -C numpy-src apply ../reintroduce-refcount-bug.patch
cd numpy-src
spin build -j 4
cd ..
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

- **No `sys.gettotalrefcount`:** check `sys.executable` and activate `.venv`
  after `pixi shell`. Compiling only NumPy with `-g` is insufficient.
- **Wrong NumPy or import failure:** restore `PYTHONPATH` and inspect
  `numpy.__file__`. The NumPy path must point into `numpy-src/build-install`.
- **No NumPy source lines:** from `numpy-src`, run
  `python vendored-meson/meson/meson.py configure build -Dbuildtype=debug`,
  then `spin build -j 4`.
  Keep the build artifacts and restart LLDB after rebuilding. Check that
  `arraydescr_new` resolves to `descriptor.c` and its locals are visible
  before continuing.
- **No `_Py_RefTotal` type information:** after imports, try
  `image lookup -s _Py_RefTotal`. The symbol must exist in the debug
  interpreter. The expression command watches its address and explicitly
  specifies the 8-byte width, rather than requiring a CPython variable type.
- **macOS startup appears stuck:** check for a privacy dialog asking whether
  Python can access Documents, Desktop, or Downloads. Allow the requested
  folder access, or enable it for Python under **System Settings → Privacy
  & Security → Files & Folders**. Using a checkout under `~/Developer`
  avoids these protected folders; choose that location before setup so
  the interpreter and build paths also stay outside them.
- **The executable resolves outside the venv:**
  For an existing venv with symlinks, run
  `cp -L .venv/bin/python .venv/bin/python-lldb` to put a real executable
  inside `.venv/bin`, keeping the venv's packages available.
  Resolve that copy's path with
  `python_executable="$(.venv/bin/python-lldb -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"`,
  then run `lldb -- "$python_executable" reproduce.py`.
- **Cannot install a watchpoint:** a container/VM must allow debugging child
  processes and expose hardware watchpoints.

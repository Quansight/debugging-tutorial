# Debugging tutorials

Tutorials for debugging native code.

Complete the [setup](SETUP.md) before the session. One root Pixi workspace
provides separate environments for the two exercises:

- The [NumPy reference-counting tutorial](debugger-tutorial/README.md) uses
  debug Python, SIGUSR1, and an LLDB hardware watchpoint to follow a one-line
  bug across the extension/interpreter boundary. Activate it with
  `pixi shell --locked`.
- The [Samply profiling tutorial](samply-tutorial/README.md) uses free-threaded
  Python to explore thread scaling and compare native profiles with Python
  sampling profiles. Activate it with `pixi shell --locked -e profiling`.

Run `exit` before switching environments. Commands in `bash` blocks run in
your terminal; commands prefixed with `(lldb)` run at the debugger prompt.

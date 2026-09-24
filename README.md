# Debugging tutorials

Hands-on tutorials for debugging native code on your own machine. Use
[pixi](https://pixi.sh) to install tools and activate an environment, then
build, run, and debug the examples yourself at a terminal prompt.

The [NumPy reference-counting tutorial](gdb-tutorial/README.md) uses debug
Python, SIGUSR1, and an LLDB hardware watchpoint to follow a one-line bug
across the extension/interpreter boundary. It runs on Linux and macOS.

Each tutorial is a standalone pixi workspace. Complete setup before the
session so the hands-on time can focus on debugging. Commands in `bash`
blocks run in your terminal; commands prefixed with `(lldb)` run at the
debugger prompt.

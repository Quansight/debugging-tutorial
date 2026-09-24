"""Construct and destroy one dtype between two debugger rendezvous points."""

import os
import signal
import sys

if not hasattr(sys, "gettotalrefcount"):
    raise SystemExit("Use the debug Python from .venv; see README.md.")

from numpy.dtypes import StringDType


def do_nothing(signum, frame):
    pass


signal.signal(signal.SIGUSR1, do_nothing)
pid = os.getpid()

# Imports are complete. Set breakpoints/watchpoints here.
os.kill(pid, signal.SIGUSR1)

dtype = StringDType()
del dtype

# Stop here to disable watchpoints before interpreter shutdown.
os.kill(pid, signal.SIGUSR1)

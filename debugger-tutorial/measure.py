"""Look for reference-count growth across repeated dtype lifetimes."""

import gc
import sys

if not hasattr(sys, "gettotalrefcount"):
    raise SystemExit("Use the debug Python from the shared setup in ../SETUP.md.")

from numpy.dtypes import StringDType


def exercise(count):
    for _ in range(count):
        dtype = StringDType()
        del dtype


def measure(count):
    gc.collect()
    before = sys.gettotalrefcount()
    exercise(count)
    gc.collect()
    return sys.gettotalrefcount() - before


if __name__ == "__main__":
    # Warm caches and interpreter specialization before collecting samples.
    for _ in range(5):
        measure(1000)

    # Compare sizes to distinguish per-construction growth from fixed overhead.
    for count in (0, 100, 1000):
        deltas = [measure(count) for _ in range(5)]
        print(f"{count:4d} constructions: {deltas}")

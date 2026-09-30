* Problem: increased concurrency should be faster, but isn't
    * Running on free-threaded Python, 3.15 in this case
    * Compare 1 thread and 4 threads
    * `python regression_pipeline_tuning.py`
* Disable 1 thread version, so we can focus on profiling just the parallelism part
* Run with Samply
    * `samply record python -X perf regression_pipeline_tuning.py`
    * `-X perf` includes Python callstacks, but only on Linux (https://docs.python.org/3/howto/perf_profiling.html)
    * Otherwise, just get native callstack
    * Generic tool, works with any compiled code, so useful outside of Python, and works with any version of Python
    * UI is Firefox Profiler, a sophisticated UI that you can use from other tools too
* Run with Python 3.15's new profiler
    * `python -m profiling.sampling run --gecko --all-threads --native regression_pipeline_tuning.py`
    * Go to https://profiler.firefox.com
    * Upload the JSON file
    * Downside: Only Python 3.15, as we'll see doesn't show the info you get from samply
* Third option: `py-spy`
    * Works with older Pythons
    * Doesn't do free-threading yet
* First question with profilers is, what are they measuring?
    * Show `python -m profiling.sampling run --help`, discuss some of the options
    * Key limitation of samply, on Linux at least: doesn't show _sleeping threads_, only _computing threads_.
        * Some bottlenecks will be harder to spot!
    * By default `profiling.sampling` shows both kinds of threads, but you can change output
* Show timeline in Firefox Profiler
    * Notice how you may have only one thread running, and then parallelism isn't helping
    * Notice some threads run longer than others
    * Not all threads show by default! Show how to turn them on
* Flamegraph
    * Make sure to select threads you care about!
    * Talk about limits of flamegraph
        * Can't show you issues due to parallelism, e.g. only one thread running
        * No concept of before/after, order is arbitrary
        * Need to look at it together with the timeline
    * Show how you can do flamegraph on subset of time, too
* Understanding flamegraph
    * Callstacks, the wider the more time spent
    * Look for wider columns
    * `is_scalar_nan` why
    * `ABCMeta.__instancecheck___()` what
    * Locks?!
* https://github.com/python/cpython/issues/157526
    * Make a reproducer
        * `is_scalar_nan`: https://github.com/scikit-learn/scikit-learn/blob/bbf8863a869f118a1a42422d8cc67ec6c07f2fe0/sklearn/utils/_missing.py#L9
    * `abc_reproducer.py`
    * Can profile, again limiting to only 4 thread case
    * Mutex conflicts are all under `in_weak_set`
    * There's a cache
    * And the cache is protected by a lock
    * No GIL in free-threaded Python, so locks are often per-object
    * Multiple threads trying to lock at same time will slow you down a lot
* Solutions
    * Within Python, try not to have a lock, or maybe read/write lock
    * Within scikit-learn, why is `is_scalar_nan`, a Python function, being called so much?
        * Lots of repeat calls to Python functions is a performance smell
        * Used to be called much more
        * In latest `scikit-learn`, is basically not called at all, so this conflict goes away

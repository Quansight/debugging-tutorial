# Copied from https://github.com/ogrisel/example_pipelines/
import argparse
import json
import os
import platform
import subprocess
import sys
import sysconfig
import traceback
import uuid
from datetime import datetime, timezone
from functools import partial
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlparse
import time
from pprint import pprint
import warnings

import joblib
import numpy as np
import pandas as pd
import threadpoolctl

import sklearn
from sklearn.compose import (
    ColumnTransformer,
    TransformedTargetRegressor,
    make_column_selector,
)
from sklearn.datasets import fetch_openml
from sklearn.kernel_approximation import Nystroem
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import RandomizedSearchCV, ShuffleSplit, KFold
from sklearn.pipeline import FeatureUnion, make_pipeline
from sklearn.preprocessing import (
    FunctionTransformer,
    MinMaxScaler,
    OneHotEncoder,
    SplineTransformer,
    TargetEncoder,
    PowerTransformer,
)
from sklearn.utils.parallel import delayed, Parallel

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "regression_pipeline_tuning"
BENCHMARK_SCRIPT = Path(__file__).name


def write_json_result(path, record):
    with path.open("w", encoding="utf-8") as file:
        json.dump(record, file, indent=2, sort_keys=True)
        file.write("\n")


def _run_command(args):
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _gil_enabled():
    if hasattr(sys, "_is_gil_enabled"):
        return sys._is_gil_enabled()
    return sysconfig.get_config_var("Py_GIL_DISABLED") != 1


def _linux_cpuinfo():
    info = {}
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as file:
            for line in file:
                if ":" not in line:
                    continue
                key, value = (part.strip() for part in line.split(":", 1))
                if key in {"vendor_id", "model name", "cpu cores"}:
                    info[key] = value
    except OSError:
        pass
    return info


def _cpu_details():
    family = platform.system()
    details = {
        "architecture": platform.machine(),
        "manufacturer": None,
        "model": None,
        "physical_cores": joblib.cpu_count(only_physical_cores=True),
        "logical_cores": joblib.cpu_count(only_physical_cores=False),
    }

    if family == "Darwin":
        brand = _run_command(["sysctl", "-n", "machdep.cpu.brand_string"])
        details["model"] = brand
        if brand:
            if "Apple" in brand:
                details["manufacturer"] = "Apple"
            elif "Intel" in brand:
                details["manufacturer"] = "Intel"
    elif family == "Linux":
        cpuinfo = _linux_cpuinfo()
        details["manufacturer"] = cpuinfo.get("vendor_id")
        details["model"] = cpuinfo.get("model name")
    elif family == "Windows":
        details["manufacturer"] = platform.processor() or None
        details["model"] = _run_command(["wmic", "cpu", "get", "Name", "/value"])
        if details["model"]:
            for line in details["model"].splitlines():
                if line.startswith("Name="):
                    details["model"] = line.split("=", 1)[1].strip()
                    break

    return details


def _normalize_channel_url(channel_url):
    if not channel_url:
        return None

    if channel_url.startswith("pypi"):
        return "pypi.org"

    parsed = urlparse(channel_url)
    if parsed.netloc in {"pypi.org", "files.pythonhosted.org"}:
        return "pypi.org"

    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc in {"conda.anaconda.org", "anaconda.org"} and path_parts:
        return path_parts[0]

    if parsed.netloc:
        return parsed.netloc

    return channel_url


def _conda_package_channel(package_name):
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if not conda_prefix:
        return None

    meta_dir = Path(conda_prefix) / "conda-meta"
    if not meta_dir.is_dir():
        return None

    for meta_file in sorted(meta_dir.glob(f"{package_name}-*.json")):
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("name") != package_name:
            continue
        channel_url = metadata.get("channel") or metadata.get("url")
        return _normalize_channel_url(channel_url)

    return None


def _pixi_environment():
    return os.environ.get("PIXI_ENVIRONMENT_NAME")


def _package_info(package_name):
    try:
        dist = distribution(package_name)
    except PackageNotFoundError:
        return {"version": None, "channel": None}

    info = {"version": dist.version, "channel": _conda_package_channel(package_name)}
    if info["channel"] is not None:
        return info

    try:
        installer = dist.read_text("INSTALLER").strip()
    except (FileNotFoundError, OSError, TypeError):
        installer = None

    if installer in {"pip", "uv", "uv-pixi"}:
        info["channel"] = "pypi.org"
        return info

    try:
        direct_url = json.loads(dist.read_text("direct_url.json"))
        info["channel"] = (
            _normalize_channel_url(direct_url.get("url", "")) or "pypi.org"
        )
    except (FileNotFoundError, OSError, TypeError, json.JSONDecodeError):
        pass

    return info


def _threadpool_libraries():
    libraries = list(threadpoolctl.threadpool_info())
    for library in libraries:
        # "prefix" is enough to identify the library.
        del library["filepath"]

    # On macOS, if the BLAS library is not found, add a new entry for the
    # newaccelerate library which is currently not visible by threadpoolctl.
    if platform.system() == "Darwin" and not any(
        library["user_api"] == "blas" for library in libraries
    ):
        libraries.append(
            {
                "user_api": "blas",
                "internal_api": "newaccelerate",
                "version": None,
                "threading_layer": None,
                "num_threads": None,
                "architecture": None,
            }
        )
    return libraries


def create_run_record(
    *,
    run_id,
    joblib_backend,
    array_api_namespace,
    device,
    max_n_workers,
    n_iter,
    cv,
    openml_data_id,
    results_file,
):
    packages = {
        package: _package_info(package)
        for package in ("numpy", "scipy", "scikit-learn", "pandas", "joblib")
    }
    cpu = _cpu_details()

    return {
        "run_id": str(run_id),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "results_file": results_file,
        "run": {
            "joblib_backend": joblib_backend,
            "array_api_namespace": array_api_namespace,
            "device": device,
            "max_n_workers": max_n_workers,
        },
        "benchmark": {
            "script": BENCHMARK_SCRIPT,
            "openml_data_id": openml_data_id,
            "n_iter": n_iter,
            "cv_n_splits": cv.n_splits,
            "cv_test_size": cv.test_size,
            "random_state": cv.random_state,
        },
        "system": {
            "python": {
                "version": sys.version.replace("\n", " "),
                "gil_enabled": _gil_enabled(),
            },
            "os": {
                "family": platform.system(),
                "version": platform.release(),
                "platform": platform.platform(),
            },
            "cpu": cpu,
            "pixi": {
                "environment": _pixi_environment(),
            },
            "packages": packages,
            "threadpool_libraries": _threadpool_libraries(),
        },
        "timings": [],
    }


# %%
warnings.filterwarnings("ignore", category=UserWarning, message=".*A worker stopped.*")
warnings.filterwarnings("error", category=RuntimeWarning)
# %%
X, y = fetch_openml(data_id=42165, as_frame=True, return_X_y=True)
y = y.astype(np.float32).values
cv = ShuffleSplit(n_splits=3, test_size=0.2, random_state=42)
OPENML_DATA_ID = 42165


# %%
# Parse the CLI arguments to retrieve the array API namespace, device and number of workers, but
# only when not run as a Jupyter notebook.
def in_notebook():
    try:
        cfg = get_ipython().config  # pyright: ignore[reportUndefinedVariable]
        if "IPKernelApp" in cfg:
            return True
    except NameError:
        return False


xp = np
device_name = "cpu"
joblib_backend = "loky"
max_n_workers = 1
array_api_namespace = "numpy"
capture_errors = True

if not in_notebook():
    parser = argparse.ArgumentParser()
    parser.add_argument("--array-api-namespace", type=str, default="numpy")
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--max-n-workers", type=int, default=1)
    parser.add_argument("--joblib-backend", type=str, default="threading")
    parser.add_argument(
        "--no-capture-errors",
        action="store_true",
        help="Do not catch fit errors; let exceptions propagate instead of recording them.",
    )
    args = parser.parse_args()

    max_n_workers = args.max_n_workers
    capture_errors = not args.no_capture_errors

    if args.joblib_backend is not None:
        joblib_backend = args.joblib_backend

    if args.array_api_namespace is not None:
        os.environ["SCIPY_ARRAY_API"] = "1"
        os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=".*is not currently supported on the MPS.*",
        )
        sklearn.set_config(array_api_dispatch=True)
        xp = import_module(args.array_api_namespace)
        device_name = args.device
        array_api_namespace = args.array_api_namespace

print(f"Array API namespace: {xp.__name__}")
print(f"Device: {device_name}")
joblib.parallel_config(backend=joblib_backend)
print(f"Joblib backend: {joblib_backend}")

# %%
target_encoder = make_pipeline(
    TargetEncoder(
        target_type="continuous", cv=KFold(n_splits=5, shuffle=True, random_state=42)
    ),
    MinMaxScaler(),
)
preprocessor = ColumnTransformer(
    transformers=[
        (
            "categorical",
            FeatureUnion(
                [
                    (
                        "onehot",
                        OneHotEncoder(
                            sparse_output=False,
                            handle_unknown="infrequent_if_exist",
                            max_categories=10,
                            min_frequency=5,
                        ),
                    ),
                    ("target", target_encoder),
                ]
            ),
            make_column_selector(dtype_include=["category", "string"]),
        ),
        (
            "numeric",
            SplineTransformer(n_knots=10, degree=2, handle_missing="zeros"),
            make_column_selector(dtype_include=["number"]),
        ),
    ]
)

poly_reg = make_pipeline(
    preprocessor,
    FunctionTransformer(
        lambda X: X.astype(np.float32),
        feature_names_out="one-to-one",
        check_inverse=False,
    ),
    FunctionTransformer(
        partial(xp.asarray, device=device_name),
        feature_names_out="one-to-one",
        check_inverse=False,
    ),
    Nystroem(kernel="poly", degree=2, n_components=300, random_state=42),
    # TODO: add array API support for PowerTransformer and TransformedTargetRegressor.
    # TransformedTargetRegressor(
    #     RidgeCV(alphas=np.logspace(-6, 6, 13)),
    #     transformer=PowerTransformer(),
    # ),
    RidgeCV(alphas=np.logspace(-6, 6, 13)),
)


param_grid = {
    "columntransformer__numeric__n_knots": [5, 10, 20, 50],
    "columntransformer__categorical__onehot__max_categories": [
        2,
        5,
        10,
        20,
        50,
    ],
    "columntransformer__categorical__onehot__min_frequency": [None, 2, 5, 10, 20],
    "columntransformer__categorical__target": ["drop", target_encoder],
    "nystroem__n_components": [10, 30, 100, 300],
    "nystroem__kernel": ["poly", "rbf"],
    "nystroem__degree": [2, 3],
    "nystroem__gamma": np.logspace(-6, 6, 25),
}
# TODO: run for various values of n_jobs and plot a scalability curve.
cpu_count = joblib.cpu_count(only_physical_cores=True)
n_jobs_list = 2 ** np.arange(int(np.log2(cpu_count) + 1))

n_iter = 30
run_record = create_run_record(
    run_id="1",
    joblib_backend=joblib_backend,
    array_api_namespace=array_api_namespace,
    device=device_name,
    max_n_workers=max_n_workers,
    n_iter=n_iter,
    cv=cv,
    openml_data_id=OPENML_DATA_ID,
    results_file="",
)

print(f"Benchmarking hyper-parameter tuning with {n_iter=} and {cv.n_splits=}...")
for n_jobs in [1, 4]:
    # Warm-up joblib workers to ignore the process startup overhead: nominally,
    # Python programs should not change the number of workers so often.
    Parallel(n_jobs=n_jobs)([delayed(lambda: None)() for _ in range(10)])

    # Measure the time taken to tuned hyperparameters.
    tic = time.time()
    hp_search = RandomizedSearchCV(
        poly_reg,
        param_grid,
        n_iter=n_iter,
        cv=cv,
        n_jobs=n_jobs,
        scoring="r2",
        error_score="raise",
        random_state=42,
    )
    timing_record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "n_jobs": int(n_jobs),
        "duration_s": None,
        "speedup": None,
        "best_r2": None,
        "error": None,
    }
    fit_error = None
    if capture_errors:
        try:
            hp_search.fit(X, y)
        except Exception:
            fit_error = traceback.format_exc()
    else:
        hp_search.fit(X, y)
    duration = time.time() - tic
    timing_record["duration_s"] = duration
    if not run_record["timings"]:
        timing_record["speedup"] = 1.0
    elif run_record["timings"][0]["error"] is not None:
        timing_record["speedup"] = None
    else:
        timing_record["speedup"] = run_record["timings"][0]["duration_s"] / duration
    timing_record["best_r2"] = float(hp_search.best_score_)
    run_record["timings"].append(timing_record)
    if timing_record["speedup"] is None:
        speedup_msg = "speedup: n/a"
    else:
        speedup_msg = f"speedup: {timing_record['speedup']:.2f}x"
    print(
        f"n_workers: {n_jobs}, duration: {duration:.3f} s, {speedup_msg}, Best R2: {hp_search.best_score_:.4f}"
    )


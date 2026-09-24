#!/usr/bin/env bash
set -euo pipefail

# Keep actual build paths in the debug information; Pixi retains this source
# tree and its object files. In particular, do not use conda's prefix maps.
# Use -g rather than -g3: GCC's macro information confuses LLDB expressions.
export CFLAGS="-O0 -g"
export OPT="-O0 -g"
export CPPFLAGS="-I$PREFIX/include"
export LDFLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib"

# Avoid detecting APIs from a newer macOS SDK than the running OS.
ac_cv_func_pipe2=no ac_cv_func_dup3=no \
./configure --prefix="$PREFIX" --with-pydebug --enable-shared \
    --with-system-expat --with-openssl="$PREFIX"
make -j4
make install
ln -sfn python3.15d "$PREFIX/bin/python"

"$PREFIX/bin/python" -VV
"$PREFIX/bin/python" -c 'import sysconfig; assert sysconfig.get_config_var("Py_DEBUG") == 1'

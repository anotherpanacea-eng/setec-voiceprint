#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PYTHON:-python3}
WHEELHOUSE="$ROOT/wheelhouse"
VENVS="$ROOT/.venvs"

if [ ! -d "$WHEELHOUSE" ]; then
  echo "missing verified wheelhouse; run ./bootstrap.sh first" >&2
  exit 2
fi

install_env() {
  name=$1
  lock=$2
  $PYTHON -m venv --clear "$VENVS/$name"
  "$VENVS/$name/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-build-isolation \
    --no-index \
    --find-links "$WHEELHOUSE" \
    --require-hashes \
    -r "$ROOT/locks/$lock"
}

install_env trafilatura-210 trafilatura-210.txt
install_env datasketch-165 datasketch-165.txt
install_env datasketch-200 datasketch-200.txt

PROFILE="$ROOT/network-deny.sb"
if ! sandbox-exec -f "$PROFILE" "$PYTHON" -c \
  'import socket; socket.create_connection(("example.com", 443), timeout=1)' \
  >/dev/null 2>&1; then
  :
else
  echo "network-denial probe unexpectedly connected" >&2
  exit 1
fi

sandbox-exec -f "$PROFILE" "$PYTHON" "$ROOT/controller.py" "$@"

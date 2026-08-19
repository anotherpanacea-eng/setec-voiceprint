#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PYTHON:-python3}
WHEELHOUSE="$ROOT/wheelhouse"

version=$($PYTHON -c 'import platform; print(platform.python_version())')
if [ "$version" != "3.13.7" ]; then
  echo "bootstrap requires Python 3.13.7, found $version" >&2
  exit 2
fi

mkdir -p "$WHEELHOUSE"
for lock in "$ROOT"/locks/*.txt; do
  $PYTHON -m pip download \
    --dest "$WHEELHOUSE" \
    --require-hashes \
    -r "$lock"
done

# feedparser's sgmllib3k dependency is published only as an sdist. Build its
# pure-Python wheel during this network-permitted phase under a fixed epoch,
# then require the known wheel hash during the offline install.
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/setec-396-wheel.XXXXXX")
trap 'rm -rf "$BUILD_DIR"' EXIT HUP INT TERM
$PYTHON -m venv "$BUILD_DIR/builder"
"$BUILD_DIR/builder/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-index \
  --find-links "$WHEELHOUSE" \
  --require-hashes \
  -r "$ROOT/locks/build-tools.txt"
SOURCE_DATE_EPOCH=315532800 PIP_NO_INDEX=1 \
  "$BUILD_DIR/builder/bin/python" -m pip wheel \
  --no-deps \
  --no-build-isolation \
  --wheel-dir "$BUILD_DIR" \
  "$WHEELHOUSE/sgmllib3k-1.0.0.tar.gz"
SGML_WHEEL="$BUILD_DIR/sgmllib3k-1.0.0-py3-none-any.whl"
SGML_EXPECTED=3e78dc821e18ad51162fe028b8ea3fd978cecc119c1e0a037047cc8935883d76
SGML_ACTUAL=$(shasum -a 256 "$SGML_WHEEL" | awk '{print $1}')
if [ "$SGML_ACTUAL" != "$SGML_EXPECTED" ]; then
  echo "sgmllib3k wheel hash mismatch: expected $SGML_EXPECTED, got $SGML_ACTUAL" >&2
  exit 1
fi
cp "$SGML_WHEEL" "$WHEELHOUSE/"

echo "verified wheelhouse: $WHEELHOUSE"

#!/bin/sh
# Opens the control panel at http://127.0.0.1:8770 and hands you a Start button.
# Want the plain headless bridge instead?  ./run.sh is `zwiftbridge ui`; the
# bridge on its own is `zwiftbridge run`.
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO" || exit 1
# PYTHONPATH rather than an install, so a fresh clone runs without one.
PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$REPO/.venv/bin/python" -m zwiftbridge ui "$@"

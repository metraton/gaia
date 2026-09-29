#!/bin/sh
# Runs a Gaia hook entrypoint ("$@") with the first working Python 3.
# The python.org Windows installer provides `python` and `py`, not `python3`,
# and Windows' App Execution Alias answers `python3` without being Python --
# so each candidate is probed before it is trusted.
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info[0] != 3)' >/dev/null 2>&1; then
    exec "$candidate" "$@"
  fi
done
if command -v py >/dev/null 2>&1; then
  exec py -3 "$@"
fi
echo "gaia: no Python 3 found on PATH (tried python3, python, py -3)" >&2
exit 1

#!/usr/bin/env bash
# CLI smoke test for `gaia paths` subcommand.
# Exit 0 on success, non-zero on any failure.

set -euo pipefail

REPO_ROOT="/home/jorge/ws/me/gaia"
GAIA_BIN="${REPO_ROOT}/bin/gaia"
PYTHON="${REPO_ROOT}/.venv/bin/python"

# Use a unique temp dir for this run; clean up on exit.
TEST_DIR="$(mktemp -d -t gaia-cli-smoke.XXXXXX)"
cleanup() {
    rm -rf "${TEST_DIR}"
}
trap cleanup EXIT

# --- Test 1: `gaia paths` (no arg) prints multiple lines, exit 0
GAIA_DATA_DIR="${TEST_DIR}/case1" "${PYTHON}" "${GAIA_BIN}" paths > "${TEST_DIR}/case1.out"
LINE_COUNT=$(wc -l < "${TEST_DIR}/case1.out")
if [ "${LINE_COUNT}" -lt 4 ]; then
    echo "FAIL: 'gaia paths' printed only ${LINE_COUNT} lines, expected >= 4" >&2
    cat "${TEST_DIR}/case1.out" >&2
    exit 1
fi

# --- Test 2: `GAIA_DATA_DIR=... gaia paths data` prints exactly that path
EXPECTED_DIR="${TEST_DIR}/case2"
ACTUAL=$(GAIA_DATA_DIR="${EXPECTED_DIR}" "${PYTHON}" "${GAIA_BIN}" paths data)
if [ "${ACTUAL}" != "${EXPECTED_DIR}" ]; then
    echo "FAIL: 'gaia paths data' returned '${ACTUAL}', expected '${EXPECTED_DIR}'" >&2
    exit 1
fi

# --- Test 3: without GAIA_DATA_DIR, `gaia paths data` prints \$HOME/.gaia
unset GAIA_DATA_DIR
EXPECTED_HOME="${HOME}/.gaia"
# Use a sandboxed HOME so we don't pollute the real ~/.gaia
export HOME="${TEST_DIR}/fake-home"
mkdir -p "${HOME}"
ACTUAL=$("${PYTHON}" "${GAIA_BIN}" paths data)
if [ "${ACTUAL}" != "${HOME}/.gaia" ]; then
    echo "FAIL: 'gaia paths data' (no env) returned '${ACTUAL}', expected '${HOME}/.gaia'" >&2
    exit 1
fi

# --- Test 4: directory was created with mode 0700
MODE=$(stat -c '%a' "${HOME}/.gaia")
if [ "${MODE}" != "700" ]; then
    echo "FAIL: ~/.gaia mode is ${MODE}, expected 700" >&2
    exit 1
fi

echo "OK"
exit 0

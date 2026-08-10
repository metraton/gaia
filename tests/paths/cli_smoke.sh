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
REAL_HOME="${HOME}"

# --- Test 5: default output names scratch, evidence, worktrees, tmp,
# rejected_turns -- scratch and evidence exist today but were hidden from
# `gaia paths`, and evidence's root was pinned to $HOME regardless of
# GAIA_DATA_DIR before this fix.
unset GAIA_DATA_DIR
"${PYTHON}" "${GAIA_BIN}" paths > "${TEST_DIR}/case5.out"
for KEY in scratch evidence worktrees tmp rejected_turns; do
    if ! grep -q "^${KEY}=" "${TEST_DIR}/case5.out"; then
        echo "FAIL: 'gaia paths' default output is missing key '${KEY}='" >&2
        cat "${TEST_DIR}/case5.out" >&2
        exit 1
    fi
done

# --- Test 6 (adversarial): with GAIA_DATA_DIR overridden, NO printed value
# may still resolve under the real (un-overridden) HOME -- including
# evidence, which previously bypassed the override entirely.
OVERRIDE_DIR="${TEST_DIR}/case6"
GAIA_DATA_DIR="${OVERRIDE_DIR}" "${PYTHON}" "${GAIA_BIN}" paths > "${TEST_DIR}/case6.out"
while IFS='=' read -r KEY VALUE; do
    case "${VALUE}" in
        "${REAL_HOME}"/*|"${REAL_HOME}")
            echo "FAIL: 'gaia paths' key '${KEY}' still resolves under the real HOME (${REAL_HOME}) with GAIA_DATA_DIR overridden: ${VALUE}" >&2
            exit 1
            ;;
    esac
    case "${VALUE}" in
        "${OVERRIDE_DIR}"/*|"${OVERRIDE_DIR}")
            ;;
        *)
            echo "FAIL: 'gaia paths' key '${KEY}' did not relocate under the override (${OVERRIDE_DIR}): ${VALUE}" >&2
            exit 1
            ;;
    esac
done < "${TEST_DIR}/case6.out"

echo "OK"
exit 0

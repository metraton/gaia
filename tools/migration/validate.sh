#!/usr/bin/env bash
# validate.sh
# Read-only: checks that the migrated rows match their sources (V1..V5).
# Prints PASS or FAIL for each; exits 1 if any fails.
set -euo pipefail

DB_PATH="${HOME}/.gaia/gaia.db"
PROJECT="me"
EPISODES_SRC="${EPISODES_SRC:?set EPISODES_SRC to the episodes.jsonl migrate_01 read}"
EVENTS_SRC="${EVENTS_SRC:?set EVENTS_SRC to the events.jsonl the harness_events rows came from}"
NOTES_DIR="${NOTES_DIR:?set NOTES_DIR to the memory directory migrate_02 read}"

# Non-blank JSONL lines count as candidate records; migrate_01 also drops
# invalid JSON, so V1 assumes the episodes source has none.
EXPECTED_V1="$(grep -c -v '^[[:space:]]*$' "${EPISODES_SRC}")"
EXPECTED_V2="$(find "${NOTES_DIR}" -maxdepth 1 -type f -name '*.md' \! -name 'MEMORY.md' | wc -l | tr -d ' ')"
EXPECTED_V3="12"
EXPECTED_V4="$(grep -c -v '^[[:space:]]*$' "${EVENTS_SRC}")"

sqlite_count() {
    sqlite3 "${DB_PATH}" "$1"
}

FAILS=0

run_check() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        echo "[validate] ${label}: PASS (expected=${expected}, actual=${actual})"
    else
        echo "[validate] ${label}: FAIL (expected=${expected}, actual=${actual})"
        FAILS=$((FAILS + 1))
    fi
}

ACTUAL_V1="$(sqlite_count "SELECT COUNT(*) FROM episodes WHERE project='${PROJECT}';")"
run_check "V1 episodes" "${EXPECTED_V1}" "${ACTUAL_V1}"

ACTUAL_V2="$(sqlite_count "SELECT COUNT(*) FROM memory WHERE project='${PROJECT}';")"
run_check "V2 memory" "${EXPECTED_V2}" "${ACTUAL_V2}"

# V3 expects the 12 sections of the original project-context.json.
ACTUAL_V3="$(sqlite_count "SELECT COUNT(*) FROM context_contracts WHERE project='${PROJECT}';")"
run_check "V3 context_contracts" "${EXPECTED_V3}" "${ACTUAL_V3}"

ACTUAL_V4="$(sqlite_count "SELECT COUNT(*) FROM harness_events WHERE project='${PROJECT}';")"
run_check "V4 harness_events" "${EXPECTED_V4}" "${ACTUAL_V4}"

ACTUAL_V5_FTS="$(sqlite_count "SELECT COUNT(*) FROM episodes_fts;")"
EXPECTED_V5="${ACTUAL_V1}"
run_check "V5 episodes_fts sync" "${EXPECTED_V5}" "${ACTUAL_V5_FTS}"

if [ "${FAILS}" -eq 0 ]; then
    echo "[validate] ALL PASS"
    exit 0
else
    echo "[validate] ${FAILS} FAIL(s)"
    exit 1
fi

#!/usr/bin/env bash
# validate.sh
# Read-only: verifica que la migración haya cargado los datos esperados.
# 5 aserciones (V1..V5). Imprime PASS/FAIL por cada una.
# Exit 0 si todas pasan, 1 si alguna falla.
set -euo pipefail

DB_PATH="${HOME}/.gaia/gaia.db"
PROJECT="me"
EPISODES_SRC="/home/jorge/ws/me/.claude/project-context/episodic-memory/episodes.jsonl"
EVENTS_SRC="/home/jorge/ws/me/.claude/events/events.jsonl"
NOTES_DIR="/home/jorge/.claude/projects/-home-jorge-ws-me/memory"

# Conteos esperados (dinámicos donde aplica).
# Líneas no-vacías en JSONL = registros candidatos (los .py descartan blank/bad,
# pero la base actual no tiene líneas inválidas -- ver auditoría previa).
EXPECTED_V1="$(grep -c -v '^[[:space:]]*$' "${EPISODES_SRC}")"
EXPECTED_V2="$(find "${NOTES_DIR}" -maxdepth 1 -type f -name '*.md' \! -name 'MEMORY.md' | wc -l | tr -d ' ')"
EXPECTED_V3="12"
EXPECTED_V4="$(grep -c -v '^[[:space:]]*$' "${EVENTS_SRC}")"

# Función helper: ejecuta un SELECT que retorna un escalar.
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

# V1: episodes count == líneas de episodes.jsonl
ACTUAL_V1="$(sqlite_count "SELECT COUNT(*) FROM episodes WHERE project='${PROJECT}';")"
run_check "V1 episodes" "${EXPECTED_V1}" "${ACTUAL_V1}"

# V2: memory count == 28 (archivos .md sin contar MEMORY.md)
ACTUAL_V2="$(sqlite_count "SELECT COUNT(*) FROM memory WHERE project='${PROJECT}';")"
run_check "V2 memory" "${EXPECTED_V2}" "${ACTUAL_V2}"

# V3: context_contracts count == 12
ACTUAL_V3="$(sqlite_count "SELECT COUNT(*) FROM context_contracts WHERE project='${PROJECT}';")"
run_check "V3 context_contracts" "${EXPECTED_V3}" "${ACTUAL_V3}"

# V4: harness_events count == líneas de events.jsonl
ACTUAL_V4="$(sqlite_count "SELECT COUNT(*) FROM harness_events WHERE project='${PROJECT}';")"
run_check "V4 harness_events" "${EXPECTED_V4}" "${ACTUAL_V4}"

# V5: episodes_fts count == episodes count (FTS sync via triggers).
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

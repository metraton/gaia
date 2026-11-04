#!/usr/bin/env bash
# bootstrap_database.sh -- idempotent initializer of the Gaia DB.
#
# The shell/test reference for scripts/bootstrap_database.py, the engine
# `gaia install` runs. bin/validate-sandbox.sh seeds its database with this
# script. Bash and sqlite3 only, no python3.

set -euo pipefail

# === Section 1: Variables and environment validation ===

# The DB path comes from GAIA_DB; everything below uses $GAIA_DB.
GAIA_DB="${GAIA_DB:-$HOME/.gaia/gaia.db}"

# The schema is resolved relative to this script, not the caller's cwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
SCHEMA_FILE="${SCHEMA_FILE:-$SCRIPT_DIR/../gaia/store/schema.sql}"

if ! command -v sqlite3 > /dev/null 2>&1; then
    echo "[bootstrap] ERROR: sqlite3 no encontrado en PATH. Instálalo (apt install sqlite3) y reintenta." >&2
    exit 1
fi

if [ ! -f "$SCHEMA_FILE" ]; then
    echo "[bootstrap] ERROR: schema.sql no encontrado en $SCHEMA_FILE" >&2
    exit 1
fi

mkdir -p "$(dirname "$GAIA_DB")"

echo "[bootstrap] Initializing Gaia DB at $GAIA_DB"
echo "[bootstrap] Using schema:  $SCHEMA_FILE"

# === Section 1.5: Pre-schema ADD COLUMN reconciliation (existing DBs) ===
#
# schema.sql runs in full and carries indexes on columns that forward
# migrations added (agent_contract_handoffs.contract_id, from v27_to_v28). On an
# existing DB whose table predates the column, CREATE TABLE IF NOT EXISTS is a
# no-op and the index build aborts with "no such column"; SQLite cannot make a
# CREATE INDEX conditional on a column, and schema.sql must run before the
# migration ledger (Section 3c) can be read.
#
# So for every `ALTER TABLE <t> ADD COLUMN <c>` in the migrations still pending,
# a column missing from an existing table <t> is added here, before schema.sql.
# A fresh DB has no tables, so nothing is added; a re-run finds the column
# present and skips it.

# An ADD COLUMN statement starts at the beginning of a line and ends on the first
# line whose code ends in `;`. Its clauses may span lines (v61_to_v62 puts each
# CHECK on its own line), so both readers below act on the whole statement.
_ADD_COLUMN_RE='^[[:space:]]*alter[[:space:]]+table[[:space:]]+([a-z0-9_]+)[[:space:]]+add[[:space:]]+column[[:space:]]+([a-z0-9_]+)'
_STATEMENT_END_RE=';[[:space:]]*(--.*)?$'

# $1 = first line of an ADD COLUMN statement. Reads the rest of it from the
# caller's stdin and leaves the whole statement in ADD_COLUMN_STATEMENT.
_read_add_column_statement() {
    local line="$1"
    ADD_COLUMN_STATEMENT="$line"
    while ! [[ "$line" =~ $_STATEMENT_END_RE ]] && { IFS= read -r line || [ -n "$line" ]; }; do
        ADD_COLUMN_STATEMENT+=$'\n'"$line"
    done
}

_reconcile_pre_schema_add_columns() {
    local mig_file target line lower table col tbl_exists col_exists ledger=0
    # Only pending migrations count: an applied one may add a column a later
    # one renamed (task_status became agent_state in v37), which must not return.
    if [ "$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_version';")" -gt 0 ]; then
        ledger="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
    fi
    for mig_file in "${SCRIPT_DIR}/migrations"/v*_to_v*.sql; do
        [ -f "$mig_file" ] || continue
        target="${mig_file##*_to_v}"
        target="${target%.sql}"
        [ "$target" -gt "$ledger" ] || continue
        while IFS= read -r line || [ -n "$line" ]; do
            lower="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')"
            if [[ "$lower" =~ $_ADD_COLUMN_RE ]]; then
                table="${BASH_REMATCH[1]}"
                col="${BASH_REMATCH[2]}"
                _read_add_column_statement "$line"
                tbl_exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='${table}';")"
                if [ "$tbl_exists" -eq 0 ]; then
                    continue
                fi
                col_exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM pragma_table_info('${table}') WHERE name='${col}';")"
                if [ "$col_exists" -eq 0 ]; then
                    echo "[bootstrap] pre-schema reconcile: adding ${table}.${col} (existing DB predates it)"
                    sqlite3 "$GAIA_DB" "$ADD_COLUMN_STATEMENT"
                fi
            fi
        done < "$mig_file"
    done
}

_reconcile_pre_schema_add_columns

# Section 2: apply the schema (DDL). Every CREATE in schema.sql uses IF NOT EXISTS, so applying it to an
# initialized DB neither fails nor recreates anything.
sqlite3 "$GAIA_DB" < "$SCHEMA_FILE"

TABLE_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")"
TRIGGER_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger';")"
FTS5_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%_fts';")"

echo "[bootstrap] Schema applied (${TABLE_COUNT} tables, ${TRIGGER_COUNT} triggers, ${FTS5_COUNT} FTS5 mirrors)"

# === Section 3: Seed agent_permissions ===

# The narrow per-domain write matrix: 13 rows for 5 agents. The fourth agent is
# "gaia-system", the name tools/scan/migrate_workspace.py::_SCANNER_AGENTS uses.
# That module grants every scanner agent every scanner table for its own scan;
# this seed is the domain matrix and does not include that grant.
# INSERT OR IGNORE keeps a re-run from failing or duplicating.
sqlite3 "$GAIA_DB" <<'EOF'
-- cloud-troubleshooter
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters', 'cloud-troubleshooter', 1);

-- developer
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('apps',      'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('features',  'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('libraries', 'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('services',  'developer', 1);

-- gaia-system
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('gaia_installations', 'gaia-system', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('integrations',       'gaia-system', 1);

-- gitops-operator
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters_defined', 'gitops-operator', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('releases',         'gitops-operator', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('workloads',        'gitops-operator', 1);

-- platform-architect
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters',   'platform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_live',    'platform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_modules', 'platform-architect', 1);
EOF

echo "[bootstrap] agent_permissions seeded (13 rows, 5 agents, brief B3 M2 mapping)"

# === Section 3b: Seed schema_version baseline (floor) ===
#
# SCHEMA_FLOOR is the oldest schema version upgraded in place; schema.sql
# produces exactly that shape. The history below it is collapsed into this
# baseline:
#
#   - Fresh DB (no schema_version rows): schema.sql already built the FLOOR
#     state, so the ledger is stamped at SCHEMA_FLOOR directly.
#   - DB at or above the FLOOR: nothing to do here; Section 3c decides whether
#     forward migrations are pending.
#   - DB below the FLOOR (1 <= version < FLOOR): refused with a request to
#     recreate it.
#
# `gaia doctor` compares MAX(version) with EXPECTED_SCHEMA_VERSION, and its
# check_schema_ddl_consistency compares the live CHECK constraints with schema.sql.
SCHEMA_FLOOR=18

NOW_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
EXISTING_VERSION="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"

if [ "$EXISTING_VERSION" -eq 0 ]; then
    sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${SCHEMA_FLOOR}, '${NOW_UTC}', 'baseline floor: schema.sql at v${SCHEMA_FLOOR}');
EOF
    echo "[bootstrap] schema_version baseline seeded at floor (v${SCHEMA_FLOOR})"
elif [ "$EXISTING_VERSION" -lt "$SCHEMA_FLOOR" ]; then
    echo "[bootstrap] ERROR: DB at schema_version=${EXISTING_VERSION} is below the supported floor v${SCHEMA_FLOOR}." >&2
    echo "[bootstrap] In-place upgrade from pre-v${SCHEMA_FLOOR} databases is no longer supported." >&2
    echo "[bootstrap] Recreate the DB: back up any data you need, delete ${GAIA_DB}, then re-run \`gaia install\`." >&2
    exit 1
else
    echo "[bootstrap] schema_version at v${EXISTING_VERSION} (>= floor v${SCHEMA_FLOOR}); no baseline seed needed"
fi

# === Section 3c: Apply pending forward migrations (floor+1 .. EXPECTED) ===
#
# Applies scripts/migrations/v{N-1}_to_v{N}.sql for N > SCHEMA_FLOOR (convention
# in scripts/migrations/README.md): each bump adds exactly one file and raises
# EXPECTED_SCHEMA_VERSION in bin/cli/doctor.py in the same commit.
#
# A fresh install is stamped at the FLOOR (Section 3b), so every fresh install
# replays FLOOR+1..EXPECTED against a DB whose objects schema.sql already
# created. Migrations must therefore be idempotent: CREATE ... IF NOT EXISTS,
# and ADD COLUMN neutralised by the runner guard below.
#
# Each migration runs in its own BEGIN/COMMIT. On failure the ledger does not
# advance, so the next bootstrap sees the same pending migration.
# tests/cli/test_schema_version_lockstep.py ties EXPECTED_SCHEMA_VERSION to the
# migrations on disk.

DOCTOR_PY="${SCRIPT_DIR}/../bin/cli/doctor.py"
if [ ! -f "$DOCTOR_PY" ]; then
    echo "[bootstrap] ERROR: doctor.py no encontrado en $DOCTOR_PY (no puedo leer EXPECTED_SCHEMA_VERSION)" >&2
    exit 1
fi

EXPECTED_LINE="$(grep -E '^EXPECTED_SCHEMA_VERSION\s*=\s*[0-9]+' "$DOCTOR_PY")"
EXPECTED_VERSION="${EXPECTED_LINE##*= }"
EXPECTED_VERSION="${EXPECTED_VERSION// /}"

if ! [[ "$EXPECTED_VERSION" =~ ^[0-9]+$ ]]; then
    echo "[bootstrap] ERROR: no pude parsear EXPECTED_SCHEMA_VERSION desde $DOCTOR_PY (got: '${EXPECTED_VERSION}')" >&2
    exit 1
fi

CURRENT_VERSION="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
echo "[bootstrap] schema_version: current=${CURRENT_VERSION}, expected=${EXPECTED_VERSION}"

# Schema-direction guard: bootstrap only migrates forward. A DB migrated by a
# newer Gaia than this code would otherwise be reported "up-to-date" and leave
# stale code reading a newer schema. The DB is left untouched: schema.sql is
# all IF NOT EXISTS and no migration or stamp runs past this point.
if [ "$CURRENT_VERSION" -gt "$EXPECTED_VERSION" ]; then
    echo "[bootstrap] ERROR: live DB schema_version=${CURRENT_VERSION} is NEWER than the schema this code expects (v${EXPECTED_VERSION})." >&2
    echo "[bootstrap] This Gaia code is OLDER than the database it would run against; installing it would leave stale code reading a newer schema (the finalize-breaking drift). Refusing to install -- the DB is left untouched (no clobber)." >&2
    echo "[bootstrap] Install a Gaia whose EXPECTED_SCHEMA_VERSION >= ${CURRENT_VERSION} (the source checkout or release artifact that produced this DB), then re-run. To validate without changing anything, run \`gaia doctor\`." >&2
    exit 1
fi

MIG_DIR="${SCRIPT_DIR}/migrations"

# Runner-level ADD COLUMN guard. schema.sql already carries each migration's
# target DDL (scripts/migrations/README.md section 1), so a migration is
# replayed against a DB that already has its columns, and SQLite has no
# `ADD COLUMN IF NOT EXISTS`. For each `ALTER TABLE <t> ADD COLUMN <c>`
# statement whose column <c> already exists on <t> (PRAGMA table_info), every
# line of the statement is commented out before the migration runs; every other
# line passes through verbatim.
_filter_add_column_idempotent() {
    # $1 = path to the migration .sql file; the filtered SQL goes to stdout.
    local mig_file="$1"
    local line lower table col exists
    while IFS= read -r line || [ -n "$line" ]; do
        lower="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')"
        if [[ "$lower" =~ $_ADD_COLUMN_RE ]]; then
            table="${BASH_REMATCH[1]}"
            col="${BASH_REMATCH[2]}"
            _read_add_column_statement "$line"
            exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM pragma_table_info('${table}') WHERE name='${col}';")"
            if [ "$exists" -gt 0 ]; then
                printf -- '-- [bootstrap] skipped (column %s.%s already present): %s\n' \
                    "$table" "$col" "${ADD_COLUMN_STATEMENT//$'\n'/$'\n'-- }"
            else
                printf '%s\n' "$ADD_COLUMN_STATEMENT"
            fi
            continue
        fi
        printf '%s\n' "$line"
    done < "$mig_file"
}

if [ "$CURRENT_VERSION" -lt "$EXPECTED_VERSION" ]; then
    # Reached by a fresh install too: Section 3b stamps the FLOOR, so a fresh DB
    # is behind EXPECTED whenever forward migrations exist. A DB below the FLOOR
    # was already refused in Section 3b.
    for N in $(seq $((CURRENT_VERSION + 1)) "$EXPECTED_VERSION"); do
        PREV=$((N - 1))
        MIG_FILE="${MIG_DIR}/v${PREV}_to_v${N}.sql"

        if [ ! -f "$MIG_FILE" ]; then
            echo "[bootstrap] ERROR: missing migration file ${MIG_FILE}" >&2
            echo "[bootstrap] Cannot advance from v${PREV} to v${N}. The ledger will remain at v${CURRENT_VERSION}." >&2
            echo "[bootstrap] When bumping EXPECTED_SCHEMA_VERSION to v${N}, add scripts/migrations/v${PREV}_to_v${N}.sql in the same commit." >&2
            exit 1
        fi

        echo "[bootstrap] migration v${PREV}->v${N}: applying ${MIG_FILE}"
        MIG_SQL="$(_filter_add_column_idempotent "$MIG_FILE")"
        if ! sqlite3 "$GAIA_DB" <<EOF
BEGIN;
${MIG_SQL}
COMMIT;
EOF
        then
            echo "[bootstrap] ERROR: migration v${PREV}->v${N} failed. Transaction rolled back." >&2
            echo "[bootstrap] schema_version ledger remains at v${CURRENT_VERSION} -- not stamping v${N}." >&2
            exit 1
        fi
        echo "[bootstrap] migration v${PREV}->v${N}: applied successfully"
        MIG_DESC="applied migration $(basename "$MIG_FILE")"
        sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', '${MIG_DESC}');
EOF
    done
else
    echo "[bootstrap] schema_version up-to-date (no migrations pending)"
fi

# The bootstrap never registers a workspace: only `gaia workspace declare` does.

# === Section 5: FTS5 backfill ===

# Inserts into each FTS5 mirror the rows it lacks. The AFTER INSERT triggers
# keep new rows in sync; this covers rows that predate the mirrors or triggers.
sqlite3 "$GAIA_DB" <<'EOF'
-- projects_fts: name, role, primary_language (mirror of `projects`)
INSERT INTO projects_fts(rowid, name, role, primary_language)
SELECT rowid, name, role, primary_language
FROM projects
WHERE rowid NOT IN (SELECT rowid FROM projects_fts);

-- apps_fts: name, description, topic_key
INSERT INTO apps_fts(rowid, name, description, topic_key)
SELECT rowid, name, description, topic_key
FROM apps
WHERE rowid NOT IN (SELECT rowid FROM apps_fts);

-- services_fts: name, description, topic_key
INSERT INTO services_fts(rowid, name, description, topic_key)
SELECT rowid, name, description, topic_key
FROM services
WHERE rowid NOT IN (SELECT rowid FROM services_fts);

-- briefs_fts: objective, context, approach (rowid = briefs.id)
INSERT INTO briefs_fts(rowid, objective, context, approach)
SELECT id, objective, context, approach
FROM briefs
WHERE id NOT IN (SELECT rowid FROM briefs_fts);
EOF

# A base/mirror count mismatch only warns; it does not fail the run.
FTS_OK=1
for pair in "projects:projects_fts" "apps:apps_fts" "services:services_fts" "briefs:briefs_fts"; do
    base="${pair%%:*}"
    mirror="${pair##*:}"
    base_count="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM ${base};")"
    mirror_count="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM ${mirror};")"
    if [ "$base_count" != "$mirror_count" ]; then
        echo "[bootstrap]   WARN: ${base} (${base_count}) != ${mirror} (${mirror_count})" >&2
        FTS_OK=0
    fi
done

if [ "$FTS_OK" = "1" ]; then
    echo "[bootstrap] FTS5 backfilled (4/4 consistency check passed)"
else
    echo "[bootstrap] FTS5 backfilled (consistency check WARNING -- ver líneas anteriores)"
fi

# Section 6: final invariants. Each check prints PASS or FAIL with its value; any FAIL makes the script exit 1.
ALL_OK=1

# The seed adds 13 rows; -ge tolerates extra rows from earlier Gaia versions.
PERMS_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM agent_permissions WHERE allow_write IS NOT NULL;")"
if [ "$PERMS_COUNT" -ge 13 ]; then
    echo "[bootstrap] check: agent_permissions rows >= 13 (got ${PERMS_COUNT}) -- PASS"
else
    echo "[bootstrap] check: agent_permissions rows >= 13 (got ${PERMS_COUNT}) -- FAIL"
    ALL_OK=0
fi

# At least the 5 seeded agents. Equality would fail on a DB that kept rows for
# an agent a later Gaia dropped (the live "gaia-operator"), which the seed never deletes.
AGENT_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions;")"
if [ "$AGENT_COUNT" -ge "5" ]; then
    echo "[bootstrap] check: distinct agents >= 5 (got ${AGENT_COUNT}) -- PASS"
else
    echo "[bootstrap] check: distinct agents >= 5 (got ${AGENT_COUNT}) -- FAIL"
    ALL_OK=0
fi

# FTS5 triggers: insert, delete and update for each of the 4 mirrors = 12.
TRIGGER_FTS_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND (name LIKE '%_fts_%' OR name LIKE 'briefs_a%');")"
if [ "$TRIGGER_FTS_COUNT" = "12" ]; then
    echo "[bootstrap] check: FTS5 triggers == 12 (got ${TRIGGER_FTS_COUNT}) -- PASS"
else
    echo "[bootstrap] check: FTS5 triggers == 12 (got ${TRIGGER_FTS_COUNT}) -- FAIL"
    ALL_OK=0
fi

SCHEMA_VER="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
if [ "$SCHEMA_VER" -ge "$SCHEMA_FLOOR" ]; then
    echo "[bootstrap] check: schema_version >= floor v${SCHEMA_FLOOR} (got ${SCHEMA_VER}) -- PASS"
else
    echo "[bootstrap] check: schema_version >= floor v${SCHEMA_FLOOR} (got ${SCHEMA_VER}) -- FAIL"
    ALL_OK=0
fi

if [ "$ALL_OK" = "1" ]; then
    echo "[bootstrap] Done. DB at $GAIA_DB ready for \`gaia\` CLI operations."
    exit 0
else
    echo "[bootstrap] Done WITH FAILURES. Revisa los checks marcados FAIL arriba." >&2
    exit 1
fi

#!/usr/bin/env bash
# validate-sandbox.sh -- end-to-end consumer-install verification harness.
#
# Two target modes:
#
#   --target sandbox (default):
#     Creates an ephemeral sandbox project populated from
#     tests/fixtures/sandbox-project/, installs the target Gaia version
#     (no postinstall hook -- bootstrap is lazy, see bin/cli/install.py),
#     and exercises the FTS5 backfill safety-net plus the read-side CLI
#     surface (version, doctor, status, context show, memory stats/search,
#     scan). It also confirms plain `npm install` never clobbers an
#     existing settings.local.json.
#
#   --target local:
#     Installs Gaia directly into a real workspace. If --workspace <path> is
#     passed, that path is used as-is. Otherwise auto-detection walks up
#     from cwd looking for a .claude/ with a Gaia instance marker
#     (.claude/hooks/, .claude/agents/, or node_modules/@jaguilar87/gaia/),
#     falling back to $HOME/ws/me/ if present. NO cleanup -- the install
#     IS the installation. A fresh tarball install avoids per-path approval
#     prompts for edited files during a session.
#     There is no npm postinstall hook (bootstrap is lazy, see
#     bin/cli/install.py), so `npm install` alone does not wire the
#     workspace's .claude/ config, symlinks, or plugin registry. This mode
#     explicitly runs `gaia install --workspace <target>` right after
#     `npm install` to perform that wiring.
#
# Exit 0 when every check passes; 1 otherwise. `--stay` keeps the sandbox
# dir for post-mortem inspection (path printed on exit). Only meaningful
# with --target sandbox.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

VERSION_SPEC=""
TARBALL_PATH=""
STAY=0
TARGET="sandbox"
WORKSPACE_OVERRIDE=""
FRESH=0

usage() {
  cat <<'EOF'
Usage:
  bin/validate-sandbox.sh [--version <spec>] [--tarball <path>]
                          [--target sandbox|local] [--workspace <path>]
                          [--fresh] [--stay]

Options:
  --version <spec>    npm version specifier, e.g. "@rc", "@5.0.0-rc1",
                      "@jaguilar87/gaia@5.0.0-rc1". Default: "@rc".
  --tarball <path>    Install from a local tarball (from `npm pack`).
                      Takes precedence over --version.
  --target <mode>     sandbox (default): ephemeral /tmp/gaia-sandbox-<ts>/.
                      local: install over a real workspace (see --workspace
                      or auto-detect from cwd).
                      Local mode skips the settings-preservation check
                      (no pre-install snapshot of the real workspace).
  --workspace <path>  Explicit target directory for --target local.
                      Bypasses auto-detection. Ignored with --target sandbox.
  --fresh             Before `npm install`, wipe node_modules/, package.json,
                      and package-lock.json from the workspace. Forces a
                      clean install — useful when a prior install left
                      conflicting metadata in package.json. NO effect on
                      --target sandbox (sandbox dirs are always pristine).
  --stay              Do NOT clean up the sandbox dir on exit. Useful for
                      debugging; sandbox path is printed on exit.
                      Ignored with --target local.
  --help, -h          Print this help and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION_SPEC="$2"
      shift 2
      ;;
    --tarball)
      TARBALL_PATH="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --workspace)
      WORKSPACE_OVERRIDE="$2"
      shift 2
      ;;
    --fresh)
      FRESH=1
      shift
      ;;
    --stay)
      STAY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${TARGET}" != "sandbox" && "${TARGET}" != "local" ]]; then
  echo "FATAL: --target must be 'sandbox' or 'local', got '${TARGET}'" >&2
  exit 2
fi

# Default to @rc when no install source given.
if [[ -z "${TARBALL_PATH}" && -z "${VERSION_SPEC}" ]]; then
  VERSION_SPEC="@rc"
fi

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/sandbox-project"

if [[ "${TARGET}" == "sandbox" && ! -d "${FIXTURE_DIR}" ]]; then
  echo "FATAL: fixture dir not found at ${FIXTURE_DIR}" >&2
  exit 1
fi

if [[ -n "${TARBALL_PATH}" ]]; then
  # Resolve relative to cwd, then check existence
  if [[ "${TARBALL_PATH}" != /* ]]; then
    TARBALL_PATH="$(cd "$(dirname "${TARBALL_PATH}")" && pwd)/$(basename "${TARBALL_PATH}")"
  fi
  if [[ ! -f "${TARBALL_PATH}" ]]; then
    echo "FATAL: tarball not found at ${TARBALL_PATH}" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Determine working directory based on target
# ---------------------------------------------------------------------------

is_gaia_instance() {
  # A directory is a Gaia instance if it has .claude/ with hook or agent
  # content, or an already-installed Gaia package in node_modules.
  local dir="$1"
  [[ -d "${dir}/.claude/hooks" ]] && return 0
  [[ -d "${dir}/.claude/agents" ]] && return 0
  [[ -d "${dir}/node_modules/@jaguilar87/gaia" ]] && return 0
  return 1
}

is_gaia_repo_root() {
  # Return 0 (true) if a directory is the root of the Gaia source repo itself.
  # Detected by the presence of both package.json with name "@jaguilar87/gaia"
  # and the bin/validate-sandbox.sh script (which only lives in the source tree).
  local dir="$1"
  [[ -f "${dir}/package.json" ]] || return 1
  [[ -f "${dir}/bin/validate-sandbox.sh" ]] || return 1
  grep -q '"name": "@jaguilar87/gaia"' "${dir}/package.json" 2>/dev/null && return 0
  return 1
}

detect_local_workspace() {
  # Priority 1: walk up from cwd looking for .claude/ with a Gaia marker.
  # Preferring cwd means `cd project-X && npm run gaia:install-local`
  # installs into project-X, not whatever comes first in $HOME.
  #
  # SAFETY GUARD: skip any directory that is the Gaia source repo itself.
  # The repo ships a node_modules/@jaguilar87/gaia/ entry (self-referencing
  # dependency) which trips is_gaia_instance(), causing auto-detect to
  # resolve to the repo root instead of the intended consumer workspace.
  # The result is a "PASS" install that wires .claude/hooks -> the repo's
  # node_modules, not the consumer workspace's node_modules.
  local dir
  dir="$(pwd)"
  while [[ "${dir}" != "/" ]]; do
    if is_gaia_repo_root "${dir}"; then
      # This directory is the Gaia source repo. Skip it -- installing here
      # would wire symlinks into the repo's own node_modules.
      dir="$(dirname "${dir}")"
      continue
    fi
    if [[ -d "${dir}/.claude" ]] && is_gaia_instance "${dir}"; then
      echo "${dir}"
      return 0
    fi
    dir="$(dirname "${dir}")"
  done
  # Priority 2: fallback to $HOME/ws/me if it exists and has .claude/.
  if [[ -d "${HOME}/ws/me/.claude" ]]; then
    echo "${HOME}/ws/me"
    return 0
  fi
  return 1
}

is_noexec_mount() {
  # Return 0 (true) if the directory's filesystem is mounted with noexec.
  # Some WSL/Linux setups mount /tmp as tmpfs with noexec, which makes the
  # installed bin shims unrunnable (rc=126 Permission denied) even though
  # the exec bit is set. We detect that and pick a safe fallback.
  local dir="$1"
  [[ -d "${dir}" ]] || return 1
  # findmnt is the most reliable; fall back to parsing /proc/mounts.
  if command -v findmnt >/dev/null 2>&1; then
    local opts
    opts="$(findmnt -no OPTIONS --target "${dir}" 2>/dev/null || true)"
    [[ "${opts}" == *noexec* ]] && return 0
    return 1
  fi
  # Best-effort fallback: walk /proc/mounts for the longest matching mountpoint.
  local resolved best_mp="" best_opts=""
  resolved="$(cd "${dir}" 2>/dev/null && pwd -P)" || resolved="${dir}"
  while IFS=' ' read -r _src mp _fs opts _rest; do
    case "${resolved}/" in
      "${mp}/"*)
        if [[ ${#mp} -gt ${#best_mp} ]]; then
          best_mp="${mp}"
          best_opts="${opts}"
        fi
        ;;
    esac
  done < /proc/mounts
  [[ "${best_opts}" == *noexec* ]] && return 0
  return 1
}

select_sandbox_prefix() {
  # Pick a parent directory for the ephemeral sandbox.
  # Order: $TMPDIR (if set and exec-capable) -> /tmp (if exec-capable) ->
  # $HOME/.cache/gaia-sandbox. Each candidate is rejected if its filesystem
  # is mounted noexec, since npm bin shims must be directly executable.
  local candidate
  if [[ -n "${TMPDIR:-}" && -d "${TMPDIR}" ]]; then
    if ! is_noexec_mount "${TMPDIR}"; then
      echo "${TMPDIR%/}"
      return 0
    fi
    echo "[sandbox] TMPDIR=${TMPDIR} is mounted noexec; falling back" >&2
  fi
  if [[ -d /tmp ]] && ! is_noexec_mount /tmp; then
    echo "/tmp"
    return 0
  fi
  if [[ -d /tmp ]]; then
    echo "[sandbox] /tmp is mounted noexec; falling back to \$HOME/.cache" >&2
  fi
  candidate="${HOME}/.cache/gaia-sandbox"
  mkdir -p "${candidate}"
  if is_noexec_mount "${candidate}"; then
    echo "FATAL: no exec-capable directory available for sandbox." >&2
    echo "       Tried: \$TMPDIR, /tmp, ${candidate} -- all noexec." >&2
    echo "       Set TMPDIR to an exec-capable path and retry." >&2
    return 1
  fi
  echo "${candidate}"
}

if [[ "${TARGET}" == "sandbox" ]]; then
  if ! SANDBOX_PREFIX="$(select_sandbox_prefix)"; then
    exit 1
  fi
  WORKSPACE="${SANDBOX_PREFIX}/gaia-sandbox-$(date +%s)-$$"
  echo "[sandbox] prefix: ${SANDBOX_PREFIX}"
  mkdir -p "${WORKSPACE}"
else
  if [[ -n "${WORKSPACE_OVERRIDE}" ]]; then
    # Explicit override from --workspace flag. Resolve relative paths
    # against cwd, then verify directory exists.
    if [[ "${WORKSPACE_OVERRIDE}" != /* ]]; then
      WORKSPACE_OVERRIDE="$(cd "$(dirname "${WORKSPACE_OVERRIDE}")" 2>/dev/null && pwd)/$(basename "${WORKSPACE_OVERRIDE}")"
    fi
    if [[ ! -d "${WORKSPACE_OVERRIDE}" ]]; then
      echo "FATAL: --workspace path does not exist: ${WORKSPACE_OVERRIDE}" >&2
      exit 1
    fi
    WORKSPACE="${WORKSPACE_OVERRIDE}"
    echo "[local] target workspace (override): ${WORKSPACE}"
  elif ! WORKSPACE="$(detect_local_workspace)"; then
    echo "FATAL: --target local could not locate a workspace." >&2
    echo "       Walked up from cwd looking for a .claude/ with a Gaia marker" >&2
    echo "       (hooks/, agents/, or node_modules/@jaguilar87/gaia/)," >&2
    echo "       fallback \$HOME/ws/me/.claude/ also absent." >&2
    echo "       Pass --workspace <path> to override." >&2
    exit 1
  else
    echo "[local] target workspace: ${WORKSPACE}"
  fi
fi

# ---------------------------------------------------------------------------
# Cleanup trap (sandbox only)
# ---------------------------------------------------------------------------

cleanup() {
  local rc=$?
  if [[ "${TARGET}" == "sandbox" ]]; then
    if [[ "${STAY}" -eq 1 ]]; then
      echo
      echo "Sandbox preserved at: ${WORKSPACE}"
      echo "Remove manually when done: rm -rf '${WORKSPACE}'"
    else
      rm -rf "${WORKSPACE}" 2>/dev/null || true
    fi
  fi
  exit "${rc}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Check harness
# ---------------------------------------------------------------------------

CHECK_NAMES=()
CHECK_STATUS=()
CHECK_DETAILS=()
CHECK_MS=()
TOTAL_START_MS=$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')

record() {
  local name="$1" status="$2" detail="$3" ms="$4"
  CHECK_NAMES+=("${name}")
  CHECK_STATUS+=("${status}")
  CHECK_DETAILS+=("${detail}")
  CHECK_MS+=("${ms}")
  printf "  [%-4s] %-36s %-50s (%sms)\n" "${status}" "${name}" "${detail}" "${ms}"
}

now_ms() {
  date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))'
}

# ---------------------------------------------------------------------------
# Prepare sandbox (fixture copy) — sandbox target only
# ---------------------------------------------------------------------------

prepare_sandbox() {
  echo "[prepare] copying fixture -> ${WORKSPACE}"
  # Copy tree, stripping .template and .fixture suffixes.
  (
    cd "${FIXTURE_DIR}"
    find . -type f | while IFS= read -r src; do
      local dest="${WORKSPACE}/${src#./}"
      # Strip .template / .fixture suffix
      case "${dest}" in
        *.template)
          dest="${dest%.template}"
          ;;
        *.fixture)
          dest="${dest%.fixture}"
          ;;
      esac
      # Rename sandbox-settings.local.json -> settings.local.json
      dest="${dest//sandbox-settings.local.json/settings.local.json}"
      mkdir -p "$(dirname "${dest}")"
      cp "${src}" "${dest}"
    done
  )
  echo "[prepare] fixture copied"
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

install_package() {
  cd "${WORKSPACE}"

  # --fresh: wipe install metadata before npm install. Only meaningful in
  # local mode (sandbox dirs are always pristine by construction). Removes
  # node_modules/, package.json, and package-lock.json so npm starts from
  # a clean slate -- avoids stale `dependencies` entries from prior installs
  # (e.g. self-referencing `file:*.tgz` artefacts).
  if [[ "${FRESH}" -eq 1 && "${TARGET}" == "local" ]]; then
    echo "[install] --fresh: wiping node_modules/, package.json, package-lock.json"
    rm -rf \
      "${WORKSPACE}/node_modules" \
      "${WORKSPACE}/package.json" \
      "${WORKSPACE}/package-lock.json"
  fi

  if [[ "${TARGET}" == "sandbox" && ! -f package.json ]]; then
    echo "FATAL: package.json missing after prepare" >&2
    return 1
  fi

  # In local mode, ensure a package.json exists so npm install has an anchor.
  if [[ "${TARGET}" == "local" && ! -f package.json ]]; then
    echo "[install] local mode: no package.json in ${WORKSPACE}, creating minimal one"
    npm init -y --silent >/dev/null 2>&1 || npm init -y
  fi

  if [[ -n "${TARBALL_PATH}" ]]; then
    echo "[install] installing tarball ${TARBALL_PATH}"
    npm install --no-audit --no-fund "${TARBALL_PATH}"
  else
    # Accept forms: "@rc", "5.0.0-rc1", "@5.0.0-rc1",
    # "@jaguilar87/gaia@5.0.0-rc1"
    local spec="${VERSION_SPEC}"
    if [[ "${spec}" == @* && "${spec}" != @jaguilar87/* ]]; then
      # e.g. "@rc" or "@5.0.0-rc1"
      spec="@jaguilar87/gaia${spec}"
    elif [[ "${spec}" != @jaguilar87/* && "${spec}" != "" ]]; then
      # Bare "5.0.0-rc1"
      spec="@jaguilar87/gaia@${spec}"
    fi
    echo "[install] installing ${spec}"
    npm install --no-audit --no-fund "${spec}"
  fi
}

# ---------------------------------------------------------------------------
# Wire the workspace (local mode only)
# ---------------------------------------------------------------------------
#
# There is no npm postinstall hook (bootstrap is lazy, see bin/cli/install.py
# and package.json's `_install_note`): `npm install` alone drops the package
# into node_modules/ but never touches the workspace's .claude/ settings,
# symlinks, or plugin registry. Without this explicit step, `--target local`
# would leave the workspace installed-but-unwired.
wire_local_workspace() {
  echo "[install] wiring workspace: gaia install --workspace ${WORKSPACE}"
  gaia install --workspace "${WORKSPACE}"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"
  fi
}

# ---------------------------------------------------------------------------
# Sandbox DB isolation + fixture seeding (sandbox target only)
# ---------------------------------------------------------------------------
#
# Problem: without GAIA_DATA_DIR, `gaia memory search` and `gaia memory stats`
# hit the global ~/.gaia/gaia.db, which is scoped by the ephemeral sandbox
# workspace-id (directory basename). The sandbox workspace never has episodes
# there, so check 6 (deploy search) returns 0 hits (FAIL) and check 5 counts
# global episodes instead of sandbox-local ones.
#
# Fix: create a sandbox-local data dir, export GAIA_DATA_DIR before running
# any check, run bootstrap to initialize the schema, then seed the episodes
# from the fixture file into the sandbox-local DB with the sandbox workspace-id.
# The episodes_fts FTS5 table is populated automatically via the INSERT trigger.

seed_sandbox_db() {
  local sandbox_data_dir="${WORKSPACE}/.gaia-sandbox"
  mkdir -p "${sandbox_data_dir}"

  # Export GAIA_DATA_DIR so every subsequent gaia CLI call (including installed
  # node_modules/.bin/gaia) resolves db_path() to the sandbox-local DB.
  export GAIA_DATA_DIR="${sandbox_data_dir}"

  local sandbox_db="${sandbox_data_dir}/gaia.db"

  echo "[sandbox-db] initializing sandbox-local DB at ${sandbox_db}"

  # Run bootstrap to apply the full schema (tables, triggers, FTS5 mirrors).
  # We pass GAIA_DB so bootstrap_database.sh writes to the sandbox DB.
  # WORKSPACE override points bootstrap at the sandbox dir for project registration.
  local bootstrap_script="${REPO_ROOT}/scripts/bootstrap_database.sh"
  if [[ -f "${bootstrap_script}" ]]; then
    GAIA_DB="${sandbox_db}" WORKSPACE="${WORKSPACE}" \
      bash "${bootstrap_script}" >/dev/null
  else
    # Fallback: create the schema directly from the installed package's schema.sql
    local schema_sql="${WORKSPACE}/node_modules/@jaguilar87/gaia/gaia/store/schema.sql"
    if [[ -f "${schema_sql}" ]]; then
      sqlite3 "${sandbox_db}" < "${schema_sql}"
    else
      echo "[sandbox-db] WARN: schema.sql not found; memory checks may fail" >&2
      return 0
    fi
  fi

  # Determine sandbox workspace_id: the directory basename (no git remote in
  # an ephemeral /tmp dir, so gaia.project.current() falls back to basename).
  local sandbox_ws_id
  sandbox_ws_id="$(basename "${WORKSPACE}")"

  # Ensure the workspace row exists (FK required by episodes).
  sqlite3 "${sandbox_db}" \
    "INSERT OR IGNORE INTO workspaces(name, status) VALUES('${sandbox_ws_id}', 'active');"

  # Seed episodes from the fixture's episodes.jsonl into the sandbox DB.
  # Each JSONL line is a complete episode object. We extract the fields that
  # match the episodes table columns and INSERT them. The episodes_fts FTS5
  # table is populated automatically by the AFTER INSERT trigger.
  local episodes_jsonl="${FIXTURE_DIR}/.claude/project-context/episodic-memory/episodes.jsonl"
  if [[ -f "${episodes_jsonl}" ]]; then
    local seeded=0
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      python3 - "${sandbox_db}" "${sandbox_ws_id}" "${line}" <<'PYEOF'
import json, sqlite3, sys

db_path, workspace_id, line = sys.argv[1], sys.argv[2], sys.argv[3]
ep = json.loads(line)

con = sqlite3.connect(db_path)
try:
    con.execute(
        "INSERT OR IGNORE INTO episodes("
        "  episode_id, workspace, timestamp, session_id, task_id,"
        "  agent, type, title, prompt, enriched_prompt,"
        "  keywords, tags, relevance_score, outcome,"
        "  exit_code, plan_status, output_length"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ep.get("episode_id", ""),
            workspace_id,
            ep.get("timestamp", ""),
            ep.get("session_id"),
            ep.get("task_id"),
            ep.get("agent"),
            ep.get("type"),
            ep.get("title"),
            ep.get("prompt"),
            ep.get("enriched_prompt"),
            json.dumps(ep.get("keywords", [])),
            json.dumps(ep.get("tags", [])),
            ep.get("relevance_score"),
            ep.get("outcome"),
            ep.get("exit_code"),
            ep.get("plan_status"),
            ep.get("output_length"),
        ),
    )
    con.commit()
finally:
    con.close()
PYEOF
      seeded=$(( seeded + 1 ))
    done < "${episodes_jsonl}"
    echo "[sandbox-db] seeded ${seeded} episodes (workspace=${sandbox_ws_id})"
  else
    echo "[sandbox-db] WARN: episodes.jsonl not found at ${episodes_jsonl}" >&2
  fi
}

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

if [[ "${TARGET}" == "sandbox" ]]; then
  prepare_sandbox
fi

SETTINGS_FILE="${WORKSPACE}/.claude/settings.local.json"
PRE_CHECKSUM=""
if [[ "${TARGET}" == "sandbox" && -f "${SETTINGS_FILE}" ]]; then
  PRE_CHECKSUM="$(sha256 "${SETTINGS_FILE}")"
fi

install_package

# Put installed bin at head of PATH so we can call `gaia` directly
# (runtime check: node-independent invocation, no npx indirection).
export PATH="${WORKSPACE}/node_modules/.bin:${PATH}"

# Local: no postinstall hook wires the workspace, so do it explicitly here
# (see wire_local_workspace() above).
if [[ "${TARGET}" == "local" ]]; then
  wire_local_workspace
fi

# Sandbox: isolate the DB from the global ~/.gaia/gaia.db and seed it
# with fixture episodes so checks 5 (FTS5 stats) and 6 (deploy search)
# validate against sandbox-local data, not the user's global state.
if [[ "${TARGET}" == "sandbox" ]]; then
  seed_sandbox_db
fi

echo
echo "=== Running checks ==="

cd "${WORKSPACE}"

# 1. gaia --version
t0="$(now_ms)"
if out="$(gaia --version 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  if grep -qE 'gaia [0-9]+\.[0-9]+\.[0-9]+' <<<"${out}"; then
    record "gaia --version" "PASS" "$(echo "${out}" | head -1)" "${ms}"
  else
    record "gaia --version" "FAIL" "unexpected output: ${out:0:60}" "${ms}"
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia --version" "FAIL" "command failed: ${out:0:60}" "${ms}"
fi

# 2. gaia doctor --json (parse and check status)
t0="$(now_ms)"
if out="$(gaia doctor --json 2>&1)"; then
  rc=0
else
  rc=$?
fi
ms=$(( $(now_ms) - t0 ))
# The harness gate must not pass a degraded doctor: a non-zero rc means at
# least one check is warning/error, so treat rc>=1 as a hard FAILURE. Only a
# clean rc=0 (all checks pass/info) with parseable JSON counts as PASS.
if [[ "${rc}" -ne 0 ]]; then
  nonpass="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(', '.join(f\"{r['name']}={r['severity']}\" for r in d['checks'] if r['severity'] not in ('pass','info')))" "${out}" 2>/dev/null || echo "unparseable output")"
  record "gaia doctor --json" "FAIL" "doctor returned rc=${rc}; non-pass: ${nonpass:-none}" "${ms}"
elif python3 -c "import json,sys; d=json.loads(sys.argv[1]); c=d['checks']; t=len(c); sys.exit(0 if t>=5 else 1)" "${out}" 2>/dev/null; then
  total=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(len(d['checks']))" "${out}")
  passed=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(sum(1 for r in d['checks'] if r['severity']=='pass'))" "${out}")
  record "gaia doctor --json" "PASS" "rc=0, ${passed}/${total} checks passed" "${ms}"
else
  record "gaia doctor --json" "FAIL" "parse/threshold failure (rc=${rc})" "${ms}"
fi

# 3. gaia status --json
t0="$(now_ms)"
if out="$(gaia status --json 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  if python3 -c "import json,sys; json.loads(sys.argv[1])" "${out}" 2>/dev/null; then
    record "gaia status --json" "PASS" "json parsed" "${ms}"
  else
    record "gaia status --json" "FAIL" "invalid json: ${out:0:60}" "${ms}"
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia status --json" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 4. gaia context show
t0="$(now_ms)"
if out="$(gaia context show 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  record "gaia context show" "PASS" "exit 0" "${ms}"
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia context show" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 5. gaia memory stats --json (verify FTS5 backfill triggered)
t0="$(now_ms)"
if out="$(gaia memory stats --json 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  indexed=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('indexed',0))" "${out}" 2>/dev/null || echo 0)
  total=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('total_episodes',0))" "${out}" 2>/dev/null || echo 0)
  if [[ "${TARGET}" == "sandbox" ]]; then
    if [[ "${indexed}" -ge 9 ]]; then
      record "memory stats (FTS5 backfill)" "PASS" "indexed=${indexed}/${total}" "${ms}"
    else
      record "memory stats (FTS5 backfill)" "FAIL" "indexed=${indexed}/${total} (need >=9)" "${ms}"
    fi
  else
    # Local mode: workspace memory state is real and arbitrary.
    # total_episodes reflects only the rolling index.json window, so comparing
    # indexed (FTS5 count across all of search.db) against it is meaningless.
    # Instead count episode-*.json files on disk — the true source of truth —
    # and accept if FTS5 has indexed at least 80% of them (allows minor lag).
    json_files=$(find "${WORKSPACE}/.claude" -name "episode-*.json" -type f 2>/dev/null | wc -l)
    threshold=$(( json_files * 8 / 10 ))
    if [[ "${indexed}" -ge "${threshold}" ]]; then
      record "memory stats (FTS5 backfill)" "PASS" "indexed=${indexed} / json_files=${json_files}" "${ms}"
    else
      record "memory stats (FTS5 backfill)" "FAIL" "indexed=${indexed} / json_files=${json_files} (below 80% threshold=${threshold})" "${ms}"
    fi
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "memory stats (FTS5 backfill)" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 6. gaia memory search "deploy" --limit 3 --json --scope=episodes
# Use --scope=episodes so the JSON output carries a top-level "results" key
# ({"scope":"episodes","results":[...]}) that the hit-count extractor below
# can parse. The default scope "both" produces {"episodes":[...],"curated":[]}
# which the extractor would misread as 0 hits even when episodes are present.
t0="$(now_ms)"
if out="$(gaia memory search deploy --limit 3 --json --scope=episodes 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  hits=$(python3 -c "
import json,sys
try:
    d=json.loads(sys.argv[1])
    if isinstance(d,list):
        print(len(d))
    elif isinstance(d,dict):
        for k in ('results','hits','matches'):
            if k in d:
                print(len(d[k])); sys.exit(0)
        print(0)
    else:
        print(0)
except Exception:
    print(0)
" "${out}" 2>/dev/null || echo 0)
  if [[ "${TARGET}" == "sandbox" ]]; then
    if [[ "${hits}" -ge 1 ]]; then
      record "memory search deploy" "PASS" "${hits} hit(s)" "${ms}"
    else
      record "memory search deploy" "FAIL" "0 hits (expected >=1)" "${ms}"
    fi
  else
    # Local mode: "deploy" is not guaranteed to appear in the real
    # workspace memory. Only assert the command ran successfully.
    record "memory search deploy" "PASS" "${hits} hit(s) (local)" "${ms}"
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "memory search deploy" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 7. gaia scan (exit 0)
# `gaia context scan --dry-run` validates freshness without running the
# scanners or writing project-context.json.
t0="$(now_ms)"
if out="$(gaia context scan --dry-run 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  record "gaia scan" "PASS" "scanner ran (dry-run)" "${ms}"
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia scan" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 8. Checksum preservation: settings.local.json unchanged by `npm install`
#    (no postinstall hook exists to touch it -- this asserts that stays true).
#    Sandbox only -- local mode has no meaningful "pre" snapshot.
if [[ "${TARGET}" == "sandbox" ]]; then
  if [[ -n "${PRE_CHECKSUM}" && -f "${SETTINGS_FILE}" ]]; then
    POST_CHECKSUM="$(sha256 "${SETTINGS_FILE}")"
    t0="$(now_ms)"
    if python3 -c "
import json,sys
p=json.load(open(sys.argv[1]))
assert p.get('_sandbox_sentinel')=='DO_NOT_TOUCH_ME', 'sentinel clobbered'
assert p.get('env',{}).get('SANDBOX_FIXTURE_MARKER')=='preserved-across-install', 'env marker clobbered'
" "${SETTINGS_FILE}" 2>/dev/null; then
      ms=$(( $(now_ms) - t0 ))
      record "settings preservation" "PASS" "sentinel + env markers intact" "${ms}"
    else
      ms=$(( $(now_ms) - t0 ))
      record "settings preservation" "FAIL" "user keys clobbered during npm install" "${ms}"
    fi
  else
    record "settings preservation" "FAIL" "settings.local.json missing pre or post" "0"
  fi
else
  record "settings preservation" "SKIP" "local mode (no pre-snapshot)" "0"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL_END_MS="$(now_ms)"
TOTAL_MS=$((TOTAL_END_MS - TOTAL_START_MS))

echo
echo "=== Summary ==="
pass_count=0
fail_count=0
skip_count=0
for status in "${CHECK_STATUS[@]}"; do
  case "${status}" in
    PASS) pass_count=$((pass_count + 1)) ;;
    SKIP) skip_count=$((skip_count + 1)) ;;
    *)    fail_count=$((fail_count + 1)) ;;
  esac
done

echo "  Passed:  ${pass_count}"
echo "  Failed:  ${fail_count}"
echo "  Skipped: ${skip_count}"
echo "  Total time: ${TOTAL_MS}ms"
echo

if [[ "${fail_count}" -gt 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
fi

echo "RESULT: PASS"

if [[ "${TARGET}" == "local" ]]; then
  echo
  echo "Gaia fresh install complete in ${WORKSPACE}. Restart Claude Code to reload skills/hooks/agents."
fi

exit 0

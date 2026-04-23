#!/usr/bin/env bash
# validate-sandbox.sh -- end-to-end consumer-install verification harness.
#
# Creates an ephemeral sandbox project populated from
# tests/fixtures/sandbox-project/, installs the target Gaia version, and
# exercises the install-time code paths (postinstall hook merge, FTS5
# backfill safety-net) plus read-side CLI surface (version, doctor,
# status, context show, memory stats/search, scan).
#
# Exit 0 when every check passes; 1 otherwise. `--stay` keeps the sandbox
# dir for post-mortem inspection (path printed on exit).

set -euo pipefail

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

VERSION_SPEC=""
TARBALL_PATH=""
STAY=0

usage() {
  cat <<'EOF'
Usage:
  bin/validate-sandbox.sh [--version <spec>] [--tarball <path>] [--stay]

Options:
  --version <spec>   npm version specifier, e.g. "@rc", "@5.0.0-rc1",
                     "@jaguilar87/gaia@5.0.0-rc1". Default: "@rc".
  --tarball <path>   Install from a local tarball (from `npm pack`).
                     Takes precedence over --version.
  --stay             Do NOT clean up the sandbox dir on exit. Useful for
                     debugging; sandbox path is printed on exit.
  --help, -h         Print this help and exit.
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

# Default to @rc when no install source given.
if [[ -z "${TARBALL_PATH}" && -z "${VERSION_SPEC}" ]]; then
  VERSION_SPEC="@rc"
fi

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/sandbox-project"

if [[ ! -d "${FIXTURE_DIR}" ]]; then
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

SANDBOX="/tmp/gaia-sandbox-$(date +%s)-$$"
mkdir -p "${SANDBOX}"

# ---------------------------------------------------------------------------
# Cleanup trap
# ---------------------------------------------------------------------------

cleanup() {
  local rc=$?
  if [[ "${STAY}" -eq 1 ]]; then
    echo
    echo "Sandbox preserved at: ${SANDBOX}"
    echo "Remove manually when done: rm -rf '${SANDBOX}'"
  else
    rm -rf "${SANDBOX}" 2>/dev/null || true
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
# Prepare sandbox
# ---------------------------------------------------------------------------

prepare_sandbox() {
  echo "[prepare] copying fixture -> ${SANDBOX}"
  # Copy tree, stripping .template and .fixture suffixes.
  (
    cd "${FIXTURE_DIR}"
    find . -type f | while IFS= read -r src; do
      local dest="${SANDBOX}/${src#./}"
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
  cd "${SANDBOX}"

  if [[ ! -f package.json ]]; then
    echo "FATAL: package.json missing after prepare" >&2
    return 1
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
# Helpers
# ---------------------------------------------------------------------------

run_step() {
  # run_step NAME EXPECTED_REGEX CMD [ARG...]
  local name="$1"; shift
  local expected="$1"; shift
  local t0 t1 out rc
  t0="$(now_ms)"
  if out="$("$@" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  t1="$(now_ms)"
  local ms=$((t1 - t0))

  if [[ "${rc}" -ne 0 ]]; then
    record "${name}" "FAIL" "exit=${rc}: ${out:0:60}" "${ms}"
    return 1
  fi
  if [[ -n "${expected}" ]]; then
    if ! grep -qE "${expected}" <<<"${out}"; then
      record "${name}" "FAIL" "regex '${expected}' not matched" "${ms}"
      return 1
    fi
  fi
  record "${name}" "PASS" "ok" "${ms}"
  return 0
}

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
# Execute
# ---------------------------------------------------------------------------

prepare_sandbox

SETTINGS_FILE="${SANDBOX}/.claude/settings.local.json"
PRE_CHECKSUM=""
if [[ -f "${SETTINGS_FILE}" ]]; then
  PRE_CHECKSUM="$(sha256 "${SETTINGS_FILE}")"
fi

install_package

echo
echo "=== Running checks ==="

cd "${SANDBOX}"

# 1. gaia --version
t0="$(now_ms)"
if out="$(npx --no-install gaia --version 2>&1)"; then
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
if out="$(npx --no-install gaia doctor --json 2>&1)"; then
  rc=0
else
  rc=$?
fi
ms=$(( $(now_ms) - t0 ))
# doctor may exit 1 on warnings; allow rc=0 or rc=1 if json is parseable
if python3 -c "import json,sys; d=json.loads(sys.argv[1]); c=d['checks']; p=sum(1 for r in c if r['severity']=='pass'); t=len(c); sys.exit(0 if t>=5 and p>=max(1,t-3) else 1)" "${out}" 2>/dev/null; then
  total=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(len(d['checks']))" "${out}")
  passed=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(sum(1 for r in d['checks'] if r['severity']=='pass'))" "${out}")
  record "gaia doctor --json" "PASS" "${passed}/${total} checks passed" "${ms}"
else
  record "gaia doctor --json" "FAIL" "parse/threshold failure (rc=${rc})" "${ms}"
fi

# 3. gaia status --json
t0="$(now_ms)"
if out="$(npx --no-install gaia status --json 2>&1)"; then
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
if out="$(npx --no-install gaia context show 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  record "gaia context show" "PASS" "exit 0" "${ms}"
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia context show" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 5. gaia memory stats --json (verify FTS5 backfill triggered)
t0="$(now_ms)"
if out="$(npx --no-install gaia memory stats --json 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  indexed=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('indexed',0))" "${out}" 2>/dev/null || echo 0)
  total=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('total_episodes',0))" "${out}" 2>/dev/null || echo 0)
  if [[ "${indexed}" -ge 9 ]]; then
    record "memory stats (FTS5 backfill)" "PASS" "indexed=${indexed}/${total}" "${ms}"
  else
    record "memory stats (FTS5 backfill)" "FAIL" "indexed=${indexed}/${total} (need >=9)" "${ms}"
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "memory stats (FTS5 backfill)" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 6. gaia memory search "deploy" --limit 3 --json
t0="$(now_ms)"
if out="$(npx --no-install gaia memory search deploy --limit 3 --json 2>&1)"; then
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
  if [[ "${hits}" -ge 1 ]]; then
    record "memory search deploy" "PASS" "${hits} hit(s)" "${ms}"
  else
    record "memory search deploy" "FAIL" "0 hits (expected >=1)" "${ms}"
  fi
else
  ms=$(( $(now_ms) - t0 ))
  record "memory search deploy" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 7. gaia scan (exit 0)
t0="$(now_ms)"
if out="$(npx --no-install gaia-scan --dry-run 2>&1 || npx --no-install gaia context scan --dry-run 2>&1)"; then
  ms=$(( $(now_ms) - t0 ))
  record "gaia scan" "PASS" "scanner ran" "${ms}"
else
  ms=$(( $(now_ms) - t0 ))
  record "gaia scan" "FAIL" "exit non-zero: ${out:0:60}" "${ms}"
fi

# 8. Checksum preservation: settings.local.json unchanged by postinstall
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
    record "settings preservation" "FAIL" "user keys clobbered by postinstall" "${ms}"
  fi
else
  record "settings preservation" "FAIL" "settings.local.json missing pre or post" "0"
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
for status in "${CHECK_STATUS[@]}"; do
  if [[ "${status}" == "PASS" ]]; then
    pass_count=$((pass_count + 1))
  else
    fail_count=$((fail_count + 1))
  fi
done

echo "  Passed: ${pass_count}"
echo "  Failed: ${fail_count}"
echo "  Total time: ${TOTAL_MS}ms"
echo

if [[ "${fail_count}" -gt 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
fi

echo "RESULT: PASS"
exit 0

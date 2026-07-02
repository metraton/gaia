#!/usr/bin/env bash
# plugin-dryrun.sh -- prove the PLUGIN surface of the exact npm tarball, headless,
# in throwaway temp dirs. This is the Layer-2 plugin dry-run for the `source: npm`
# delivery model: with no dist/ bundle, the published package ROOT *is* the plugin,
# so we validate what actually ships by packing, extracting, and inspecting the
# tarball root.
#
# Two problems this deliberately avoids:
#   1. Polluting a real workspace. Everything lives in mktemp dirs (the extracted
#      plugin AND the cwd for any `claude` run), removed by a trap on EXIT. No real
#      project's .claude/ is ever touched.
#   2. Nested interactive sessions. The DEFAULT gate is deterministic and offline:
#      filesystem asserts + `claude plugin validate` (a non-interactive validator).
#      The `claude --plugin-dir ... -p '...'` functional probe (print mode, needs
#      Claude auth + tokens, and is a nested Claude invocation) is OPT-IN via
#      --functional, so CI / an orchestrated agent never spawns it implicitly.
#
# Usage:
#   bash bin/plugin-dryrun.sh              # structural + `claude plugin validate`
#   bash bin/plugin-dryrun.sh --functional # also run a headless `claude -p` probe
#
# Exit 0 when every check passes; 1 otherwise.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FUNCTIONAL=0
if [[ "${1:-}" == "--functional" ]]; then
  FUNCTIONAL=1
fi

# ---------------------------------------------------------------------------
# (a) Pack -> prepack regenerates the root inline plugin.json + hooks/hooks.json
# ---------------------------------------------------------------------------
cd "${REPO_ROOT}"
echo "[dryrun] packing tarball (prepack regenerates root manifests)..."
TARBALL_NAME="$(npm pack --silent)"
TARBALL="${REPO_ROOT}/${TARBALL_NAME}"
if [[ ! -f "${TARBALL}" ]]; then
  echo "FATAL: npm pack did not produce a tarball" >&2
  exit 1
fi
echo "[dryrun] packed ${TARBALL_NAME}"

# ---------------------------------------------------------------------------
# (b) Extract to a disposable temp dir; npm tarballs unpack under package/
# ---------------------------------------------------------------------------
PLUGIN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gaia-plugin-dryrun-XXXXXX")"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gaia-plugin-work-XXXXXX")"

cleanup() {
  rm -rf "${PLUGIN_DIR}" "${WORK_DIR}" "${TARBALL}" 2>/dev/null || true
}
trap cleanup EXIT

tar -xzf "${TARBALL}" -C "${PLUGIN_DIR}"
ROOT="${PLUGIN_DIR}/package"
if [[ ! -d "${ROOT}" ]]; then
  echo "FATAL: extracted tarball has no package/ dir at ${ROOT}" >&2
  exit 1
fi
echo "[dryrun] extracted plugin root -> ${ROOT}"

# ---------------------------------------------------------------------------
# (c) Headless structural gate -- deterministic, offline, no auth
# ---------------------------------------------------------------------------
echo
echo "=== Structural checks ==="
fail=0

assert_file() {
  if [[ -f "$1" ]]; then
    echo "  [PASS] $2"
  else
    echo "  [FAIL] $2 (missing: $1)"
    fail=1
  fi
}
assert_dir() {
  if [[ -d "$1" ]]; then
    echo "  [PASS] $2"
  else
    echo "  [FAIL] $2 (missing: $1)"
    fail=1
  fi
}

assert_file "${ROOT}/.claude-plugin/plugin.json" "root .claude-plugin/plugin.json present"
assert_file "${ROOT}/hooks/hooks.json"           "root hooks/hooks.json present"
assert_file "${ROOT}/bin/gaia"                   "bin/gaia present"
assert_dir  "${ROOT}/agents"                     "agents/ present"
assert_dir  "${ROOT}/skills"                     "skills/ present"

# The tarball MUST NOT ship a dist/ bundle anymore.
if [[ -d "${ROOT}/dist" ]]; then
  echo "  [FAIL] tarball still ships dist/ (should be excluded from files[])"
  fail=1
else
  echo "  [PASS] no dist/ in tarball (package root is the plugin)"
fi

# The inline-hooks workaround MUST be present, and every referenced entry-point
# file must exist under the plugin root.
if python3 - "${ROOT}/.claude-plugin/plugin.json" "${ROOT}" <<'PY'
import json, os, re, sys

plugin_json, root = sys.argv[1], sys.argv[2]
data = json.load(open(plugin_json))
hooks = data.get("hooks")
assert isinstance(hooks, dict) and hooks, "plugin.json is missing a non-empty inline 'hooks' block"

missing = []
for event, entries in hooks.items():
    for entry in entries:
        for h in entry.get("hooks", []):
            m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+\.py)", h.get("command", ""))
            if m and not os.path.isfile(os.path.join(root, m.group(1))):
                missing.append(m.group(1))
assert not missing, f"inline hook entry points not found under plugin root: {sorted(set(missing))}"
print(f"  inline hooks: {len(hooks)} events, all entry points resolve")
PY
then
  echo "  [PASS] inline hooks block valid + entry points resolve"
else
  echo "  [FAIL] inline hooks block invalid or entry points missing"
  fail=1
fi

# Deterministic CC validator (offline, no session) if the CLI is available.
if command -v claude >/dev/null 2>&1; then
  echo
  echo "=== claude plugin validate ==="
  if claude plugin validate "${ROOT}"; then
    echo "  [PASS] claude plugin validate"
  else
    echo "  [FAIL] claude plugin validate"
    fail=1
  fi
else
  echo "  [SKIP] claude CLI not on PATH -- skipping 'claude plugin validate'"
fi

# ---------------------------------------------------------------------------
# (c') OPTIONAL functional probe -- explicit opt-in only
#      Runs from WORK_DIR (a throwaway cwd) so any .claude/ CC might create lands
#      in the temp, never in a real workspace. Needs Claude auth + tokens.
# ---------------------------------------------------------------------------
if [[ "${FUNCTIONAL}" -eq 1 ]]; then
  echo
  echo "=== Functional probe (claude -p, headless) ==="
  if command -v claude >/dev/null 2>&1; then
    ( cd "${WORK_DIR}" && claude --plugin-dir "${ROOT}" \
        -p 'gaia doctor; ¿quién eres?' --output-format json ) \
      && echo "  [PASS] functional probe ran" \
      || { echo "  [FAIL] functional probe"; fail=1; }
  else
    echo "  [SKIP] claude CLI not on PATH -- cannot run functional probe"
  fi
fi

# ---------------------------------------------------------------------------
# (d) Cleanup handled by the EXIT trap.
# ---------------------------------------------------------------------------
echo
if [[ "${fail}" -eq 0 ]]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi

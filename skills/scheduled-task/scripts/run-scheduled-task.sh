#!/usr/bin/env bash
# run-scheduled-task.sh -- wrapper that runs ONE Gaia task headless, unattended.
#
# This is the TEMPLATE a scheduled task is built from (see the scheduled-task
# skill). crontab runs THIS, not `claude` directly, because a cron environment
# is almost empty: no interactive shell profile, minimal PATH, no exported
# credentials. Everything the run needs is exported EXPLICITLY here.
#
# Validated invocation (empirically confirmed, do not "simplify"):
#   claude -p "<prompt>" \
#     --dangerously-skip-permissions \
#     --disallowedTools AskUserQuestion \
#     --output-format json
#
# Why each flag:
#   -p / --print                     headless, non-interactive (one shot).
#   --dangerously-skip-permissions   no TUI permission prompts (there is no TUI).
#                                    Gaia's OWN T3 layer still gates/accumulates
#                                    mutations independently -- this only removes
#                                    Claude Code's interactive dialog, not Gaia's
#                                    consent. Blocked T3s come back with an
#                                    approval_id the task ACCUMULATES.
#   --disallowedTools AskUserQuestion  a headless run must never try to ask the
#                                    user anything; forbidding the tool makes
#                                    "ask" impossible instead of hoping it won't.
#   --output-format json             machine-readable; we parse session_id out.
# NOT passed: --no-session-persistence. The session MUST persist so the user can
#   `claude --resume <session_id>` later to grant accumulated approvals.
#
# Customize the ==CONFIG== block per task, then reference this file from crontab
# (see crontab.template). Keep one wrapper per task so schedules stagger cleanly.
set -euo pipefail

# ===================== CONFIG (edit per task) =========================
TASK_NAME="${TASK_NAME:-example-task}"          # stable name; appears in notifications
PROJECT_DIR="${PROJECT_DIR:-/home/jorge/ws/me}" # cwd the task runs in
PROMPT_FILE="${PROMPT_FILE:-}"                   # optional: read prompt from a file
PROMPT="${PROMPT:-Eres una tarea programada headless. Describe la tarea aqui.}"

# Explicit environment. cron does NOT source your shell profile, so export
# every credential / path the run needs. Prefer a per-task env file over
# inlining secrets; source it here if present.
ENV_FILE="${ENV_FILE:-$HOME/.gaia/scheduled-tasks/${TASK_NAME}.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi
export PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}"
export GAIA_DATA_DIR="${GAIA_DATA_DIR:-$HOME/.gaia}"
# export ANTHROPIC_API_KEY=...   # or rely on your logged-in credentials
# ======================================================================

cd "$PROJECT_DIR"

if [ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ]; then
  PROMPT="$(cat "$PROMPT_FILE")"
fi

# Run headless. Capture the JSON result; do not let a non-zero exit kill the
# wrapper before we can record what happened.
set +e
RESULT_JSON="$(claude -p "$PROMPT" \
  --dangerously-skip-permissions \
  --disallowedTools AskUserQuestion \
  --output-format json)"
CLAUDE_EXIT=$?
set -e

# Extract the resumable session id (best-effort; field name may be session_id).
SESSION_ID="$(printf '%s' "$RESULT_JSON" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get("session_id") or d.get("sessionId") or "")
except Exception:
    print("")')"

# The task itself is responsible for calling `gaia notifications add` as its
# LAST step (see the scheduled-task skill, Flow B). This fallback records a
# minimal notification if the run crashed before it could report, so a failed
# headless run is never silent.
if [ "$CLAUDE_EXIT" -ne 0 ]; then
  gaia notifications add \
    --task "$TASK_NAME" \
    --headline "La tarea $TASK_NAME termino con error (exit $CLAUDE_EXIT)" \
    --body "El run headless salio con codigo $CLAUDE_EXIT antes de reportar. Revisa el log." \
    --session-id "$SESSION_ID" || true
fi

exit "$CLAUDE_EXIT"

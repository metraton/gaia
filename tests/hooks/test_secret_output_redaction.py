"""No secret reaches the host, an approval record or the audit log in clear.

Each case runs the real PreToolUse boundary of both hosts, then executes the
command the hook hands back -- rewritten or not -- against stand-in binaries
on PATH that print known sentinel values, the way the host would run it. A
refused read must not run at all. Every file Gaia wrote under its data
directory (approval rows, hook state, audit lines) is then searched for the
sentinels.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402

SESSION = "ses-secret-output"
AGENT_ID = "a" + "6" * 16
AGENT_TYPE = "developer"

KUBE_SENTINEL = "sentinel-kube-" + "4f1c9e"
STORE_SENTINEL = "sentinel-store-" + "8b2d07"
USER_SENTINEL = "sentinel-user-" + "31aa5c"
REPO_SENTINEL = "sentinel-repo-" + "c0ffee"
BEARER_SENTINEL = "sentinel-bearer-" + "9d4e2b"
CLOUD_SENTINEL = "sentinel-cloud-" + "5e7a13"
SENTINELS = (
    KUBE_SENTINEL, STORE_SENTINEL, USER_SENTINEL, REPO_SENTINEL, BEARER_SENTINEL, CLOUD_SENTINEL,
)

FAKE_GCLOUD = f"""#!/usr/bin/env bash
case "$*" in
  *--secret=json*) printf '{{"user": "app", "password": "{CLOUD_SENTINEL}"}}\\n' ;;
  *) printf '{CLOUD_SENTINEL}' ;;
esac
exit "${{FAKE_EXIT:-0}}"
"""

FAKE_AWS = f"""#!/usr/bin/env bash
case "$*" in
  *secretsmanager*)
    printf '{{"Name": "app", "SecretString": "{{\\\\"password\\\\": \\\\"{CLOUD_SENTINEL}\\\\"}}"}}\\n' ;;
  *get-parameters*)
    printf '{{"Parameters": [{{"Name": "/app/db", "Type": "SecureString", "Value": "{CLOUD_SENTINEL}"}}]}}\\n' ;;
  *)
    printf '{{"Parameter": {{"Name": "/app/db", "Type": "SecureString", "Value": "{CLOUD_SENTINEL}"}}}}\\n' ;;
esac
exit "${{FAKE_EXIT:-0}}"
"""

FAKE_SOPS = f"""#!/usr/bin/env bash
case "$*" in
  *.json*) printf '{{"db": {{"password": "{CLOUD_SENTINEL}"}}}}\\n' ;;
  *) printf 'db:\\n  password: {CLOUD_SENTINEL}\\n' ;;
esac
exit "${{FAKE_EXIT:-0}}"
"""

FAKE_KUBECTL = f"""#!/usr/bin/env bash
case "$*" in
  *describe*deploy*)
    printf 'Name: api\\n  Containers:\\n   api:\\n    Environment:\\n      DB_PASSWORD:  {KUBE_SENTINEL}\\n' ;;
  *configmap*-o\\ json*|*cm*-o\\ json*)
    printf '{{"kind": "ConfigMap", "data": {{"DB_PASSWORD": "{KUBE_SENTINEL}", "mode": "on"}}}}\\n' ;;
  *secret*)
    printf 'kind: Secret\\ndata:\\n  password: {KUBE_SENTINEL}\\n' ;;
  *)
    printf 'kind: Deployment\\nspec:\\n  template:\\n    spec:\\n      containers:\\n      - env:\\n        - name: DB_PASSWORD\\n          value: {KUBE_SENTINEL}\\n        - name: API_TOKEN\\n          value: "{KUBE_SENTINEL}"\\n' ;;
esac
echo "warning for stderr" 1>&2
exit "${{FAKE_EXIT:-0}}"
"""

FAKE_STORE = f"""#!/usr/bin/env bash
case "$*" in
  *-format=json*)
    printf '{{"data": {{"data": {{"password": "{STORE_SENTINEL}", "username": "{USER_SENTINEL}"}}}}}}\\n' ;;
  *-field=*)
    printf '{STORE_SENTINEL}\\n' ;;
  *)
    printf '====== Data ======\\nKey         Value\\n---         -----\\npassword    {STORE_SENTINEL}\\nusername    {USER_SENTINEL}\\n' ;;
esac
exit "${{FAKE_EXIT:-0}}"
"""


FAKE_WATCH = """#!/usr/bin/env bash
while [[ "$1" == -* ]]; do [[ "$1" == -n ]] && shift; shift; done
exec bash -c "$*"
"""


FAKE_SCRIPT = """#!/usr/bin/env bash
while [[ "$1" == -* ]]; do
  case "$1" in -*c|--command) exec bash -c "$2" ;; esac
  shift
done
"""


@pytest.fixture()
def world(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (
        ("kubectl", FAKE_KUBECTL), ("vault", FAKE_STORE), ("bao", FAKE_STORE),
        ("gcloud", FAKE_GCLOUD), ("aws", FAKE_AWS), ("sops", FAKE_SOPS), ("watch", FAKE_WATCH),
        ("script", FAKE_SCRIPT),
    ):
        binary = fake_bin / name
        binary.write_text(body, encoding="utf-8")
        binary.chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "settings.py").write_text(f'API_TOKEN = "{REPO_SENTINEL}"\n', encoding="utf-8")
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    return {"bin": fake_bin, "repo": repo, "data": data_dir}


def _claude(command: str) -> dict:
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": "toolu_secret_output",
        "cwd": "/",
        "agent_id": AGENT_ID,
        "agent_type": AGENT_TYPE,
    })))
    if isinstance(response.output, str):
        return {"decision": "deny", "run": None, "reason": response.output}
    specific = response.output.get("hookSpecificOutput", {})
    decision = specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny")
    run = specific.get("updatedInput", {}).get("command", command)
    return {
        "decision": decision,
        "run": run if decision == "allow" else None,
        "reason": specific.get("permissionDecisionReason", ""),
    }


def _opencode(command: str) -> dict:
    adapter = OpenCodeAdapter()
    output = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": SESSION,
        "callID": "call-secret-output",
        "agentID": AGENT_ID,
        "roleContext": {
            "role": AGENT_TYPE,
            "issuer": "opencode-runtime",
            "attestation": f"{SESSION}:{AGENT_TYPE}",
            "verified": True,
        },
        "tool": "bash",
        "args": {"command": command},
    }))).output
    decision = output.get("action")
    run = (output.get("updated_input") or {}).get("command", command)
    return {
        "decision": decision,
        "run": run if decision == "allow" else None,
        "reason": output.get("reason", ""),
    }


HOSTS = {"claude": _claude, "opencode": _opencode}


def _execute(world, command: str, fake_exit: int) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{world['bin']}{os.pathsep}{os.environ['PATH']}",
        "FAKE_EXIT": str(fake_exit),
    }
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=env)


def _stored_sentinels(world) -> list[str]:
    found = []
    for path in world["data"].rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            found += [f"{path.name}:{s}" for s in SENTINELS if s.encode() in content]
    return found


REDACTED_READS = [
    "kubectl get deployment api -n prod -o yaml",
    "kubectl describe deployment api",
    "kubectl get configmap app -o json",
    "kubectl get cm,deploy -A -o yaml",
    "vault kv get secret/app",
    "bao read secret/app",
    "bao kv get -format=json secret/app",
    "vault kv get -field=password secret/app",
    "grep -rn TOKEN {repo}",
    "gcloud secrets versions access latest --secret=db-password --project prod",
    "gcloud --project prod secrets versions access 3 --secret=json-config",
    "gcloud beta secrets versions access latest --secret db-password",
    "aws secretsmanager get-secret-value --secret-id app",
    "aws --profile prod ssm get-parameter --name /app/db --with-decryption",
    "aws ssm get-parameters --names /app/db --with-decryption --output json",
    "sops -d app.enc.yaml",
    "sops --decrypt app.enc.json",
    "sops decrypt app.enc.yaml",
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("fake_exit", [3])
@pytest.mark.parametrize("read", REDACTED_READS)
def test_secret_bearing_read_output_is_redacted_and_keeps_its_exit_code(world, host, read, fake_exit):
    command = read.format(repo=world["repo"])
    verdict = HOSTS[host](command)
    assert verdict["decision"] == "allow", verdict

    result = _execute(world, verdict["run"], fake_exit)

    shown = result.stdout + result.stderr
    assert shown.strip(), "the redacted read printed nothing"
    assert not [s for s in SENTINELS if s in shown], shown
    expected_exit = 0 if read.startswith("grep") else fake_exit
    assert result.returncode == expected_exit
    assert _stored_sentinels(world) == []


REFUSED_READS = [
    "kubectl get -A secrets -o yaml",
    "kubectl get secrets,configmaps -n prod -o yaml",
    "kubectl get secret db -o jsonpath={.data.password}",
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("command", REFUSED_READS)
def test_secret_values_read_that_cannot_be_redacted_is_refused(world, host, command):
    verdict = HOSTS[host](command)

    assert verdict["decision"] == "deny", verdict
    assert _stored_sentinels(world) == []


# Each wrapping of a read the redactor already knows, with the exit code the
# whole command line ends in: None is the read's own, xargs reports 123 for a
# failing command, and a substitution inside echo never fails the echo.
REDACTED_SPELLINGS = [
    ("echo `sops -d app.enc.yaml`", 0),
    ('echo "$(sops -d app.enc.yaml)"', 0),
    ('echo "`gcloud secrets versions access latest --secret=db-password`"', 0),
    ('echo "$(aws secretsmanager get-secret-value --secret-id app)"', 0),
    ("cat <(sops -d app.enc.yaml)", 0),
    ("command sops -d app.enc.yaml", None),
    ("exec sops -d app.enc.yaml", None),
    ("command kubectl get deployment api -o yaml", None),
    ("exec vault kv get secret/app", None),
    ("echo secret/app | xargs vault kv get", 123),
    ("echo app.enc.yaml | xargs -I{} sops -d {}", 123),
    ("watch -n 5 kubectl get cm app -o yaml", None),
    ("env sops -d app.enc.yaml", None),
    ("env FOO=1 vault kv get secret/app", None),
    ("nice -n 5 aws secretsmanager get-secret-value --secret-id app", None),
    ("timeout 10 gcloud secrets versions access latest --secret=db-password", None),
    ("command kubectl describe secret db", None),
    ("echo 'sops -d app.enc.yaml' | bash", None),
    ("bash <<'EOF'\nsops -d app.enc.yaml\nEOF", None),
    ("script -qc 'sops -d app.enc.yaml' /dev/null", None),
    ("script --command 'sops -d app.enc.yaml' /dev/null", None),
    ("flock REPO/.lock sops -d app.enc.yaml", None),
    ("flock -w 5 REPO/.lock -c 'sops -d app.enc.yaml'", None),
    ("env -S 'sops -d app.enc.yaml'", None),
    ("nice -n 5 env -S 'sops -d app.enc.yaml'", None),
    ("aws secretsmanager batch-get-secret-value --secret-id-list a b", None),
    ("aws ssm get-parameter-history --name /app/db --with-decryption", None),
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("read,expected_exit", REDACTED_SPELLINGS)
def test_a_wrapped_secret_read_is_redacted_whatever_the_wrapping(world, host, read, expected_exit):
    verdict = HOSTS[host](read.replace("REPO", str(world["repo"])))
    assert verdict["decision"] == "allow", verdict

    result = _execute(world, verdict["run"], 3)

    shown = result.stdout + result.stderr
    assert shown.strip(), "the redacted read printed nothing"
    assert not [s for s in SENTINELS if s in shown], shown
    assert result.returncode == (3 if expected_exit is None else expected_exit)
    assert _stored_sentinels(world) == []


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize(
    "command",
    [
        "sops exec-env app.enc.yaml env",
        "sops exec-file app.enc.yaml 'cat {}'",
        "find REPO -name settings.py -exec sops -d {} \\;",
    ],
)
def test_a_granted_t3_wrapping_of_a_secret_read_runs_under_the_redactor(world, host, command, monkeypatch):
    """These are T3 and the rewrite follows the grant, so the grant is stood in for."""
    from modules.security.tiers import SecurityTier
    from modules.tools import bash_validator

    monkeypatch.setattr(
        bash_validator.BashValidator, "validate",
        lambda self, cmd, **_: bash_validator.BashValidationResult(
            allowed=True, tier=SecurityTier.T3_BLOCKED, reason="granted",
        ),
    )
    verdict = HOSTS[host](command.replace("REPO", str(world["repo"])))
    assert verdict["decision"] == "allow", verdict

    result = _execute(world, verdict["run"], 0)

    shown = result.stdout + result.stderr
    assert shown.strip() and CLOUD_SENTINEL not in shown, shown


REFUSED_SPELLINGS = [
    "env kubectl get secret db -o jsonpath={.data.password}",
    "nice -n 5 kubectl get secret db -o yaml",
    "timeout 5 kubectl get secret db -o json",
    "command kubectl get secret db -o yaml",
    "exec kubectl get secret db -o yaml",
    "xargs kubectl get secret -o yaml",
    "watch kubectl get secret db -o yaml",
    'echo "$(kubectl get secret db -o jsonpath={.data.password})"',
    "echo `kubectl get secret db -o jsonpath={.data.password}`",
    "find . -name x -exec kubectl get secret db -o json {} \\;",
    "echo 'kubectl get secret db -o yaml' | bash",
    "kubectl get secret db -o go-template-file=/tmp/t",
    "kubectl get secret db -o jsonpath-file=/tmp/t",
    "kubectl get secret db -o templatefile=/tmp/t",
    "kubectl get secret db -o custom-columns-file=/tmp/t",
    "script -qc 'kubectl get secret db -o yaml' /dev/null",
    "flock /tmp/gaia.lock kubectl get secret db -o yaml",
    "env -S 'kubectl get secret db -o yaml'",
    "kubectl get --raw /api/v1/namespaces/x/secrets/db",
    "bash -c 'sops -d app.enc.yaml'",
    "sh -c 'gcloud secrets versions access latest --secret=db-password'",
    "eval 'aws secretsmanager get-secret-value --secret-id app'",
    "env bash -c 'kubectl get secret db -o yaml'",
    "timeout 5 bash -c 'vault kv get secret/app'",
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("command", REFUSED_SPELLINGS)
def test_a_wrapped_secret_read_that_cannot_be_redacted_is_refused(world, host, command):
    verdict = HOSTS[host](command)

    assert verdict["decision"] == "deny", verdict
    assert verdict["reason"].strip()
    assert _stored_sentinels(world) == []


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize(
    "command",
    [
        "echo 'sops -d notes'",
        "git log --oneline --grep='sops -d' -1",
        "cat <<'EOF'\nsops -d notes\nEOF",
        "echo $(date) 2>&1",
        "kubectl get pods -n prod -o yaml",
    ],
)
def test_text_that_only_mentions_a_secret_read_runs_unchanged(world, host, command):
    verdict = HOSTS[host](command)

    assert verdict["decision"] == "allow", verdict
    assert "secret_reads" not in (verdict["run"] or "")


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_a_mutation_carrying_a_credential_in_clear_leaves_no_approval_record_holding_it(world, host):
    command = f'curl -X POST -H "Authorization: Bearer {BEARER_SENTINEL}" https://api.example.com/v1/items'

    verdict = HOSTS[host](command)

    assert verdict["decision"] == "deny", verdict
    assert "credential in clear" in verdict["reason"]
    assert BEARER_SENTINEL not in verdict["reason"]
    assert _stored_sentinels(world) == []


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_a_read_that_carries_no_secret_runs_unchanged(world, host):
    command = "kubectl get pods -n prod"

    verdict = HOSTS[host](command)

    assert verdict["decision"] in {"allow", None}
    assert verdict["run"] is None or command in verdict["run"]
    assert "secret_reads" not in (verdict["run"] or "")

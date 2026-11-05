"""A synonym of a change verb makes an unmodelled CLI ask; the same word quoted, as an option value, or as an exec verb stays free."""
from __future__ import annotations

import pytest

from tests.hooks.modules.security.test_effect_lexicon_probes import _observe, isolated  # noqa: F401

SYNONYMS_ASK = [
    ("delete-control", "frobctl delete prod-db"),
    ("delete-nuke", "frobctl nuke prod-db"),
    ("delete-obliterate", "frobctl obliterate --target prod"),
    ("delete-zap", "frobctl zap cache"),
    ("delete-annihilate", "frobctl annihilate cluster-1"),
    ("delete-expunge", "frobctl expunge records"),
    ("delete-eradicate", "frobctl eradicate old-data"),
    ("delete-decommission", "frobctl decommission node-3"),
    ("delete-teardown", "frobctl teardown env-1"),
    ("delete-deprovision", "frobctl deprovision db1"),
    ("delete-hyphen-fragment", "frobctl force-nuke prod-db"),
    ("create-provision", "frobctl provision db1"),
    ("update-upsert", "frobctl upsert row.json"),
    ("publish-upload", "frobctl upload artifact.tgz"),
    ("publish-submit", "frobctl submit job.yml"),
    ("publish-ship", "frobctl ship v2"),
]

STAYS_FREE = [
    ("grep-quoted-delete", "grep -rn 'delete user' src/"),
    ("grep-quoted-nuke", "grep -rn 'nuke prod' src/"),
    ("echo-quoted-deploy", "echo 'deploy done'"),
    ("echo-quoted-obliterate", "echo 'obliterate later'"),
    ("commit-deploy-message", "git commit -m 'fix: deploy the nuke fix'"),
    ("describe-release", "frobctl describe release v1"),
    ("list-attached-value", "frobctl list --label=nuke"),
]

EXEC_HELD_BACK = [
    ("exec-run", "frobctl run job"),
    ("exec-build", "frobctl build app"),
    ("exec-test", "frobctl test suite"),
    ("exec-make", "make build"),
]


@pytest.mark.parametrize("case_id,command", SYNONYMS_ASK, ids=[c[0] for c in SYNONYMS_ASK])
def test_change_synonym_asks(case_id, command, isolated):  # noqa: F811
    verdict, reason = _observe(command, isolated)
    assert verdict != "FREE", f"{command} ran free"


@pytest.mark.parametrize("case_id,command", STAYS_FREE, ids=[c[0] for c in STAYS_FREE])
def test_quoted_word_or_option_value_stays_free(case_id, command, isolated):  # noqa: F811
    verdict, reason = _observe(command, isolated)
    assert verdict == "FREE", f"{verdict} :: {command} :: {reason[:120]}"


@pytest.mark.parametrize("case_id,command", EXEC_HELD_BACK, ids=[c[0] for c in EXEC_HELD_BACK])
def test_exec_class_word_does_not_ask_yet(case_id, command, isolated):  # noqa: F811
    verdict, reason = _observe(command, isolated)
    assert verdict == "FREE", f"{verdict} :: {command} :: {reason[:120]}"

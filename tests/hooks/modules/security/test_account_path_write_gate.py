#!/usr/bin/env python3
"""A persistent write that grants access to the account is gated, both ways.

``tee -a ~/.ssh/authorized_keys`` and ``echo 'ssh-rsa ...' >> ~/.ssh/
authorized_keys`` reach the identical effect -- a key appended to the file
that decides who may log in as this user -- and both classified READ_ONLY,
for two unrelated reasons:

* the ``tee`` discriminator treated any ``~/``-relative or unexpanded
  ``$HOME``-relative token as safe, because it judged the destination by
  LOCATION (a privileged OS prefix, or Gaia's hooks tree) and home is
  neither; and
* the sanitizer strips a trailing redirect before classification runs, so
  the destination was gone by the time anything could look at it and only
  the ``echo`` remained.

Location alone is not the judgement -- the same principle the ``rm``
scratch override already states: an object valuable by its contents stays
T3 wherever it sits. These files are valuable by their contents: they hand
out login access (``~/.ssh``), or they run on the next shell (the rc
files), or they are the credentials themselves.

Both spellings are asserted here because closing one leaves the other open,
and a gate reachable by a second spelling is not a gate. That is why ``>|``,
bash's clobber override, is measured beside ``>``: same destination, same
write, one operator apart. And because membership follows contents, the file
that DECLARES a credential helper belongs with the store that helper reads --
a helper rewritten in the config redirects the same credentials without ever
touching the store.

The negatives are half the suite. The sensitive set is small on purpose: an
ordinary write under ``~/.cache``, the Gaia scratch directory, or the
working tree must keep costing nothing, or the fix trades a hole for a
fatigue that trains blind approval.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import mutative_verbs as mutative_verbs_module
from modules.security import sensitive_paths as sensitive_paths_module
from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier
from modules.tools.bash_validator import validate_bash_command

T3 = SecurityTier.T3_BLOCKED

HOME = str(Path.home())

# The tee route: the destination is an argument the discriminator reads.
TEE_WRITES = [
    ("ssh-tilde", "tee -a ~/.ssh/authorized_keys"),
    ("ssh-home-var", "tee -a $HOME/.ssh/authorized_keys"),
    ("ssh-absolute", f"tee -a {HOME}/.ssh/authorized_keys"),
    ("shell-rc", "tee ~/.bashrc"),
    ("aws-credentials", "tee -a ~/.aws/credentials"),
    ("git-config", "tee -a ~/.gitconfig"),
    ("git-config-home-var", "tee -a $HOME/.gitconfig"),
    ("git-config-home-braced", "tee -a ${HOME}/.gitconfig"),
    ("git-config-absolute", f"tee -a {HOME}/.gitconfig"),
]

# The redirect route: the destination is an operator target the sanitizer
# used to remove before anything classified it.
REDIRECT_WRITES = [
    ("ssh-append", "echo 'ssh-rsa AAAAB3Nza key' >> ~/.ssh/authorized_keys"),
    ("ssh-truncate", f"printf key > {HOME}/.ssh/authorized_keys"),
    ("ssh-home-var", "echo 'ssh-rsa AAAAB3Nza key' >> $HOME/.ssh/authorized_keys"),
    ("shell-rc", "echo 'export PATH=/tmp/x:$PATH' >> ~/.zshrc"),
    ("git-config-xdg", "echo '[alias]' >> ~/.config/git/config"),
    ("git-config-xdg-home-var", "echo '[alias]' >> $HOME/.config/git/config"),
    ("git-config-xdg-absolute", f"echo '[alias]' >> {HOME}/.config/git/config"),
    ("ssh-clobber-override", "echo 'ssh-rsa AAAAB3Nza key' >| ~/.ssh/authorized_keys"),
]

ACCOUNT_WRITES = TEE_WRITES + REDIRECT_WRITES

# Destinations that must keep costing nothing. A cache entry, Gaia's own
# scratch space and /tmp are where ordinary turns write, and charging consent
# for them is the failure mode this half of the suite exists to catch. A
# working-tree destination is deliberately absent: a shell-authored write into
# a git tree is refused by shell_write_guard on the CHANNEL, a categorical
# verdict that has nothing to do with the destination set measured here.
#
# The two neighbour entries fix the COMPARISON, not the product they happen to
# name: a sensitive file matches in full and a sensitive prefix matches whole
# segments. Relaxing either to a bare `startswith` -- dropping the `+ "/"` from
# the prefix test -- would charge consent for every adjacent name under
# `~/.config`, and nothing else in this suite would notice.
ORDINARY_WRITES = [
    ("cache", "tee ~/.cache/build.log"),
    ("scratch", "tee -a ~/.gaia/scratch/a967a85a994f1db23.dc233de56ed6.txt"),
    ("tmp", "tee /tmp/output.txt"),
    ("cache-redirect", "echo building > ~/.cache/build.log"),
    ("scratch-redirect", "echo '{}' > ~/.gaia/scratch/a967a85a994f1db23.txt"),
    ("tmp-redirect", "echo hello > /tmp/notes.txt"),
    ("unrelated-config", "tee ~/.config/nvim/init.lua"),
    ("neighbour-of-a-sensitive-name", "tee ~/.gitconfig.bak"),
    ("neighbour-of-a-sensitive-prefix", "tee ~/.config/github-copilot/hosts.json"),
]


def _clear_classifier_caches():
    """Drop every memoized verdict so a table edit is actually observed.

    Both entry points are ``lru_cache``d on the command string alone, so a
    verdict computed under one table state would survive the change and the
    counterfactual would silently measure nothing.
    """
    detect_mutative_command.cache_clear()
    mutative_verbs_module._detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.fixture
def without_the_account_path_set(monkeypatch):
    """Withdraw the sensitive set this work adds, caches cleared on both edges.

    Emptying the two containers rather than patching the predicate keeps the
    counterfactual a measurement of the ENTRY: every code path stays live and
    only the membership it consults goes away.
    """
    monkeypatch.setattr(
        sensitive_paths_module, "ACCOUNT_SENSITIVE_HOME_PREFIXES", frozenset()
    )
    monkeypatch.setattr(
        sensitive_paths_module, "ACCOUNT_SENSITIVE_HOME_FILES", frozenset()
    )
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize(
    "case,command", ACCOUNT_WRITES, ids=[c for c, _ in ACCOUNT_WRITES]
)
def test_account_path_write_requires_consent(case, command):
    """Every spelling of the write reaches the consent path, not T0."""
    result = validate_bash_command(command)
    assert result.allowed is False, (
        f"{case}: a write that grants account access must require consent -- "
        f"{command!r} was allowed at {result.tier}"
    )
    assert result.tier == T3


@pytest.mark.parametrize(
    "case,command", ACCOUNT_WRITES, ids=[c for c, _ in ACCOUNT_WRITES]
)
def test_account_path_write_is_approvable_not_categorical(case, command):
    """The verdict is a consent request, not a categorical denial.

    A block with no ``approval_id`` withholds the effect with no way to ask
    for it, and these writes are legitimate work when the user wants them.
    Both approvable shapes count: the inline ``ask`` prompt, and the
    out-of-band ``deny`` whose reason carries the ``approval_id`` the user
    decides against. What must never appear is a denial with neither.
    """
    result = validate_bash_command(command)
    assert result.block_response is not None, (
        f"{case}: {command!r} was denied with no approval path"
    )
    hook_output = result.block_response["hookSpecificOutput"]
    decision = hook_output["permissionDecision"]
    reason = hook_output.get("permissionDecisionReason", "")
    assert decision == "ask" or "approval_id" in reason, (
        f"{case}: {command!r} was denied with no way to consent -- "
        f"decision={decision}, reason={reason[:200]}"
    )


@pytest.mark.parametrize(
    "case,command", ACCOUNT_WRITES, ids=[c for c, _ in ACCOUNT_WRITES]
)
def test_account_path_write_counterfactual(
    case, command, without_the_account_path_set
):
    """With the set withdrawn, each form returns to the verdict it had before.

    This repository has shipped tables that read as coverage and decided
    nothing; an entry is closed only once its absence is observable.
    """
    result = validate_bash_command(command)
    assert result.allowed is True, (
        f"{case}: with the sensitive set withdrawn this form must classify "
        f"exactly as it did before this work, or the positive case proves "
        f"nothing -- got {result.tier}: {result.reason}"
    )


@pytest.mark.parametrize(
    "case,command", ORDINARY_WRITES, ids=[c for c, _ in ORDINARY_WRITES]
)
def test_ordinary_writes_stay_free(case, command):
    """An ordinary destination keeps paying nothing."""
    result = validate_bash_command(command)
    assert result.allowed is True, (
        f"{case}: an ordinary write must not start demanding consent -- "
        f"{command!r} got {result.tier}: {result.reason}"
    )


def test_tee_passthrough_still_stands_aside():
    """``tee`` with no file argument writes nothing and stays where it was.

    The discriminator adds a verdict and never removes one, so the read idiom
    (`cmd | tee`) must remain untouched by a widened destination set.
    """
    result = detect_mutative_command("tee")
    assert result.is_mutative is False


def test_both_spellings_of_the_same_effect_agree():
    """The two routes to the same file return the same verdict.

    Not a restatement of the cases above: what is asserted here is the
    AGREEMENT itself, which is the property the no-elusion rule needs. A fix
    that closed one route and left the other would pass every positive case
    for its own route and still leave the effect reachable.
    """
    via_tee = validate_bash_command("tee -a ~/.ssh/authorized_keys")
    via_redirect = validate_bash_command(
        "echo 'ssh-rsa AAAAB3Nza key' >> ~/.ssh/authorized_keys"
    )
    assert via_tee.allowed == via_redirect.allowed is False
    assert via_tee.tier == via_redirect.tier == T3


def test_the_credential_store_and_its_declaration_agree():
    """The file naming the credential helper costs what the store costs.

    Protecting ``~/.git-credentials`` while leaving ``~/.gitconfig`` free is an
    imbalance rather than a narrower set: pointing ``credential.helper`` at
    another program redirects the same credentials with no write to the store.
    """
    store = validate_bash_command("tee -a ~/.git-credentials")
    declaration = validate_bash_command("tee -a ~/.gitconfig")
    assert store.allowed == declaration.allowed is False
    assert store.tier == declaration.tier == T3


def test_clobber_override_agrees_with_plain_truncation():
    """``>|`` is ``>`` with noclobber overridden, and must classify as ``>``."""
    plain = validate_bash_command(f"printf key > {HOME}/.ssh/authorized_keys")
    override = validate_bash_command(f"printf key >| {HOME}/.ssh/authorized_keys")
    assert plain.allowed == override.allowed is False
    assert plain.tier == override.tier == T3

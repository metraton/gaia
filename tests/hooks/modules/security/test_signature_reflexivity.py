#!/usr/bin/env python3
"""Tests for matches_approval_signature() reflexivity (Brief 71, Change D-C).

Root cause being closed: a curl grant is BUILT with verb '-x put' (derived by
the flag classifier via the danger_verb argument), but the previous matcher
RE-derived the verb at match time via detect_mutative_command(), which for curl
returns the URL token -- not '-x put'. The verb-equality guard therefore
rejected curl against its OWN grant on every retry, byte-identical and
same-session included. Terraform escaped this only because it uses
detect_mutative_command on both the build and match sides.

The fix derives identity for a SCOPE_SEMANTIC_SIGNATURE grant ENTIRELY from
analyze_command (base_cmd + semantic_tokens + normalized_flags) -- the same
source used at build time -- so the comparison is symmetric and reflexive for
every command class. Byte-binding is preserved: semantic_tokens captures every
non-flag token (URL, positional args) and normalized_flags captures every flag
token (-X method, -d body, -H header).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Sys-path setup (hooks package is imported as top-level `modules.*`)
# ---------------------------------------------------------------------------

# tests/hooks/modules/security/<file>.py -> parents[4] is the gaia repo root.
# (Matches test_approval_scopes.py; the hooks dir must be on sys.path BEFORE the
# module-top `from modules.security...` import resolves at collection time.)
_REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = _REPO_ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_scopes import (  # noqa: E402
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
    matches_approval_signature,
)


# A representative Bitbucket PUT call: -X method + -H auth header + -d JSON body
# + positional URL. This is the exact shape that regressed in production.
_CURL_PUT = (
    'curl -X PUT '
    '-H "Authorization: Bearer abc123" '
    '-d \'{"title":"updated"}\' '
    'https://api.bitbucket.org/2.0/repositories/aaxis/aos/pullrequests/42'
)

_CURL_POST = (
    'curl -X POST '
    '-H "Content-Type: application/json" '
    '-d \'{"name":"feature"}\' '
    'https://api.bitbucket.org/2.0/repositories/aaxis/aos/pullrequests'
)

_TERRAFORM = "terraform apply -auto-approve"


def _build(command: str, danger_verb: str) -> object:
    sig = build_approval_signature(
        command,
        scope_type=SCOPE_SEMANTIC_SIGNATURE,
        danger_verb=danger_verb,
        danger_category="MUTATIVE",
    )
    assert sig is not None, f"signature build should succeed for: {command}"
    return sig


# ---------------------------------------------------------------------------
# Reflexivity: a command must match the grant built from it (load-bearing)
# ---------------------------------------------------------------------------

def test_curl_put_matches_own_grant():
    """The acute curl regression: PUT must match the grant built from it."""
    sig = _build(_CURL_PUT, danger_verb="-X PUT")
    assert matches_approval_signature(sig, _CURL_PUT) is True


def test_curl_post_matches_own_grant():
    """Same reflexivity guarantee for curl -X POST."""
    sig = _build(_CURL_POST, danger_verb="-X POST")
    assert matches_approval_signature(sig, _CURL_POST) is True


def test_terraform_still_matches_own_grant():
    """Regression guard: terraform (which always matched) keeps matching."""
    sig = _build(_TERRAFORM, danger_verb="apply")
    assert matches_approval_signature(sig, _TERRAFORM) is True


# ---------------------------------------------------------------------------
# Byte-binding preserved: decorated / tampered commands must NOT match
# ---------------------------------------------------------------------------

def test_curl_decoration_does_not_match():
    """Wrapping the approved curl in a pipe or capture must not match.

    The grant authorizes ONE operation; a pipe or command substitution is a
    different command that could exfiltrate or transform output, so it must
    re-trigger T3. A BARE shell redirect (``2>&1``) is the deliberate exception
    -- it is a pure I/O side-effect, normalized out of the signature (Fix A),
    and is covered by test_curl_redirect_matches below.
    """
    sig = _build(_CURL_PUT, danger_verb="-X PUT")
    assert matches_approval_signature(sig, _CURL_PUT + " | jq .") is False
    assert matches_approval_signature(sig, "RESP=$(" + _CURL_PUT + ")") is False


def test_curl_redirect_matches():
    """A bare shell redirect appended to the approved curl MUST match (Fix A).

    Redirects (``2>&1``, ``> out``, ``2> err``) are pure I/O side-effects, not
    part of the command's identity. The signature normalizes them out so the
    block-approve-retry flow does not double-prompt when the agent appends a
    redirect on the retry. Pipes and substitutions (above) still do NOT match.
    """
    sig = _build(_CURL_PUT, danger_verb="-X PUT")
    assert matches_approval_signature(sig, _CURL_PUT + " 2>&1") is True
    assert matches_approval_signature(sig, _CURL_PUT + " > out.json") is True


def test_curl_different_url_does_not_match():
    """A different target URL is a different operation -- must not match."""
    sig = _build(_CURL_PUT, danger_verb="-X PUT")
    tampered_url = _CURL_PUT.replace("pullrequests/42", "pullrequests/99")
    assert matches_approval_signature(sig, tampered_url) is False


def test_curl_tampered_body_does_not_match():
    """Changing the SPACE-FORM -d JSON body is a different payload -- must not match."""
    sig = _build(_CURL_PUT, danger_verb="-X PUT")
    tampered_body = _CURL_PUT.replace('{"title":"updated"}', '{"title":"HIJACKED"}')
    assert matches_approval_signature(sig, tampered_body) is False


# ---------------------------------------------------------------------------
# Long-flag value binding (Brief 71 over-match fix)
#
# The inline `--flag=value` form must bind the VALUE into the signature, or two
# commands differing only in a `--flag=value` value collapse to the same
# signature and one grant authorizes the other. The space-form (`-d '{...}'`)
# was already safe; these guard the previously-unbound long-flag form.
# ---------------------------------------------------------------------------

# Inline long-flag URL (no separate -d token; value rides on the flag).
_CURL_LONG_DATA = (
    "curl -X POST --data=amount=10 "
    "https://api.bitbucket.org/2.0/repositories/aaxis/aos/pay"
)


def test_curl_long_flag_data_tamper_does_not_match():
    """--data=amount=10 grant must NOT authorize --data=amount=1000000 (over-match)."""
    sig = _build(_CURL_LONG_DATA, danger_verb="-X POST")
    tampered = _CURL_LONG_DATA.replace("amount=10", "amount=1000000")
    assert matches_approval_signature(sig, tampered) is False


def test_curl_long_flag_header_tamper_does_not_match():
    """--header=Authorization:safe must NOT authorize --header=Authorization:STOLEN."""
    cmd = (
        "curl --header=Authorization:safe "
        "https://api.bitbucket.org/2.0/repositories/aaxis/aos/pay"
    )
    sig = _build(cmd, danger_verb="--header")
    tampered = cmd.replace("Authorization:safe", "Authorization:STOLEN")
    assert matches_approval_signature(sig, tampered) is False


def test_curl_data_binary_file_tamper_does_not_match():
    """--data-binary=@safe.json must NOT authorize --data-binary=@evil.json."""
    cmd = (
        "curl --data-binary=@safe.json "
        "https://api.bitbucket.org/2.0/repositories/aaxis/aos/pay"
    )
    sig = _build(cmd, danger_verb="--data-binary")
    tampered = cmd.replace("@safe.json", "@evil.json")
    assert matches_approval_signature(sig, tampered) is False


def test_curl_long_flag_data_reflexive_match():
    """Reflexivity for the long-flag form: --data=amount=10 must match itself."""
    sig = _build(_CURL_LONG_DATA, danger_verb="-X POST")
    assert matches_approval_signature(sig, _CURL_LONG_DATA) is True

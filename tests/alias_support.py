"""Declare classifier aliases for a test without a database: the cache a tool row would have filled."""

from __future__ import annotations


def declare_alias(monkeypatch, *pairs: str) -> None:
    """Install ``wrapper=cli`` pairs as the declared aliases for the test; no pair means no alias."""
    from modules.security import cli_aliases

    aliases = {}
    for pair in pairs:
        wrapper, _, target = pair.partition("=")
        if wrapper and target and wrapper != target:
            aliases[wrapper.strip()] = target.strip()
    monkeypatch.setattr(cli_aliases, "_CACHE", aliases)

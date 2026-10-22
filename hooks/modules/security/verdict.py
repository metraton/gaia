"""The consent verdict a command receives, the rule that decided it, and the sentence that says how to proceed."""

from __future__ import annotations

import re
import shlex
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple


class Verdict(str, Enum):
    FREE = "FREE"
    SIGNED = "SIGNED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


UNDETERMINED = "could not be determined"

UNKNOWN_SPAN = "unknown.dynamic-span"
UNKNOWN_TARGET = "unknown.target"

_FLOOR_REMEDIATION = "Nothing can sign this command; reach the goal another way, or ask the user to run it themselves."

REMEDIATION: Dict[str, str] = {
    "sign.destroys": "Request a signature for this exact command naming what it destroys and how it is undone, or reach the outcome without the destructive step.",
    "sign.lands": "Request a signature for this exact command naming the shared or protected state it changes, or keep the change on your own branch or in scratch.",
    "sign.speaks": "Request a signature for this exact command naming who receives what it sends, or hand the message to the user to send.",
    "sign.foreign-code": "Request a signature naming the package and where it comes from, or use code already installed in the project.",
    "sign.lifecycle-body-verb": "Request a signature naming the lifecycle script that mutates, or install with lifecycle scripts disabled.",
    UNKNOWN_SPAN: "Write the word the decision depends on literally, with no variable, substitution or unrecognised flag, or request a signature naming it.",
    UNKNOWN_TARGET: "Name the target literally, with no variable or substitution, or request a signature naming it.",
    "floor.gaia-cli-only": "Run only Gaia CLI commands from this role, and dispatch a specialist for anything else.",
    "floor.empty-command": "Send the command you meant to run.",
    "floor.host-consent-verb": "Leave recording and applying consent to the host plugin; it is never run from a shell.",
    "floor.publish-attribution": "Remove the AI attribution from the published text and run the command again.",
    "floor.comment-push-gate": "Cut comment and docstring lines until gaia lint comments passes, then push again.",
    "floor.gaia-db-write": "Change Gaia's database through the gaia CLI, never by writing the file.",
    "floor.subagent-memory-write": "Propose the memory in your contract's memory_delta; the orchestrator decides what is kept.",
    "floor.memory-skill-not-loaded": "Load Skill('memory') first, then run the memory write again.",
    "floor.subagent-resume-point": "Leave the resume point to the orchestrator and report the state in your contract.",
    "floor.contract-ownership": "Address your own contract with --draft-id <contract_id>.",
    "floor.protected-path": "Edit the source in the Gaia repository and install it; never write the installed copy.",
    "floor.shell-write": "Write the file with the Write or Edit tool; a throwaway dump belongs under ~/.gaia/scratch.",
    "floor.obfuscated-nesting": "Run the innermost command directly, with no shell wrapper around it.",
    "floor.indirect-execution": "Run the command directly, or commit it as a script file and run that file.",
    "floor.kubectl-secret-output": "Read the secret's metadata without its data, or ask the user for the value.",
    "floor.cloud-pipe": "Run the cloud command on its own and read its output, with no pipe, redirect or chain.",
    "floor.secret-read": "Leave the secret where it is and ask the user for the value or for a scoped credential.",
    "floor.commit-message": "Rewrite the commit message as the error describes and commit again.",
    "floor.clear-secret": "Move the secret out of the command line into a variable or file the command reads, then run it again.",
    "floor.no-requester": "Run the command from a dispatched turn whose host event names its agent, so the request can be bound to it.",
    "floor.approval-store": "Run the command again once the approval store is reachable; no request was recorded.",
    "floor.composition-network-to-exec": "Download the code to a file, read it, and run it as a separate step only if it is yours to run.",
    "floor.composition-decode-to-exec": "Decode the payload to a file, read it, and run it as a separate step only if it is yours to run.",
}

COMPOSITION_RULES: Dict[str, str] = {
    "exfiltration": "floor.secret-read",
    "rce": "floor.composition-network-to-exec",
    "network_write_rce": "floor.composition-network-to-exec",
    "obfuscated_exec": "floor.composition-decode-to-exec",
}

_DESTROY_WORDS: FrozenSet[str] = frozenset({
    "rm", "rmdir", "rmtree", "unlink", "delete", "destroy", "remove", "purge", "drop", "truncate",
    "wipe", "shred", "clean", "reset", "terminate", "uninstall", "force", "force-with-lease",
})
_SPEAK_WORDS: FrozenSet[str] = frozenset({
    "approve", "review", "comment", "close", "ready", "draft", "abandoned", "rerun",
    "set-vote", "post", "send", "urlopen",
})
_NETWORK_SENDERS: FrozenSet[str] = frozenset({"curl", "wget", "nc", "ncat", "netcat", "http", "https"})
_PACKAGE_INSTALLERS: FrozenSet[str] = frozenset({
    "npm", "pnpm", "yarn", "bun", "pip", "pip3", "pipx", "uv", "cargo", "brew", "apt", "apt-get",
    "dnf", "yum", "apk", "gem", "go",
})
_INTERACTIVE_FLAGS: FrozenSet[str] = frozenset({"-i", "-t", "-it", "-ti", "--interactive", "--tty", "--stdin"})
_DYNAMIC = re.compile(r"^[\"']?(\$|`)")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_CAMEL = re.compile(r"[a-z][A-Z]")
_CAMEL_PART = re.compile(r"[A-Z]?[a-z0-9]+")


def remediation(rule: str) -> str:
    """Return the sentence that tells the caller how to proceed under *rule*."""
    if rule in REMEDIATION:
        return REMEDIATION[rule]
    return _FLOOR_REMEDIATION if rule.startswith("floor.") else REMEDIATION["sign.lands"]


def render(rule: str) -> str:
    """Return the line every refusal and signature request carries."""
    return f"Rule: {rule}. Remediation: {remediation(rule)}"


def blocked_rule(category: str) -> str:
    """Return the rule of a permanently blocked pattern group."""
    return f"floor.{category}"


def _tokens(command: str) -> Tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=False))
    except ValueError:
        return tuple(command.split())


def undetermined_span(verb: str, category: str, command: str) -> Optional[Tuple[str, str]]:
    """Return (rule, what could not be determined) when the program, executor payload, script or pushed ref is dynamic."""
    tokens = _tokens(command)
    if verb == "script-file-unreadable":
        return UNKNOWN_SPAN, "the script that decides its effect"
    if verb == "exec" and "--" in tokens:
        payload = tokens[tokens.index("--") + 1:]
        if payload and _DYNAMIC.match(payload[0]):
            return UNKNOWN_SPAN, "the command it runs"
    if verb == "push" and "push" in tokens:
        operands = [t for t in tokens[tokens.index("push") + 1:] if not t.startswith("-")]
        if any(_DYNAMIC.search(t) or "$(" in t for t in operands[1:]):
            return UNKNOWN_TARGET, "the ref it pushes to"
    return None


def effect_rule(verb: str, command: str) -> str:
    """Return the leg a signed command stands on: destroys, speaks, foreign code or lands."""
    tokens = _tokens(command)
    lowered = [t.lower() for t in tokens]
    raw_words = _WORD.findall(command)
    words = {w.lower() for w in raw_words}
    words |= {part.lower() for w in raw_words if _CAMEL.search(w) for part in _CAMEL_PART.findall(w)}
    if verb == "registry-fetch":
        return "sign.foreign-code"
    if verb == "install" and "install" in lowered:
        installer = lowered[lowered.index("install") - 1] if lowered.index("install") else ""
        if installer.rsplit("/", 1)[-1] in _PACKAGE_INSTALLERS:
            return "sign.foreign-code"
    push_rewrites = verb == "push" and any(t[:1] in "+:" and len(t) > 1 for t in tokens)
    if push_rewrites or words & _DESTROY_WORDS or verb.lower() in _DESTROY_WORDS:
        return "sign.destroys"
    interactive = verb == "exec" and any(t in _INTERACTIVE_FLAGS for t in lowered)
    sender = bool(lowered) and lowered[0].rsplit("/", 1)[-1] in _NETWORK_SENDERS
    if interactive or sender or words & _SPEAK_WORDS or verb.lower() in _SPEAK_WORDS:
        return "sign.speaks"
    return "sign.lands"


def signing_rule(verb: str, category: str, command: str, rule: str = "") -> Tuple[str, str]:
    """Return (rule, what could not be determined or "") for a command that asks; a classifier-settled rule wins."""
    if rule:
        return rule, ""
    unknown = undetermined_span(verb, category, command)
    if unknown is not None:
        return unknown
    return effect_rule(verb, command), ""

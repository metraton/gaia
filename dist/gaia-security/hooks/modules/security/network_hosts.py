"""
Network host classification for curl/wget/httpie command targets.

This module is a helper consumed by flag_classifiers.py (T3).  It is NOT called
directly from bash_validator.py.

Classification categories:
  LOCALHOST       -- localhost, 127.0.0.1, ::1, 0.0.0.0, private RFC 1918 ranges
  KNOWN_REGISTRY  -- well-known development/CI registries (npm, PyPI, GitHub, etc.)
  UNKNOWN         -- all other external targets (triggers T3 approval for GET requests)

Private IP ranges (RFC 1918):
  10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
These are treated as LOCALHOST (internal network) for classification purposes.

Usage in flag_classifiers (T3):
  - GET + KNOWN_REGISTRY / LOCALHOST  => READ_ONLY
  - GET + UNKNOWN                      => MUTATIVE (T3 approval required)
  - POST/PUT/DELETE/PATCH (any host)  => MUTATIVE  (always, regardless of host)

Dependencies: Python stdlib only (urllib.parse).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Known-safe registries (GET-only; POST to any host is still MUTATIVE)
# ---------------------------------------------------------------------------
# This is a module-level constant. Extend by adding entries to the frozenset.

KNOWN_REGISTRIES: frozenset = frozenset({
    # npm
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "registry.npmmirror.com",
    "www.npmjs.org",
    "npmjs.org",
    # PyPI
    "pypi.org",
    "files.pythonhosted.org",
    # GitHub
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    # Rust / Go / Ruby / PHP
    "crates.io",
    "rubygems.org",
    "packagist.org",
    "pkg.go.dev",
    "proxy.golang.org",
    # Other common CI/build targets
    "dl.google.com",
    "repo.maven.apache.org",
    # Docker registries
    "hub.docker.com",
    "registry.hub.docker.com",
    "ghcr.io",
})

# Localhost addresses (exact match after port stripping)
_LOCALHOST_EXACT: frozenset = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
})

# RFC 1918 private ranges
_RFC1918_10 = re.compile(r"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_RFC1918_172 = re.compile(r"^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$")
_RFC1918_192 = re.compile(r"^192\.168\.\d{1,3}\.\d{1,3}$")
# Link-local
_LINK_LOCAL = re.compile(r"^169\.254\.\d{1,3}\.\d{1,3}$")
# Loopback range beyond 127.0.0.1
_LOOPBACK_RANGE = re.compile(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
# *.local mDNS
_MDNS_LOCAL = re.compile(r"\.local$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class HostCategory(str, Enum):
    """Classification category for a network host."""
    LOCALHOST = "LOCALHOST"
    KNOWN_REGISTRY = "KNOWN_REGISTRY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HostClassification:
    """Structured result of network host classification.

    Attributes:
        host:     Normalized hostname (no port, no scheme).
        category: One of HostCategory.LOCALHOST, KNOWN_REGISTRY, or UNKNOWN.
        reason:   Human-readable explanation.
    """
    host: str
    category: HostCategory
    reason: str

    @property
    def is_local(self) -> bool:
        return self.category == HostCategory.LOCALHOST

    @property
    def is_known_registry(self) -> bool:
        return self.category == HostCategory.KNOWN_REGISTRY

    @property
    def is_unknown(self) -> bool:
        return self.category == HostCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Host extraction helpers
# ---------------------------------------------------------------------------

def _strip_port(host: str) -> str:
    """Remove port suffix from a bare hostname (host:port -> host)."""
    if host.startswith("["):
        # IPv6 bracketed form: [::1]:8080 -> ::1
        bracket_end = host.find("]")
        if bracket_end != -1:
            return host[1:bracket_end]
        return host
    # Bare IPv6 addresses contain multiple colons (e.g. ::1, fe80::1)
    # Only strip port when there is exactly one colon (host:port form)
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def _extract_host_from_url(url: str) -> Optional[str]:
    """Extract the hostname from a URL string.

    Handles:
    - Full URLs with scheme:  https://host:port/path
    - Scheme-relative URLs:   //host/path
    - Bare hostname+path:     host/path  or  host:port/path
    - IPv6 literal:           http://[::1]:8080/

    Returns the hostname string without port, or None if not parseable.
    """
    url = url.strip()
    if not url:
        return None

    # URLs with explicit scheme
    if "://" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname:
                return parsed.hostname.lower()
        except ValueError:
            pass
        return None

    # Scheme-relative: //host/path
    if url.startswith("//"):
        try:
            parsed = urllib.parse.urlparse("https:" + url)
            if parsed.hostname:
                return parsed.hostname.lower()
        except ValueError:
            pass
        return None

    # Bare form: host/path, host:port, host:port/path
    # Split off any path component
    host_part = url.split("/")[0]
    if not host_part:
        return None
    return _strip_port(host_part).lower()


# ---------------------------------------------------------------------------
# Primary classification logic
# ---------------------------------------------------------------------------

def _is_localhost(host: str) -> bool:
    """Return True when the host is a loopback/local address."""
    if host in _LOCALHOST_EXACT:
        return True
    if _LOOPBACK_RANGE.match(host):
        return True
    if _RFC1918_10.match(host):
        return True
    if _RFC1918_172.match(host):
        return True
    if _RFC1918_192.match(host):
        return True
    if _LINK_LOCAL.match(host):
        return True
    if _MDNS_LOCAL.search(host):
        return True
    return False


def classify_host(url_or_host: str) -> HostClassification:
    """Classify a URL or bare hostname into LOCALHOST, KNOWN_REGISTRY, or UNKNOWN.

    Args:
        url_or_host: A full URL (https://registry.npmjs.org/express),
                     a bare hostname (registry.npmjs.org),
                     or host:port (localhost:8080).

    Returns:
        HostClassification with category and reason.
    """
    if not url_or_host or not url_or_host.strip():
        return HostClassification(
            host="",
            category=HostCategory.UNKNOWN,
            reason="empty host/url",
        )

    raw = url_or_host.strip()

    # Attempt to extract the hostname component
    host = _extract_host_from_url(raw)
    if not host:
        return HostClassification(
            host=raw,
            category=HostCategory.UNKNOWN,
            reason=f"could not parse host from {raw!r}",
        )

    # 1. Localhost / loopback / private ranges
    if _is_localhost(host):
        return HostClassification(
            host=host,
            category=HostCategory.LOCALHOST,
            reason=f"localhost/private: {host}",
        )

    # 2. Known registries
    if host in KNOWN_REGISTRIES:
        return HostClassification(
            host=host,
            category=HostCategory.KNOWN_REGISTRY,
            reason=f"known registry: {host}",
        )

    # 3. Unknown external target
    return HostClassification(
        host=host,
        category=HostCategory.UNKNOWN,
        reason=f"unknown external host: {host}",
    )


# ---------------------------------------------------------------------------
# URL extraction from command token lists
# ---------------------------------------------------------------------------

# Flags that consume the NEXT token as their value (not a URL)
_CURL_VALUE_FLAGS: frozenset = frozenset({
    "-H", "--header",
    "-u", "--user",
    "-A", "--user-agent",
    "-e", "--referer",
    "-o", "--output",
    "-O", "--remote-name",
    "--output-dir",
    "-x", "--proxy",
    "--proxy-user",
    "-T", "--upload-file",
    "-d", "--data",
    "--data-raw", "--data-binary", "--data-urlencode",
    "-F", "--form", "--form-string",
    "-X", "--request",
    "-b", "--cookie",
    "-c", "--cookie-jar",
    "--max-time", "-m",
    "--connect-timeout",
    "-r", "--range",
    "--resolve",
    "--cacert", "--capath",
    "--cert", "--key",
    "--tlspassword",
    "-w", "--write-out",
    "--interface",
    "--dns-servers",
    "--parallel-max",
    "--json",
    "-K", "--config",
    "--limit-rate",
    "--max-redirs",
    "--retry",
    "--retry-delay",
    "--retry-max-time",
    "--socks4", "--socks4a", "--socks5", "--socks5-hostname",
})

_WGET_VALUE_FLAGS: frozenset = frozenset({
    "-O", "--output-document",
    "-o", "--output-file",
    "--append-output",
    "-P", "--directory-prefix",
    "-U", "--user-agent",
    "--referer",
    "--header",
    "--http-user", "--http-password",
    "--proxy-user", "--proxy-password",
    "--post-data", "--post-file",
    "--method",
    "--body-data", "--body-file",
    "--ca-certificate", "--ca-directory",
    "--certificate", "--private-key",
    "--password",
    "-e", "--execute",
    "--bind-address",
    "--connect-timeout",
    "--dns-timeout",
    "--read-timeout",
    "--timeout",
    "--tries", "-t",
    "--waitretry",
    "--wait", "-w",
    "--random-wait",
    "--limit-rate",
    "--quota", "-Q",
    "--input-file", "-i",
    "--base",
    "--domains", "-D",
    "--exclude-domains",
    "--follow-tags",
    "--ignore-tags",
    "--accept", "-A",
    "--reject", "-R",
    "--accept-regex",
    "--reject-regex",
    "--include-directories", "-I",
    "--exclude-directories", "-X",
    "--cut-dirs",
    "--level", "-l",
})


def extract_url_from_tokens(tokens: List[str]) -> Optional[str]:
    """Parse a curl/wget/httpie token list and return the first URL argument.

    Skips flags and their value arguments to find the bare URL positional arg.

    Args:
        tokens: Tokenized command list (tokens[0] is the command name).

    Returns:
        The URL string if found, else None.
    """
    if not tokens:
        return None

    cmd = tokens[0].lower()
    args = tokens[1:]

    if cmd == "curl":
        return _extract_url_curl(args)
    if cmd == "wget":
        return _extract_url_wget(args)
    if cmd in ("http", "https"):
        return _extract_url_httpie(args)

    # Generic fallback: first non-flag argument
    return _extract_url_generic(args)


def _extract_url_curl(args: List[str]) -> Optional[str]:
    """Extract URL from curl arguments, skipping flags and their values."""
    i = 0
    while i < len(args):
        a = args[i]
        # Skip -- end of flags marker
        if a == "--":
            i += 1
            # Everything after -- is treated as positional
            if i < len(args):
                return args[i]
            return None
        # Flags that eat the next token as value
        if a in _CURL_VALUE_FLAGS:
            i += 2
            continue
        # Flags with inline value (--flag=value)
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        # Single-char flags that may or may not eat the next token:
        # if the flag is a single char standalone (e.g. -v, -s, -L, -k) it
        # consumes no value; if it's in our known value set, already handled.
        if a.startswith("-") and len(a) == 2:
            i += 1
            continue
        # Bundled short flags (e.g. -sLk) -- no values consumed; skip
        if a.startswith("-") and len(a) > 2 and a[1] != "-":
            i += 1
            continue
        # Non-flag argument: this is the URL
        if not a.startswith("-"):
            return a
        i += 1
    return None


def _extract_url_wget(args: List[str]) -> Optional[str]:
    """Extract URL from wget arguments, skipping flags and their values."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            if i < len(args):
                return args[i]
            return None
        if a in _WGET_VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-") and len(a) == 2:
            i += 1
            continue
        if a.startswith("-") and len(a) > 2 and a[1] != "-":
            i += 1
            continue
        if not a.startswith("-"):
            return a
        i += 1
    return None


_HTTPIE_METHODS = frozenset({
    "POST", "PUT", "DELETE", "PATCH", "GET", "HEAD", "OPTIONS",
})
_HTTPIE_DATA_ITEM = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*(:=|==|=@|:@|@|:=@|=)")


def _extract_url_httpie(args: List[str]) -> Optional[str]:
    """Extract URL from httpie (http/https) arguments.

    httpie positional syntax: http [METHOD] URL [ITEM ...]
    Skip flags, optional METHOD token, then return the URL.
    """
    for a in args:
        if a.startswith("-"):
            continue
        # Skip explicit method token
        if a.upper() in _HTTPIE_METHODS:
            continue
        # Skip data items (key=value, key:=json, key@file)
        if _HTTPIE_DATA_ITEM.match(a):
            continue
        # First remaining positional arg is the URL
        return a
    return None


def _extract_url_generic(args: List[str]) -> Optional[str]:
    """Generic URL extraction: return first non-flag argument."""
    for a in args:
        if a == "--":
            break
        # Long flags with = carry their value inline
        if a.startswith("--") and "=" in a:
            continue
        # Short/long flags without value: skip
        if a.startswith("-"):
            continue
        return a
    return None

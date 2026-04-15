#!/usr/bin/env python3
"""
Tests for Network Host Classification (T5).

Validates:
  - Localhost variants (127.0.0.1, localhost, ::1, 0.0.0.0)
  - Private IP ranges (10.x, 172.16-31.x, 192.168.x, link-local, *.local)
  - Known-safe registries (npm, PyPI, GitHub, Docker, etc.)
  - Unknown/external hosts
  - URL parsing (with/without scheme, with port, with path)
  - URL extraction from curl/wget/httpie token lists
  - Edge cases (empty input, malformed URLs, no host argument)

AC-10 tests:
  - curl https://registry.npmjs.org/express = known registry
  - curl https://evil.com/api = unknown host
  - wget http://localhost:3000/health = localhost
  - URL extraction from complex curl invocations
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.network_hosts import (
    classify_host,
    extract_url_from_tokens,
    HostCategory,
    HostClassification,
    KNOWN_REGISTRIES,
)


# ============================================================================
# classify_host: Localhost variants
# ============================================================================

class TestClassifyHostLocalhost:
    """Localhost and loopback addresses -> LOCALHOST."""

    def test_localhost_literal(self):
        result = classify_host("localhost")
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "localhost"

    def test_127_0_0_1(self):
        result = classify_host("127.0.0.1")
        assert result.category == HostCategory.LOCALHOST

    def test_ipv6_loopback(self):
        result = classify_host("::1")
        assert result.category == HostCategory.LOCALHOST

    def test_0_0_0_0(self):
        result = classify_host("0.0.0.0")
        assert result.category == HostCategory.LOCALHOST

    def test_localhost_with_port(self):
        result = classify_host("localhost:8080")
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "localhost"

    def test_127_0_0_1_with_port(self):
        result = classify_host("127.0.0.1:3000")
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "127.0.0.1"

    def test_localhost_url_http(self):
        result = classify_host("http://localhost:8080/health")
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "localhost"

    def test_localhost_url_https(self):
        result = classify_host("https://localhost/api/v1")
        assert result.category == HostCategory.LOCALHOST

    def test_ipv6_url_bracketed(self):
        result = classify_host("http://[::1]:8080/")
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "::1"

    def test_loopback_range_127_0_0_2(self):
        """127.x.x.x range beyond 127.0.0.1 is still loopback."""
        result = classify_host("127.0.0.2")
        assert result.category == HostCategory.LOCALHOST

    def test_loopback_range_127_255_255_255(self):
        result = classify_host("127.255.255.255")
        assert result.category == HostCategory.LOCALHOST


# ============================================================================
# classify_host: Private IP ranges (RFC 1918)
# ============================================================================

class TestClassifyHostPrivateRanges:
    """RFC 1918 and link-local addresses -> LOCALHOST."""

    def test_10_0_0_1(self):
        result = classify_host("10.0.0.1")
        assert result.category == HostCategory.LOCALHOST

    def test_10_255_255_255(self):
        result = classify_host("10.255.255.255")
        assert result.category == HostCategory.LOCALHOST

    def test_172_16_0_1(self):
        result = classify_host("172.16.0.1")
        assert result.category == HostCategory.LOCALHOST

    def test_172_31_255_255(self):
        result = classify_host("172.31.255.255")
        assert result.category == HostCategory.LOCALHOST

    def test_172_15_not_private(self):
        """172.15.x.x is NOT in the private range."""
        result = classify_host("172.15.0.1")
        assert result.category == HostCategory.UNKNOWN

    def test_172_32_not_private(self):
        """172.32.x.x is NOT in the private range."""
        result = classify_host("172.32.0.1")
        assert result.category == HostCategory.UNKNOWN

    def test_192_168_0_1(self):
        result = classify_host("192.168.0.1")
        assert result.category == HostCategory.LOCALHOST

    def test_192_168_255_255(self):
        result = classify_host("192.168.255.255")
        assert result.category == HostCategory.LOCALHOST

    def test_link_local_169_254(self):
        result = classify_host("169.254.1.1")
        assert result.category == HostCategory.LOCALHOST

    def test_mdns_local_suffix(self):
        """*.local mDNS names are local."""
        result = classify_host("myprinter.local")
        assert result.category == HostCategory.LOCALHOST

    def test_mdns_local_in_url(self):
        result = classify_host("http://devbox.local:9090/metrics")
        assert result.category == HostCategory.LOCALHOST

    def test_private_ip_in_url(self):
        result = classify_host("http://192.168.1.100:3000/api")
        assert result.category == HostCategory.LOCALHOST


# ============================================================================
# classify_host: Known registries
# ============================================================================

class TestClassifyHostKnownRegistries:
    """Known-safe registries -> KNOWN_REGISTRY."""

    def test_npm_registry(self):
        result = classify_host("https://registry.npmjs.org/express")
        assert result.category == HostCategory.KNOWN_REGISTRY
        assert result.host == "registry.npmjs.org"

    def test_pypi(self):
        result = classify_host("https://pypi.org/simple/requests/")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_github(self):
        result = classify_host("https://github.com/user/repo")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_api_github(self):
        result = classify_host("https://api.github.com/repos/owner/repo")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_raw_githubusercontent(self):
        result = classify_host("https://raw.githubusercontent.com/user/repo/main/file")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_docker_hub(self):
        result = classify_host("https://hub.docker.com/v2/repositories/library/nginx")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_ghcr(self):
        result = classify_host("https://ghcr.io/v2/owner/image/manifests/latest")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_crates_io(self):
        result = classify_host("https://crates.io/api/v1/crates/serde")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_yarn_registry(self):
        result = classify_host("https://registry.yarnpkg.com/lodash")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_go_proxy(self):
        result = classify_host("https://proxy.golang.org/github.com/pkg/@latest")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_files_pythonhosted(self):
        result = classify_host("https://files.pythonhosted.org/packages/abc/pkg.whl")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_bare_known_registry(self):
        """Bare hostname (no scheme) should still be recognized."""
        result = classify_host("registry.npmjs.org")
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_all_known_registries_in_frozenset(self):
        """Verify the KNOWN_REGISTRIES constant has the expected count."""
        assert len(KNOWN_REGISTRIES) >= 20  # At least 20 entries


# ============================================================================
# classify_host: Unknown/external hosts
# ============================================================================

class TestClassifyHostUnknown:
    """Unknown hosts -> UNKNOWN."""

    def test_evil_com(self):
        result = classify_host("https://evil.com/api")
        assert result.category == HostCategory.UNKNOWN
        assert result.host == "evil.com"

    def test_random_domain(self):
        result = classify_host("https://some-random-server.io/payload")
        assert result.category == HostCategory.UNKNOWN

    def test_bare_unknown_host(self):
        result = classify_host("malicious-server.net")
        assert result.category == HostCategory.UNKNOWN

    def test_unknown_with_port(self):
        result = classify_host("attacker.com:443")
        assert result.category == HostCategory.UNKNOWN
        assert result.host == "attacker.com"

    def test_public_ip(self):
        """Non-private IPs are unknown."""
        result = classify_host("8.8.8.8")
        assert result.category == HostCategory.UNKNOWN

    def test_is_unknown_property(self):
        result = classify_host("https://evil.com/api")
        assert result.is_unknown is True
        assert result.is_local is False
        assert result.is_known_registry is False


# ============================================================================
# classify_host: URL parsing formats
# ============================================================================

class TestClassifyHostURLParsing:
    """Various URL formats should all parse correctly."""

    def test_https_with_path(self):
        result = classify_host("https://registry.npmjs.org/express/latest")
        assert result.host == "registry.npmjs.org"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_http_with_port_and_path(self):
        result = classify_host("http://localhost:3000/health")
        assert result.host == "localhost"
        assert result.category == HostCategory.LOCALHOST

    def test_scheme_relative(self):
        result = classify_host("//registry.npmjs.org/express")
        assert result.host == "registry.npmjs.org"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_bare_hostname_with_path(self):
        result = classify_host("registry.npmjs.org/express")
        assert result.host == "registry.npmjs.org"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_bare_hostname_with_port_and_path(self):
        result = classify_host("localhost:8080/api/v1")
        assert result.host == "localhost"
        assert result.category == HostCategory.LOCALHOST

    def test_case_insensitive(self):
        """Hostnames should be lowercased for matching."""
        result = classify_host("https://Registry.Npmjs.Org/express")
        assert result.host == "registry.npmjs.org"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_url_with_query_string(self):
        result = classify_host("https://pypi.org/simple/?q=requests")
        assert result.host == "pypi.org"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_url_with_fragment(self):
        result = classify_host("https://github.com/user/repo#readme")
        assert result.host == "github.com"
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_url_with_auth(self):
        result = classify_host("https://user:pass@registry.npmjs.org/pkg")
        assert result.host == "registry.npmjs.org"
        assert result.category == HostCategory.KNOWN_REGISTRY


# ============================================================================
# classify_host: Edge cases
# ============================================================================

class TestClassifyHostEdgeCases:
    """Edge cases for classify_host."""

    def test_empty_string(self):
        result = classify_host("")
        assert result.category == HostCategory.UNKNOWN

    def test_none_input(self):
        result = classify_host(None)
        assert result.category == HostCategory.UNKNOWN

    def test_whitespace_only(self):
        result = classify_host("   ")
        assert result.category == HostCategory.UNKNOWN

    def test_result_is_frozen_dataclass(self):
        result = classify_host("localhost")
        with pytest.raises(AttributeError):
            result.host = "other"

    def test_is_local_property(self):
        result = classify_host("localhost")
        assert result.is_local is True
        assert result.is_known_registry is False
        assert result.is_unknown is False

    def test_is_known_registry_property(self):
        result = classify_host("https://pypi.org/simple/")
        assert result.is_known_registry is True
        assert result.is_local is False
        assert result.is_unknown is False


# ============================================================================
# extract_url_from_tokens: curl
# ============================================================================

class TestExtractURLCurl:
    """URL extraction from curl token lists."""

    def test_simple_curl(self):
        tokens = ["curl", "https://example.com"]
        assert extract_url_from_tokens(tokens) == "https://example.com"

    def test_curl_with_flags_before_url(self):
        tokens = ["curl", "-s", "-L", "https://registry.npmjs.org/express"]
        assert extract_url_from_tokens(tokens) == "https://registry.npmjs.org/express"

    def test_curl_with_header(self):
        """Header flag (-H) consumes next token -- should not be mistaken for URL."""
        tokens = ["curl", "-H", "Authorization: Bearer tok", "https://api.github.com/repos"]
        assert extract_url_from_tokens(tokens) == "https://api.github.com/repos"

    def test_curl_with_output(self):
        tokens = ["curl", "-o", "/tmp/out.json", "https://pypi.org/simple/"]
        assert extract_url_from_tokens(tokens) == "https://pypi.org/simple/"

    def test_curl_with_request_method(self):
        tokens = ["curl", "-X", "POST", "-d", '{"key":"val"}', "https://api.example.com"]
        assert extract_url_from_tokens(tokens) == "https://api.example.com"

    def test_curl_bundled_short_flags(self):
        tokens = ["curl", "-sLk", "https://localhost:8080/health"]
        assert extract_url_from_tokens(tokens) == "https://localhost:8080/health"

    def test_curl_with_inline_long_flag(self):
        tokens = ["curl", "--max-time=30", "https://example.com"]
        assert extract_url_from_tokens(tokens) == "https://example.com"

    def test_curl_no_url(self):
        tokens = ["curl", "-v", "-s"]
        assert extract_url_from_tokens(tokens) is None

    def test_curl_double_dash_separator(self):
        tokens = ["curl", "--", "https://example.com"]
        assert extract_url_from_tokens(tokens) == "https://example.com"

    def test_curl_complex_invocation(self):
        """AC-10: complex curl with multiple flags before URL."""
        tokens = [
            "curl", "-s", "-L",
            "-H", "Accept: application/json",
            "-H", "X-Custom: value",
            "--max-time", "30",
            "--connect-timeout", "5",
            "https://registry.npmjs.org/express"
        ]
        assert extract_url_from_tokens(tokens) == "https://registry.npmjs.org/express"


# ============================================================================
# extract_url_from_tokens: wget
# ============================================================================

class TestExtractURLWget:
    """URL extraction from wget token lists."""

    def test_simple_wget(self):
        tokens = ["wget", "http://localhost:3000/health"]
        assert extract_url_from_tokens(tokens) == "http://localhost:3000/health"

    def test_wget_with_output(self):
        tokens = ["wget", "-O", "/tmp/file", "https://example.com/file.tar.gz"]
        assert extract_url_from_tokens(tokens) == "https://example.com/file.tar.gz"

    def test_wget_with_user_agent(self):
        tokens = ["wget", "-U", "Mozilla/5.0", "https://pypi.org/simple/"]
        assert extract_url_from_tokens(tokens) == "https://pypi.org/simple/"

    def test_wget_no_url(self):
        tokens = ["wget", "--quiet"]
        assert extract_url_from_tokens(tokens) is None

    def test_wget_with_header(self):
        tokens = ["wget", "--header", "Authorization: Bearer tok", "https://api.github.com/"]
        assert extract_url_from_tokens(tokens) == "https://api.github.com/"


# ============================================================================
# extract_url_from_tokens: httpie
# ============================================================================

class TestExtractURLHttpie:
    """URL extraction from httpie (http/https) token lists."""

    def test_httpie_simple_get(self):
        tokens = ["http", "https://api.github.com/repos"]
        assert extract_url_from_tokens(tokens) == "https://api.github.com/repos"

    def test_httpie_with_method(self):
        tokens = ["http", "GET", "https://api.github.com/repos"]
        assert extract_url_from_tokens(tokens) == "https://api.github.com/repos"

    def test_httpie_post_with_data(self):
        tokens = ["http", "POST", "https://api.example.com", "name=value"]
        assert extract_url_from_tokens(tokens) == "https://api.example.com"

    def test_httpie_skip_data_items(self):
        """Data items (key=value) should not be returned as the URL."""
        tokens = ["http", "https://example.com", "key=value", "other:=json"]
        assert extract_url_from_tokens(tokens) == "https://example.com"

    def test_httpie_no_url(self):
        tokens = ["http", "--verbose"]
        assert extract_url_from_tokens(tokens) is None


# ============================================================================
# extract_url_from_tokens: edge cases
# ============================================================================

class TestExtractURLEdgeCases:
    """Edge cases for URL extraction."""

    def test_empty_tokens(self):
        assert extract_url_from_tokens([]) is None

    def test_none_tokens(self):
        assert extract_url_from_tokens(None) is None

    def test_unknown_command_generic_fallback(self):
        """Unknown commands use generic extraction (first non-flag arg)."""
        tokens = ["somecommand", "-v", "https://example.com"]
        assert extract_url_from_tokens(tokens) == "https://example.com"

    def test_command_only_no_args(self):
        tokens = ["curl"]
        assert extract_url_from_tokens(tokens) is None


# ============================================================================
# Integration-style: classify_host + extract_url_from_tokens together
# ============================================================================

class TestNetworkHostIntegration:
    """AC-10: End-to-end host classification from command tokens."""

    def test_curl_known_registry_read(self):
        """curl https://registry.npmjs.org/express = known registry."""
        tokens = ["curl", "https://registry.npmjs.org/express"]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.KNOWN_REGISTRY
        assert result.host == "registry.npmjs.org"

    def test_curl_unknown_host(self):
        """curl https://evil.com/api = unknown host."""
        tokens = ["curl", "https://evil.com/api"]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.UNKNOWN
        assert result.host == "evil.com"

    def test_wget_localhost(self):
        """wget http://localhost:3000/health = localhost."""
        tokens = ["wget", "http://localhost:3000/health"]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.LOCALHOST
        assert result.host == "localhost"

    def test_curl_with_many_flags_known_registry(self):
        """Complex curl invocation with known registry URL."""
        tokens = [
            "curl", "-s", "-L",
            "-H", "Accept: application/json",
            "--connect-timeout", "10",
            "https://registry.npmjs.org/express"
        ]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.KNOWN_REGISTRY

    def test_httpie_github_api(self):
        """httpie to known GitHub API."""
        tokens = ["http", "GET", "https://api.github.com/repos/owner/repo"]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.KNOWN_REGISTRY
        assert result.host == "api.github.com"

    def test_curl_private_ip(self):
        """curl to private network IP."""
        tokens = ["curl", "http://10.0.1.50:9090/metrics"]
        url = extract_url_from_tokens(tokens)
        assert url is not None
        result = classify_host(url)
        assert result.category == HostCategory.LOCALHOST

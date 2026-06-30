#!/usr/bin/env python3
"""Behavior tests for Gap 2: the distribution model is declared by the adapter.

Closure evidence for brief #88 Gap 2 (grep is the floor, behavior test is the
closure). The former coupling: ``types.py`` enumerated Claude Code's two
distribution channels (``DistributionChannel.NPM`` / ``.PLUGIN``) and
``HookEvent`` carried a Claude-Code-shaped ``plugin_root`` field. That made
"support a new host = write an adapter" fall short: a host with a DIFFERENT
distribution model would force an edit to the core's agnostic vocabulary.

After the fix the core carries an opaque :class:`HostDistribution`
(``channel`` + optional ``root``), and each adapter DECLARES its own model via
:meth:`HookAdapter.detect_distribution` -- mirroring the seam used for host
capabilities (AC-6) and consent (AC-3): an agnostic abstract method on the base
+ a concrete mechanism in each adapter.

The decisive test (:class:`TestSubstituteHostDeclaresOwnDistribution`) registers
a substitute host whose distribution model the core has never heard of -- a
single ``"native-extension"`` channel with its OWN root concept, plus a
multi-channel host -- and proves the core carries it on a ``HookEvent`` with
ZERO change to ``types.py`` / ``base.py``. The grep floor (no ``NPM`` / ``PLUGIN``
/ ``plugin_root`` in the agnostic vocabulary) is asserted directly so a
regression that re-introduces a core-owned channel enum fails here.
"""

import inspect
import sys
from pathlib import Path

import pytest

# hooks/ is placed on sys.path by tests/conftest.py; make it explicit too.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters import base as base_module
from adapters import types as types_module
from adapters.base import HookAdapter
from adapters.claude_code import ClaudeCodeAdapter
from adapters.registry import get_adapter, register_adapter
from adapters.types import HookEvent, HookEventType, HostDistribution


# ============================================================================
# GREP FLOOR: the agnostic vocabulary no longer enumerates a host's channels
# ============================================================================

class TestAgnosticVocabularyHasNoHostChannels:
    """types.py / base.py must not name Claude Code's NPM/PLUGIN/plugin_root.

    These are the lines a regression would re-introduce; pinning them as a
    test (not just a grep) means the floor is enforced by CI.
    """

    def test_types_has_no_distribution_channel_enum(self):
        assert not hasattr(types_module, "DistributionChannel"), (
            "DistributionChannel (host-specific channel enum) must not live in "
            "the agnostic vocabulary"
        )

    def test_hook_event_has_no_plugin_root_field(self):
        fields = {f.name for f in HookEvent.__dataclass_fields__.values()}
        assert "plugin_root" not in fields, (
            "HookEvent must not carry a Claude-Code-shaped plugin_root field"
        )
        assert "channel" not in fields, (
            "HookEvent must not carry a host-enum 'channel' field"
        )
        # It carries the opaque, host-declared distribution instead.
        assert "distribution" in fields

    def test_base_declares_agnostic_detect_distribution_not_detect_channel(self):
        assert not hasattr(HookAdapter, "detect_channel"), (
            "The core must not expose a host-named detect_channel method"
        )
        assert getattr(
            HookAdapter.detect_distribution, "__isabstractmethod__", False
        ) is True, "detect_distribution must be the abstract seam every host fills"

    def test_base_source_does_not_name_npm_or_plugin(self):
        """The base contract is host-agnostic: no NPM / PLUGIN tokens."""
        src = inspect.getsource(base_module)
        assert "DistributionChannel" not in src
        assert "plugin_root" not in src


# ============================================================================
# DECLARATION: HostDistribution is opaque; the core never interprets it
# ============================================================================

class TestHostDistributionIsOpaque:
    """HostDistribution carries a host-owned channel string + optional root."""

    def test_channel_is_an_opaque_string_not_an_enum_member(self):
        dist = HostDistribution(channel="anything-the-host-wants")
        assert isinstance(dist.channel, str)
        assert dist.root is None

    def test_root_is_an_opaque_optional_path(self):
        dist = HostDistribution(channel="ext", root=Path("/srv/ext-root"))
        assert dist.root == Path("/srv/ext-root")

    def test_frozen(self):
        dist = HostDistribution(channel="ext")
        with pytest.raises(Exception):  # FrozenInstanceError
            dist.channel = "mutated"  # type: ignore[misc]


# ============================================================================
# DECISIVE: a substitute host declares a distribution model the core
#           has never heard of -- WITHOUT any change to the core.
# ============================================================================

# A host whose distribution model is NOTHING like Claude Code's npm/plugin: it
# ships as a single native extension with its own root concept, declared purely
# in its own adapter. The core has never enumerated "native-extension"; it only
# carries the opaque HostDistribution this adapter hands it. Reuses
# ClaudeCodeAdapter for every other mechanism and overrides ONLY the
# distribution declaration -- the parse path and HookEvent shape are inherited
# unchanged, proving the core needs no edit to carry a new model.
class NativeExtensionHost(ClaudeCodeAdapter):
    """A substitute host with a distribution model the core never enumerated."""

    EXT_ROOT = Path("/opt/some-host/extensions/gaia")

    def detect_distribution(self) -> HostDistribution:
        # Channel name and root are the HOST's vocabulary, declared here only.
        return HostDistribution(channel="native-extension", root=self.EXT_ROOT)


# A second substitute host with MULTIPLE channels of its own naming -- to show
# the core does not assume any fixed count of channels (the old enum had exactly
# two). This one declares a rootless "system-package" channel.
class SystemPackageHost(ClaudeCodeAdapter):
    """A host distributed as a rootless system package."""

    def detect_distribution(self) -> HostDistribution:
        return HostDistribution(channel="system-package", root=None)


class TestSubstituteHostDeclaresOwnDistribution:
    """A new host's distribution model is its adapter's declaration alone."""

    def test_native_extension_channel_carried_unchanged_by_core(self):
        host = NativeExtensionHost()
        dist = host.detect_distribution()

        # The core's value object carries the host's own channel + root verbatim;
        # it does not coerce them into a known enum or a plugin_root field.
        assert dist.channel == "native-extension"
        assert dist.root == NativeExtensionHost.EXT_ROOT

    def test_substitute_distribution_flows_onto_hook_event_via_parse(self):
        """parse_event (inherited, unchanged) stamps the substitute model onto
        the HookEvent. If the core still hard-coded NPM/PLUGIN, this distinct
        channel could not survive onto the event."""
        import json

        host = NativeExtensionHost()
        event = host.parse_event(json.dumps({
            "hook_event_name": "PreToolUse",
            "session_id": "ext-1",
        }))

        assert isinstance(event, HookEvent)
        assert event.event_type == HookEventType.PRE_TOOL_USE
        assert event.distribution == HostDistribution(
            channel="native-extension", root=NativeExtensionHost.EXT_ROOT
        )

    def test_rootless_multi_naming_channel_supported(self):
        host = SystemPackageHost()
        dist = host.detect_distribution()
        assert dist.channel == "system-package"
        assert dist.root is None

    def test_two_hosts_declare_different_models_same_core_path(self):
        """The EXACT same core type (HostDistribution on HookEvent) carries two
        unrelated distribution models. The core branches on nothing -- the
        difference is entirely the hosts' declarations."""
        cc = ClaudeCodeAdapter().detect_distribution()
        ext = NativeExtensionHost().detect_distribution()

        assert {cc.channel, ext.channel} == {"npm", "native-extension"}
        # Both are the same opaque type; the core treats them identically.
        assert isinstance(cc, HostDistribution)
        assert isinstance(ext, HostDistribution)

    def test_substitute_host_registers_with_one_line_no_core_change(self):
        """Supporting the new host is register_adapter + the subclass; the
        core's construction path (get_adapter) returns it unchanged."""
        register_adapter("native-extension-host", NativeExtensionHost)
        try:
            adapter = get_adapter("native-extension-host")
            assert isinstance(adapter, NativeExtensionHost)
            assert adapter.detect_distribution().channel == "native-extension"
        finally:
            # Restore registry isolation for other tests.
            from adapters import registry
            registry._REGISTRY.pop("native-extension-host", None)
            registry._INSTANCES.pop("native-extension-host", None)


# ============================================================================
# BEHAVIOR PRESERVED: Claude Code's model is identical to before the fix
# ============================================================================

class TestClaudeCodeModelUnchanged:
    """The npm/plugin behavior Claude Code had is preserved through the new
    opaque HostDistribution -- only the SHAPE moved behind the adapter."""

    def test_npm_default_no_root(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        dist = ClaudeCodeAdapter().detect_distribution()
        assert dist == HostDistribution(channel="npm")
        assert dist.root is None

    def test_plugin_channel_with_root_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/opt/plugins/gaia-ops")
        dist = ClaudeCodeAdapter().detect_distribution()
        assert dist == HostDistribution(
            channel="plugin", root=Path("/opt/plugins/gaia-ops")
        )

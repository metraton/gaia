"""
Cross-Layer Consistency Tests

These tests validate that the different layers of the system agree with each other.
They catch the class of bugs where "each module works in isolation but the system
is inconsistent" — the #1 source of silent failures in gaia-ops.

What these tests cover:
1. plugin_setup.py permissions ↔ blocked_commands.py consistency
2. Security tier classification matches skill definitions
3. bash_validator enforces defense-in-depth order (deny before allow)
4. task_validator agents/indicators match CLAUDE.md and skills
5. Skills cross-references point to files that exist
6. plugin_setup.py permissions are present in live settings.local.json
"""

import json
import re
import sys
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Setup: resolve paths and add hooks to sys.path
# ---------------------------------------------------------------------------
GAIA_OPS_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = GAIA_OPS_ROOT / "hooks"
HOOKS_MODULES_DIR = HOOKS_DIR / "modules"
SKILLS_DIR = GAIA_OPS_ROOT / "skills"
TEMPLATES_DIR = GAIA_OPS_ROOT / "templates"
CONFIG_DIR = GAIA_OPS_ROOT / "config"

# Add hooks to path for imports
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_commands import BLOCKED_PATTERNS, get_blocked_patterns
from modules.security.tiers import (
    ULTRA_COMMON_T0_COMMANDS,
    T1_PATTERNS,
    T2_PATTERNS,
    SecurityTier,
    classify_command_tier,
)
from modules.tools.task_validator import (
    AVAILABLE_AGENTS,
    META_AGENTS,
    T3_KEYWORDS,
)
from modules.security.approval_constants import NONCE_APPROVAL_PATTERN, NONCE_APPROVAL_PREFIX
from modules.security.approval_messages import (
    CANONICAL_APPROVAL_TOKEN,
    LATEST_BLOCKED_COMMAND_PHRASE,
)
from modules.security.gitops_validator import (
    FORBIDDEN_FLUX_COMMANDS,
    FORBIDDEN_HELM_COMMANDS,
    FORBIDDEN_KUBECTL_COMMANDS,
)


def _load_permissions_from_plugin_setup() -> dict:
    """Load permissions from plugin_setup.py (the single source of truth).

    Returns a dict with 'permissions' key containing allow/deny/ask lists.
    """
    from modules.core.plugin_setup import OPS_PERMISSIONS
    return OPS_PERMISSIONS


def _load_settings_live_local() -> dict:
    """Load the live .claude/settings.local.json if available.

    Permissions and env vars now live in settings.local.json (not settings.json).
    """
    for candidate in [
        GAIA_OPS_ROOT.parent / ".claude" / "settings.local.json",
        Path.cwd() / ".claude" / "settings.local.json",
    ]:
        if candidate.exists():
            return json.loads(candidate.read_text())
    return None


# ===========================================================================
# 1. settings.json ↔ code consistency
# ===========================================================================

class TestSettingsCodeConsistency:
    """Verify plugin_setup.py permissions align with code classifications.

    Permissions are defined in plugin_setup.py (single source of truth)
    and merged into settings.local.json at install/update time.
    """

    @pytest.fixture(scope="class")
    def settings(self):
        return _load_permissions_from_plugin_setup()

    @pytest.fixture(scope="class")
    def allow_list(self, settings):
        return settings.get("permissions", {}).get("allow", [])

    @pytest.fixture(scope="class")
    def deny_list(self, settings):
        return settings.get("permissions", {}).get("deny", [])

    @pytest.fixture(scope="class")
    def ask_list(self, settings):
        return settings.get("permissions", {}).get("ask", [])

    def _extract_command(self, entry: str) -> str:
        """Extract command from Bash(command:*) format."""
        match = re.match(r'Bash\((.+?):\*\)', entry)
        return match.group(1) if match else ""

    def test_deny_commands_not_in_allow(self, allow_list, deny_list):
        """Commands in deny must NOT also be in allow."""
        allow_cmds = {self._extract_command(e) for e in allow_list if e.startswith("Bash(")}
        deny_cmds = {self._extract_command(e) for e in deny_list if e.startswith("Bash(")}

        overlap = allow_cmds & deny_cmds
        assert not overlap, f"Commands in BOTH allow and deny: {overlap}"

    def test_ask_commands_not_in_allow(self, allow_list, ask_list):
        """Commands in ask must NOT also be in allow (would bypass approval)."""
        allow_cmds = {self._extract_command(e) for e in allow_list if e.startswith("Bash(")}
        ask_cmds = {self._extract_command(e) for e in ask_list if e.startswith("Bash(")}

        overlap = allow_cmds & ask_cmds
        assert not overlap, f"Commands in BOTH allow and ask: {overlap}"

    def test_t3_mutation_commands_not_in_allow(self, allow_list):
        """State-modifying commands (T3 per security-tiers skill) must NOT be in allow."""
        allow_cmds = {self._extract_command(e) for e in allow_list if e.startswith("Bash(")}

        # These are T3 mutations that should NEVER be auto-allowed
        t3_mutations = {
            "terraform apply", "terragrunt apply",
            "terraform destroy", "terragrunt destroy",
            "kubectl apply", "kubectl delete", "kubectl create",
            "kubectl exec", "kubectl label", "kubectl annotate",
            "kubectl cordon", "kubectl uncordon",
            "helm install", "helm upgrade", "helm rollback",
            "helm uninstall", "helm delete",
            "flux suspend", "flux resume",
            "git push", "git commit", "git rebase",
            "git reset", "git merge",
        }

        violations = allow_cmds & t3_mutations
        assert not violations, (
            f"T3 mutation commands in allow (should be in ask): {violations}"
        )

    def test_flux_suspend_resume_protected(self, ask_list, deny_list):
        """flux suspend/resume must be protected (in ask, deny, or enforced by hooks).

        When settings uses Bash(*) with deny-only, the hook layer (gitops_validator)
        enforces approval for flux suspend/resume at runtime.
        """
        ask_cmds = {self._extract_command(e) for e in ask_list if e.startswith("Bash(")}
        deny_cmds = {self._extract_command(e) for e in deny_list if e.startswith("Bash(")}
        protected = ask_cmds | deny_cmds

        for cmd in ["flux suspend", "flux resume"]:
            if cmd not in protected:
                # Not in settings lists -- verify the hook layer covers it
                from modules.security.gitops_validator import is_forbidden_gitops_command
                assert is_forbidden_gitops_command(cmd), (
                    f"'{cmd}' not protected by settings.json or gitops_validator hook"
                )

    def test_gitops_forbidden_commands_protected(self, ask_list, deny_list):
        """Commands forbidden by gitops_validator must be in ask/deny or enforced by hooks.

        When settings uses Bash(*) with deny-only, the hook layer (gitops_validator)
        provides the enforcement for commands not explicitly in the deny list.
        """
        ask_cmds = {self._extract_command(e) for e in ask_list if e.startswith("Bash(")}
        deny_cmds = {self._extract_command(e) for e in deny_list if e.startswith("Bash(")}
        protected = ask_cmds | deny_cmds

        # flux commands are forbidden in gitops_validator
        for pattern in FORBIDDEN_FLUX_COMMANDS:
            # Extract the command from the regex (e.g., r"flux\s+suspend" -> "flux suspend")
            cmd = re.sub(r'\\s\+', ' ', pattern).strip()
            found = any(cmd in p for p in protected)
            if not found:
                # Verify the hook layer covers it
                from modules.security.gitops_validator import is_forbidden_gitops_command
                assert is_forbidden_gitops_command(cmd), (
                    f"Forbidden gitops command '{cmd}' not protected by settings.json or hook"
                )


# ===========================================================================
# 2. Security tier consistency
# ===========================================================================

class TestTierConsistency:
    """Verify tier classification matches skill definitions."""

    def test_ultra_common_t0_are_genuinely_read_only(self):
        """ULTRA_COMMON_T0_COMMANDS must contain core read-only commands."""
        genuinely_read_only = {
            "ls", "pwd", "cat", "echo", "git status", "git diff",
            "git log", "kubectl get",
        }

        assert genuinely_read_only.issubset(ULTRA_COMMON_T0_COMMANDS), (
            f"ULTRA_COMMON_T0_COMMANDS missing expected entries: "
            f"{genuinely_read_only - ULTRA_COMMON_T0_COMMANDS}"
        )

    def test_terraform_plan_is_t2_not_t0(self):
        """terraform plan is a simulation (T2), not read-only (T0)."""
        tier = classify_command_tier("terraform plan -out=plan.tfplan")
        assert tier == SecurityTier.T2_DRY_RUN, (
            f"terraform plan should be T2, got {tier}"
        )

    def test_terraform_validate_is_t1(self):
        """terraform validate is local validation (T1)."""
        tier = classify_command_tier("terraform validate")
        assert tier == SecurityTier.T1_VALIDATION, (
            f"terraform validate should be T1, got {tier}"
        )

    def test_kubectl_diff_is_t2(self):
        """kubectl diff is simulation (T2)."""
        tier = classify_command_tier("kubectl diff -f manifest.yaml")
        assert tier == SecurityTier.T2_DRY_RUN, (
            f"kubectl diff should be T2, got {tier}"
        )

    def test_dry_run_flag_always_t2(self):
        """Any command with --dry-run must be classified T2."""
        commands = [
            "kubectl apply -f manifest.yaml --dry-run=client",
            "helm upgrade release chart --dry-run",
            "kubectl create deployment test --dry-run=server",
        ]
        for cmd in commands:
            tier = classify_command_tier(cmd)
            assert tier == SecurityTier.T2_DRY_RUN, (
                f"'{cmd}' with --dry-run should be T2, got {tier}"
            )

    def test_t1_patterns_are_local_only(self):
        """T1 patterns must only match local validation operations."""
        expected_t1_keywords = {"validate", "lint", "check", "fmt"}
        actual_t1_keywords = set()
        for pattern in T1_PATTERNS:
            # Extract word from \bword\b pattern
            match = re.search(r'\\b(\w+)\\b', pattern)
            if match:
                actual_t1_keywords.add(match.group(1))

        assert expected_t1_keywords.issubset(actual_t1_keywords), (
            f"T1 patterns missing expected keywords: {expected_t1_keywords - actual_t1_keywords}"
        )

    def test_t2_patterns_are_simulations(self):
        """T2 patterns must include core simulation operations."""
        expected_t2_keywords = {"plan", "template", "diff"}
        actual_t2_keywords = set()
        for pattern in T2_PATTERNS:
            match = re.search(r'\\b(\w+)\\b', pattern)
            if match:
                actual_t2_keywords.add(match.group(1))

        assert expected_t2_keywords.issubset(actual_t2_keywords), (
            f"T2 patterns missing expected keywords: {expected_t2_keywords - actual_t2_keywords}"
        )


# ===========================================================================
# 3. Defense-in-depth: bash_validator order
# ===========================================================================

class TestDefenseInDepth:
    """Verify that security checks happen in the correct order."""

    def test_blocked_checked_before_safe(self):
        """bash_validator.validate() must check blocked_commands BEFORE mutative verb detection.

        The blocked check runs in validate() (the public entry point) before
        dispatching to _validate_single_command.  This test verifies the
        actual defense-in-depth order in the method that callers invoke.
        """
        source = (HOOKS_MODULES_DIR / "tools" / "bash_validator.py").read_text()

        # Extract the validate() method body (the public entry point).
        # The signature may span multiple lines (e.g. def validate(\n  self, ...)).
        method_match = re.search(
            r'def validate\(\s*self.*?\).*?:\n(.*?)(?=\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "Could not find validate method"

        method_body = method_match.group(1)

        blocked_pos = method_body.find("is_blocked_command")
        safe_pos = method_body.find("_validate_single_command")

        assert blocked_pos != -1, "is_blocked_command call not found in validate()"
        assert safe_pos != -1, "_validate_single_command call not found in validate()"
        assert blocked_pos < safe_pos, (
            "SECURITY: is_blocked_command must be checked BEFORE _validate_single_command "
            f"(blocked at pos {blocked_pos}, single_command at pos {safe_pos})"
        )


# ===========================================================================
# 4. task_validator consistency
# ===========================================================================

class TestTaskValidatorConsistency:
    """Verify task_validator configuration matches the rest of the system."""

    def test_nonce_approval_pattern_is_current(self):
        """Nonce approval must use the exact canonical token format."""
        match = NONCE_APPROVAL_PATTERN.search(
            f"{NONCE_APPROVAL_PREFIX}deadbeefdeadbeefdeadbeefdeadbeef"
        )
        assert match is not None
        assert match.group(1) == "deadbeefdeadbeefdeadbeefdeadbeef"

    def test_meta_agents_are_subset_of_available(self):
        """All META_AGENTS must exist in AVAILABLE_AGENTS."""
        missing = set(META_AGENTS) - set(AVAILABLE_AGENTS)
        assert not missing, f"META_AGENTS not in AVAILABLE_AGENTS: {missing}"

    def test_t3_keywords_match_security_tiers_skill(self):
        """T3 keywords must match operations classified as T3 in security-tiers skill."""
        # These are the canonical T3 operations from the skill
        expected_t3_operations = {
            "git commit", "git push", "terraform apply", "terragrunt apply",
            "kubectl apply", "kubectl delete", "kubectl create",
            "helm install", "helm upgrade",
        }

        actual = set(T3_KEYWORDS)
        # Allow superset (more specific variants like "git push origin main" are OK)
        missing = expected_t3_operations - actual
        assert not missing, (
            f"T3 operations from security-tiers skill missing in T3_KEYWORDS: {missing}"
        )

    def test_approval_mechanism_in_execution_skill(self):
        """The execution skill must reference the approval mechanism."""
        execution_skill = SKILLS_DIR / "execution" / "SKILL.md"
        if not execution_skill.exists():
            pytest.skip("execution/SKILL.md not found")

        content = execution_skill.read_text().lower()
        assert "elicitationresult" in content or "askuserquestion" in content or CANONICAL_APPROVAL_TOKEN.lower() in content, (
            "Execution skill must reference the approval mechanism "
            "(ElicitationResult, AskUserQuestion, or canonical token)"
        )


# ===========================================================================
# 5. Skills cross-references
# ===========================================================================

class TestSkillsCrossReferences:
    """Verify that skills reference each other correctly."""

    @pytest.fixture(scope="class")
    def all_skills(self):
        """Load all skill names."""
        return {d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists()}

    def test_agent_protocol_references_exist(self, all_skills):
        """Skills referenced by agent-protocol must exist."""
        content = (SKILLS_DIR / "agent-protocol" / "SKILL.md").read_text()

        # Extract skill references: backtick-wrapped names followed by "skill"
        refs = re.findall(r'`(\w[\w-]+)`\s+skill', content)
        for ref in refs:
            assert ref in all_skills, (
                f"agent-protocol references skill '{ref}' which doesn't exist. "
                f"Available: {sorted(all_skills)}"
            )

    def test_execution_skill_references_approval_mechanism(self):
        """Execution skill must reference the approval mechanism."""
        content = (SKILLS_DIR / "execution" / "SKILL.md").read_text().lower()
        assert "elicitationresult" in content or "askuserquestion" in content or CANONICAL_APPROVAL_TOKEN.lower() in content, (
            "Execution skill must reference the approval mechanism "
            "(ElicitationResult, AskUserQuestion, or canonical token)"
        )

    def test_approval_skill_references_approval_id_concept(self):
        """Approval skill must reference the approval_id mechanism."""
        content = (SKILLS_DIR / "approval" / "SKILL.md").read_text().lower()
        assert "approval_id" in content, (
            "Approval skill must mention the approval_id mechanism"
        )

    def test_identity_references_approval_workflow(self):
        """Approval workflow must be documented in identity or orchestrator-approval skill.

        The ops_identity.py delegates approval protocol details to the
        orchestrator-approval skill. The approval contract must exist in the system.
        """
        identity_path = GAIA_OPS_ROOT / "hooks" / "modules" / "identity" / "ops_identity.py"
        identity_content = identity_path.read_text().lower()
        approval_skill_path = SKILLS_DIR / "orchestrator-approval" / "SKILL.md"
        combined = identity_content
        if approval_skill_path.exists():
            combined += "\n" + approval_skill_path.read_text().lower()
        assert "elicitationresult" in combined or "askuserquestion" in combined or "nonce" in combined, (
            "System must mention the approval mechanism (ElicitationResult, AskUserQuestion, or nonce)"
        )
        assert "approve" in combined or "approval" in combined, (
            "System must mention the approval workflow"
        )

    def test_security_tiers_t3_references_agent_protocol(self):
        """Security-tiers skill must reference agent-protocol for T3 workflow."""
        content = (SKILLS_DIR / "security-tiers" / "SKILL.md").read_text()
        assert "agent-protocol" in content, (
            "security-tiers must reference agent-protocol for T3 workflow"
        )


# ===========================================================================
# 6. Template sync
# ===========================================================================

class TestPermissionsSync:
    """Verify plugin_setup.py permissions are a subset of live settings.local.json.

    plugin_setup.py is the source of truth for GAIA permissions.
    settings.local.json is where they are merged at install/update time.
    The live file may have additional user-added entries (smart merge).
    """

    def test_plugin_deny_rules_in_live_local(self):
        """All deny rules from plugin_setup.py must be present in live settings.local.json."""
        source = _load_permissions_from_plugin_setup()
        live = _load_settings_live_local()
        if live is None:
            pytest.skip("Live .claude/settings.local.json not found")

        source_deny = set(source.get("permissions", {}).get("deny", []))
        live_deny = set(live.get("permissions", {}).get("deny", []))

        missing_in_live = source_deny - live_deny
        assert not missing_in_live, (
            f"Deny rules from plugin_setup.py missing in settings.local.json:\n"
            f"{missing_in_live}"
        )

    def test_plugin_allow_rules_in_live_local(self):
        """All allow rules from plugin_setup.py must be present in live settings.local.json."""
        source = _load_permissions_from_plugin_setup()
        live = _load_settings_live_local()
        if live is None:
            pytest.skip("Live .claude/settings.local.json not found")

        source_allow = set(source.get("permissions", {}).get("allow", []))
        live_allow = set(live.get("permissions", {}).get("allow", []))

        missing_in_live = source_allow - live_allow
        assert not missing_in_live, (
            f"Allow rules from plugin_setup.py missing in settings.local.json:\n"
            f"{missing_in_live}"
        )

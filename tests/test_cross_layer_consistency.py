"""
Cross-Layer Consistency Tests

These tests validate that the different layers of the system agree with each other.
They catch the class of bugs where "each module works in isolation but the system
is inconsistent" — the #1 source of silent failures in gaia-ops.

What these tests cover:
1. settings.json ↔ safe_commands.py ↔ blocked_commands.py consistency
2. Security tier classification matches skill definitions
3. bash_validator enforces defense-in-depth order (deny before allow)
4. task_validator agents/indicators match CLAUDE.md and skills
5. Skills cross-references point to files that exist
6. settings.json template stays in sync with settings.json
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

from modules.security.safe_commands import (
    ALWAYS_SAFE_COMMANDS,
    ALWAYS_SAFE_MULTIWORD,
    SAFE_PATTERNS,
    CONDITIONAL_SAFE_COMMANDS,
)
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
    APPROVAL_INDICATORS,
)
from modules.security.gitops_validator import (
    FORBIDDEN_FLUX_COMMANDS,
    FORBIDDEN_HELM_COMMANDS,
    FORBIDDEN_KUBECTL_COMMANDS,
)


def _load_settings_template() -> dict:
    """Load settings.template.json."""
    path = TEMPLATES_DIR / "settings.template.json"
    if not path.exists():
        pytest.skip("settings.template.json not found")
    return json.loads(path.read_text())


def _load_settings_live() -> dict:
    """Load the live .claude/settings.json if available."""
    # Try common locations
    for candidate in [
        GAIA_OPS_ROOT.parent / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.json",
    ]:
        if candidate.exists():
            return json.loads(candidate.read_text())
    return None


# ===========================================================================
# 1. settings.json ↔ code consistency
# ===========================================================================

class TestSettingsCodeConsistency:
    """Verify settings.json permissions align with code classifications."""

    @pytest.fixture(scope="class")
    def settings(self):
        return _load_settings_template()

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

    def test_flux_suspend_resume_in_ask(self, ask_list):
        """flux suspend/resume must require approval (they stop/start reconciliation)."""
        ask_cmds = {self._extract_command(e) for e in ask_list if e.startswith("Bash(")}
        assert "flux suspend" in ask_cmds, "flux suspend must be in ask list"
        assert "flux resume" in ask_cmds, "flux resume must be in ask list"

    def test_gitops_forbidden_commands_in_ask_or_deny(self, ask_list, deny_list):
        """Commands forbidden by gitops_validator must be in ask or deny."""
        ask_cmds = {self._extract_command(e) for e in ask_list if e.startswith("Bash(")}
        deny_cmds = {self._extract_command(e) for e in deny_list if e.startswith("Bash(")}
        protected = ask_cmds | deny_cmds

        # flux suspend/resume are forbidden in gitops_validator
        for pattern in FORBIDDEN_FLUX_COMMANDS:
            # Extract the command from the regex (e.g., r"flux\s+suspend" -> "flux suspend")
            cmd = re.sub(r'\\s\+', ' ', pattern).strip()
            found = any(cmd in p for p in protected)
            assert found, f"Forbidden gitops command '{cmd}' not protected in settings.json"


# ===========================================================================
# 2. Security tier consistency
# ===========================================================================

class TestTierConsistency:
    """Verify tier classification matches skill definitions."""

    def test_ultra_common_t0_are_genuinely_read_only(self):
        """Every command in ULTRA_COMMON_T0_COMMANDS must be truly read-only."""
        # These are the ONLY commands that should be in the T0 fast-path
        genuinely_read_only = {
            "ls", "pwd", "cat", "echo", "git status", "git diff",
            "git log", "git branch", "kubectl get",
        }

        assert ULTRA_COMMON_T0_COMMANDS == genuinely_read_only, (
            f"ULTRA_COMMON_T0_COMMANDS has unexpected entries: "
            f"{ULTRA_COMMON_T0_COMMANDS - genuinely_read_only}"
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

        assert actual_t1_keywords == expected_t1_keywords, (
            f"T1 patterns should be {expected_t1_keywords}, got {actual_t1_keywords}"
        )

    def test_t2_patterns_are_simulations(self):
        """T2 patterns must only match simulation operations."""
        expected_t2_keywords = {"plan", "template", "diff"}
        actual_t2_keywords = set()
        for pattern in T2_PATTERNS:
            match = re.search(r'\\b(\w+)\\b', pattern)
            if match:
                actual_t2_keywords.add(match.group(1))

        assert actual_t2_keywords == expected_t2_keywords, (
            f"T2 patterns should be {expected_t2_keywords}, got {actual_t2_keywords}"
        )

    def test_always_safe_commands_are_truly_read_only(self):
        """Commands in ALWAYS_SAFE must not be able to modify state."""
        # Commands that can execute arbitrary code or modify filesystem
        not_read_only = {
            "python", "python3", "node", "ruby", "perl",  # script execution
            "tar", "gzip", "gunzip", "zip", "unzip",      # archive operations (extract writes)
            "mv", "cp", "rm", "mkdir", "touch",            # filesystem mutation
            "chmod", "chown",                               # permission changes
            "tee",                                          # writes to files
        }

        violations = ALWAYS_SAFE_COMMANDS & not_read_only
        assert not violations, (
            f"Non-read-only commands in ALWAYS_SAFE: {violations}"
        )

    def test_terraform_plan_not_in_safe_multiword(self):
        """terraform plan must NOT be in always_safe_multiword (it's T2, not T0)."""
        for cmd in ALWAYS_SAFE_MULTIWORD:
            assert cmd != "terraform plan", "terraform plan should not be in ALWAYS_SAFE_MULTIWORD"
            assert cmd != "terragrunt plan", "terragrunt plan should not be in ALWAYS_SAFE_MULTIWORD"


# ===========================================================================
# 3. Defense-in-depth: bash_validator order
# ===========================================================================

class TestDefenseInDepth:
    """Verify that security checks happen in the correct order."""

    def test_blocked_checked_before_safe(self):
        """bash_validator must check blocked_commands BEFORE safe_commands."""
        # Read the source code and verify the order
        source = (HOOKS_MODULES_DIR / "tools" / "bash_validator.py").read_text()

        # Find the positions of blocked check and safe check in _validate_single_command
        method_match = re.search(
            r'def _validate_single_command\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "Could not find _validate_single_command method"

        method_body = method_match.group(1)

        blocked_pos = method_body.find("is_blocked_command")
        safe_pos = method_body.find("is_read_only_command")

        assert blocked_pos != -1, "is_blocked_command call not found in _validate_single_command"
        assert safe_pos != -1, "is_read_only_command call not found in _validate_single_command"
        assert blocked_pos < safe_pos, (
            "SECURITY: is_blocked_command must be checked BEFORE is_read_only_command "
            f"(blocked at pos {blocked_pos}, safe at pos {safe_pos})"
        )


# ===========================================================================
# 4. task_validator consistency
# ===========================================================================

class TestTaskValidatorConsistency:
    """Verify task_validator configuration matches the rest of the system."""

    def test_approval_indicators_are_current(self):
        """Approval indicators must only contain strings from current protocol."""
        valid_indicators = {
            "user approval received",  # canonical token from execution skill
            "approved by user",        # backward-compatible variant
        }

        actual = set(APPROVAL_INDICATORS)
        unexpected = actual - valid_indicators
        assert not unexpected, (
            f"Legacy/unknown approval indicators found: {unexpected}. "
            f"These could allow accidental approval bypass."
        )

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

    def test_canonical_approval_token_in_execution_skill(self):
        """The canonical approval token must appear in the execution skill."""
        execution_skill = SKILLS_DIR / "execution" / "SKILL.md"
        if not execution_skill.exists():
            pytest.skip("execution/SKILL.md not found")

        content = execution_skill.read_text().lower()
        canonical_token = APPROVAL_INDICATORS[0]  # "user approval received"
        assert canonical_token in content, (
            f"Canonical approval token '{canonical_token}' not found in execution skill"
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

    def test_execution_skill_references_approval_token(self):
        """Execution skill must define the canonical approval token."""
        content = (SKILLS_DIR / "execution" / "SKILL.md").read_text()
        assert "User approval received" in content, (
            "Execution skill must contain the canonical approval token"
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

class TestTemplateSync:
    """Verify settings.template.json stays in sync with live settings."""

    def test_template_and_live_deny_lists_match(self):
        """The deny list must be identical between template and live settings."""
        template = _load_settings_template()
        live = _load_settings_live()
        if live is None:
            pytest.skip("Live .claude/settings.json not found")

        template_deny = set(template.get("permissions", {}).get("deny", []))
        live_deny = set(live.get("permissions", {}).get("deny", []))

        missing_in_live = template_deny - live_deny
        missing_in_template = live_deny - template_deny

        assert not missing_in_live and not missing_in_template, (
            f"Deny lists out of sync!\n"
            f"In template but not live: {missing_in_live}\n"
            f"In live but not template: {missing_in_template}"
        )

    def test_template_and_live_ask_lists_match(self):
        """The ask list must be identical between template and live settings."""
        template = _load_settings_template()
        live = _load_settings_live()
        if live is None:
            pytest.skip("Live .claude/settings.json not found")

        template_ask = set(template.get("permissions", {}).get("ask", []))
        live_ask = set(live.get("permissions", {}).get("ask", []))

        missing_in_live = template_ask - live_ask
        missing_in_template = live_ask - template_ask

        assert not missing_in_live and not missing_in_template, (
            f"Ask lists out of sync!\n"
            f"In template but not live: {missing_in_live}\n"
            f"In live but not template: {missing_in_template}"
        )

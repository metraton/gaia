#!/usr/bin/env python3
"""
Pre-tool use hook for Claude Code Agent System
Implements security policy gates, tier-based command filtering,
and routing metadata verification with centralized capabilities

Optimizations (2025-12-11):
- R1: Unified safe command lists (SAFE_COMMANDS_CONFIG as single source of truth)
- R2: Unified validation flow (classify_command_tier uses is_read_only_command)
- R3: Removed dead code (_contains_command_chaining)
- R4: Singleton ShellCommandParser instance
"""

import sys
import json
import re
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# Configure logging early - before any usage
log_file = Path(__file__).parent.parent / "logs" / f"pre_tool_use-{os.getenv('USER', 'unknown')}.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  # Also log to stderr
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"Pre-tool-use hook initialized. Logging to {log_file}")

from pre_kubectl_security import validate_gitops_workflow

# Add shell parser for compound command validation (workaround for Claude Code bug #13340)
from shell_parser import ShellCommandParser

# R4: Singleton ShellCommandParser instance - avoids multiple instantiation
_shell_parser = ShellCommandParser()

# Add workflow enforcer
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "0-guards"))
try:
    from workflow_enforcer import WorkflowEnforcer, GuardViolation
    WORKFLOW_ENFORCER_AVAILABLE = True
except ImportError:
    logger.warning("WorkflowEnforcer not available - guards will not be enforced")
    WORKFLOW_ENFORCER_AVAILABLE = False

# ============================================================================
# CLAUDE CODE ATTRIBUTION FOOTER DETECTION
# ============================================================================

def detect_claude_footers(command: str) -> bool:
    """
    Detect Claude Code attribution footers in any command.

    Looks for patterns like:
    - "Generated with Claude Code"
    - "Co-Authored-By: Claude"
    """
    forbidden_patterns = [
        r"Generated with\s+Claude Code",
        r"Co-Authored-By:\s+Claude",
    ]

    for pattern in forbidden_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True

    return False


# ============================================================================
# R1: UNIFIED SAFE COMMANDS CONFIGURATION
# ============================================================================
# Single source of truth for safe commands.
# Both auto-approval (is_read_only_command) and tier classification
# (classify_command_tier) use this configuration.
# ============================================================================

SAFE_COMMANDS_CONFIG = {
    # Commands that are ALWAYS read-only (no dangerous flags possible)
    "always_safe": {
        # System info
        "uname", "hostname", "whoami", "date", "uptime", "free", "id", "groups",
        "arch", "nproc", "lscpu", "lsmem", "locale", "printenv", "env",

        # Directory/file listing (read-only)
        "ls", "pwd", "tree", "which", "whereis", "type", "realpath", "dirname", "basename",

        # File info (read-only)
        "stat", "file", "wc", "du", "df",

        # Text processing (always read-only)
        "awk", "cut", "tr", "sort", "uniq", "head", "tail", "less", "more",
        "grep", "egrep", "fgrep", "diff", "comm",

        # Output/display (read-only - just prints to stdout)
        "echo", "printf", "true", "false",

        # JSON/YAML processing
        "jq", "yq",

        # Network diagnostics
        "ping", "traceroute", "nslookup", "dig", "host", "netstat", "ss",
        "ifconfig", "ip", "route", "arp",

        # Encoding utilities (read-only)
        "base64", "md5sum", "sha256sum", "sha1sum",

        # Archive listing (read-only)
        "tar", "gzip", "gunzip", "zip", "unzip",

        # Shell utilities
        "test", "time", "timeout", "sleep",
    },

    # Multi-word commands that are always safe (prefix matching)
    "always_safe_multiword": {
        # Git read-only
        "git status", "git diff", "git log", "git show", "git branch",
        "git remote", "git describe", "git rev-parse", "git ls-files",
        "git cat-file", "git blame", "git shortlog", "git reflog", "git tag",

        # Terraform read-only
        "terraform version", "terraform validate", "terraform fmt",
        "terraform show", "terraform output", "terraform plan",
        "terragrunt plan", "terragrunt output", "terragrunt validate",

        # Kubernetes read-only
        "kubectl get", "kubectl describe", "kubectl logs", "kubectl explain",
        "kubectl version", "kubectl cluster-info", "kubectl api-resources",
        "kubectl top", "kubectl auth",

        # Helm read-only
        "helm list", "helm status", "helm template", "helm lint",
        "helm version", "helm show", "helm search",

        # Flux read-only
        "flux check", "flux get", "flux version", "flux logs",

        # Docker read-only
        "docker ps", "docker images", "docker inspect", "docker logs",
        "docker stats", "docker version", "docker info",

        # GCP read-only (list/describe operations)
        "gcloud compute instances list", "gcloud compute instances describe",
        "gcloud container clusters list", "gcloud container clusters describe",
        "gcloud sql instances list", "gcloud sql instances describe",
        "gcloud config list", "gcloud auth list",

        # AWS read-only (list/describe operations)
        "aws ec2 describe", "aws s3 ls", "aws rds describe",
        "aws iam list", "aws iam get", "aws sts get-caller-identity",
    },

    # Commands that are read-only UNLESS certain flags are present
    "conditional_safe": {
        # sed is safe unless -i (in-place edit) is used
        "sed": [r"-i\b", r"--in-place"],

        # cat is always safe (just reads files)
        "cat": [],

        # sort is safe unless -o (output to file) is used
        "sort": [r"-o\b", r"--output"],

        # tee writes to files, but is often used in pipes for read-only display
        "tee": [],

        # curl is safe for GET, dangerous with upload flags
        "curl": [r"-T\b", r"--upload-file", r"-X\s*(PUT|POST|DELETE|PATCH)", r"--data", r"-d\b"],

        # wget is safe for download, dangerous with POST
        "wget": [r"--post-data", r"--post-file"],

        # find is read-only unless -delete or -exec with dangerous commands
        "find": [r"-delete", r"-exec\s+rm", r"-exec\s+chmod"],

        # xargs can be dangerous depending on what it executes
        "xargs": [],

        # openssl is mostly read-only
        "openssl": [],
    },
}

# Derived sets for backward compatibility and fast lookup
ALWAYS_SAFE_COMMANDS = SAFE_COMMANDS_CONFIG["always_safe"]
ALWAYS_SAFE_MULTIWORD = SAFE_COMMANDS_CONFIG["always_safe_multiword"]
CONDITIONAL_SAFE_COMMANDS = SAFE_COMMANDS_CONFIG["conditional_safe"]


def _is_single_command_safe(single_cmd: str) -> tuple[bool, str]:
    """
    Check if a single command (no operators) is read-only and safe.

    This is the core safety check for individual commands.
    Uses SAFE_COMMANDS_CONFIG as single source of truth.
    """
    if not single_cmd or not single_cmd.strip():
        return False, "Empty command"

    single_cmd = single_cmd.strip()

    # Extract base command (first word, without path)
    parts = single_cmd.split()
    if not parts:
        return False, "No command parts"

    base_cmd = parts[0]
    # Remove path if present: /usr/bin/cat -> cat
    if '/' in base_cmd:
        base_cmd = base_cmd.split('/')[-1]

    # Check multi-word commands first (more specific)
    for safe_cmd in ALWAYS_SAFE_MULTIWORD:
        if single_cmd.startswith(safe_cmd):
            return True, f"Always-safe: {safe_cmd}"

    # Check single-word always safe commands
    if base_cmd in ALWAYS_SAFE_COMMANDS:
        return True, f"Always-safe: {base_cmd}"

    # Check CONDITIONAL_SAFE_COMMANDS
    if base_cmd in CONDITIONAL_SAFE_COMMANDS:
        dangerous_patterns = CONDITIONAL_SAFE_COMMANDS[base_cmd]

        if not dangerous_patterns:
            # No dangerous patterns defined - always safe
            return True, f"Conditional-safe: {base_cmd}"

        # Check if any dangerous pattern is present
        for pattern in dangerous_patterns:
            if re.search(pattern, single_cmd):
                return False, f"Dangerous flag: {pattern}"

        # No dangerous patterns found
        return True, f"Conditional-safe: {base_cmd}"

    # Not in our safe lists
    return False, f"Not in safe list: {base_cmd}"


def is_read_only_command(command: str) -> tuple[bool, str]:
    """
    Detect if a command is purely read-only and safe to auto-approve.

    Supports compound commands - if ALL components are safe, auto-approve.

    Returns:
        (is_safe, reason) - Tuple of boolean and explanation string

    This function is used to bypass Claude Code's ASK prompt for commands
    that are clearly read-only and should not require user approval.

    Examples:
        "ls -la"                    -> True (simple safe command)
        "tail -100 file.log"        -> True (simple safe command)
        "cat file | grep foo"       -> True (all components safe)
        "ls && pwd"                 -> True (all components safe)
        "tail file || echo error"   -> True (all components safe)
        "ls && rm -rf /"            -> False (rm is dangerous)
        "cat file | kubectl apply"  -> False (kubectl apply is dangerous)
    """
    if not command or not command.strip():
        return False, "Empty command"

    command = command.strip()

    # R4: Use singleton parser instance
    components = _shell_parser.parse(command)

    if len(components) == 0:
        return False, "No command components"

    if len(components) == 1:
        # Simple command - check directly
        return _is_single_command_safe(components[0])

    # Compound command - check ALL components
    # ALL must be safe for auto-approval
    safe_components = []
    for i, comp in enumerate(components):
        is_safe, reason = _is_single_command_safe(comp)
        if not is_safe:
            return False, f"Component {i+1}/{len(components)} not safe: {reason}"
        safe_components.append(reason)

    # All components are safe!
    return True, f"All {len(components)} components safe: {', '.join(safe_components)}"


def create_permission_allow_response(reason: str) -> str:
    """
    Create the JSON response that tells Claude Code to auto-approve the command.

    This is the key to bypassing the ASK prompt - returning this JSON structure
    causes Claude Code to skip the permission check entirely.
    """
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason
        }
    }
    return json.dumps(response)


class SecurityTier:
    """Security tier definitions for command classification"""

    T0_READ_ONLY = "T0"        # describe, get, show, list operations
    T1_VALIDATION = "T1"       # validate, plan, template, lint operations
    T2_DRY_RUN = "T2"         # --dry-run, --plan-only operations
    T3_BLOCKED = "T3"         # apply, reconcile, deploy operations


class PolicyEngine:
    """Policy engine for command validation and security enforcement"""

    def __init__(self):
        # Load agent capabilities for dynamic routing verification
        self.capabilities = self._load_capabilities()
        self.skills = self.capabilities.get("routing_matrix", {}).get("skills", {})
        self.integration_config = self.capabilities.get("integration_metadata", {})

        # R4: Use singleton shell parser
        self.shell_parser = _shell_parser
        logger.info("Using singleton ShellCommandParser - compound commands will be validated per component")

        # Initialize workflow enforcer if available
        if WORKFLOW_ENFORCER_AVAILABLE:
            self.workflow_enforcer = WorkflowEnforcer()
            logger.info("WorkflowEnforcer initialized - guards are active")
        else:
            self.workflow_enforcer = None
            logger.warning("WorkflowEnforcer not available - guards disabled")

        self.blocked_commands = [
            # Terraform destructive operations
            r"terraform\s+apply(?!\s+--help)",
            r"terraform\s+destroy",
            r"terragrunt\s+apply(?!\s+--help)",
            r"terragrunt\s+destroy",

            # Kubernetes write operations
            r"kubectl\s+apply(?!\s+.*--dry-run)",
            r"kubectl\s+create(?!\s+.*--dry-run)",
            r"kubectl\s+delete",
            r"kubectl\s+patch",
            r"kubectl\s+replace(?!\s+.*--dry-run)",

            # Helm write operations
            r"helm\s+install(?!\s+.*--dry-run)",
            r"helm\s+upgrade(?!\s+.*--dry-run)",
            r"helm\s+uninstall",
            r"helm\s+delete",

            # Flux write operations
            r"flux\s+reconcile(?!\s+.*--dry-run)",
            r"flux\s+create",
            r"flux\s+delete",

            # GCP write operations
            r"gcloud\s+[\w-]+\s+(create|update|delete|patch)",
            r"gcloud\s+[\w-]+\s+[\w-]+\s+(create|update|delete|patch)",

            # AWS write operations
            r"aws\s+[\w-]+\s+(?!--)(create|update|delete|put)",
            r"aws\s+[\w-]+\s+[\w-]+\s+(?!--)(create|update|delete|put)",

            # Docker write operations
            r"docker\s+build",
            r"docker\s+push",
            r"docker\s+run(?!\s+.*--rm)",

            # Git write operations
            r"git\s+push(?!\s+--dry-run)",
            r"git\s+commit(?!\s+.*--dry-run)",

            # File destruction operations
            r"^rm\s+",
            r"\brm\s+-[rRfF]",
            r"^shred\s+",
            r"^wipe\s+",
            r"^srm\s+",

            # Disk operations (CRITICAL)
            r"^dd\s+",
            r"^fdisk\s+",
            r"^parted\s+",
            r"^gdisk\s+",
            r"^cfdisk\s+",
            r"^sfdisk\s+",

            # System modification operations
            r"^systemctl\s+(stop|disable|mask)",
            r"^service\s+\w+\s+stop",
            r"^kill\s+-9",
            r"^killall\s+-9",
            r"^pkill\s+-9",

            # Network security operations
            r"^iptables\s+-[FD]",
            r"^nmap\s+.*-s[USATFXMNO]",
            r"^hping3\s+",

            # Privilege escalation
            r"^sudo\s+",
            r"^su\s+-",

            # Dangerous flag combinations
            r"curl\s+.*(-T|--upload-file)",
            r"wget\s+.*(--post-data|--post-file)",
            r"nc\s+.*-e",
            r"socat\s+.*EXEC",
            r"docker\s+run\s+.*--privileged",
            r"chmod\s+(000|777)",
            r"git\s+clean\s+-[fdxFDX]",
        ]

        self.ask_commands = [
            r"terragrunt\s+apply",
            r"terraform\s+apply",
            r"git\s+push",
            r"git\s+commit",
        ]

        self.credential_required_patterns = [
            r"kubectl\s+(?!version)",
            r"flux\s+(?!version)",
            r"helm\s+(?!version)",
            r"gcloud\s+container\s+",
            r"gcloud\s+sql\s+",
            r"gcloud\s+redis\s+",
        ]

    def check_credentials_required(self, command: str) -> Tuple[bool, str]:
        """Check if command requires credentials and provide guidance"""
        for pattern in self.credential_required_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                if "gcloud auth" in command or "gcloud config" in command:
                    return False, ""
                if "source" in command and "load-cluster-credentials.sh" in command:
                    return False, ""
                if any(script in command for script in ["k8s-health.sh", "flux-status.sh"]):
                    return False, ""

                warning = (
                    "This command requires GCP/Kubernetes credentials to be loaded.\n\n"
                    "Recommended patterns:\n"
                    "  1. Load credentials inline:\n"
                    "     gcloud auth application-default login && kubectl ...\n\n"
                    "  2. Use gcloud container clusters get-credentials first:\n"
                    "     gcloud container clusters get-credentials <cluster> --region <region> && kubectl ...\n\n"
                    "  3. Ensure KUBECONFIG is set for kubectl/helm/flux commands\n"
                )
                return True, warning

        return False, ""

    def classify_command_tier(self, command: str) -> str:
        """
        Classify command into security tier.

        R2: Uses is_read_only_command for T0 classification (unified flow).
        """
        # Check for blocked operations first (T3)
        for pattern in self.blocked_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return SecurityTier.T3_BLOCKED

        # Check for dry-run operations (T2)
        if "--dry-run" in command or "--plan-only" in command:
            return SecurityTier.T2_DRY_RUN

        # Check for validation operations (T1)
        validation_patterns = [
            r"\bvalidate\b", r"\bplan\b", r"\btemplate\b", r"\blint\b", r"\bcheck\b", r"\bfmt\b"
        ]
        for pattern in validation_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return SecurityTier.T1_VALIDATION

        # R2: Use unified is_read_only_command for T0 classification
        is_safe, _ = is_read_only_command(command)
        if is_safe:
            return SecurityTier.T0_READ_ONLY

        # Default to blocked for unknown commands
        return SecurityTier.T3_BLOCKED

    def _load_capabilities(self) -> Dict[str, Any]:
        """Load agent capabilities configuration with robust error handling"""
        capabilities_file = Path(__file__).parent.parent / "tools" / "agent_capabilities.json"

        try:
            if not capabilities_file.exists():
                logger.warning(f"Capabilities file does not exist: {capabilities_file}")
                return self._get_fallback_capabilities()

            with open(capabilities_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("Capabilities file must be a JSON object")

            logger.debug(f"Successfully loaded capabilities from {capabilities_file}")
            return data

        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Could not load agent capabilities: {e}")
            logger.info("Using fallback capabilities configuration")
            return self._get_fallback_capabilities()

    def _get_fallback_capabilities(self) -> Dict[str, Any]:
        """Provide fallback capabilities when agent_capabilities.json is not available."""
        logger.info("Using fallback agent capabilities (hardcoded)")

        return {
            "routing_matrix": {
                "skills": {
                    "terraform-architect": {
                        "description": "Terraform/Terragrunt infrastructure management",
                        "triggers": ["terraform", "terragrunt", "infrastructure", "vpc", "gcs", "s3"],
                        "tools": ["terraform", "terragrunt"]
                    },
                    "gitops-operator": {
                        "description": "Kubernetes/GitOps deployment management",
                        "triggers": ["kubectl", "kubernetes", "k8s", "deploy", "helm", "flux"],
                        "tools": ["kubectl", "helm", "flux", "kustomize"]
                    },
                    "gcp-troubleshooter": {
                        "description": "GCP infrastructure diagnostics",
                        "triggers": ["gcp", "gcloud", "gke", "cloud sql", "google cloud"],
                        "tools": ["gcloud", "kubectl"]
                    },
                    "aws-troubleshooter": {
                        "description": "AWS infrastructure diagnostics",
                        "triggers": ["aws", "eks", "ec2", "rds", "s3", "cloudwatch"],
                        "tools": ["aws", "kubectl", "eksctl"]
                    },
                    "devops-developer": {
                        "description": "Application development and testing",
                        "triggers": ["npm", "test", "build", "lint", "docker", "application"],
                        "tools": ["npm", "pnpm", "docker", "pytest", "jest"]
                    }
                }
            },
            "integration_metadata": {
                "version": "2.0.0",
                "source": "fallback_hardcoded",
                "note": "Using hardcoded capabilities - agent_capabilities.json not found"
            }
        }

    def _inspect_script_content(self, script_path: str) -> Tuple[bool, str, Optional[str]]:
        """Inspects script content for blocked or sensitive commands."""
        try:
            with open(script_path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    for pattern in self.blocked_commands:
                        if re.search(pattern, line, re.IGNORECASE):
                            return False, SecurityTier.T3_BLOCKED, f"Script contains blocked command on line {line_num}: '{line}'"

                    for pattern in self.ask_commands:
                        if re.search(pattern, line, re.IGNORECASE):
                            return False, SecurityTier.T3_BLOCKED, f"Script contains sensitive command requiring direct approval on line {line_num}: '{line}'. Execute it directly, not from a script."

        except FileNotFoundError:
            return False, SecurityTier.T3_BLOCKED, f"Script file not found: {script_path}"
        except Exception as e:
            return False, SecurityTier.T3_BLOCKED, f"Error reading script file {script_path}: {e}"

        return True, SecurityTier.T0_READ_ONLY, "Script content seems safe."

    def verify_routing_metadata(self, tool_name: str, command: str, bundle_metadata: Optional[Dict] = None) -> Tuple[bool, str]:
        """Verify routing metadata against bundle expectations"""
        if not self.integration_config.get("handshake_validation", {}).get("verify_agent_match", False):
            return True, "Routing verification disabled"

        if not bundle_metadata:
            bundle_metadata = self._get_current_bundle_metadata()

        if not bundle_metadata:
            return True, "No bundle metadata available"

        suggested_agent = bundle_metadata.get("suggested_agent")
        expected_tier = bundle_metadata.get("security_tier")
        actual_tier = self.classify_command_tier(command)

        if expected_tier and actual_tier != expected_tier:
            if self.integration_config.get("handshake_validation", {}).get("require_approval_on_mismatch", False):
                return False, f"Tier mismatch: expected {expected_tier}, got {actual_tier}"
            else:
                logger.warning(f"Tier mismatch detected but proceeding: expected {expected_tier}, got {actual_tier}")

        if self.integration_config.get("handshake_validation", {}).get("log_routing_decisions", False):
            logger.info(f"Routing verification - Agent: {suggested_agent}, Tier: {expected_tier} -> {actual_tier}")

        return True, "Routing verification passed"

    def _get_current_bundle_metadata(self) -> Optional[Dict]:
        """Get current bundle metadata from session or environment"""
        try:
            bundle_data = os.environ.get("CLAUDE_TASK_METADATA")
            if bundle_data:
                return json.loads(bundle_data)

            session_dir = Path(__file__).parent.parent / "session" / "active"
            context_file = session_dir / "context.json"
            if context_file.exists():
                with open(context_file) as f:
                    context = json.load(f)
                    return context.get("current_task_metadata")
        except Exception as e:
            logger.debug(f"Could not load bundle metadata: {e}")

        return None

    def validate_command(self, tool_name: str, command: str) -> Tuple[bool, str, str]:
        """Validate command against security policies"""
        try:
            if not isinstance(tool_name, str):
                logger.error(f"Invalid tool_name type: {type(tool_name)}")
                return False, SecurityTier.T3_BLOCKED, "Invalid tool name"

            if not isinstance(command, str):
                logger.error(f"Invalid command type: {type(command)}")
                return False, SecurityTier.T3_BLOCKED, "Invalid command"

            if tool_name.lower() == "task":
                logger.info("Task tool invocation detected - validating workflow")
                try:
                    task_parameters = json.loads(command) if command else {}
                except json.JSONDecodeError:
                    logger.error(f"Invalid Task parameters JSON: {command}")
                    return False, SecurityTier.T3_BLOCKED, "Invalid Task parameters"
                return self._validate_task_invocation(task_parameters)

            elif tool_name.lower() != "bash":
                return True, SecurityTier.T0_READ_ONLY, "Non-bash tool allowed"

            if not command or not command.strip():
                logger.warning("Empty command provided")
                return False, SecurityTier.T3_BLOCKED, "Empty command not allowed"

            command_components = self.shell_parser.parse(command)

            if len(command_components) > 1:
                logger.info(f"Compound command detected with {len(command_components)} components")

                for i, component in enumerate(command_components, 1):
                    component_tier = self.classify_command_tier(component)
                    logger.info(f"Component {i}/{len(command_components)}: '{component[:50]}...' -> {component_tier}")

                    if component_tier == SecurityTier.T3_BLOCKED:
                        return False, SecurityTier.T3_BLOCKED, (
                            f"Compound command blocked: component '{component[:50]}' is not allowed\n\n"
                            f"Component {i} of {len(command_components)} would perform a blocked operation.\n"
                            "Execute safe commands separately or request approval for the blocked component."
                        )

                    if component_tier in [SecurityTier.T1_VALIDATION, SecurityTier.T2_DRY_RUN]:
                        logger.info(f"Component {i} requires validation but is safe: {component_tier}")

                logger.info(f"All {len(command_components)} components validated successfully")
                highest_tier = max([self.classify_command_tier(c) for c in command_components])
                return True, highest_tier, f"Compound command allowed: all {len(command_components)} components are safe"

            script_match = re.match(r"^\s*(bash|sh)\s+([\w\-\./_]+)", command)
            if script_match:
                script_path = script_match.group(2)
                is_allowed, tier, reason = self._inspect_script_content(script_path)
                if not is_allowed:
                    return is_allowed, tier, reason

            if detect_claude_footers(command):
                logger.warning(f"Command contains Claude Code attribution footers: {command[:100]}")
                return False, SecurityTier.T3_BLOCKED, (
                    "Command contains Claude Code attribution footers\n\n"
                    "Remove these patterns and retry:\n"
                    "  - 'Generated with Claude Code'\n"
                    "  - 'Co-Authored-By: Claude'"
                )

            if any(keyword in command for keyword in ("kubectl", "helm", "flux")):
                try:
                    gitops_validation = validate_gitops_workflow(command)
                except Exception as exc:
                    logger.error(f"GitOps validation failed: {exc}", exc_info=True)
                    return (
                        False,
                        SecurityTier.T3_BLOCKED,
                        "GitOps security validation error; blocking command for safety"
                    )

                if not gitops_validation.get("allowed", False):
                    suggestions = gitops_validation.get("suggestions") or []
                    suggestion_text = ""
                    if suggestions:
                        suggestion_text = "\n\nSuggestions:\n  - " + "\n  - ".join(suggestions)

                    return (
                        False,
                        SecurityTier.T3_BLOCKED,
                        f"GitOps policy violation: {gitops_validation.get('reason', 'operation not permitted')}"
                        f"{suggestion_text}"
                    )

                if gitops_validation.get("severity") in ("warning", "high"):
                    logger.warning(
                        "GitOps validation warning for command '%s': %s",
                        command,
                        gitops_validation.get("reason", "unspecified reason")
                    )

            requires_creds, creds_warning = self.check_credentials_required(command)
            if requires_creds:
                logger.info(f"Credential warning issued for command: {command[:100]}")

            tier = self.classify_command_tier(command)

            if tier == SecurityTier.T3_BLOCKED:
                return False, tier, f"Command blocked by security policy: {command[:100]}"

            routing_allowed, routing_reason = self.verify_routing_metadata(tool_name, command)
            if not routing_allowed:
                logger.warning(f"Routing verification failed: {routing_reason}")
                return False, tier, f"Routing verification failed: {routing_reason}"

            final_reason = f"Command allowed in tier {tier} ({routing_reason})"
            if requires_creds and creds_warning:
                final_reason = f"{final_reason}\n\n{creds_warning}"

            return True, tier, final_reason

        except Exception as e:
            logger.error(f"Error during command validation: {e}", exc_info=True)
            return False, SecurityTier.T3_BLOCKED, f"Validation error: {str(e)}"

    def _validate_task_invocation(self, parameters: Dict) -> Tuple[bool, str, str]:
        """Validate Task tool invocation against workflow rules."""
        try:
            agent_name = parameters.get("subagent_type", "unknown")
            prompt = parameters.get("prompt", "")
            description = parameters.get("description", "")

            logger.info(f"Task tool validation for agent: {agent_name}")

            available_agents = [
                "terraform-architect",
                "gitops-operator",
                "gcp-troubleshooter",
                "aws-troubleshooter",
                "devops-developer",
                "gaia",
                "Explore",
                "Plan"
            ]

            if self.workflow_enforcer:
                try:
                    passed, reason = self.workflow_enforcer.guard_phase_1_agent_exists(
                        agent_name, available_agents
                    )
                    if not passed:
                        logger.warning(f"WorkflowEnforcer blocked: {reason}")
                        return False, SecurityTier.T3_BLOCKED, reason

                except GuardViolation as e:
                    logger.error(f"Guard violation: {e}")
                    return False, SecurityTier.T3_BLOCKED, str(e)
            else:
                if agent_name not in available_agents:
                    logger.warning(f"Unknown agent requested: {agent_name}")
                    return False, SecurityTier.T3_BLOCKED, f"Unknown agent: {agent_name}. Available agents: {', '.join(available_agents)}"

            has_context = (
                "# Project Context" in prompt or
                "contract" in prompt.lower() or
                "context_provider.py" in prompt.lower()
            )

            if not has_context and agent_name not in ["gaia", "Explore", "Plan"]:
                logger.warning(
                    f"Task invocation for {agent_name} without apparent context provisioning. "
                    f"Orchestrator should call context_provider.py first (Phase 2)."
                )

            t3_keywords = [
                "terraform apply", "kubectl apply", "git push origin main",
                "flux reconcile", "helm install", "production", "prod"
            ]

            is_t3_operation = any(
                keyword in description.lower() or keyword in prompt.lower()
                for keyword in t3_keywords
            )

            has_approval = False
            if is_t3_operation:
                approval_indicators = [
                    "approved by user",
                    "user approval received",
                    "validation[\"approved\"] == True",
                    "Phase 5: Realization"
                ]

                has_approval = any(
                    indicator in prompt.lower()
                    for indicator in approval_indicators
                )

                if self.workflow_enforcer:
                    try:
                        passed, reason = self.workflow_enforcer.guard_phase_4_approval_mandatory(
                            tier="T3",
                            approval_received=has_approval
                        )
                        if not passed:
                            logger.warning(f"WorkflowEnforcer blocked T3 operation: {reason}")
                            return False, SecurityTier.T3_BLOCKED, (
                                f"{reason}\n\n"
                                "Phase 4 (Approval Gate) is MANDATORY before Task invocation.\n"
                                "Orchestrator must:\n"
                                "  1. Call approval_gate.request_approval()\n"
                                "  2. Get user approval via AskUserQuestion\n"
                                "  3. Validate with approval_gate.process_approval_response()\n"
                                "  4. Include 'User approval received' in Task prompt\n\n"
                                "See CLAUDE.md Rule 5.2 for approval gate protocol."
                            )
                    except Exception as e:
                        logger.error(f"Error checking Phase 4 guard: {e}")

                elif not has_approval:
                    return False, SecurityTier.T3_BLOCKED, (
                        "T3 operation detected without approval indication.\n\n"
                        "Phase 4 (Approval Gate) is MANDATORY before Task invocation.\n"
                        "Orchestrator must:\n"
                        "  1. Call approval_gate.request_approval()\n"
                        "  2. Get user approval via AskUserQuestion\n"
                        "  3. Validate with approval_gate.process_approval_response()\n"
                        "  4. Include 'User approval received' in Task prompt\n\n"
                        "See CLAUDE.md Rule 5.2 for approval gate protocol."
                    )

            logger.info(
                f"Task invocation validated: {agent_name} "
                f"(T3={is_t3_operation}, has_approval={has_approval if is_t3_operation else 'N/A'}, "
                f"has_context={has_context})"
            )

            tier = SecurityTier.T3_BLOCKED if is_t3_operation and not has_approval else SecurityTier.T0_READ_ONLY
            return True, tier, f"Task invocation allowed for {agent_name}"

        except Exception as e:
            logger.error(f"Error validating Task invocation: {e}", exc_info=True)
            return False, SecurityTier.T3_BLOCKED, f"Task validation error: {str(e)}"


def pre_tool_use_hook(tool_name: str, parameters: Dict) -> Optional[str]:
    """Pre-tool use hook implementation with robust error handling"""
    logger.info(f"Hook invoked: tool={tool_name}, params={json.dumps(parameters)[:200]}")

    try:
        if not isinstance(tool_name, str):
            logger.error(f"Invalid tool_name type: {type(tool_name)}")
            return "Error: Invalid tool name provided"

        if not isinstance(parameters, dict):
            logger.error(f"Invalid parameters type: {type(parameters)}")
            return "Error: Invalid parameters provided"

        policy_engine = PolicyEngine()

        command = ""
        if tool_name.lower() == "bash":
            command = parameters.get("command", "")
            if not command:
                logger.warning("Bash tool invoked without command")
                return "Error: Bash tool requires a command"
        elif tool_name.lower() == "task":
            command = json.dumps(parameters)

        is_allowed, tier, reason = policy_engine.validate_command(tool_name, command)

        log_data = {
            "tool": tool_name,
            "command": command[:100] if command else "N/A",
            "tier": tier,
            "allowed": is_allowed,
            "reason": reason
        }

        if not is_allowed:
            logger.warning(f"BLOCKED: {json.dumps(log_data)}")
            return (
                f"Command blocked by security policy\n\n"
                f"Tier: {tier}\n"
                f"Reason: {reason}\n\n"
                f"Use validation or read-only alternatives instead:"
                f"\n  - terraform plan (instead of apply)"
                f"\n  - kubectl get/describe (instead of apply/delete)"
                f"\n  - --dry-run flag for testing changes\n"
            )

        logger.info(f"ALLOWED: {json.dumps(log_data)}")
        return None

    except Exception as e:
        logger.error(f"Unexpected error in pre_tool_use_hook: {e}", exc_info=True)
        return f"Error during security validation: {str(e)}\n\nPlease contact DevOps team."


def main():
    """CLI interface for testing the policy engine"""
    if len(sys.argv) < 2:
        print("Usage: python pre_tool_use.py <command>")
        print("       python pre_tool_use.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        policy_engine = PolicyEngine()

        test_cases = [
            ("terraform validate", True, SecurityTier.T1_VALIDATION),
            ("terraform apply", False, SecurityTier.T3_BLOCKED),
            ("kubectl get pods", True, SecurityTier.T0_READ_ONLY),
            ("kubectl apply -f manifest.yaml", False, SecurityTier.T3_BLOCKED),
            ("kubectl apply -f manifest.yaml --dry-run=client", True, SecurityTier.T2_DRY_RUN),
            ("helm template myapp", True, SecurityTier.T1_VALIDATION),
            ("helm install myapp", False, SecurityTier.T3_BLOCKED),
            ("gcloud container clusters describe my-cluster", True, SecurityTier.T0_READ_ONLY),
        ]

        print("Testing Policy Engine...")
        for command, expected_allowed, expected_tier in test_cases:
            is_allowed, tier, reason = policy_engine.validate_command("bash", command)
            status = "PASS" if is_allowed == expected_allowed and tier == expected_tier else "FAIL"
            print(f"{status} {command}: {tier} ({'ALLOWED' if is_allowed else 'BLOCKED'})")

        print("Test completed")

    else:
        command = " ".join(sys.argv[1:])
        result = pre_tool_use_hook("bash", {"command": command})

        if result:
            print(f"BLOCKED: {result}")
            sys.exit(1)
        else:
            print(f"ALLOWED: {command}")


if __name__ == "__main__":
    if not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
            hook_data = json.loads(stdin_data)

            logger.info(f"Hook invoked: {hook_data.get('hook_event_name')}")
            logger.info(f"Tool: {hook_data.get('tool_name')}, Command: {hook_data.get('tool_input', {}).get('command', '')[:100]}")

            tool_name = hook_data.get("tool_name", "")
            tool_input = hook_data.get("tool_input", {})
            command = tool_input.get("command", "")

            if tool_name.lower() == "bash" and command:
                is_read_only, reason = is_read_only_command(command)

                if is_read_only:
                    logger.info(f"AUTO-APPROVED (read-only): {command[:80]}... | Reason: {reason}")
                    response = create_permission_allow_response(f"Read-only command: {reason}")
                    print(response)
                    sys.exit(0)
                else:
                    logger.info(f"Not auto-approved: {command[:80]}... | Reason: {reason}")

            result = pre_tool_use_hook(tool_name, tool_input)

            if result:
                print(result)
                sys.exit(1)
            else:
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"Hook error: {str(e)}")
            sys.exit(1)
    else:
        main()

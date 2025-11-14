#!/usr/bin/env python3
"""
Pre-tool use hook for Claude Code Agent System
Implements security policy gates, tier-based command filtering,
and routing metadata verification with centralized capabilities
"""

import sys
import json
import re
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from tenacity import retry, stop_after_attempt, wait_exponential

from pre_kubectl_security import validate_gitops_workflow

# ============================================================================
# COMMIT MESSAGE VALIDATION AND CLEANING
# ============================================================================

def clean_and_validate_git_commit(command: str) -> Tuple[str, bool, str]:
    """
    Clean and validate git commit messages.

    Removes Claude Code attribution footers and validates Conventional Commits format.

    Args:
        command: The full git commit command

    Returns:
        (cleaned_command: str, is_valid: bool, validation_reason: str)
    """
    # Extract commit message from command
    # Handles: git commit -m "message" or git commit -m 'message'
    msg_match = re.search(r"-m\s+['\"]([^'\"]*)['\"]", command)
    if not msg_match:
        # No message found, might be interactive - allow
        return command, True, "Interactive commit"

    original_message = msg_match.group(1)
    message = original_message

    # 1. CLEAN: Remove Claude Code footers
    # Remove "Generated with Claude Code"
    message = re.sub(
        r'\n.*Generated with\s+Claude Code.*',
        '',
        message,
        flags=re.IGNORECASE
    )

    # Remove "Co-Authored-By: Claude"
    message = re.sub(
        r'\n.*Co-Authored-By:\s+Claude\s+<.*>.*',
        '',
        message,
        flags=re.IGNORECASE
    )

    # Strip trailing whitespace
    message = message.rstrip()

    # 2. VALIDATE: Conventional Commits format
    lines = message.split('\n')
    subject = lines[0] if lines else ""

    validation_errors = []

    # Check subject length (max 72 chars)
    if len(subject) > 72:
        validation_errors.append(
            f"Subject line too long ({len(subject)} > 72 chars): '{subject[:50]}...'"
        )

    # Check Conventional Commits format: type(scope): description
    conventional_pattern = r'^(feat|fix|docs|style|refactor|perf|test|build|ci|chore)(\(.+\))?:\s+.+'
    if not re.match(conventional_pattern, subject):
        validation_errors.append(
            f"Not in Conventional Commits format. Use: type(scope): description\n"
            f"Valid types: feat, fix, docs, style, refactor, perf, test, build, ci, chore\n"
            f"Got: {subject}"
        )

    # Check no period at end of subject
    if subject.endswith('.'):
        validation_errors.append("Subject line should not end with a period")

    # Build result
    if validation_errors:
        return command, False, "\n".join(validation_errors)

    # If message was cleaned, return the updated command
    if message != original_message:
        # Reconstruct command with cleaned message
        cleaned_command = command.replace(
            f"-m \"{original_message}\"",
            f"-m \"{message}\"",
            1
        )
        if cleaned_command == command:
            # Try single quotes variant
            cleaned_command = command.replace(
                f"-m '{original_message}'",
                f"-m '{message}'",
                1
            )
        return cleaned_command, True, "✅ Cleaned Claude attribution footers"

    return command, True, "✅ Commit message valid"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

            # GCP write operations (create/update/delete/patch as subcommands with word boundaries)
            r"gcloud\s+[\w-]+\s+(create|update|delete|patch)",
            r"gcloud\s+[\w-]+\s+[\w-]+\s+(create|update|delete|patch)",

            # AWS write operations (create/update/delete/put as subcommands with word boundaries)
            r"aws\s+[\w-]+\s+(?!--)(create|update|delete|put)",
            r"aws\s+[\w-]+\s+[\w-]+\s+(?!--)(create|update|delete|put)",

            # Docker write operations
            r"docker\s+build",
            r"docker\s+push",
            r"docker\s+run(?!\s+.*--rm)",

            # Git write operations (unless explicitly allowed)
            r"git\s+push(?!\s+--dry-run)",
            r"git\s+commit(?!\s+.*--dry-run)",
        ]

        self.ask_commands = [
            r"terragrunt\s+apply",
            r"terraform\s+apply",
            r"git\s+push",
            r"git\s+commit",
        ]

        self.allowed_read_operations = [
            # Terraform read operations
            r"terraform\s+(fmt|validate|plan|show|output|version)",
            r"terragrunt\s+(plan|output|validate)",

            # Kubernetes read operations
            r"kubectl\s+(get|describe|logs|explain|version)",
            r"kubectl\s+.*--dry-run(=client)?",

            # Helm read operations
            r"helm\s+(template|lint|list|status|version)",
            r"helm\s+.*--dry-run",

            # Flux read operations
            r"flux\s+(check|get|version)",

            # Docker read operations
            r"docker\s+(ps|images|inspect|logs|stats|version|info)",
            r"docker\s+(container|image|network|volume)\s+(ls|inspect|list)",

            # GCP read operations
            r"gcloud\s+[\w-]+\s+(describe|list|show|get)",
            r"gcloud\s+[\w-]+\s+[\w-]+\s+(describe|list|show|get)",

            # AWS read operations
            r"aws\s+[\w-]+\s+(describe|list|get).*",
            r"aws\s+[\w-]+\s+[\w-]+\s+(describe|list|get).*",

            # General utilities
            r"ls|pwd|cd|cat|head|tail|grep|find|which",
            r"echo|printf",

            # Network verification commands (diagnostic/monitoring)
            r"ping(\s|$)",
            r"nslookup(\s|$)",
            r"dig(\s|$)",
            r"traceroute(\s|$)",
            r"curl(\s|$)",
            r"wget(\s|$)",
            r"nc(\s|$)",
            r"telnet(\s|$)",
            r"netstat(\s|$)",
            r"ss(\s|$)",
            r"ifconfig(\s|$)",
            r"ip\s+(addr|route|link|neigh)",
            r"route(\s|$)",
            r"arp(\s|$)",

            # Python scripts (session management)
            r"python3?\s+.*session.*\.py",
            r"python3?\s+\.claude/session/scripts/.*\.py",
        ]

        # Commands that require credentials to be loaded
        self.credential_required_patterns = [
            r"kubectl\s+(?!version)",      # kubectl (except version)
            r"flux\s+(?!version)",          # flux (except version)
            r"helm\s+(?!version)",          # helm (except version)
            r"gcloud\s+container\s+",       # gcloud container
            r"gcloud\s+sql\s+",             # gcloud sql
            r"gcloud\s+redis\s+",           # gcloud redis
        ]

    def check_credentials_required(self, command: str) -> Tuple[bool, str]:
        """
        Check if command requires credentials and provide guidance

        Returns:
            (requires_creds: bool, warning_message: str)
        """
        # Check if command requires credentials
        for pattern in self.credential_required_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                # Check if credentials are being loaded in the command
                if "gcloud auth" in command or "gcloud config" in command:
                    # Auth commands don't need pre-loading
                    return False, ""

                if "source" in command and "load-cluster-credentials.sh" in command:
                    # Credentials are being loaded via cluster-specific script
                    return False, ""

                # Check if using optimized scripts that auto-load credentials
                if any(script in command for script in ["k8s-health.sh", "flux-status.sh"]):
                    # These scripts auto-load credentials, no warning needed
                    return False, ""

                # Command requires credentials but not loading them
                warning = (
                    "⚠️  This command requires GCP/Kubernetes credentials to be loaded.\n\n"
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
        """Classify command into security tier"""

        # Check for blocked operations first
        for pattern in self.blocked_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return SecurityTier.T3_BLOCKED

        # Check for dry-run operations
        if "--dry-run" in command or "--plan-only" in command:
            return SecurityTier.T2_DRY_RUN

        # Check for validation operations
        validation_patterns = [
            r"validate", r"plan", r"template", r"lint", r"check", r"fmt"
        ]
        for pattern in validation_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return SecurityTier.T1_VALIDATION

        # Check for read operations
        for pattern in self.allowed_read_operations:
            if re.search(pattern, command, re.IGNORECASE):
                return SecurityTier.T0_READ_ONLY

        # Default to blocked for unknown commands
        return SecurityTier.T3_BLOCKED

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    def _load_capabilities_with_retry(self, capabilities_file: Path) -> Dict[str, Any]:
        """Load capabilities file with retry logic"""
        try:
            with open(capabilities_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Validate structure
            if not isinstance(data, dict):
                raise ValueError("Capabilities file must be a JSON object")

            logger.debug(f"Successfully loaded capabilities from {capabilities_file}")
            return data

        except FileNotFoundError:
            logger.error(f"Capabilities file not found: {capabilities_file}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in capabilities file: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error loading capabilities: {e}")
            raise

    def _load_capabilities(self) -> Dict[str, Any]:
        """Load agent capabilities configuration with robust error handling"""
        capabilities_file = Path(__file__).parent.parent / "tools" / "agent_capabilities.json"

        try:
            if not capabilities_file.exists():
                logger.warning(f"Capabilities file does not exist: {capabilities_file}")
                return self._get_fallback_capabilities()

            # Try to load with retry
            return self._load_capabilities_with_retry(capabilities_file)

        except Exception as e:
            logger.warning(f"Could not load agent capabilities after retries: {e}")
            logger.info("Using fallback capabilities configuration")
            return self._get_fallback_capabilities()

    def _get_fallback_capabilities(self) -> Dict[str, Any]:
        """
        Provide fallback capabilities when agent_capabilities.json is not available.
        Uses hardcoded agent definitions based on gaia-ops agent catalog.
        """
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

                    # Check against blocked commands
                    for pattern in self.blocked_commands:
                        if re.search(pattern, line, re.IGNORECASE):
                            return False, SecurityTier.T3_BLOCKED, f"Script contains blocked command on line {line_num}: '{line}'"

                    # Check against commands requiring approval
                    for pattern in self.ask_commands:
                        if re.search(pattern, line, re.IGNORECASE):
                            # This doesn't directly trigger 'ask', but blocks it for safety.
                            # The Claude Code environment itself will trigger 'ask' for the direct command.
                            # The hook's job is to prevent unnoticed execution inside a script.
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
            # Try to load from environment or session
            bundle_metadata = self._get_current_bundle_metadata()

        if not bundle_metadata:
            return True, "No bundle metadata available"

        suggested_agent = bundle_metadata.get("suggested_agent")
        expected_tier = bundle_metadata.get("security_tier")

        # Classify current command
        actual_tier = self.classify_command_tier(command)

        # Check tier compatibility
        if expected_tier and actual_tier != expected_tier:
            if self.integration_config.get("handshake_validation", {}).get("require_approval_on_mismatch", False):
                return False, f"Tier mismatch: expected {expected_tier}, got {actual_tier}"
            else:
                logger.warning(f"Tier mismatch detected but proceeding: expected {expected_tier}, got {actual_tier}")

        # Log routing decision for audit
        if self.integration_config.get("handshake_validation", {}).get("log_routing_decisions", False):
            logger.info(f"Routing verification - Agent: {suggested_agent}, Tier: {expected_tier} -> {actual_tier}")

        return True, "Routing verification passed"

    def _get_current_bundle_metadata(self) -> Optional[Dict]:
        """Get current bundle metadata from session or environment"""
        try:
            # Try to get from environment variable
            bundle_data = os.environ.get("CLAUDE_TASK_METADATA")
            if bundle_data:
                return json.loads(bundle_data)

            # Try to get from session active context
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
        """
        Validate command against security policies

        Returns:
            (is_allowed: bool, tier: str, reason: str)
        """
        try:
            # Validate inputs
            if not isinstance(tool_name, str):
                logger.error(f"Invalid tool_name type: {type(tool_name)}")
                return False, SecurityTier.T3_BLOCKED, "Invalid tool name"

            if not isinstance(command, str):
                logger.error(f"Invalid command type: {type(command)}")
                return False, SecurityTier.T3_BLOCKED, "Invalid command"

            # Skip validation for non-bash tools
            if tool_name.lower() != "bash":
                return True, SecurityTier.T0_READ_ONLY, "Non-bash tool allowed"

            # Handle empty commands
            if not command or not command.strip():
                logger.warning("Empty command provided")
                return False, SecurityTier.T3_BLOCKED, "Empty command not allowed"

            # Check if command is a script execution
            script_match = re.match(r"^\s*(bash|sh)\s+([\w\-\./_]+)", command)
            if script_match:
                script_path = script_match.group(2)
                is_allowed, tier, reason = self._inspect_script_content(script_path)
                if not is_allowed:
                    return is_allowed, tier, reason

            # INTERCEPT: Clean and validate git commit messages
            if "git commit" in command:
                cleaned_cmd, is_valid, reason = clean_and_validate_git_commit(command)
                if not is_valid:
                    logger.warning(f"Git commit validation failed: {reason}")
                    return False, SecurityTier.T3_BLOCKED, f"❌ Commit validation failed:\n{reason}"
                if cleaned_cmd != command:
                    # Footers detected - reject and suggest clean version
                    logger.warning(f"Git commit contains Claude attribution footers")
                    error_msg = (
                        f"❌ Commit contains Claude Code attribution footers\n\n"
                        f"Use this cleaned command instead:\n\n"
                        f"  {cleaned_cmd}\n\n"
                        f"Reason: {reason}"
                    )
                    return False, SecurityTier.T3_BLOCKED, error_msg

            # Enforce GitOps security rules for cluster-related commands
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

            # Check if credentials are required (provide warning, don't block)
            requires_creds, creds_warning = self.check_credentials_required(command)
            if requires_creds:
                logger.info(f"Credential warning issued for command: {command[:100]}")
                # Log warning but don't block (allow command to proceed)
                # User will see authentication error if credentials are actually missing

            tier = self.classify_command_tier(command)

            if tier == SecurityTier.T3_BLOCKED:
                return False, tier, f"Command blocked by security policy: {command[:100]}"

            # Verify routing metadata if enabled
            routing_allowed, routing_reason = self.verify_routing_metadata(tool_name, command)
            if not routing_allowed:
                logger.warning(f"Routing verification failed: {routing_reason}")
                return False, tier, f"Routing verification failed: {routing_reason}"

            # Include credential warning in reason if present
            final_reason = f"Command allowed in tier {tier} ({routing_reason})"
            if requires_creds and creds_warning:
                final_reason = f"{final_reason}\n\n{creds_warning}"

            return True, tier, final_reason

        except Exception as e:
            logger.error(f"Error during command validation: {e}", exc_info=True)
            # Fail closed - if validation errors, block the command
            return False, SecurityTier.T3_BLOCKED, f"Validation error: {str(e)}"

def pre_tool_use_hook(tool_name: str, parameters: Dict) -> Optional[str]:
    """
    Pre-tool use hook implementation with robust error handling

    Args:
        tool_name: Name of the tool being invoked
        parameters: Tool parameters

    Returns:
        None if allowed, error message if blocked
    """
    try:
        # Validate inputs
        if not isinstance(tool_name, str):
            logger.error(f"Invalid tool_name type: {type(tool_name)}")
            return "🚫 Error: Invalid tool name provided"

        if not isinstance(parameters, dict):
            logger.error(f"Invalid parameters type: {type(parameters)}")
            return "🚫 Error: Invalid parameters provided"

        policy_engine = PolicyEngine()

        # Extract command from parameters
        command = ""
        if tool_name.lower() == "bash":
            command = parameters.get("command", "")

            # Validate command exists for bash tool
            if not command:
                logger.warning("Bash tool invoked without command")
                return "🚫 Error: Bash tool requires a command"

        # Validate command
        is_allowed, tier, reason = policy_engine.validate_command(tool_name, command)

        # Log the decision with structured data
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
                f"🚫 Command blocked by security policy\n\n"
                f"Tier: {tier}\n"
                f"Reason: {reason}\n\n"
                f"💡 Use validation or read-only alternatives instead:"
                f"\n  - terraform plan (instead of apply)"
                f"\n  - kubectl get/describe (instead of apply/delete)"
                f"\n  - --dry-run flag for testing changes\n"
            )

        logger.info(f"ALLOWED: {json.dumps(log_data)}")
        return None

    except Exception as e:
        logger.error(f"Unexpected error in pre_tool_use_hook: {e}", exc_info=True)
        # Fail closed - return error message
        return f"🚫 Error during security validation: {str(e)}\n\nPlease contact DevOps team."

def main():
    """CLI interface for testing the policy engine"""

    if len(sys.argv) < 2:
        print("Usage: python pre_tool_use.py <command>")
        print("       python pre_tool_use.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        # Run test cases
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

        print("🧪 Testing Policy Engine...")
        for command, expected_allowed, expected_tier in test_cases:
            is_allowed, tier, reason = policy_engine.validate_command("bash", command)
            status = "✅" if is_allowed == expected_allowed and tier == expected_tier else "❌"
            print(f"{status} {command}: {tier} ({'ALLOWED' if is_allowed else 'BLOCKED'})")

        print("✨ Test completed")

    else:
        # Test specific command
        command = " ".join(sys.argv[1:])
        result = pre_tool_use_hook("bash", {"command": command})

        if result:
            print(f"❌ BLOCKED: {result}")
            sys.exit(1)
        else:
            print(f"✅ ALLOWED: {command}")

if __name__ == "__main__":
    main()

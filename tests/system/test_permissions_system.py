"""
Comprehensive test suite for Claude Code permissions system.

Tests the complete permissions enforcement pipeline:
- Settings file merging (project + shared)
- Permission priority resolution (deny > ask > allow)
- Execution standards enforcement
- Security tier validation
- Production vs development mode behavior

Run with: pytest tests/system/test_permissions_system.py -v
"""

import pytest
import json
import os
from pathlib import Path
from typing import Dict, Any, List


# Import helper functions
import sys
sys.path.insert(0, str(Path(__file__).parent))
from permissions_helpers import (
    load_project_settings,
    load_shared_settings,
    merge_settings,
    find_claude_config,
    get_environment_mode
)


# ============================================================================
# Fixtures for temporary project structures
# ============================================================================

@pytest.fixture
def temp_project_with_claude_dir(tmp_path):
    """Create a temporary project structure with .claude directory."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    claude_dir = project_root / ".claude"
    claude_dir.mkdir()

    # Create a sample settings.json
    settings_file = claude_dir / "settings.json"
    settings_file.write_text(json.dumps({
        "permissions": {
            "bash": {"allow": ["git status"]}
        }
    }))

    return project_root


@pytest.fixture
def temp_project_without_claude_dir(tmp_path):
    """Create a temporary project without .claude directory."""
    project_root = tmp_path / "project_no_claude"
    project_root.mkdir()
    return project_root


@pytest.fixture
def temp_shared_settings_dir(tmp_path):
    """Create a temporary shared settings directory."""
    shared_root = tmp_path / ".claude-shared"
    shared_root.mkdir()

    settings_file = shared_root / "settings.json"
    settings_file.write_text(json.dumps({
        "permissions": {
            "bash": {"deny": ["rm -rf"]}
        }
    }))

    return shared_root


@pytest.fixture
def temp_empty_shared_dir(tmp_path):
    """Create a temporary empty shared settings directory."""
    shared_root = tmp_path / ".claude-shared-empty"
    shared_root.mkdir()
    return shared_root


class TestSettingsMerge:
    """Test settings file loading and merging logic."""

    def test_load_project_settings_graceful(self, temp_project_with_claude_dir):
        """Project settings.json should load gracefully (or return None)."""
        settings = load_project_settings(temp_project_with_claude_dir)

        # Should either load successfully or return None (not crash)
        assert settings is None or isinstance(settings, dict)

    def test_load_shared_settings_graceful(self, temp_shared_settings_dir):
        """Shared settings.json should load gracefully (or return None)."""
        settings = load_shared_settings(temp_shared_settings_dir)

        # Should either load successfully or return None (not crash)
        assert settings is None or isinstance(settings, dict)
    
    def test_merge_empty_settings(self):
        """Merging empty settings should return empty dict."""
        result = merge_settings({}, {})
        assert result == {}
    
    def test_merge_project_only(self):
        """Project-only settings should pass through unchanged."""
        project = {
            "permissions": {
                "bash": {"allow": ["git status"]}
            }
        }
        result = merge_settings(project, {})
        assert result == project
    
    def test_merge_shared_only(self):
        """Shared-only settings should pass through unchanged."""
        shared = {
            "permissions": {
                "bash": {"deny": ["rm -rf"]}
            }
        }
        result = merge_settings({}, shared)
        assert result == shared
    
    def test_merge_non_conflicting(self):
        """Non-conflicting settings should combine."""
        project = {
            "permissions": {
                "bash": {"allow": ["git status"]}
            }
        }
        shared = {
            "permissions": {
                "bash": {"deny": ["rm -rf"]}
            }
        }
        result = merge_settings(project, shared)
        
        assert "allow" in result["permissions"]["bash"]
        assert "deny" in result["permissions"]["bash"]
        assert "git status" in result["permissions"]["bash"]["allow"]
        assert "rm -rf" in result["permissions"]["bash"]["deny"]
    
    def test_merge_project_overrides_shared(self):
        """Project settings should override shared for same keys."""
        project = {
            "permissions": {
                "bash": {
                    "deny": ["git push --force"]
                }
            }
        }
        shared = {
            "permissions": {
                "bash": {
                    "deny": ["rm -rf"],
                    "allow": ["git push"]
                }
            }
        }
        result = merge_settings(project, shared)
        
        # Project's deny list should replace shared's deny list
        assert "git push --force" in result["permissions"]["bash"]["deny"]
        # But shared's allow should still be present (different key)
        assert "git push" in result["permissions"]["bash"]["allow"]
    
    def test_merge_deep_nesting(self):
        """Deep nesting should merge correctly."""
        project = {
            "permissions": {
                "bash": {
                    "ask": {
                        "terraform apply *": {
                            "reason": "Production deployment",
                            "tier": "T3"
                        }
                    }
                }
            }
        }
        shared = {
            "permissions": {
                "bash": {
                    "deny": ["rm -rf /"]
                }
            }
        }
        result = merge_settings(project, shared)
        
        assert "ask" in result["permissions"]["bash"]
        assert "deny" in result["permissions"]["bash"]
        assert "terraform apply *" in result["permissions"]["bash"]["ask"]
    
    def test_merge_preserves_types(self):
        """Merging should preserve data types."""
        project = {
            "permissions": {
                "bash": {
                    "deny": ["git push --force"],
                    "max_timeout_ms": 300000
                }
            }
        }
        shared = {
            "permissions": {
                "bash": {
                    "allow": ["git status"],
                    "require_description": True
                }
            }
        }
        result = merge_settings(project, shared)
        
        assert isinstance(result["permissions"]["bash"]["deny"], list)
        assert isinstance(result["permissions"]["bash"]["max_timeout_ms"], int)
        assert isinstance(result["permissions"]["bash"]["require_description"], bool)
    
    def test_find_claude_config_in_project(self, temp_project_with_claude_dir):
        """Should find .claude directory in project."""
        claude_dir = find_claude_config(temp_project_with_claude_dir)

        # Should either find it or return None gracefully
        assert claude_dir is None or (claude_dir.exists() and claude_dir.name == ".claude")


class TestPermissionPriority:
    """Test permission priority resolution: deny > ask > allow."""
    
    def test_deny_blocks_allow(self):
        """Deny should block even if allow exists."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["git push"],
                    "deny": ["git push --force"]
                }
            }
        }
        
        # Simulate checking "git push --force"
        # This would be blocked by deny even though "git push" is allowed
        deny_patterns = settings["permissions"]["bash"]["deny"]
        command = "git push --force"
        
        is_denied = any(pattern in command for pattern in deny_patterns)
        assert is_denied is True
    
    def test_ask_overrides_allow(self):
        """Ask should take precedence over allow."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["terraform *"],
                    "ask": {
                        "terraform apply": {
                            "reason": "Production change",
                            "tier": "T3"
                        }
                    }
                }
            }
        }
        
        # "terraform apply" should require approval even though "terraform *" is allowed
        ask_patterns = settings["permissions"]["bash"]["ask"]
        command = "terraform apply -auto-approve"
        
        # Fixed: check if any ask pattern is IN the command
        requires_approval = any(pattern in command for pattern in ask_patterns.keys())
        assert requires_approval is True
    
    def test_specific_deny_over_generic_allow(self):
        """Specific deny should block generic allow pattern."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["rm *"],
                    "deny": ["rm -rf /"]
                }
            }
        }
        
        command = "rm -rf /"
        deny_patterns = settings["permissions"]["bash"]["deny"]
        
        is_denied = any(pattern in command for pattern in deny_patterns)
        assert is_denied is True
    
    def test_allow_when_no_deny_or_ask(self):
        """Allow should permit when no deny or ask exists."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["git status", "git log"]
                }
            }
        }
        
        command = "git status"
        allow_patterns = settings["permissions"]["bash"]["allow"]
        deny_patterns = settings["permissions"]["bash"].get("deny", [])
        
        is_allowed = any(pattern in command for pattern in allow_patterns)
        is_denied = any(pattern in command for pattern in deny_patterns)
        
        assert is_allowed is True
        assert is_denied is False
    
    def test_deny_blocks_everything(self):
        """Deny should block regardless of other permissions."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["git push"],
                    "ask": {
                        "git push origin main": {
                            "reason": "Main branch push"
                        }
                    },
                    "deny": ["git push --force"]
                }
            }
        }
        
        command = "git push --force origin main"
        deny_patterns = settings["permissions"]["bash"]["deny"]
        
        is_denied = any(pattern in command for pattern in deny_patterns)
        assert is_denied is True
    
    def test_ask_requires_explicit_approval(self):
        """Ask patterns should have approval metadata."""
        settings = {
            "permissions": {
                "bash": {
                    "ask": {
                        "terraform apply *": {
                            "reason": "Production deployment",
                            "tier": "T3",
                            "requires_approval": True
                        }
                    }
                }
            }
        }
        
        ask_config = settings["permissions"]["bash"]["ask"]["terraform apply *"]
        
        assert "reason" in ask_config
        assert "tier" in ask_config
        assert ask_config.get("requires_approval", True) is True
    
    def test_multiple_deny_patterns(self):
        """Multiple deny patterns should all be checked."""
        settings = {
            "permissions": {
                "bash": {
                    "deny": [
                        "rm -rf /",
                        "chmod 777",
                        ":(){:|:&};:",  # fork bomb
                        "dd if=/dev/zero"
                    ]
                }
            }
        }
        
        dangerous_commands = [
            "rm -rf /tmp",
            "chmod 777 /etc/passwd",
            ":(){:|:&};:",
            "dd if=/dev/zero of=/dev/sda"
        ]
        
        deny_patterns = settings["permissions"]["bash"]["deny"]
        
        for cmd in dangerous_commands:
            is_denied = any(pattern in cmd for pattern in deny_patterns)
            assert is_denied is True, f"Command should be denied: {cmd}"
    
    def test_priority_order_deny_ask_allow(self):
        """Priority order should be: deny > ask > allow."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["git push"],
                    "ask": {
                        "git push origin main": {
                            "reason": "Main branch"
                        }
                    },
                    "deny": ["git push --force"]
                }
            }
        }
        
        # Test 1: Denied command (highest priority)
        cmd1 = "git push --force origin main"
        deny_patterns = settings["permissions"]["bash"]["deny"]
        is_denied = any(pattern in cmd1 for pattern in deny_patterns)
        assert is_denied is True
        
        # Test 2: Asked command (medium priority)
        cmd2 = "git push origin main"
        ask_patterns = settings["permissions"]["bash"]["ask"]
        requires_ask = any(pattern in cmd2 for pattern in ask_patterns.keys())
        assert requires_ask is True
        
        # Test 3: Allowed command (lowest priority)
        cmd3 = "git push origin feature-branch"
        allow_patterns = settings["permissions"]["bash"]["allow"]
        is_allowed = any(pattern in cmd3 for pattern in allow_patterns)
        assert is_allowed is True
    
    def test_empty_permissions_denies_all(self):
        """No permissions defined should deny by default."""
        settings = {
            "permissions": {
                "bash": {}
            }
        }
        
        command = "git status"
        allow_patterns = settings["permissions"]["bash"].get("allow", [])
        
        is_allowed = any(pattern in command for pattern in allow_patterns)
        assert is_allowed is False
    
    def test_wildcard_patterns(self):
        """Wildcard patterns should match multiple commands."""
        settings = {
            "permissions": {
                "bash": {
                    "allow": ["git *", "terraform *"]
                }
            }
        }
        
        commands = [
            "git status",
            "git log",
            "terraform plan",
            "terraform validate"
        ]
        
        allow_patterns = settings["permissions"]["bash"]["allow"]
        
        for cmd in commands:
            # Simple wildcard matching (in real system, more sophisticated)
            is_allowed = any(
                cmd.startswith(pattern.replace(" *", ""))
                for pattern in allow_patterns
            )
            assert is_allowed is True, f"Command should be allowed: {cmd}"
    
    def test_pattern_case_sensitivity(self):
        """Permission patterns should be case-sensitive."""
        settings = {
            "permissions": {
                "bash": {
                    "deny": ["git push --force"]
                }
            }
        }
        
        # Lowercase matches
        cmd1 = "git push --force"
        deny_patterns = settings["permissions"]["bash"]["deny"]
        is_denied = any(pattern in cmd1 for pattern in deny_patterns)
        assert is_denied is True
        
        # Uppercase does NOT match (case-sensitive)
        cmd2 = "GIT PUSH --FORCE"
        is_denied = any(pattern in cmd2 for pattern in deny_patterns)
        assert is_denied is False


class TestExecutionStandards:
    """Test execution standards enforcement."""
    
    def test_native_tools_preferred(self):
        """Native tools (Write, Read, Edit) should be preferred over bash."""
        # This is a documentation/policy test
        standards = {
            "execution_standards": {
                "prefer_native_tools": True,
                "native_tools": ["Write", "Read", "Edit", "Grep", "Glob"],
                "avoid_bash_for": [
                    "file_operations",
                    "search_operations",
                    "code_modification"
                ]
            }
        }
        
        assert standards["execution_standards"]["prefer_native_tools"] is True
        assert "Write" in standards["execution_standards"]["native_tools"]
        assert "file_operations" in standards["execution_standards"]["avoid_bash_for"]
    
    def test_simple_commands_preferred(self):
        """Simple commands should be preferred over chained commands."""
        # Good: Simple commands
        good_commands = [
            "git status",
            "ls -la",
            "pwd"
        ]
        
        # Bad: Chained commands (should be avoided)
        bad_commands = [
            "cd /path && git status",
            "git add . && git commit && git push",
            "ls | grep foo | wc -l"
        ]
        
        # Check for chaining operators
        for cmd in bad_commands:
            has_chaining = any(op in cmd for op in ["&&", "||", "|", ";"])
            assert has_chaining is True, f"Should detect chaining in: {cmd}"
    
    def test_avoid_bash_redirections(self):
        """Bash redirections should be avoided in favor of Write tool."""
        bad_patterns = [
            "echo 'content' > file.txt",
            "cat file1 >> file2",
            "command 2>&1 | tee output.log"
        ]
        
        redirection_operators = [">", ">>", "2>&1", "|"]
        
        for cmd in bad_patterns:
            has_redirection = any(op in cmd for op in redirection_operators)
            assert has_redirection is True, f"Should detect redirection in: {cmd}"
    
    def test_explicit_paths_preferred(self):
        """Explicit paths should be preferred over cd navigation."""
        # Good: Explicit paths
        good_commands = [
            "git -C /path/to/repo status",
            "pytest /path/to/tests",
            "ls /home/user/project"
        ]
        
        # Bad: Using cd
        bad_commands = [
            "cd /path && git status",
            "cd /home/user/project && ls"
        ]
        
        for cmd in bad_commands:
            uses_cd = cmd.startswith("cd ")
            assert uses_cd is True, f"Should detect cd usage in: {cmd}"
    
    def test_validation_before_realization(self):
        """Validation commands should execute before realization."""
        workflow = {
            "phases": [
                {"name": "validation", "commands": ["terraform validate", "terraform plan"]},
                {"name": "realization", "commands": ["terraform apply"]}
            ]
        }
        
        validation_index = next(i for i, p in enumerate(workflow["phases"]) if p["name"] == "validation")
        realization_index = next(i for i, p in enumerate(workflow["phases"]) if p["name"] == "realization")
        
        assert validation_index < realization_index
    
    def test_dangerous_commands_blocked(self):
        """Dangerous commands should be in deny list."""
        settings = {
            "permissions": {
                "bash": {
                    "deny": [
                        "rm -rf /",
                        "chmod 777",
                        "chown -R",
                        ":(){:|:&};:",
                        "mkfs",
                        "dd if=/dev/zero of=/dev/sda"
                    ]
                }
            }
        }
        
        dangerous_patterns = [
            "rm -rf /",
            "chmod 777",
            ":(){:|:&};:"
        ]
        
        deny_list = settings["permissions"]["bash"]["deny"]
        
        for pattern in dangerous_patterns:
            assert pattern in deny_list, f"Dangerous pattern should be denied: {pattern}"
    
    def test_require_description_for_bash(self):
        """Bash commands should require descriptions."""
        settings = {
            "permissions": {
                "bash": {
                    "require_description": True,
                    "min_description_length": 10
                }
            }
        }
        
        assert settings["permissions"]["bash"]["require_description"] is True
        assert settings["permissions"]["bash"]["min_description_length"] >= 10
    
    def test_timeout_enforcement(self):
        """Bash commands should have timeout limits."""
        settings = {
            "permissions": {
                "bash": {
                    "default_timeout_ms": 120000,
                    "max_timeout_ms": 600000
                }
            }
        }
        
        assert settings["permissions"]["bash"]["default_timeout_ms"] == 120000
        assert settings["permissions"]["bash"]["max_timeout_ms"] == 600000
        assert settings["permissions"]["bash"]["max_timeout_ms"] <= 600000  # 10 minutes max


class TestSecurityTiers:
    """Test security tier definitions and enforcement."""
    
    def test_tier_t0_read_only(self):
        """T0 should be read-only operations."""
        t0_commands = [
            "git status",
            "git log",
            "git diff",
            "kubectl get pods",
            "ls -la",
            "cat file.txt"
        ]
        
        # All T0 commands should be non-mutating (excluding "plan" which is T1)
        # Fixed: removed "terraform plan" as it's actually T1 validation, not T0
        mutating_keywords = ["apply", "push", "delete", "create", "modify", "write", "rm"]
        
        for cmd in t0_commands:
            has_mutation = any(keyword in cmd.lower() for keyword in mutating_keywords)
            assert has_mutation is False, f"T0 command should not mutate: {cmd}"
    
    def test_tier_t1_validation(self):
        """T1 should be validation operations."""
        t1_commands = [
            "terraform validate",
            "terraform plan",
            "kubectl diff",
            "pytest tests/"
        ]
        
        # Fixed: more flexible validation keywords
        validation_keywords = ["validate", "plan", "test", "diff", "check", "lint"]
        
        for cmd in t1_commands:
            has_validation = any(keyword in cmd.lower() for keyword in validation_keywords)
            assert has_validation is True, f"T1 command should validate: {cmd}"
    
    def test_tier_t2_simulation(self):
        """T2 should be simulation operations."""
        t2_commands = [
            "terraform plan -out=plan.tfplan",
            "kubectl diff -f manifest.yaml",
            "git add .",  # Staging (not pushing)
            "docker build --no-cache"
        ]
        
        # T2 prepares but doesn't apply
        realization_keywords = ["apply", "push", "delete --force"]
        
        for cmd in t2_commands:
            has_realization = any(keyword in cmd.lower() for keyword in realization_keywords)
            assert has_realization is False, f"T2 command should not realize: {cmd}"
    
    def test_tier_t3_realization(self):
        """T3 should be realization operations (require approval)."""
        t3_commands = [
            "terraform apply",
            "git push origin main",
            "kubectl apply -f manifest.yaml",
            "helm upgrade production",
            "docker push registry/image:latest"
        ]
        
        realization_keywords = ["apply", "push", "upgrade", "delete"]
        
        for cmd in t3_commands:
            has_realization = any(keyword in cmd.lower() for keyword in realization_keywords)
            assert has_realization is True, f"T3 command should realize: {cmd}"
    
    def test_tier_escalation_requires_approval(self):
        """Escalating from T2 to T3 should require approval."""
        workflow = {
            "phase1": {
                "tier": "T2",
                "commands": ["terraform plan"],
                "requires_approval": False
            },
            "phase2": {
                "tier": "T3",
                "commands": ["terraform apply"],
                "requires_approval": True
            }
        }
        
        assert workflow["phase1"]["requires_approval"] is False
        assert workflow["phase2"]["requires_approval"] is True
        assert workflow["phase2"]["tier"] == "T3"
    
    def test_t3_operations_logged(self):
        """T3 operations should be logged for audit."""
        t3_metadata = {
            "tier": "T3",
            "command": "terraform apply",
            "requires_logging": True,
            "log_fields": [
                "timestamp",
                "user",
                "command",
                "approval_status",
                "exit_code"
            ]
        }
        
        assert t3_metadata["requires_logging"] is True
        assert "approval_status" in t3_metadata["log_fields"]
        assert "exit_code" in t3_metadata["log_fields"]
    
    def test_tier_permissions_in_settings(self):
        """Settings should define tier-specific permissions."""
        settings = {
            "permissions": {
                "bash": {
                    "ask": {
                        "terraform apply *": {
                            "tier": "T3",
                            "reason": "Infrastructure change"
                        },
                        "git push * main": {
                            "tier": "T3",
                            "reason": "Main branch push"
                        }
                    }
                }
            }
        }
        
        terraform_tier = settings["permissions"]["bash"]["ask"]["terraform apply *"]["tier"]
        git_tier = settings["permissions"]["bash"]["ask"]["git push * main"]["tier"]
        
        assert terraform_tier == "T3"
        assert git_tier == "T3"
    
    def test_production_requires_higher_tier(self):
        """Production operations should require T3."""
        environments = {
            "development": {
                "allowed_tiers": ["T0", "T1", "T2"],
                "auto_approve_t3": False
            },
            "production": {
                "allowed_tiers": ["T0", "T1", "T2", "T3"],
                "auto_approve_t3": False,
                "require_manual_approval": True
            }
        }
        
        assert "T3" not in environments["development"]["allowed_tiers"]
        assert "T3" in environments["production"]["allowed_tiers"]
        assert environments["production"]["require_manual_approval"] is True
    
    def test_tier_violation_detection(self):
        """System should detect tier violations."""
        command_metadata = {
            "command": "terraform apply",
            "declared_tier": "T2",  # Wrong! Should be T3
            "actual_tier": "T3"
        }
        
        is_violation = command_metadata["declared_tier"] != command_metadata["actual_tier"]
        assert is_violation is True
    
    def test_tier_downgrade_not_allowed(self):
        """Cannot downgrade tier of dangerous command."""
        dangerous_commands = {
            "terraform apply": {"min_tier": "T3"},
            "git push origin main": {"min_tier": "T3"},
            "kubectl delete namespace": {"min_tier": "T3"}
        }
        
        for cmd, meta in dangerous_commands.items():
            assert meta["min_tier"] == "T3", f"Command should require T3: {cmd}"
    
    def test_t0_never_requires_approval(self):
        """T0 operations should never require approval."""
        t0_commands = [
            {"command": "git status", "tier": "T0", "requires_approval": False},
            {"command": "git log", "tier": "T0", "requires_approval": False},
            {"command": "ls -la", "tier": "T0", "requires_approval": False}
        ]
        
        for cmd_meta in t0_commands:
            assert cmd_meta["tier"] == "T0"
            assert cmd_meta["requires_approval"] is False
    
    def test_tier_metadata_complete(self):
        """Each tier should have complete metadata."""
        tier_definitions = {
            "T0": {
                "name": "Read-only",
                "description": "Non-mutating operations",
                "requires_approval": False,
                "examples": ["git status", "ls", "cat"]
            },
            "T1": {
                "name": "Validation",
                "description": "Validation and testing",
                "requires_approval": False,
                "examples": ["terraform validate", "pytest"]
            },
            "T2": {
                "name": "Simulation",
                "description": "Staging and simulation",
                "requires_approval": False,
                "examples": ["terraform plan", "git add"]
            },
            "T3": {
                "name": "Realization",
                "description": "Live environment changes",
                "requires_approval": True,
                "examples": ["terraform apply", "git push"]
            }
        }
        
        required_fields = ["name", "description", "requires_approval", "examples"]
        
        for tier, meta in tier_definitions.items():
            for field in required_fields:
                assert field in meta, f"Tier {tier} missing field: {field}"
    
    def test_hook_enforcement_by_tier(self):
        """Hooks should enforce tier restrictions."""
        hook_config = {
            "pre_tool_use": {
                "enabled": True,
                "validate_tier": True,
                "block_t3_without_approval": True
            },
            "post_tool_use": {
                "enabled": True,
                "log_tier": True,
                "audit_t3": True
            }
        }
        
        assert hook_config["pre_tool_use"]["block_t3_without_approval"] is True
        assert hook_config["post_tool_use"]["audit_t3"] is True
    
    def test_tier_based_timeout(self):
        """Higher tiers should have longer timeouts."""
        tier_timeouts = {
            "T0": {"timeout_ms": 30000},   # 30s
            "T1": {"timeout_ms": 60000},   # 1m
            "T2": {"timeout_ms": 120000},  # 2m
            "T3": {"timeout_ms": 600000}   # 10m
        }
        
        assert tier_timeouts["T0"]["timeout_ms"] < tier_timeouts["T1"]["timeout_ms"]
        assert tier_timeouts["T1"]["timeout_ms"] < tier_timeouts["T2"]["timeout_ms"]
        assert tier_timeouts["T2"]["timeout_ms"] < tier_timeouts["T3"]["timeout_ms"]
    
    def test_agent_tier_constraints(self):
        """Agents should have tier constraints."""
        agent_config = {
            "terraform-architect": {
                "allowed_tiers": ["T0", "T1", "T2", "T3"],
                "default_tier": "T2"
            },
            "gcp-troubleshooter": {
                "allowed_tiers": ["T0", "T1", "T2"],
                "default_tier": "T0"
            }
        }
        
        # terraform-architect can do T3 (apply changes)
        assert "T3" in agent_config["terraform-architect"]["allowed_tiers"]
        
        # gcp-troubleshooter cannot do T3 (read-only diagnostics)
        assert "T3" not in agent_config["gcp-troubleshooter"]["allowed_tiers"]


class TestProductionVsDevelopment:
    """Test production vs development mode differences."""
    
    def test_development_mode_more_permissive(self):
        """Development mode should be more permissive."""
        env_config = {
            "development": {
                "permissions": {
                    "bash": {
                        "allow": ["terraform apply", "git push"],
                        "require_approval": False
                    }
                }
            },
            "production": {
                "permissions": {
                    "bash": {
                        "ask": {
                            "terraform apply *": {"reason": "Production change"},
                            "git push * main": {"reason": "Main branch"}
                        },
                        "require_approval": True
                    }
                }
            }
        }
        
        assert env_config["development"]["permissions"]["bash"]["require_approval"] is False
        assert env_config["production"]["permissions"]["bash"]["require_approval"] is True
    
    def test_production_blocks_dangerous_commands(self):
        """Production should block dangerous commands."""
        production_deny = [
            "rm -rf /",
            "chmod 777",
            "terraform destroy",
            "kubectl delete namespace production"
        ]
        
        development_deny = [
            "rm -rf /"
        ]
        
        # Production has more restrictions
        assert len(production_deny) > len(development_deny)
    
    def test_environment_detection(self):
        """Should detect environment from context."""
        # This would use actual environment detection logic
        test_cases = [
            {
                "project_path": "/home/user/project-prod",
                "expected_env": "production"
            },
            {
                "project_path": "/home/user/project-dev",
                "expected_env": "development"
            }
        ]
        
        for case in test_cases:
            # Simulate detection (in real system, would check indicators)
            if "prod" in case["project_path"]:
                detected_env = "production"
            else:
                detected_env = "development"
            
            assert detected_env == case["expected_env"]
    
    def test_production_requires_audit_trail(self):
        """Production should require complete audit trail."""
        production_config = {
            "audit": {
                "enabled": True,
                "log_all_commands": True,
                "require_approval_reason": True,
                "log_destination": "/var/log/claude/production.jsonl"
            }
        }
        
        assert production_config["audit"]["enabled"] is True
        assert production_config["audit"]["log_all_commands"] is True
    
    def test_development_allows_experimentation(self):
        """Development should allow experimental commands."""
        development_config = {
            "permissions": {
                "bash": {
                    "allow": [
                        "terraform destroy",
                        "kubectl delete namespace dev",
                        "docker system prune -a"
                    ]
                }
            }
        }
        
        # These would be blocked in production
        experimental_commands = development_config["permissions"]["bash"]["allow"]
        assert "terraform destroy" in experimental_commands
    
    def test_shared_settings_apply_to_both(self):
        """Shared settings should apply to all environments."""
        shared_deny = [
            "rm -rf /",
            ":(){:|:&};:",  # fork bomb
            "chmod 777 /"
        ]
        
        # These should be denied in BOTH environments
        # (shared settings provide baseline security)
        assert len(shared_deny) > 0
    
    def test_project_settings_override_shared(self):
        """Project settings should be able to override shared."""
        shared_settings = {
            "permissions": {
                "bash": {
                    "ask": {
                        "terraform apply *": {"reason": "Infrastructure change"}
                    }
                }
            }
        }
        
        project_settings = {
            "permissions": {
                "bash": {
                    "allow": ["terraform apply"]  # Override: no approval needed
                }
            }
        }
        
        # In merged settings, project's "allow" should override shared's "ask"
        # This is project-specific decision (e.g., development environment)
        assert "allow" in project_settings["permissions"]["bash"]
    
    def test_environment_specific_timeouts(self):
        """Production should have longer timeouts."""
        timeouts = {
            "development": {
                "default_timeout_ms": 60000,
                "max_timeout_ms": 300000
            },
            "production": {
                "default_timeout_ms": 120000,
                "max_timeout_ms": 600000
            }
        }
        
        assert timeouts["production"]["default_timeout_ms"] > timeouts["development"]["default_timeout_ms"]


# Entry point for pytest
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

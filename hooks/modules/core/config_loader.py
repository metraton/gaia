"""
Configuration loader for hooks system.

Loads JSON configuration files from hooks/config/ directory.
Provides caching and fallback defaults.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from functools import lru_cache

from .paths import get_hooks_config_dir

logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    Load and cache configuration from JSON files.

    Configs are loaded from hooks/config/ directory.
    Provides fallback defaults if files don't exist.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize config loader.

        Args:
            config_dir: Override config directory (for testing)
        """
        self.config_dir = config_dir or get_hooks_config_dir()
        self._cache: Dict[str, Any] = {}

    def load(self, config_name: str, default: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Load a configuration file.

        Args:
            config_name: Name of config file (without .json extension)
            default: Default value if file doesn't exist

        Returns:
            Configuration dictionary
        """
        # Check cache first
        if config_name in self._cache:
            return self._cache[config_name]

        config_path = self.config_dir / f"{config_name}.json"

        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
                    self._cache[config_name] = config
                    logger.debug(f"Loaded config: {config_name}")
                    return config
            else:
                logger.debug(f"Config file not found: {config_path}")
                return default or {}

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {config_path}: {e}")
            return default or {}
        except Exception as e:
            logger.error(f"Error loading config {config_name}: {e}")
            return default or {}

    def reload(self, config_name: str) -> Dict[str, Any]:
        """
        Force reload a configuration file.

        Args:
            config_name: Name of config file

        Returns:
            Configuration dictionary
        """
        if config_name in self._cache:
            del self._cache[config_name]
        return self.load(config_name)

    def clear_cache(self):
        """Clear all cached configurations."""
        self._cache.clear()


# Singleton instance
_config_loader: Optional[ConfigLoader] = None


def get_config(config_name: str, default: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Get configuration by name (convenience function).

    Uses singleton ConfigLoader instance.

    Args:
        config_name: Name of config file (without .json)
        default: Default value if not found

    Returns:
        Configuration dictionary
    """
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader.load(config_name, default)


def reset_config_loader():
    """Reset the singleton config loader (for testing)."""
    global _config_loader
    if _config_loader:
        _config_loader.clear_cache()
    _config_loader = None


# Default configurations (used when JSON files don't exist)
DEFAULT_CONFIGS = {
    "safe_commands": {
        "always_safe": [
            "ls", "pwd", "cat", "head", "tail", "grep", "find", "echo",
            "date", "whoami", "hostname", "uname", "env", "printenv"
        ],
        "always_safe_multiword": [
            "git status", "git diff", "git log", "git show", "git branch",
            "kubectl get", "kubectl describe", "kubectl logs",
            "terraform plan", "terraform validate", "terraform show"
        ],
        "conditional_safe": {
            "sed": ["-i", "--in-place"],
            "curl": ["-X POST", "-X PUT", "-X DELETE", "--data", "-d"],
            "wget": ["--post-data", "--post-file"]
        }
    },
    "blocked_commands": {
        "patterns": [
            "rm -rf",
            "terraform destroy",
            "kubectl delete",
            "> /dev/sda"
        ],
        "keywords": [
            "DROP TABLE",
            "DELETE FROM",
            "--force"
        ]
    },
    "security_tiers": {
        "T0": {
            "name": "Read-Only",
            "description": "Read-only operations, no cluster modification",
            "approval_required": False
        },
        "T1": {
            "name": "Validation",
            "description": "Validation operations (plan, lint, check)",
            "approval_required": False
        },
        "T2": {
            "name": "Dry-Run",
            "description": "Dry-run operations",
            "approval_required": False
        },
        "T3": {
            "name": "Destructive",
            "description": "State-modifying operations",
            "approval_required": True
        }
    },
    "thresholds": {
        "long_execution_seconds": 120,
        "consecutive_failures_alert": 3,
        "max_output_length": 100000
    }
}


def get_default_config(config_name: str) -> Dict[str, Any]:
    """
    Get default configuration for a given name.

    Args:
        config_name: Name of the config

    Returns:
        Default configuration dictionary
    """
    return DEFAULT_CONFIGS.get(config_name, {})

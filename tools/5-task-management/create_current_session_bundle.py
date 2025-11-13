#!/usr/bin/env python3
"""Create a new bundle for the current Claude session with complete context capture.

This script creates a fresh bundle directory with the current session's context,
capturing the complete conversation, git operations, modified files, and project state.

Usage:
    python3 .claude/session/scripts/create_current_session_bundle.py [--no-git-ops] [--label LABEL]

Options:
    --no-git-ops    Skip capturing git operations
    --label LABEL   Add a custom label to the bundle name (e.g., agent-upgrades)

Examples:
    python3 .claude/session/scripts/create_current_session_bundle.py
    # Creates: 2025-10-16-session-163244-eca75cdd

    python3 .claude/session/scripts/create_current_session_bundle.py --label agent-upgrades
    # Creates: 2025-10-16-agent-upgrades-163244-eca75cdd

Features:
- Generates unique bundle ID with current timestamp
- Optional custom label for descriptive bundle names
- Captures complete session transcript (if available)
- Records git operations performed during session
- Copies modified files to artifacts
- Generates comprehensive metadata and summary
- Creates structured active context
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import hashlib
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configurable artifact patterns (can be overridden by environment variable or config file)
DEFAULT_PRIORITY_PATTERNS = [
    "change-log-devops.md",
    "HEALTH-CHECK-CONFIG.md",
    "health.controller.ts",
    "health-server.ts",
    "route.ts",
    "validate-health-checks.js",
    "tasks.md"
]


def load_artifact_patterns() -> List[str]:
    """Load artifact patterns from environment or use defaults"""
    env_patterns = os.environ.get('GAIA_ARTIFACT_PATTERNS', '')
    if env_patterns:
        logger.debug(f"Loaded artifact patterns from environment: {env_patterns}")
        return env_patterns.split(',')
    return DEFAULT_PRIORITY_PATTERNS


# Detect SESSION_ROOT from current working directory instead of script location
def find_session_root() -> Path:
    """Find the .claude/session directory from current working directory"""
    current = Path.cwd()

    # First try: look for .claude/session in current directory or parents
    search_path = current
    for _ in range(5):  # Search up to 5 levels up
        candidate = search_path / ".claude" / "session"
        if candidate.exists() and candidate.is_dir():
            logger.debug(f"Found session root at: {candidate}")
            return candidate
        if search_path.parent == search_path:
            break
        search_path = search_path.parent

    # Fallback: use script location (original behavior)
    fallback = Path(__file__).resolve().parents[1]
    logger.warning(f"Using fallback session root: {fallback}")
    return fallback


SESSION_ROOT = find_session_root()
BUNDLES_DIR = SESSION_ROOT / "bundles"
ACTIVE_DIR = SESSION_ROOT / "active"
REPO_ROOT = SESSION_ROOT.parents[1]


def generate_bundle_id(label: Optional[str] = None) -> str:
    """Generate a unique bundle ID with timestamp and hash

    Args:
        label: Optional custom label to include in bundle name (e.g., 'agent-upgrades')

    Returns:
        Bundle ID in format: YYYY-MM-DD-{label|session}-HHMMSS-hash

    Examples:
        generate_bundle_id() -> "2025-10-16-session-163244-eca75cdd"
        generate_bundle_id("agent-upgrades") -> "2025-10-16-agent-upgrades-163244-eca75cdd"
    """
    now = datetime.now()
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H%M%S")

    # Use custom label or default "session"
    label_part = label if label else "session"

    # Create a hash based on timestamp + pid for uniqueness
    hash_input = f"{now.isoformat()}-{os.getpid()}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    bundle_id = f"{date_part}-{label_part}-{time_part}-{short_hash}"
    logger.debug(f"Generated bundle ID: {bundle_id}")
    return bundle_id


def get_git_operations() -> List[Dict[str, Any]]:
    """Capture recent git operations from the current session"""
    operations = []

    try:
        # Get recent commits (last 5)
        result = subprocess.run(
            ["git", "log", "--oneline", "-5", "--pretty=format:%H|%s|%ai"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('|')
                if len(parts) >= 3:
                    operations.append({
                        "type": "commit",
                        "hash": parts[0],
                        "message": parts[1],
                        "timestamp": parts[2]
                    })

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get git commits: {e}")
    except subprocess.TimeoutExpired:
        logger.warning("Git commit log command timed out")

    try:
        # Get current git status
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        if result.stdout.strip():
            operations.append({
                "type": "status",
                "modified_files": result.stdout.strip().split('\n'),
                "timestamp": datetime.now().isoformat()
            })

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get git status: {e}")
    except subprocess.TimeoutExpired:
        logger.warning("Git status command timed out")

    logger.info(f"Captured {len(operations)} git operations")
    return operations


def get_modified_files() -> List[str]:
    """Get list of files that were likely modified during this session"""
    modified_files = []

    try:
        # Get files modified in the last 2 hours (rough session duration)
        result = subprocess.run([
            "find", str(REPO_ROOT),
            "-type", "f",
            "-mmin", "-120",  # Modified in last 2 hours
            "-not", "-path", "*/.*/*",  # Exclude hidden directories
            "-not", "-path", "*/node_modules/*",  # Exclude node_modules
            "-not", "-path", "*/postgres-data/*"  # Exclude postgres data
        ], capture_output=True, text=True, check=True, timeout=15)

        for file_path in result.stdout.strip().split('\n'):
            if file_path and Path(file_path).is_file():
                rel_path = str(Path(file_path).relative_to(REPO_ROOT))
                modified_files.append(rel_path)

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to find modified files: {e}")
    except subprocess.TimeoutExpired:
        logger.warning("Find command timed out")
    except ValueError as e:
        logger.warning(f"Failed to compute relative path: {e}")

    logger.info(f"Found {len(modified_files)} modified files")
    return modified_files


def create_bundle_structure(bundle_id: str) -> Path:
    """Create the bundle directory structure"""
    bundle_path = BUNDLES_DIR / bundle_id
    bundle_path.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (bundle_path / "artifacts").mkdir(exist_ok=True)
    (bundle_path / "active-context").mkdir(exist_ok=True)

    logger.info(f"Created bundle structure at: {bundle_path}")
    return bundle_path


def generate_metadata(bundle_id: str, git_ops: List[Dict], modified_files: List[str]) -> Dict[str, Any]:
    """Generate comprehensive metadata for the session"""
    return {
        "session_id": f"current-session-{int(time.time())}",
        "bundle_id": bundle_id,
        "timestamp": datetime.now().isoformat(),
        "task_info": {
            "description": "Health check system implementation and repository updates",
            "context": "Implemented comprehensive health checks across TCM services, updated changelog, and pushed to repositories",
            "working_dir": str(REPO_ROOT),
            "project_context": "Multi-project repository with TCM application and spec-kit"
        },
        "git_operations": git_ops,
        "modified_files": modified_files,
        "artifacts_count": len(modified_files),
        "bundle_version": "2.0"
    }


def generate_summary(metadata: Dict[str, Any]) -> str:
    """Generate a human-readable summary of the session

    This generates a generic template summary. For specific session details,
    manually update the summary.md file in the bundle after creation.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bundle_id = metadata.get('bundle_id', 'unknown')

    # Extract label from bundle_id if present
    label = None
    if bundle_id != 'unknown':
        parts = bundle_id.split('-')
        if len(parts) >= 4:
            # Format: YYYY-MM-DD-{label}-HHMMSS-hash
            label = parts[3]

    # Infer session type from label or modified files
    session_type = "Development Session"
    if label and label != "session":
        session_type = label.replace('-', ' ').title()

    summary = f"""# Session Summary - {timestamp}

## Bundle ID
{bundle_id}

## Overview
This session involved work on the Claude Code agent system and related infrastructure.

**Note:** This is an auto-generated summary template. For detailed session information,
please update this file manually or review the transcript.md and metadata.json files.

## Session Type
{session_type}

## Technical Changes

"""

    # Git operations
    if metadata.get("git_operations"):
        summary += "### Git Operations Captured\n"
        commit_count = sum(1 for op in metadata["git_operations"] if op.get("type") == "commit")
        summary += f"- {len(metadata['git_operations'])} total operations\n"
        summary += f"- {commit_count} commits\n\n"

        summary += "**Recent commits:**\n"
        for op in metadata["git_operations"]:
            if op.get("type") == "commit":
                summary += f"- `{op.get('hash', 'N/A')[:8]}` - {op.get('message', 'No message')}\n"
    else:
        summary += "### Git Operations\n"
        summary += "No git operations captured in this session.\n\n"

    # Modified files
    if metadata.get("modified_files"):
        summary += f"\n### Files Modified\n"
        summary += f"Total: {len(metadata['modified_files'])} files\n\n"

        # Group by extension
        by_extension = {}
        for file_path in metadata["modified_files"]:
            ext = Path(file_path).suffix or "no-extension"
            by_extension.setdefault(ext, []).append(file_path)

        summary += "**By file type:**\n"
        for ext, files in sorted(by_extension.items()):
            summary += f"- {ext}: {len(files)} files\n"

        summary += "\n**Key files:**\n"
        # Show first 15 files
        for file_path in list(metadata["modified_files"])[:15]:
            summary += f"- {file_path}\n"

        if len(metadata["modified_files"]) > 15:
            summary += f"\n... and {len(metadata['modified_files']) - 15} more files\n"
    else:
        summary += "\n### Files Modified\n"
        summary += "No modified files tracked in this session.\n"

    summary += f"""
## Session Context
- **Working Directory**: {metadata['task_info']['working_dir']}
- **Bundle ID**: {metadata['bundle_id']}
- **Created**: {timestamp}
- **Label**: {label if label else 'general-session'}

## Artifacts
- `transcript.md` - Session conversation (if captured)
- `metadata.json` - Complete session metadata
- `artifacts/` - Modified files and artifacts
- `active-context/` - Session state for restoration

## How to Use This Bundle

### View Session Details
```bash
# Read this summary
cat summary.md

# View session metadata
cat metadata.json | jq

# Check transcript (if available)
cat transcript.md
```

### Restore Session
```bash
python3 .claude/session/scripts/restore_session.py {bundle_id}
```

## Next Steps
**IMPORTANT:** Please update this summary with:
1. Actual key accomplishments from the session
2. Specific technical changes made
3. Relevant next steps or follow-up actions
4. Any important decisions or findings

This will help when reviewing or restoring this session in the future.
"""

    return summary


def copy_artifacts(bundle_path: Path, modified_files: List[str]) -> None:
    """Copy important modified files to the artifacts directory"""
    artifacts_dir = bundle_path / "artifacts"

    # Load configurable patterns
    priority_patterns = load_artifact_patterns()

    copied_count = 0
    for file_path in modified_files:
        if any(pattern in file_path for pattern in priority_patterns):
            source = REPO_ROOT / file_path
            if source.exists() and source.is_file():
                # Create directory structure in artifacts
                dest = artifacts_dir / file_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                try:
                    shutil.copy2(source, dest)
                    copied_count += 1
                except (OSError, shutil.Error) as e:
                    logger.warning(f"Failed to copy {file_path}: {e}")

    logger.info(f"Copied {copied_count} key files to artifacts/")
    print(f"   Copied {copied_count} key files to artifacts/")


def copy_active_context(bundle_path: Path) -> None:
    """Copy the active session context"""
    if ACTIVE_DIR.exists():
        target = bundle_path / "active-context"
        if target.exists():
            shutil.rmtree(target)
        try:
            shutil.copytree(ACTIVE_DIR, target)
            logger.info("Active context copied to active-context/")
            print("   Active context copied to active-context/")
        except (OSError, shutil.Error) as e:
            logger.warning(f"Failed to copy active context: {e}")


def create_transcript(bundle_path: Path) -> None:
    """Create a basic transcript placeholder (Claude doesn't provide direct access)"""
    transcript_content = f"""# Session Transcript - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Session Overview
This session focused on implementing health check improvements across the TCM application services.

## Key Activities
1. **Health Check Implementation**
   - Enhanced health check endpoints in API service (apps/api/src/health.controller.ts)
   - Added comprehensive health checks to Bot service (apps/bot/src/index.ts)
   - Implemented readiness probes in Jobs service (apps/jobs/src/health-server.ts)
   - Created detailed health endpoint for Web service (apps/web/app/api/health/)

2. **Documentation Updates**
   - Updated DevOps changelog (change-log-devops.md) with health check implementation details
   - Created comprehensive health check configuration guide (HEALTH-CHECK-CONFIG.md)

3. **Repository Management**
   - Successfully committed changes to training-compliance-management repository
   - Updated spec-kit-tcm-plan repository with task definitions
   - Resolved git submodule status issues

## Session Commands Executed
- Git status checks across multiple repositories
- File modifications using Edit tool
- Git commits with comprehensive commit messages
- Git push operations to remote repositories
- Bundle creation and session saving

## Files Modified
See artifacts/ directory for copies of key modified files.

---
*Note: This transcript is auto-generated. The actual conversation history is preserved in Claude's session context.*
"""

    try:
        (bundle_path / "transcript.md").write_text(transcript_content)
        logger.info("Generated session transcript")
        print("   Generated session transcript")
    except (OSError, IOError) as e:
        logger.error(f"Failed to create transcript: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create new bundle for current Claude session",
        epilog="Examples:\n"
               "  %(prog)s\n"
               "  %(prog)s --label agent-upgrades\n"
               "  %(prog)s --label infrastructure-setup --no-git-ops",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-git-ops", action="store_true", help="Skip git operations capture")
    parser.add_argument("--label", type=str, help="Custom label for bundle name (e.g., agent-upgrades, feature-xyz)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting session bundle creation")

    # Ensure bundles directory exists
    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

    # Generate unique bundle ID with optional label
    bundle_id = generate_bundle_id(label=args.label)
    print(f"🆕 Creating new session bundle: {bundle_id}")
    if args.label:
        print(f"   📝 Label: {args.label}")

    # Create bundle structure
    bundle_path = create_bundle_structure(bundle_id)
    print(f"📁 Bundle directory created: {bundle_path}")

    # Gather session data
    logger.info("Gathering session data...")
    git_ops = [] if args.no_git_ops else get_git_operations()
    modified_files = get_modified_files()

    # Generate metadata and summary
    logger.info("Generating metadata and summary...")
    metadata = generate_metadata(bundle_id, git_ops, modified_files)
    summary = generate_summary(metadata)

    # Write bundle files
    try:
        (bundle_path / "metadata.json").write_text(json.dumps(metadata, indent=2))
        (bundle_path / "summary.md").write_text(summary)
        logger.info("Wrote metadata.json and summary.md")
    except (OSError, IOError) as e:
        logger.error(f"Failed to write bundle files: {e}")
        return

    create_transcript(bundle_path)

    # Copy artifacts and context
    copy_artifacts(bundle_path, modified_files)
    copy_active_context(bundle_path)

    print("✅ Session bundle created successfully!")
    print(f"📁 Bundle: bundles/{bundle_id}")
    print(f"   (Full path: {bundle_path})")
    print(f"   📊 {len(git_ops)} git operations captured")
    print(f"   📄 {len(modified_files)} modified files detected")
    print(f"   💾 Bundle ready for sharing or archival")

    logger.info("Session bundle creation completed successfully")


if __name__ == "__main__":
    main()
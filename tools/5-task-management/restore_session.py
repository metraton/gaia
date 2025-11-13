#!/usr/bin/env python3
"""
Session restoration system for Claude Code.

Provides intelligent session restoration with context loading for both main Claude
and specialized sub-agents on-demand.
"""

import json
import shutil
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SESSION_ROOT = Path(__file__).resolve().parents[1]
BUNDLES_DIR = SESSION_ROOT / "bundles"
ACTIVE_DIR = SESSION_ROOT / "active"
REPO_ROOT = SESSION_ROOT.parents[1]

logger.debug(f"SESSION_ROOT: {SESSION_ROOT}")
logger.debug(f"BUNDLES_DIR: {BUNDLES_DIR}")
logger.debug(f"ACTIVE_DIR: {ACTIVE_DIR}")
logger.debug(f"REPO_ROOT: {REPO_ROOT}")


def find_bundle(bundle_id: str) -> Optional[Path]:
    """Find a bundle by ID (exact match or partial match)"""
    if not BUNDLES_DIR.exists():
        logger.warning(f"Bundles directory not found: {BUNDLES_DIR}")
        return None

    # Try exact match first
    exact_path = BUNDLES_DIR / bundle_id
    if exact_path.exists():
        logger.debug(f"Found exact bundle match: {exact_path}")
        return exact_path

    # Try partial match (for convenience)
    try:
        for bundle_path in BUNDLES_DIR.iterdir():
            if bundle_path.is_dir() and bundle_id in bundle_path.name:
                logger.debug(f"Found partial bundle match: {bundle_path}")
                return bundle_path
    except (OSError, IOError) as e:
        logger.error(f"Failed to search bundles directory: {e}")

    logger.warning(f"No bundle found matching: {bundle_id}")
    return None


def load_bundle_metadata(bundle_path: Path) -> Optional[Dict[str, Any]]:
    """Load bundle metadata"""
    metadata_file = bundle_path / "metadata.json"
    if not metadata_file.exists():
        logger.warning(f"No metadata.json found in {bundle_path}")
        return None

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
            logger.debug(f"Loaded metadata from {metadata_file}")
            return metadata
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse metadata JSON: {e}")
        return None
    except (OSError, IOError) as e:
        logger.error(f"Failed to read metadata file: {e}")
        return None


def restore_active_context(bundle_path: Path) -> bool:
    """Restore active context from bundle"""
    source_context = bundle_path / "active-context"
    if not source_context.exists():
        logger.warning(f"No active-context found in bundle: {bundle_path.name}")
        print(f"⚠️  No active-context found in bundle")
        return False

    try:
        # Backup current active context
        if ACTIVE_DIR.exists():
            backup_dir = ACTIVE_DIR.parent / f"active-backup-{int(datetime.now().timestamp())}"
            shutil.move(ACTIVE_DIR, backup_dir)
            logger.info(f"Backed up current context to {backup_dir.name}")
            print(f"💾 Backed up current context to {backup_dir.name}")

        # Restore from bundle
        shutil.copytree(source_context, ACTIVE_DIR)
        logger.info(f"Active context restored from bundle")
        print(f"✅ Active context restored from bundle")
        return True

    except (OSError, shutil.Error) as e:
        logger.error(f"Failed to restore active context: {e}")
        print(f"❌ Failed to restore active context: {e}")
        return False


def extract_agent_context(bundle_path: Path, agent_type: str) -> Dict[str, Any]:
    """Extract relevant context for specific agent types"""
    metadata = load_bundle_metadata(bundle_path)
    if not metadata:
        logger.warning(f"Cannot extract agent context without metadata")
        return {}

    agent_context = {
        'bundle_id': bundle_path.name,
        'timestamp': metadata.get('timestamp'),
        'task_info': metadata.get('task_info', {}),
        'specific_context': {}
    }

    logger.info(f"Extracting context for agent: {agent_type}")

    # Agent-specific context extraction
    if agent_type == "terraform-architect":
        agent_context['specific_context'] = {
            'infrastructure_changes': [],
            'terraform_operations': [],
            'relevant_commits': []
        }

        # Extract terraform-related git operations
        for git_op in metadata.get('git_operations', []):
            if git_op.get('type') == 'commit':
                commit_msg = git_op.get('message', '').lower()
                if any(kw in commit_msg for kw in ['terraform', 'infrastructure', 'tf', 'plan', 'apply']):
                    agent_context['specific_context']['terraform_operations'].append(git_op)
                    logger.debug(f"Found terraform operation: {git_op.get('message')}")

    elif agent_type == "gitops-operator":
        agent_context['specific_context'] = {
            'deployment_changes': [],
            'kubernetes_operations': [],
            'flux_operations': []
        }

        for git_op in metadata.get('git_operations', []):
            if git_op.get('type') == 'commit':
                commit_msg = git_op.get('message', '').lower()
                if any(kw in commit_msg for kw in ['kubernetes', 'k8s', 'helm', 'flux', 'gitops']):
                    agent_context['specific_context']['kubernetes_operations'].append(git_op)
                    logger.debug(f"Found gitops operation: {git_op.get('message')}")

    elif agent_type == "devops-developer":
        agent_context['specific_context'] = {
            'code_changes': [],
            'health_checks': [],
            'service_modifications': []
        }

        # Extract code-related changes
        for git_op in metadata.get('git_operations', []):
            if git_op.get('type') == 'commit':
                commit_msg = git_op.get('message', '').lower()
                if any(kw in commit_msg for kw in ['health', 'check', 'service', 'api', 'endpoint']):
                    agent_context['specific_context']['health_checks'].append(git_op)
                    logger.debug(f"Found devops operation: {git_op.get('message')}")

    logger.info(f"Extracted {len(agent_context['specific_context'])} context items for {agent_type}")
    return agent_context


def show_bundle_details(bundle_path: Path) -> None:
    """Show detailed information about a bundle"""
    metadata = load_bundle_metadata(bundle_path)
    if not metadata:
        logger.error(f"Could not load metadata for bundle {bundle_path.name}")
        print(f"❌ Could not load metadata for bundle {bundle_path.name}")
        return

    print(f"📦 **Bundle Details: {bundle_path.name}**")
    print("=" * 60)

    task_info = metadata.get('task_info', {})
    print(f"📅 Created: {metadata.get('timestamp', 'Unknown')}")
    print(f"📋 Description: {task_info.get('description', 'No description')}")
    print(f"🎯 Context: {task_info.get('context', 'No context')}")
    print(f"📁 Working Dir: {task_info.get('working_dir', 'Unknown')}")

    # Git operations
    git_ops = metadata.get('git_operations', [])
    if git_ops:
        print(f"\n🔄 Git Operations ({len(git_ops)}):")
        for i, op in enumerate(git_ops[:5]):  # Show first 5
            if op.get('type') == 'commit':
                print(f"   {i+1}. Commit: {op.get('message', 'No message')}")
            elif op.get('type') == 'status':
                modified_count = len(op.get('modified_files', []))
                print(f"   {i+1}. Status: {modified_count} modified files")

    # Bundle contents
    try:
        contents = list(bundle_path.iterdir())
        print(f"\n📄 Bundle Contents ({len(contents)}):")
        for item in sorted(contents):
            if item.is_dir():
                item_count = len(list(item.iterdir()))
                print(f"   📁 {item.name}/ ({item_count} items)")
            else:
                size_kb = item.stat().st_size // 1024
                print(f"   📄 {item.name} ({size_kb}KB)")
    except (OSError, IOError) as e:
        logger.warning(f"Failed to list bundle contents: {e}")

    # Read summary if available
    summary_file = bundle_path / "summary.md"
    if summary_file.exists():
        print(f"\n📝 **Session Summary:**")
        print("-" * 40)
        try:
            with open(summary_file, 'r') as f:
                # Show first few lines of summary
                lines = f.readlines()[:10]
                for line in lines:
                    print(f"   {line.rstrip()}")
                if len(f.readlines()) > 10:
                    print("   ... (truncated)")
        except (OSError, IOError) as e:
            logger.warning(f"Failed to read summary file: {e}")


def main():
    parser = argparse.ArgumentParser(description="Restore Claude Code session")
    parser.add_argument("bundle_id", nargs='?', help="Bundle ID to restore")
    parser.add_argument("--show", action="store_true", help="Show bundle details without restoring")
    parser.add_argument("--agent", help="Extract context for specific agent type")
    parser.add_argument("--list", action="store_true", help="List available bundles")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting session restoration")

    if args.list:
        if not BUNDLES_DIR.exists():
            logger.info("No bundles directory found")
            print("📭 No bundles directory found")
            return

        try:
            bundles = [d for d in BUNDLES_DIR.iterdir()
                      if d.is_dir() and d.name not in ["latest", ".git"]]
        except (OSError, IOError) as e:
            logger.error(f"Failed to list bundles: {e}")
            print(f"❌ Failed to list bundles: {e}")
            return

        if not bundles:
            logger.info("No bundles available")
            print("📭 No bundles available")
            return

        print(f"📚 Available Bundles ({len(bundles)}):")
        print("=" * 50)

        bundles.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        for bundle_path in bundles:
            metadata = load_bundle_metadata(bundle_path)
            if metadata:
                task_desc = metadata.get('task_info', {}).get('description', 'No description')
                timestamp = metadata.get('timestamp', 'Unknown time')
                print(f"• {bundle_path.name}")
                print(f"  📋 {task_desc}")
                print(f"  📅 {timestamp}")
                print()

        return

    if not args.bundle_id:
        logger.warning("Bundle ID required")
        print("❌ Bundle ID required. Use --list to see available bundles.")
        return

    bundle_path = find_bundle(args.bundle_id)
    if not bundle_path:
        logger.error(f"Bundle not found: {args.bundle_id}")
        print(f"❌ Bundle '{args.bundle_id}' not found")
        print("💡 Use --list to see available bundles")
        return

    if args.show:
        logger.info(f"Showing details for bundle: {bundle_path.name}")
        show_bundle_details(bundle_path)
        return

    if args.agent:
        logger.info(f"Extracting context for agent: {args.agent}")
        context = extract_agent_context(bundle_path, args.agent)
        print(f"🤖 Context for {args.agent}:")
        print(json.dumps(context, indent=2))
        return

    # Full session restoration
    logger.info(f"Restoring session from bundle: {bundle_path.name}")
    print(f"🔄 Restoring session from bundle: {bundle_path.name}")

    metadata = load_bundle_metadata(bundle_path)
    if metadata:
        task_info = metadata.get('task_info', {})
        print(f"📋 Session: {task_info.get('description', 'Unknown session')}")
        print(f"📅 Created: {metadata.get('timestamp', 'Unknown')}")

    success = restore_active_context(bundle_path)

    if success:
        logger.info("Session restored successfully")
        print("✅ Session restored successfully!")
        print(f"🎯 You can now continue working where you left off")
        print(f"📄 Session details: {bundle_path / 'summary.md'}")
    else:
        logger.error("Session restoration failed")
        print("❌ Session restoration failed")


if __name__ == "__main__":
    main()
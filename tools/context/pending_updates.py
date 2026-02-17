#!/usr/bin/env python3
"""
Pending Update Store for GAIA-OPS Agent Context Feedback Loop

This module manages pending update suggestions to project-context.json from agents.
It provides deduplication, approval workflow, and automatic application of changes.

Architecture:
- JSONL append-only audit trail for all events
- JSON index for fast queries and deduplication
- Content-based hashing for deduplication
- Atomic file operations for safety
- Automatic backup before applying changes

Storage layout:
  pending-updates/
  ├── pending-updates.jsonl    # Append-only audit trail
  ├── pending-index.json       # Mutable index for fast queries
  └── applied/                 # Archive of applied updates
      └── update-<id>.json
"""

import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict, field
from enum import Enum


class DiscoveryCategory(str, Enum):
    """Categories of discoveries that can be made by agents."""
    NEW_RESOURCE = "new_resource"
    CONFIGURATION_ISSUE = "configuration_issue"
    DRIFT_DETECTED = "drift_detected"
    DEPENDENCY_DISCOVERED = "dependency_discovered"
    TOPOLOGY_CHANGE = "topology_change"


class UpdateStatus(str, Enum):
    """Status of a pending update."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


# Mapping of categories to valid target sections
CATEGORY_TO_SECTIONS = {
    "new_resource": ["application_services", "cluster_details", "infrastructure_topology"],
    "configuration_issue": ["project_details", "terraform_infrastructure", "gitops_configuration", "application_services"],
    "drift_detected": ["application_services", "cluster_details", "gitops_configuration", "terraform_infrastructure"],
    "dependency_discovered": ["application_services", "infrastructure_topology"],
    "topology_change": ["infrastructure_topology", "cluster_details"],
}


@dataclass
class DiscoveryResult:
    """Input DTO for creating a pending update."""
    category: str
    target_section: str
    proposed_change: dict
    summary: str
    confidence: float
    source_agent: str
    source_task: str = ""
    source_episode_id: str = ""


@dataclass
class PendingUpdate:
    """Represents a pending update to project-context.json."""
    update_id: str
    content_hash: str
    source_agent: str
    source_task: str
    source_episode_id: str
    category: str
    confidence: float
    target_section: str
    proposed_change: dict
    summary: str
    status: str
    created_at: str
    updated_at: str
    seen_count: int
    last_seen_at: str
    seen_by_agents: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, removing None values."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


class PendingUpdateStore:
    """
    Manages pending updates to project-context.json.

    This class provides methods to:
    - Create and deduplicate update suggestions
    - Approve/reject updates
    - Apply approved updates with automatic backup
    - Track update statistics
    - Maintain an efficient index for fast retrieval
    """

    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        """
        Initialize PendingUpdateStore with specified or default path.

        Args:
            base_path: Base directory for pending updates storage.
                      Defaults to .claude/project-context/pending-updates/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Default location
            self.base_path = Path(".claude/project-context/pending-updates")

        self.updates_jsonl = self.base_path / "pending-updates.jsonl"
        self.index_file = self.base_path / "pending-index.json"
        self.applied_dir = self.base_path / "applied"

        # Auto-create directories
        self._ensure_directories()

    def _ensure_directories(self):
        """Create required directories if they don't exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.applied_dir.mkdir(parents=True, exist_ok=True)

        # Create empty index if it doesn't exist
        if not self.index_file.exists():
            self._save_index({
                "version": "1.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_count": 0,
                "pending_count": 0,
                "updates": {},
                "hash_index": {}
            })

    def _save_index(self, index_data: Dict[str, Any]):
        """Save index to JSON file."""
        with open(self.index_file, 'w') as f:
            json.dump(index_data, f, indent=2)

    def _load_index(self) -> Dict[str, Any]:
        """Load index from JSON file."""
        if not self.index_file.exists():
            return {
                "version": "1.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_count": 0,
                "pending_count": 0,
                "updates": {},
                "hash_index": {}
            }

        try:
            with open(self.index_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # Return empty index if file is corrupted
            return {
                "version": "1.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_count": 0,
                "pending_count": 0,
                "updates": {},
                "hash_index": {}
            }

    def _compute_hash(self, target_section: str, proposed_change: dict) -> str:
        """
        Compute content hash for deduplication.

        Args:
            target_section: Target section in project-context.json
            proposed_change: Proposed change dictionary

        Returns:
            SHA-256 hash (first 12 characters)
        """
        # Create normalized representation for hashing
        content = json.dumps({
            "section": target_section,
            "change": proposed_change
        }, sort_keys=True)

        # Compute SHA-256 and take first 12 characters
        hash_full = hashlib.sha256(content.encode('utf-8')).hexdigest()
        return hash_full[:12]

    def _log_event(self, event: Dict[str, Any]):
        """Append event to JSONL audit trail."""
        with open(self.updates_jsonl, 'a') as f:
            f.write(json.dumps(event) + '\n')

    def _validate_discovery(self, discovery: DiscoveryResult) -> bool:
        """
        Validate discovery result.

        Args:
            discovery: Discovery result to validate

        Returns:
            True if valid, False otherwise
        """
        # Check confidence threshold
        if discovery.confidence < 0.7:
            print(f"Error: Confidence {discovery.confidence} is below threshold 0.7", file=sys.stderr)
            return False

        # Check valid category
        if discovery.category not in CATEGORY_TO_SECTIONS:
            print(f"Error: Invalid category '{discovery.category}'", file=sys.stderr)
            return False

        # Check valid target section for category
        valid_sections = CATEGORY_TO_SECTIONS[discovery.category]
        if discovery.target_section not in valid_sections:
            print(f"Error: Invalid target section '{discovery.target_section}' for category '{discovery.category}'", file=sys.stderr)
            print(f"Valid sections: {valid_sections}", file=sys.stderr)
            return False

        return True

    def create(self, discovery: DiscoveryResult) -> str:
        """
        Create a new pending update or deduplicate with existing.

        Args:
            discovery: Discovery result from agent

        Returns:
            Update ID (new or deduplicated)

        Raises:
            ValueError: If discovery is invalid
        """
        # Validate discovery
        if not self._validate_discovery(discovery):
            raise ValueError("Invalid discovery result")

        # Compute content hash for deduplication
        content_hash = self._compute_hash(discovery.target_section, discovery.proposed_change)

        # Load index
        index = self._load_index()

        # Check for existing update with same hash
        existing_id = index["hash_index"].get(content_hash)

        now = datetime.now(timezone.utc).isoformat()

        if existing_id and existing_id in index["updates"]:
            # Deduplicate - increment seen_count
            existing = index["updates"][existing_id]
            existing["seen_count"] += 1
            existing["last_seen_at"] = now
            existing["updated_at"] = now

            # Add source agent to seen_by_agents if not already present
            if discovery.source_agent not in existing["seen_by_agents"]:
                existing["seen_by_agents"].append(discovery.source_agent)

            # Update index
            self._save_index(index)

            # Log deduplication event
            self._log_event({
                "event": "dedup_increment",
                "update_id": existing_id,
                "timestamp": now,
                "seen_count": existing["seen_count"],
                "source_agent": discovery.source_agent
            })

            print(f"Deduplicated update: {existing_id} (seen_count={existing['seen_count']})", file=sys.stderr)
            return existing_id

        # Create new update
        update_id = f"pu_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{content_hash[:4]}"

        update = PendingUpdate(
            update_id=update_id,
            content_hash=content_hash,
            source_agent=discovery.source_agent,
            source_task=discovery.source_task,
            source_episode_id=discovery.source_episode_id,
            category=discovery.category,
            confidence=discovery.confidence,
            target_section=discovery.target_section,
            proposed_change=discovery.proposed_change,
            summary=discovery.summary,
            status=UpdateStatus.PENDING.value,
            created_at=now,
            updated_at=now,
            seen_count=1,
            last_seen_at=now,
            seen_by_agents=[discovery.source_agent]
        )

        # Add to index
        index["updates"][update_id] = update.to_dict()
        index["hash_index"][content_hash] = update_id
        index["total_count"] += 1
        index["pending_count"] += 1
        index["last_updated"] = now

        self._save_index(index)

        # Log creation event
        self._log_event({
            "event": "created",
            "update_id": update_id,
            "timestamp": now,
            "data": update.to_dict()
        })

        print(f"Created pending update: {update_id}", file=sys.stderr)
        return update_id

    def get(self, update_id: str) -> Optional[PendingUpdate]:
        """
        Get a specific pending update by ID.

        Args:
            update_id: Update ID to retrieve

        Returns:
            PendingUpdate or None if not found
        """
        index = self._load_index()
        update_data = index["updates"].get(update_id)

        if not update_data:
            return None

        return PendingUpdate(**update_data)

    def list_pending(self) -> List[PendingUpdate]:
        """
        List all pending updates, ordered by created_at descending.

        Returns:
            List of PendingUpdate objects with status=pending
        """
        index = self._load_index()
        pending = [
            PendingUpdate(**data)
            for data in index["updates"].values()
            if data["status"] == UpdateStatus.PENDING.value
        ]

        # Sort by created_at descending
        pending.sort(key=lambda x: x.created_at, reverse=True)
        return pending

    def list_all(self, status: Optional[str] = None) -> List[PendingUpdate]:
        """
        List all updates with optional status filter.

        Args:
            status: Optional status filter

        Returns:
            List of PendingUpdate objects
        """
        index = self._load_index()
        updates = []

        for data in index["updates"].values():
            if status is None or data["status"] == status:
                updates.append(PendingUpdate(**data))

        # Sort by created_at descending
        updates.sort(key=lambda x: x.created_at, reverse=True)
        return updates

    def approve(self, update_id: str) -> PendingUpdate:
        """
        Approve a pending update.

        Args:
            update_id: Update ID to approve

        Returns:
            Updated PendingUpdate

        Raises:
            ValueError: If update not found or not pending
        """
        index = self._load_index()
        update_data = index["updates"].get(update_id)

        if not update_data:
            raise ValueError(f"Update {update_id} not found")

        if update_data["status"] != UpdateStatus.PENDING.value:
            raise ValueError(f"Update {update_id} is not pending (status={update_data['status']})")

        # Update status
        old_status = update_data["status"]
        update_data["status"] = UpdateStatus.APPROVED.value
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update counts
        index["pending_count"] -= 1
        index["last_updated"] = update_data["updated_at"]

        self._save_index(index)

        # Log status change
        self._log_event({
            "event": "status_change",
            "update_id": update_id,
            "timestamp": update_data["updated_at"],
            "old_status": old_status,
            "new_status": UpdateStatus.APPROVED.value
        })

        print(f"Approved update: {update_id}", file=sys.stderr)
        return PendingUpdate(**update_data)

    def reject(self, update_id: str) -> PendingUpdate:
        """
        Reject a pending update.

        Args:
            update_id: Update ID to reject

        Returns:
            Updated PendingUpdate

        Raises:
            ValueError: If update not found or not pending
        """
        index = self._load_index()
        update_data = index["updates"].get(update_id)

        if not update_data:
            raise ValueError(f"Update {update_id} not found")

        if update_data["status"] != UpdateStatus.PENDING.value:
            raise ValueError(f"Update {update_id} is not pending (status={update_data['status']})")

        # Update status
        old_status = update_data["status"]
        update_data["status"] = UpdateStatus.REJECTED.value
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update counts
        index["pending_count"] -= 1
        index["last_updated"] = update_data["updated_at"]

        self._save_index(index)

        # Log status change
        self._log_event({
            "event": "status_change",
            "update_id": update_id,
            "timestamp": update_data["updated_at"],
            "old_status": old_status,
            "new_status": UpdateStatus.REJECTED.value
        })

        print(f"Rejected update: {update_id}", file=sys.stderr)
        return PendingUpdate(**update_data)

    def apply(self, update_id: str, context_path: Optional[Union[str, Path]] = None) -> dict:
        """
        Apply an approved update to project-context.json.

        Args:
            update_id: Update ID to apply
            context_path: Path to project-context.json (defaults to standard location)

        Returns:
            Dict with result: {success: bool, update_id: str, target_section: str, backup_path: str}

        Raises:
            ValueError: If update not found or not approved
        """
        index = self._load_index()
        update_data = index["updates"].get(update_id)

        if not update_data:
            raise ValueError(f"Update {update_id} not found")

        if update_data["status"] != UpdateStatus.APPROVED.value:
            raise ValueError(f"Update {update_id} is not approved (status={update_data['status']})")

        # Determine context file path
        if context_path:
            context_file = Path(context_path)
        else:
            context_file = Path(".claude/project-context/project-context.json")

        if not context_file.exists():
            raise ValueError(f"Project context file not found: {context_file}")

        try:
            # Read current context
            with open(context_file, 'r') as f:
                context_data = json.load(f)

            # Validate target section exists
            if "sections" not in context_data:
                raise ValueError("Invalid project-context.json: missing 'sections' key")

            target_section = update_data["target_section"]
            if target_section not in context_data["sections"]:
                raise ValueError(f"Target section '{target_section}' not found in project-context.json")

            # Create backup
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = context_file.parent / f"project-context.backup.{timestamp}.json"
            with open(backup_path, 'w') as f:
                json.dump(context_data, f, indent=2)

            # Apply JSON merge patch
            section_data = context_data["sections"][target_section]
            proposed_change = update_data["proposed_change"]

            # Simple merge: update keys from proposed_change
            self._merge_dicts(section_data, proposed_change)

            # Update metadata
            if "metadata" not in context_data:
                context_data["metadata"] = {}
            context_data["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Atomic write: write to temp file then rename
            temp_file = context_file.parent / f".{context_file.name}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(context_data, f, indent=2)
            temp_file.rename(context_file)

            # Update status
            old_status = update_data["status"]
            update_data["status"] = UpdateStatus.APPLIED.value
            update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

            index["last_updated"] = update_data["updated_at"]
            self._save_index(index)

            # Archive applied update
            applied_file = self.applied_dir / f"update-{update_id}.json"
            with open(applied_file, 'w') as f:
                json.dump(update_data, f, indent=2)

            # Log application event
            self._log_event({
                "event": "status_change",
                "update_id": update_id,
                "timestamp": update_data["updated_at"],
                "old_status": old_status,
                "new_status": UpdateStatus.APPLIED.value
            })

            print(f"Applied update {update_id} to {target_section}", file=sys.stderr)
            print(f"Backup saved: {backup_path}", file=sys.stderr)

            return {
                "success": True,
                "update_id": update_id,
                "target_section": target_section,
                "backup_path": str(backup_path)
            }

        except Exception as e:
            print(f"Error applying update {update_id}: {e}", file=sys.stderr)
            raise

    def _merge_dicts(self, target: dict, source: dict):
        """
        Recursively merge source dict into target dict.

        Args:
            target: Target dictionary to merge into
            source: Source dictionary to merge from
        """
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                # Recursive merge for nested dicts
                self._merge_dicts(target[key], value)
            else:
                # Overwrite or add key
                target[key] = value

    def get_statistics(self) -> dict:
        """
        Get statistics about pending updates.

        Returns:
            Dict with counts by status, category, and agent
        """
        index = self._load_index()

        stats = {
            "total_count": index["total_count"],
            "pending_count": index["pending_count"],
            "by_status": {},
            "by_category": {},
            "by_agent": {}
        }

        # Count by status, category, and agent
        for update_data in index["updates"].values():
            status = update_data["status"]
            category = update_data["category"]
            agent = update_data["source_agent"]

            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
            stats["by_agent"][agent] = stats["by_agent"].get(agent, 0) + 1

        return stats

    def get_pending_count(self) -> int:
        """
        Get count of pending updates (fast path from index).

        Returns:
            Number of pending updates
        """
        index = self._load_index()
        return index["pending_count"]


# CLI interface for testing and management
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pending Update Store Management")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new pending update")
    create_parser.add_argument("--category", required=True, choices=list(CATEGORY_TO_SECTIONS.keys()), help="Discovery category")
    create_parser.add_argument("--section", required=True, help="Target section")
    create_parser.add_argument("--change", required=True, help="Proposed change (JSON string)")
    create_parser.add_argument("--summary", required=True, help="Summary of change")
    create_parser.add_argument("--confidence", type=float, default=0.8, help="Confidence score")
    create_parser.add_argument("--agent", required=True, help="Source agent")
    create_parser.add_argument("--task", default="", help="Source task")

    # List command
    list_parser = subparsers.add_parser("list", help="List pending updates")
    list_parser.add_argument("--status", choices=["pending", "approved", "rejected", "applied"], help="Filter by status")

    # Get command
    get_parser = subparsers.add_parser("get", help="Get a specific update")
    get_parser.add_argument("update_id", help="Update ID")

    # Approve command
    approve_parser = subparsers.add_parser("approve", help="Approve a pending update")
    approve_parser.add_argument("update_id", help="Update ID")

    # Reject command
    reject_parser = subparsers.add_parser("reject", help="Reject a pending update")
    reject_parser.add_argument("update_id", help="Update ID")

    # Apply command
    apply_parser = subparsers.add_parser("apply", help="Apply an approved update")
    apply_parser.add_argument("update_id", help="Update ID")
    apply_parser.add_argument("--context", help="Path to project-context.json")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")

    args = parser.parse_args()

    store = PendingUpdateStore()

    if args.command == "create":
        try:
            proposed_change = json.loads(args.change)
            discovery = DiscoveryResult(
                category=args.category,
                target_section=args.section,
                proposed_change=proposed_change,
                summary=args.summary,
                confidence=args.confidence,
                source_agent=args.agent,
                source_task=args.task
            )
            update_id = store.create(discovery)
            print(f"Created update: {update_id}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        if args.status:
            updates = store.list_all(status=args.status)
        else:
            updates = store.list_pending()

        if not updates:
            print("No updates found")
        else:
            print(f"\nFound {len(updates)} update(s):\n")
            for update in updates:
                print(f"ID: {update.update_id}")
                print(f"  Status: {update.status}")
                print(f"  Category: {update.category}")
                print(f"  Section: {update.target_section}")
                print(f"  Agent: {update.source_agent}")
                print(f"  Confidence: {update.confidence}")
                print(f"  Seen: {update.seen_count} time(s)")
                print(f"  Summary: {update.summary}")
                print(f"  Created: {update.created_at}")
                print()

    elif args.command == "get":
        update = store.get(args.update_id)
        if not update:
            print(f"Update {args.update_id} not found", file=sys.stderr)
            sys.exit(1)

        print(f"\nUpdate: {update.update_id}")
        print(f"  Status: {update.status}")
        print(f"  Category: {update.category}")
        print(f"  Section: {update.target_section}")
        print(f"  Agent: {update.source_agent}")
        print(f"  Task: {update.source_task}")
        print(f"  Confidence: {update.confidence}")
        print(f"  Seen: {update.seen_count} time(s) by {update.seen_by_agents}")
        print(f"  Summary: {update.summary}")
        print(f"  Created: {update.created_at}")
        print(f"  Updated: {update.updated_at}")
        print(f"\n  Proposed change:")
        print(f"  {json.dumps(update.proposed_change, indent=4)}")

    elif args.command == "approve":
        try:
            update = store.approve(args.update_id)
            print(f"Approved update: {update.update_id}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "reject":
        try:
            update = store.reject(args.update_id)
            print(f"Rejected update: {update.update_id}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "apply":
        try:
            result = store.apply(args.update_id, context_path=args.context)
            print(f"Successfully applied update: {result['update_id']}")
            print(f"  Section: {result['target_section']}")
            print(f"  Backup: {result['backup_path']}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "stats":
        stats = store.get_statistics()
        print("\nPending Update Statistics:")
        print(f"  Total updates: {stats['total_count']}")
        print(f"  Pending: {stats['pending_count']}")

        if stats["by_status"]:
            print("\n  By status:")
            for status, count in stats["by_status"].items():
                print(f"    {status}: {count}")

        if stats["by_category"]:
            print("\n  By category:")
            for category, count in stats["by_category"].items():
                print(f"    {category}: {count}")

        if stats["by_agent"]:
            print("\n  By agent:")
            for agent, count in stats["by_agent"].items():
                print(f"    {agent}: {count}")

    else:
        parser.print_help()

#!/usr/bin/env python3
"""
Session Manager for Claude Code Agent System
Orchestrates session intelligence, memory, and context restoration
"""

import json
import sys
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
import argparse
import shutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import our modules
sys.path.append(str(Path(__file__).parent))
try:
    from prime_session import SessionPrimer
    from restore_context import ContextRestorer
    logger.debug("Successfully imported SessionPrimer and ContextRestorer")
except ImportError as e:
    logger.debug(f"Failed to import modules directly: {e}")
    # Fallback for direct execution
    try:
        import importlib.util

        prime_spec = importlib.util.spec_from_file_location("prime_session", Path(__file__).parent / "prime-session.py")
        prime_module = importlib.util.module_from_spec(prime_spec)
        prime_spec.loader.exec_module(prime_module)
        SessionPrimer = prime_module.SessionPrimer

        restore_spec = importlib.util.spec_from_file_location("restore_context", Path(__file__).parent / "restore-context.py")
        restore_module = importlib.util.module_from_spec(restore_spec)
        restore_spec.loader.exec_module(restore_module)
        ContextRestorer = restore_module.ContextRestorer
        logger.info("Loaded modules via importlib.util")
    except Exception as e:
        logger.warning(f"Failed to load optional modules: {e}")
        SessionPrimer = None
        ContextRestorer = None


class SessionManager:
    """Central session management orchestrator"""

    def __init__(self, session_dir: str = ".claude/session"):
        self.session_dir = Path(session_dir)
        self.active_dir = self.session_dir / "active"
        self.memory_dir = self.session_dir / "memory"
        self.bundles_dir = self.session_dir / "bundles"

        # Legacy compatibility
        self.legacy_bundles_dir = Path("contexts/bundles")

        logger.info(f"Initializing SessionManager with session_dir: {session_dir}")

        # Ensure directories exist
        for dir_path in [self.active_dir, self.memory_dir, self.bundles_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        if SessionPrimer:
            self.primer = SessionPrimer(str(self.session_dir))
        if ContextRestorer:
            self.restorer = ContextRestorer(str(self.session_dir))

    def migrate_legacy_bundles(self) -> int:
        """Migrate bundles from legacy location"""
        if not self.legacy_bundles_dir.exists():
            logger.info("No legacy bundles directory found")
            return 0

        migrated_count = 0
        logger.info(f"Migrating bundles from {self.legacy_bundles_dir}")

        try:
            for bundle_dir in self.legacy_bundles_dir.iterdir():
                if bundle_dir.is_dir() and bundle_dir.name != "latest":
                    dest_dir = self.bundles_dir / bundle_dir.name
                    if not dest_dir.exists():
                        shutil.copytree(bundle_dir, dest_dir)
                        migrated_count += 1
                        logger.debug(f"Migrated bundle: {bundle_dir.name}")

            # Update latest symlink
            legacy_latest = self.legacy_bundles_dir / "latest"
            if legacy_latest.exists():
                target = legacy_latest.resolve()
                new_latest = self.bundles_dir / "latest"
                if new_latest.exists():
                    new_latest.unlink()
                new_latest.symlink_to(target.name)

            logger.info(f"Successfully migrated {migrated_count} bundles")
        except (OSError, shutil.Error) as e:
            logger.error(f"Failed to migrate legacy bundles: {e}")

        return migrated_count

    def list_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List available sessions"""
        sessions = []

        logger.debug(f"Listing sessions (limit: {limit})")

        try:
            # Scan bundle directories
            for bundle_dir in sorted(self.bundles_dir.iterdir(), reverse=True):
                if bundle_dir.is_dir() and bundle_dir.name != "latest":
                    session_info = self._analyze_bundle_info(bundle_dir)
                    if session_info:
                        sessions.append(session_info)

            logger.info(f"Found {len(sessions)} sessions")
        except (OSError, IOError) as e:
            logger.error(f"Failed to list sessions: {e}")

        return sessions[:limit]

    def _analyze_bundle_info(self, bundle_dir: Path) -> Optional[Dict[str, Any]]:
        """Analyze bundle to extract session information"""
        metadata_file = bundle_dir / "metadata.json"

        if not metadata_file.exists():
            logger.debug(f"No metadata.json in {bundle_dir.name}")
            return None

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            task_info = metadata.get('task_info', {})

            # Count artifacts
            artifacts_dir = bundle_dir / "artifacts"
            artifact_count = len(list(artifacts_dir.iterdir())) if artifacts_dir.exists() else 0

            # Check for synthesis
            synthesis_exists = (bundle_dir / "synthesis.md").exists()

            return {
                "bundle_id": bundle_dir.name,
                "session_id": metadata.get('session_id', 'unknown'),
                "timestamp": metadata.get('timestamp', ''),
                "agent": task_info.get('agent', 'unknown'),
                "task_id": task_info.get('task_id', 'unknown'),
                "description": task_info.get('description', '')[:100],
                "artifact_count": artifact_count,
                "has_synthesis": synthesis_exists,
                "bundle_path": str(bundle_dir)
            }

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse metadata for {bundle_dir.name}: {e}")
            return None
        except (OSError, IOError) as e:
            logger.warning(f"Failed to read metadata for {bundle_dir.name}: {e}")
            return None

    def synthesize_session(self, bundle_id: str) -> str:
        """Run synthesis on a specific session"""
        bundle_path = self.bundles_dir / bundle_id

        if not bundle_path.exists():
            logger.error(f"Bundle not found: {bundle_id}")
            raise FileNotFoundError(f"Bundle not found: {bundle_id}")

        if not SessionPrimer:
            raise RuntimeError("SessionPrimer module not available")

        logger.info(f"Synthesizing session: {bundle_id}")

        # Run synthesis
        synthesis = self.primer.prime_from_bundle(bundle_id, "full")

        # Generate synthesis report
        synthesis_file = bundle_path / "synthesis.md"
        synthesis_content = self._generate_synthesis_report(synthesis)

        try:
            with open(synthesis_file, 'w') as f:
                f.write(synthesis_content)
            logger.info(f"Synthesis written to {synthesis_file}")
        except (OSError, IOError) as e:
            logger.error(f"Failed to write synthesis file: {e}")
            raise

        return str(synthesis_file)

    def _generate_synthesis_report(self, synthesis) -> str:
        """Generate detailed synthesis report"""
        report_parts = [
            f"# Session Synthesis Report",
            f"",
            f"**Session ID**: {getattr(synthesis, 'session_id', 'unknown')}  ",
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"**Agent**: {getattr(synthesis.critical_decisions[0], 'agent', 'unknown') if hasattr(synthesis, 'critical_decisions') and synthesis.critical_decisions else 'unknown'}",
            f"",
            f"## Executive Summary",
            f"",
        ]

        # Add key insights
        if hasattr(synthesis, 'key_insights') and synthesis.key_insights:
            for insight in synthesis.key_insights:
                report_parts.append(f"- {insight}")
            report_parts.append("")

        # Add critical decisions
        if hasattr(synthesis, 'critical_decisions') and synthesis.critical_decisions:
            report_parts.extend([
                f"## Critical Decisions ({len(synthesis.critical_decisions)})",
                f""
            ])

            for decision in synthesis.critical_decisions:
                # Safe attribute access with fallbacks
                decision_id = getattr(decision, 'id', 'unknown')
                decision_category = getattr(decision, 'decision_category', 'general')
                decision_text = getattr(decision, 'decision', 'No decision text')
                reasoning = getattr(decision, 'reasoning', 'No reasoning provided')
                impact = getattr(decision, 'impact_assessment', getattr(decision, 'impact', 'Unknown impact'))
                status = getattr(decision, 'validation_status', 'Unknown status')
                confidence = getattr(decision, 'confidence_score', getattr(decision, 'confidence', 0.0))

                report_parts.extend([
                    f"### {decision_id}: {decision_category.title()}",
                    f"**Decision**: {decision_text}  ",
                    f"**Reasoning**: {reasoning}  ",
                    f"**Impact**: {impact}  ",
                    f"**Status**: {status}  ",
                    f"**Confidence**: {confidence:.2f}",
                    f""
                ])

        # Add recommendations
        if hasattr(synthesis, 'recommended_actions') and synthesis.recommended_actions:
            report_parts.extend([
                f"## Recommended Next Actions",
                f""
            ])

            for i, action in enumerate(synthesis.recommended_actions, 1):
                report_parts.append(f"{i}. {action}")
            report_parts.append("")

        # Add risk awareness
        if hasattr(synthesis, 'risk_awareness') and synthesis.risk_awareness:
            report_parts.extend([
                f"## Risk Factors",
                f""
            ])

            for risk in synthesis.risk_awareness:
                report_parts.append(f"⚠️ {risk}")
            report_parts.append("")

        report_parts.extend([
            f"---",
            f"*Generated by Session Intelligence Engine*"
        ])

        return '\n'.join(report_parts)

    def clean_old_sessions(self, days: int = 30) -> int:
        """Clean sessions older than specified days"""
        cutoff_date = datetime.now() - timedelta(days=days)
        cleaned_count = 0

        logger.info(f"Cleaning sessions older than {days} days")

        try:
            for bundle_dir in self.bundles_dir.iterdir():
                if bundle_dir.is_dir() and bundle_dir.name != "latest":
                    try:
                        # Parse date from bundle name
                        if bundle_dir.name.startswith("2"):  # Year prefix
                            date_part = bundle_dir.name[:10]  # YYYY-MM-DD
                            bundle_date = datetime.strptime(date_part, "%Y-%m-%d")

                            if bundle_date < cutoff_date:
                                shutil.rmtree(bundle_dir)
                                cleaned_count += 1
                                logger.debug(f"Cleaned bundle: {bundle_dir.name}")

                    except ValueError:
                        logger.debug(f"Skipped bundle with malformed name: {bundle_dir.name}")

            logger.info(f"Cleaned {cleaned_count} old sessions")
        except (OSError, shutil.Error) as e:
            logger.error(f"Failed to clean old sessions: {e}")

        return cleaned_count

    def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics"""
        sessions = self.list_sessions(100)  # Get more for stats

        if not sessions:
            logger.info("No sessions found for statistics")
            return {"total_sessions": 0}

        # Agent usage
        agent_usage = {}
        for session in sessions:
            agent = session.get('agent', 'unknown')
            agent_usage[agent] = agent_usage.get(agent, 0) + 1

        # Recent activity (last 7 days)
        recent_cutoff = datetime.now() - timedelta(days=7)
        recent_sessions = []

        for session in sessions:
            try:
                session_date = datetime.fromisoformat(session.get('timestamp', ''))
                if session_date > recent_cutoff:
                    recent_sessions.append(session)
            except ValueError:
                logger.debug(f"Skipped session with invalid timestamp: {session.get('session_id')}")

        # Memory usage
        total_bundles_size = 0
        try:
            for bundle_dir in self.bundles_dir.iterdir():
                if bundle_dir.is_dir():
                    total_bundles_size += self._get_directory_size(bundle_dir)
        except (OSError, IOError) as e:
            logger.warning(f"Failed to calculate bundle sizes: {e}")

        stats = {
            "total_sessions": len(sessions),
            "recent_sessions": len(recent_sessions),
            "agent_usage": agent_usage,
            "most_used_agent": max(agent_usage.items(), key=lambda x: x[1])[0] if agent_usage else None,
            "total_storage_mb": total_bundles_size / (1024 * 1024),
            "active_context_available": (self.active_dir / "context.json").exists(),
            "memory_entries": len(list(self.memory_dir.glob("*.json")))
        }

        logger.info(f"Session stats: {stats['total_sessions']} total, {stats['recent_sessions']} recent")
        return stats

    def _get_directory_size(self, directory: Path) -> int:
        """Calculate total size of directory"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = Path(dirpath) / filename
                    try:
                        total_size += filepath.stat().st_size
                    except (OSError, IOError):
                        continue
        except (OSError, IOError) as e:
            logger.warning(f"Failed to walk directory {directory}: {e}")

        return total_size

    def export_session(self, bundle_id: str, export_path: str) -> str:
        """Export session bundle to specified path"""
        source_bundle = self.bundles_dir / bundle_id

        if not source_bundle.exists():
            logger.error(f"Bundle not found for export: {bundle_id}")
            raise FileNotFoundError(f"Bundle not found: {bundle_id}")

        export_path = Path(export_path)
        if export_path.exists():
            logger.error(f"Export path already exists: {export_path}")
            raise FileExistsError(f"Export path already exists: {export_path}")

        try:
            shutil.copytree(source_bundle, export_path)
            logger.info(f"Exported session {bundle_id} to {export_path}")
        except (OSError, shutil.Error) as e:
            logger.error(f"Failed to export session: {e}")
            raise

        return str(export_path)

    def import_session(self, bundle_path: str) -> str:
        """Import session bundle from external path"""
        source_path = Path(bundle_path)

        if not source_path.exists():
            logger.error(f"Source bundle not found: {bundle_path}")
            raise FileNotFoundError(f"Bundle not found: {bundle_path}")

        # Generate bundle ID
        bundle_id = source_path.name
        dest_path = self.bundles_dir / bundle_id

        if dest_path.exists():
            logger.error(f"Bundle already exists: {bundle_id}")
            raise FileExistsError(f"Bundle already exists: {bundle_id}")

        try:
            shutil.copytree(source_path, dest_path)
            logger.info(f"Imported session as {bundle_id}")
        except (OSError, shutil.Error) as e:
            logger.error(f"Failed to import session: {e}")
            raise

        return bundle_id


def main():
    """CLI interface for session manager"""
    parser = argparse.ArgumentParser(description="Session Manager")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Management commands")

    # List sessions
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("-l", "--limit", type=int, default=10, help="Limit results")

    # Migrate legacy bundles
    migrate_parser = subparsers.add_parser("migrate", help="Migrate legacy bundles")

    # Synthesize session
    synth_parser = subparsers.add_parser("synthesize", help="Synthesize session")
    synth_parser.add_argument("bundle_id", help="Bundle ID to synthesize")

    # Clean old sessions
    clean_parser = subparsers.add_parser("clean", help="Clean old sessions")
    clean_parser.add_argument("-d", "--days", type=int, default=30, help="Days threshold")

    # Session stats
    stats_parser = subparsers.add_parser("stats", help="Session statistics")

    # Export session
    export_parser = subparsers.add_parser("export", help="Export session")
    export_parser.add_argument("bundle_id", help="Bundle ID to export")
    export_parser.add_argument("export_path", help="Export destination")

    # Import session
    import_parser = subparsers.add_parser("import", help="Import session")
    import_parser.add_argument("bundle_path", help="Bundle path to import")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting session manager")

    try:
        manager = SessionManager()

        if args.command == "list":
            sessions = manager.list_sessions(args.limit)

            print(f"📋 Available Sessions ({len(sessions)})")
            print("=" * 50)

            for session in sessions:
                print(f"**{session['bundle_id']}**")
                print(f"  Agent: {session['agent']}")
                print(f"  Task: {session['task_id']} - {session['description']}")
                print(f"  Artifacts: {session['artifact_count']}")
                print(f"  Synthesis: {'✅' if session['has_synthesis'] else '❌'}")
                print()

        elif args.command == "migrate":
            count = manager.migrate_legacy_bundles()
            print(f"✅ Migrated {count} legacy bundles")

        elif args.command == "synthesize":
            synthesis_file = manager.synthesize_session(args.bundle_id)
            print(f"✅ Synthesis completed: {synthesis_file}")

        elif args.command == "clean":
            count = manager.clean_old_sessions(args.days)
            print(f"✅ Cleaned {count} old sessions (>{args.days} days)")

        elif args.command == "stats":
            stats = manager.get_session_stats()

            print("📊 Session Statistics")
            print("=" * 30)
            print(f"Total Sessions: {stats['total_sessions']}")
            print(f"Recent Activity: {stats['recent_sessions']} (last 7 days)")
            print(f"Storage Used: {stats['total_storage_mb']:.1f} MB")
            print(f"Active Context: {'Available' if stats['active_context_available'] else 'None'}")
            print()

            if stats.get('agent_usage'):
                print("Agent Usage:")
                for agent, count in sorted(stats['agent_usage'].items(), key=lambda x: x[1], reverse=True):
                    print(f"  {agent}: {count} sessions")

        elif args.command == "export":
            export_path = manager.export_session(args.bundle_id, args.export_path)
            print(f"✅ Exported to: {export_path}")

        elif args.command == "import":
            bundle_id = manager.import_session(args.bundle_path)
            print(f"✅ Imported as: {bundle_id}")

        else:
            parser.print_help()

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)
    except FileExistsError as e:
        logger.error(f"File already exists: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

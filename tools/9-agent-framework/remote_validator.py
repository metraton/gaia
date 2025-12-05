#!/usr/bin/env python3
"""
Remote Validator - Phase D (Capa 4)

Validates findings against live infrastructure state.
Supports: Kubernetes, Terraform, GCP, AWS
"""

import logging
import subprocess
import json
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from enum import Enum

logger = logging.getLogger(__name__)


class ValidationMethod(Enum):
    """Available validation methods"""
    KUBECTL = "kubectl"
    TERRAFORM = "terraform"
    GCLOUD = "gcloud"
    AWS = "aws"


@dataclass
class RemoteValidationResult:
    """Result of remote validation"""
    method: ValidationMethod
    resource_type: str
    resource_name: str
    found: bool
    state: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    command_executed: Optional[str] = None


class RemoteValidator:
    """
    Validates findings against live infrastructure.

    Executes read-only commands to verify state:
    - kubectl get/describe
    - terraform show
    - gcloud describe
    - aws describe
    """

    def __init__(self, dry_run: bool = False):
        """
        Initialize remote validator.

        Args:
            dry_run: If True, log commands without executing
        """
        self.dry_run = dry_run
        self.results: List[RemoteValidationResult] = []

    def validate_kubernetes_resource(
        self,
        resource_type: str,
        resource_name: str,
        namespace: str = "default"
    ) -> RemoteValidationResult:
        """
        Validate Kubernetes resource exists and get state.

        Args:
            resource_type: e.g., "pod", "deployment", "service"
            resource_name: Name of resource
            namespace: Kubernetes namespace

        Returns:
            RemoteValidationResult with resource state
        """
        cmd = f"kubectl get {resource_type} {resource_name} -n {namespace} -o json"

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {cmd}")
            return RemoteValidationResult(
                method=ValidationMethod.KUBECTL,
                resource_type=resource_type,
                resource_name=resource_name,
                found=True,  # Assume found in dry-run
                command_executed=cmd
            )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                state = json.loads(result.stdout)
                return RemoteValidationResult(
                    method=ValidationMethod.KUBECTL,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    found=True,
                    state=state,
                    command_executed=cmd
                )
            else:
                return RemoteValidationResult(
                    method=ValidationMethod.KUBECTL,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    found=False,
                    error=result.stderr,
                    command_executed=cmd
                )

        except subprocess.TimeoutExpired:
            return RemoteValidationResult(
                method=ValidationMethod.KUBECTL,
                resource_type=resource_type,
                resource_name=resource_name,
                found=False,
                error="Command timeout (30s)",
                command_executed=cmd
            )
        except Exception as e:
            return RemoteValidationResult(
                method=ValidationMethod.KUBECTL,
                resource_type=resource_type,
                resource_name=resource_name,
                found=False,
                error=str(e),
                command_executed=cmd
            )

    def validate_terraform_resource(
        self,
        resource_address: str,
        terraform_dir: Optional[str] = None
    ) -> RemoteValidationResult:
        """
        Validate Terraform resource exists in state.

        Args:
            resource_address: e.g., "google_compute_instance.example"
            terraform_dir: Path to terraform directory

        Returns:
            RemoteValidationResult with resource state from tfstate
        """
        # Build command
        cmd_parts = []
        if terraform_dir:
            cmd_parts.append(f"cd {terraform_dir} &&")
        cmd_parts.append(f"terraform show -json")

        cmd = " ".join(cmd_parts)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {cmd}")
            return RemoteValidationResult(
                method=ValidationMethod.TERRAFORM,
                resource_type="terraform",
                resource_name=resource_address,
                found=True,
                command_executed=cmd
            )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                tfstate = json.loads(result.stdout)

                # Search for resource in state
                found = False
                resource_state = None

                if "values" in tfstate and "root_module" in tfstate["values"]:
                    resources = tfstate["values"]["root_module"].get("resources", [])
                    for resource in resources:
                        if resource.get("address") == resource_address:
                            found = True
                            resource_state = resource
                            break

                return RemoteValidationResult(
                    method=ValidationMethod.TERRAFORM,
                    resource_type="terraform",
                    resource_name=resource_address,
                    found=found,
                    state=resource_state,
                    command_executed=cmd
                )
            else:
                return RemoteValidationResult(
                    method=ValidationMethod.TERRAFORM,
                    resource_type="terraform",
                    resource_name=resource_address,
                    found=False,
                    error=result.stderr,
                    command_executed=cmd
                )

        except subprocess.TimeoutExpired:
            return RemoteValidationResult(
                method=ValidationMethod.TERRAFORM,
                resource_type="terraform",
                resource_name=resource_address,
                found=False,
                error="Command timeout (60s)",
                command_executed=cmd
            )
        except Exception as e:
            return RemoteValidationResult(
                method=ValidationMethod.TERRAFORM,
                resource_type="terraform",
                resource_name=resource_address,
                found=False,
                error=str(e),
                command_executed=cmd
            )

    def validate_gcp_resource(
        self,
        resource_type: str,
        resource_name: str,
        project: Optional[str] = None,
        zone: Optional[str] = None
    ) -> RemoteValidationResult:
        """
        Validate GCP resource exists.

        Args:
            resource_type: e.g., "instances", "disks", "networks"
            resource_name: Name of resource
            project: GCP project ID
            zone: GCP zone (if applicable)

        Returns:
            RemoteValidationResult with resource description
        """
        cmd_parts = ["gcloud", "compute", resource_type, "describe", resource_name]

        if project:
            cmd_parts.extend(["--project", project])
        if zone:
            cmd_parts.extend(["--zone", zone])

        cmd_parts.append("--format=json")
        cmd = " ".join(cmd_parts)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {cmd}")
            return RemoteValidationResult(
                method=ValidationMethod.GCLOUD,
                resource_type=resource_type,
                resource_name=resource_name,
                found=True,
                command_executed=cmd
            )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                state = json.loads(result.stdout)
                return RemoteValidationResult(
                    method=ValidationMethod.GCLOUD,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    found=True,
                    state=state,
                    command_executed=cmd
                )
            else:
                return RemoteValidationResult(
                    method=ValidationMethod.GCLOUD,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    found=False,
                    error=result.stderr,
                    command_executed=cmd
                )

        except subprocess.TimeoutExpired:
            return RemoteValidationResult(
                method=ValidationMethod.GCLOUD,
                resource_type=resource_type,
                resource_name=resource_name,
                found=False,
                error="Command timeout (30s)",
                command_executed=cmd
            )
        except Exception as e:
            return RemoteValidationResult(
                method=ValidationMethod.GCLOUD,
                resource_type=resource_type,
                resource_name=resource_name,
                found=False,
                error=str(e),
                command_executed=cmd
            )

    def validate_aws_resource(
        self,
        service: str,
        resource_type: str,
        resource_id: str,
        region: Optional[str] = None
    ) -> RemoteValidationResult:
        """
        Validate AWS resource exists.

        Args:
            service: AWS service (e.g., "ec2", "s3", "rds")
            resource_type: Resource type (e.g., "instances", "buckets")
            resource_id: Resource identifier
            region: AWS region

        Returns:
            RemoteValidationResult with resource description
        """
        cmd_parts = ["aws", service, f"describe-{resource_type}"]

        # Add resource identifier based on service
        if service == "ec2":
            cmd_parts.extend(["--instance-ids", resource_id])
        elif service == "rds":
            cmd_parts.extend(["--db-instance-identifier", resource_id])
        else:
            cmd_parts.extend([resource_id])

        if region:
            cmd_parts.extend(["--region", region])

        cmd_parts.append("--output=json")
        cmd = " ".join(cmd_parts)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {cmd}")
            return RemoteValidationResult(
                method=ValidationMethod.AWS,
                resource_type=f"{service}:{resource_type}",
                resource_name=resource_id,
                found=True,
                command_executed=cmd
            )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                state = json.loads(result.stdout)
                return RemoteValidationResult(
                    method=ValidationMethod.AWS,
                    resource_type=f"{service}:{resource_type}",
                    resource_name=resource_id,
                    found=True,
                    state=state,
                    command_executed=cmd
                )
            else:
                return RemoteValidationResult(
                    method=ValidationMethod.AWS,
                    resource_type=f"{service}:{resource_type}",
                    resource_name=resource_id,
                    found=False,
                    error=result.stderr,
                    command_executed=cmd
                )

        except subprocess.TimeoutExpired:
            return RemoteValidationResult(
                method=ValidationMethod.AWS,
                resource_type=f"{service}:{resource_type}",
                resource_name=resource_id,
                found=False,
                error="Command timeout (30s)",
                command_executed=cmd
            )
        except Exception as e:
            return RemoteValidationResult(
                method=ValidationMethod.AWS,
                resource_type=f"{service}:{resource_type}",
                resource_name=resource_id,
                found=False,
                error=str(e),
                command_executed=cmd
            )

    def generate_report(self) -> str:
        """
        Generate human-readable report of all validations.

        Returns:
            Formatted report string
        """
        if not self.results:
            return "No remote validations performed."

        lines = [
            "="*60,
            "REMOTE VALIDATION RESULTS",
            "="*60,
            ""
        ]

        for i, result in enumerate(self.results, 1):
            status = "✅ FOUND" if result.found else "❌ NOT FOUND"
            lines.append(f"{i}. {result.method.value}: {result.resource_name}")
            lines.append(f"   Status: {status}")
            lines.append(f"   Command: {result.command_executed}")

            if result.error:
                lines.append(f"   Error: {result.error}")

            if result.state and not self.dry_run:
                # Show brief state info
                if result.method == ValidationMethod.KUBECTL:
                    status_phase = result.state.get("status", {}).get("phase", "N/A")
                    lines.append(f"   State: phase={status_phase}")
                elif result.method == ValidationMethod.GCLOUD:
                    gcp_status = result.state.get("status", "N/A")
                    lines.append(f"   State: status={gcp_status}")

            lines.append("")

        lines.append("="*60)
        return "\n".join(lines)

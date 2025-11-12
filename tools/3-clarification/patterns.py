"""
Ambiguity Detection Patterns

Defines keyword patterns and heuristics for detecting
ambiguous user prompts that require clarification.
"""

from typing import List, Dict, Any, Optional
import re


class AmbiguityPattern:
    """Base class for ambiguity detection patterns."""

    def __init__(self, name: str, keywords: List[str], weight: int):
        """
        Args:
            name: Pattern name (e.g., "service_ambiguity")
            keywords: Keywords that trigger this pattern
            weight: Ambiguity weight (0-100)
        """
        self.name = name
        self.keywords = keywords
        self.weight = weight

    def detect(self, prompt: str, project_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Detect ambiguity in prompt.

        Returns:
            None if no ambiguity, otherwise:
            {
                "pattern": str,
                "detected_keyword": str,
                "ambiguity_reason": str,
                "available_options": List[str],
                "suggested_question": str,
                "weight": int,
                "allow_multiple": bool
            }
        """
        raise NotImplementedError


class ServiceAmbiguityPattern(AmbiguityPattern):
    """Detects ambiguous service references."""

    def __init__(self):
        super().__init__(
            name="service_ambiguity",
            keywords=[
                "the api", "el api", "la api", "api service", "the service",
                "el servicio", "the app", "la app", "the web", "web service",
                "the bot", "el bot", "the jobs", "los jobs", "el worker",
                "check service", "chequea servicio", "deploy service",
                "service status", "estado del servicio"
            ],
            weight=80  # High weight - very ambiguous
        )

    def detect(self, prompt: str, project_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt_lower = prompt.lower()

        # Check if any keyword matches
        detected_keyword = None
        for keyword in self.keywords:
            if keyword in prompt_lower:
                detected_keyword = keyword
                break

        if not detected_keyword:
            return None

        # Extract available services from project_context
        services = []
        services_metadata = {}

        if "sections" in project_context and "application_services" in project_context["sections"]:
            for svc in project_context["sections"]["application_services"]:
                service_name = svc.get("name", "unknown")
                services.append(service_name)
                services_metadata[service_name] = svc

        # Check if user already specified a service name
        for service_name in services:
            if service_name in prompt_lower:
                # User already specified which service, no ambiguity
                return None

        # If multiple services, this is ambiguous
        if len(services) > 1:
            return {
                "pattern": self.name,
                "detected_keyword": detected_keyword,
                "ambiguity_reason": f"Mencionaste '{detected_keyword}', pero hay {len(services)} servicios en este proyecto.",
                "available_options": services,
                "services_metadata": services_metadata,
                "suggested_question": f"¿Qué servicio quieres {self._infer_action(prompt)}?",
                "weight": self.weight,
                "allow_multiple": False
            }

        return None

    def _infer_action(self, prompt: str) -> str:
        """Infer user action from prompt for better question phrasing."""
        prompt_lower = prompt.lower()

        if any(word in prompt_lower for word in ["check", "chequea", "revisar", "status"]):
            return "revisar"
        elif any(word in prompt_lower for word in ["deploy", "desplegar", "update", "actualizar"]):
            return "desplegar"
        elif any(word in prompt_lower for word in ["fix", "arreglar", "debug"]):
            return "debuggear"
        else:
            return "trabajar con"


class NamespaceAmbiguityPattern(AmbiguityPattern):
    """Detects ambiguous namespace references."""

    def __init__(self):
        super().__init__(
            name="namespace_ambiguity",
            keywords=[
                "deploy to cluster", "desplegar en cluster", "check cluster",
                "chequear cluster", "the namespace", "el namespace",
                "to the cluster", "en el cluster", "in the cluster",
                "al cluster", "cluster status", "estado del cluster"
            ],
            weight=60  # Medium-high weight
        )

    def detect(self, prompt: str, project_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt_lower = prompt.lower()

        detected_keyword = None
        for keyword in self.keywords:
            if keyword in prompt_lower:
                detected_keyword = keyword
                break

        if not detected_keyword:
            return None

        # Extract namespaces (support both old and new structures)
        namespaces = []
        namespace_metadata = {}

        if "sections" in project_context:
            # New structure: sections.namespaces (list of objects)
            if "namespaces" in project_context["sections"]:
                namespace_list = project_context["sections"]["namespaces"]
                namespaces = [ns["name"] for ns in namespace_list] if isinstance(namespace_list, list) else []
            # Old structure: sections.cluster_details.primary_namespaces (list of strings)
            elif "cluster_details" in project_context["sections"]:
                cluster_details = project_context["sections"]["cluster_details"]
                if isinstance(cluster_details, dict) and "primary_namespaces" in cluster_details:
                    namespaces = cluster_details["primary_namespaces"]

            # Build metadata: count services per namespace
            if "application_services" in project_context["sections"]:
                for ns in namespaces:
                    services_in_ns = [
                        svc["name"] for svc in project_context["sections"]["application_services"]
                        if svc.get("namespace") == ns
                    ]
                    namespace_metadata[ns] = {
                        "services": services_in_ns,
                        "service_count": len(services_in_ns)
                    }

        if len(namespaces) > 1:
            return {
                "pattern": self.name,
                "detected_keyword": detected_keyword,
                "ambiguity_reason": f"El cluster tiene {len(namespaces)} namespaces.",
                "available_options": namespaces,
                "namespace_metadata": namespace_metadata,
                "suggested_question": "¿En qué namespace debería trabajar?",
                "weight": self.weight,
                "allow_multiple": False
            }

        return None


class EnvironmentAmbiguityPattern(AmbiguityPattern):
    """Detects environment confusion."""

    def __init__(self):
        super().__init__(
            name="environment_ambiguity",
            keywords=["production", "prod", "staging", "producción"],
            weight=90  # Very high weight - critical
        )

    def detect(self, prompt: str, project_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt_lower = prompt.lower()

        detected_keyword = None
        for keyword in self.keywords:
            # Use word boundaries to avoid matching "non-prod" when looking for "prod"
            import re
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, prompt_lower):
                # Additional check: don't match "prod" if it's part of "non-prod"
                if keyword == "prod" or keyword == "production":
                    # Check if "non-prod" or "nonprod" is in the prompt
                    if re.search(r'non[-\s]?prod', prompt_lower):
                        continue  # Skip this keyword, it's part of "non-prod"
                detected_keyword = keyword
                break

        if not detected_keyword:
            return None

        # Check current environment
        current_env = "unknown"
        if "sections" in project_context and "project_details" in project_context["sections"]:
            current_env = project_context["sections"]["project_details"].get("environment", "unknown")

        # If user says "prod" but environment is "non-prod", warn
        if detected_keyword in ["production", "prod", "producción"] and current_env == "non-prod":
            return {
                "pattern": self.name,
                "detected_keyword": detected_keyword,
                "ambiguity_reason": f"⚠️ Mencionaste '{detected_keyword}', pero project-context.json muestra environment='{current_env}'.",
                "available_options": [
                    f"Continuar en {current_env}",
                    "Detener (voy a cambiar configuración)",
                    "Forzar producción (PELIGROSO)"
                ],
                "current_environment": current_env,
                "requested_environment": detected_keyword,
                "suggested_question": f"Mencionaste '{detected_keyword}', pero el proyecto está configurado como '{current_env}'. ¿Cómo proceder?",
                "weight": self.weight,
                "allow_multiple": False
            }

        return None


class ResourceAmbiguityPattern(AmbiguityPattern):
    """Detects ambiguous infrastructure resource references."""

    def __init__(self):
        super().__init__(
            name="resource_ambiguity",
            keywords=[
                "the redis", "el redis", "redis instance", "instancia redis",
                "the database", "la database", "the postgres", "el postgres",
                "the sql", "cloud sql", "postgres instance", "instancia postgres"
            ],
            weight=70  # Medium-high weight
        )

    def detect(self, prompt: str, project_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt_lower = prompt.lower()

        detected_keyword = None
        resource_type = None
        for keyword in self.keywords:
            if keyword in prompt_lower:
                detected_keyword = keyword
                # Infer resource type
                if "redis" in keyword:
                    resource_type = "Redis"
                elif any(db in keyword for db in ["postgres", "sql", "database"]):
                    resource_type = "Database"
                break

        if not detected_keyword:
            return None

        # Extract resources from terraform_infrastructure or application_services
        resources = []
        resource_metadata = {}

        # Check terraform infrastructure
        if "sections" in project_context and "terraform_infrastructure" in project_context["sections"]:
            infra = project_context["sections"]["terraform_infrastructure"]
            if "modules" in infra:
                for module_name, module_info in infra["modules"].items():
                    # Check if module matches resource type
                    if resource_type == "Redis" and "redis" in module_name.lower():
                        resources.append(module_name)
                        resource_metadata[module_name] = {
                            "type": module_info.get("resources", "Memorystore Redis"),
                            "status": module_info.get("status", "unknown"),
                            "tier": module_info.get("tier", "N/A")
                        }
                    elif resource_type == "Database" and any(db in module_name.lower() for db in ["sql", "postgres", "database"]):
                        resources.append(module_name)
                        resource_metadata[module_name] = {
                            "type": module_info.get("resources", "Cloud SQL PostgreSQL"),
                            "status": module_info.get("status", "unknown"),
                            "tier": module_info.get("tier", "N/A")
                        }

        # Also check application_services for Redis/DB references
        if "sections" in project_context and "application_services" in project_context["sections"]:
            for svc in project_context["sections"]["application_services"]:
                service_name = svc.get("name", "")
                if resource_type and resource_type.lower() in service_name.lower():
                    if service_name not in resources:
                        resources.append(service_name)
                        resource_metadata[service_name] = {
                            "type": "Application Service",
                            "status": svc.get("status", "unknown"),
                            "namespace": svc.get("namespace", "N/A")
                        }

        if len(resources) > 1:
            return {
                "pattern": self.name,
                "detected_keyword": detected_keyword,
                "ambiguity_reason": f"Encontré {len(resources)} recursos de {resource_type} en el proyecto.",
                "available_options": resources,
                "resource_metadata": resource_metadata,
                "resource_type": resource_type,
                "suggested_question": f"¿Qué recurso de {resource_type} quieres usar?",
                "weight": self.weight,
                "allow_multiple": False
            }

        return None


# ============================================================================
# PATTERN REGISTRY
# ============================================================================

AMBIGUITY_PATTERNS = [
    ServiceAmbiguityPattern(),
    NamespaceAmbiguityPattern(),
    EnvironmentAmbiguityPattern(),
    ResourceAmbiguityPattern()
]


def detect_all_ambiguities(prompt: str, project_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run all ambiguity patterns and return detected ambiguities.

    Args:
        prompt: User prompt to analyze
        project_context: Loaded project-context.json

    Returns:
        List of detected ambiguities (sorted by weight, descending)
    """
    ambiguities = []

    for pattern in AMBIGUITY_PATTERNS:
        result = pattern.detect(prompt, project_context)
        if result:
            ambiguities.append(result)

    # Sort by weight (highest first)
    ambiguities.sort(key=lambda x: x["weight"], reverse=True)

    return ambiguities
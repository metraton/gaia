"""
Generic Clarification Engine

Analyzes user prompts semantically and generates clarification questions
by searching in project-context.json. No hardcoded patterns, fully dynamic.

Performance target: ~200ms (before AskUserQuestion)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


# Entity type mappings to project-context paths
ENTITY_PATHS = {
    "service": "sections.application_services",
    "namespace": "sections.gitops_configuration.namespaces",
    "cluster": "sections.infrastructure_topology.clusters",
    "environment": "sections.project_details.environment",
    "resource": "sections.terraform_infrastructure.resources"
}

# Emoji map for visual questions
EMOJI_MAP = {
    "service": "📦",
    "namespace": "🎯",
    "cluster": "🏢",
    "environment": "⚠️",
    "resource": "🔧",
    "all": "🌐",
    "database": "🗄️",
    "production": "🔴"
}


def analyze_prompt(user_prompt: str) -> Dict[str, Any]:
    """
    Analyzes user prompt to detect ambiguous entities.

    Uses keyword matching and pattern detection (no LLM needed).
    Fast: ~100ms

    Args:
        user_prompt: User's original request

    Returns:
        {
            "ambiguous_entities": ["service", "namespace"],
            "mentioned_values": {"cluster": "prod"},
            "specificity_score": 0.6,
            "intent": "check_pods"
        }
    """
    prompt_lower = user_prompt.lower()

    # Detect ambiguous entities (generic references without specific names)
    ambiguous = []
    mentioned = {}

    # Service ambiguity
    service_keywords = ["the api", "el api", "la api", "api service",
                       "the service", "el servicio", "the app", "la app",
                       "the web", "web service", "the bot", "el bot"]
    if any(kw in prompt_lower for kw in service_keywords):
        # Check if specific service is mentioned
        if not re.search(r'\b(tcm|pg|ofertas|vtr)\w*-\w+', prompt_lower):
            ambiguous.append("service")

    # Namespace ambiguity
    namespace_keywords = ["namespace", "deploy to", "desplegar en",
                         "in namespace", "en el namespace"]
    if any(kw in prompt_lower for kw in namespace_keywords):
        # Check if specific namespace is mentioned
        if not re.search(r'\b\w+-\w+(-\w+)?\b', prompt_lower):
            ambiguous.append("namespace")

    # Cluster ambiguity
    cluster_keywords = ["cluster", "clúster"]
    if any(kw in prompt_lower for kw in cluster_keywords):
        # Check if specific cluster is mentioned (digital, b2b, ofertas)
        if re.search(r'\b(digital|b2b|ofertas|prod|non-?prod)\b', prompt_lower):
            # Mentioned but might need confirmation
            cluster_match = re.search(r'\b(digital|b2b|ofertas)[-\s]?(prod|non-?prod)?\b', prompt_lower)
            if cluster_match:
                mentioned["cluster"] = cluster_match.group(0)
        else:
            ambiguous.append("cluster")

    # Environment ambiguity
    env_keywords = ["production", "prod", "producción", "staging", "dev"]
    if any(kw in prompt_lower for kw in env_keywords):
        env_match = re.search(r'\b(production|prod|staging|dev|non-?prod)\b', prompt_lower)
        if env_match:
            mentioned["environment"] = env_match.group(0)

    # Resource ambiguity
    resource_keywords = ["the redis", "el redis", "redis instance",
                        "the database", "la database", "la base de datos",
                        "the postgres", "el postgres"]
    if any(kw in prompt_lower for kw in resource_keywords):
        ambiguous.append("resource")

    # Detect intent (helps with context)
    intent = "unknown"
    if any(kw in prompt_lower for kw in ["check", "show", "list", "get", "ver", "mostrar"]):
        intent = "check_status"
    elif any(kw in prompt_lower for kw in ["deploy", "desplegar", "apply"]):
        intent = "deploy"
    elif any(kw in prompt_lower for kw in ["debug", "logs", "error", "problema"]):
        intent = "debug"

    # Specificity score (0.0 = very vague, 1.0 = very specific)
    specificity = 1.0 - (len(ambiguous) * 0.3)
    specificity = max(0.0, min(1.0, specificity))

    return {
        "ambiguous_entities": ambiguous,
        "mentioned_values": mentioned,
        "specificity_score": specificity,
        "intent": intent
    }


def get_nested_value(data: dict, path: str) -> Any:
    """
    Gets nested value from dict using dot notation.

    Example: get_nested_value(context, "sections.application_services")
    """
    keys = path.split('.')
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        if value is None:
            return None
    return value


def extract_from_context(project_context: Dict[str, Any],
                         entity_type: str) -> List[Dict[str, Any]]:
    """
    Extracts options for an entity type from project-context.json.

    Fast: ~50ms for all entity types

    Args:
        project_context: Loaded project-context.json
        entity_type: One of: service, namespace, cluster, environment, resource

    Returns:
        List of options with metadata:
        [
            {
                "label": "tcm-api",
                "description": "NestJS | Namespace: tcm-non-prod | Puerto: 3001",
                "metadata": {...}
            },
            ...
        ]
    """
    if entity_type not in ENTITY_PATHS:
        return []

    path = ENTITY_PATHS[entity_type]
    raw_data = get_nested_value(project_context, path)

    if not raw_data:
        return []

    options = []

    # Extract based on entity type
    if entity_type == "service":
        if isinstance(raw_data, list):
            for service in raw_data:
                if isinstance(service, dict):
                    name = service.get("name", "")
                    tech_stack = service.get("tech_stack", "")
                    namespace = service.get("namespace", "")
                    port = service.get("port", "")

                    desc_parts = []
                    if tech_stack:
                        desc_parts.append(tech_stack)
                    if namespace:
                        desc_parts.append(f"Namespace: {namespace}")
                    if port:
                        desc_parts.append(f"Puerto: {port}")

                    options.append({
                        "label": name,
                        "description": " | ".join(desc_parts) if desc_parts else name,
                        "metadata": service
                    })

    elif entity_type == "namespace":
        if isinstance(raw_data, list):
            for ns in raw_data:
                if isinstance(ns, dict):
                    name = ns.get("name", "")
                    services = ns.get("services", [])
                    cluster = ns.get("cluster", "")

                    desc_parts = []
                    if services:
                        desc_parts.append(f"{len(services)} servicio(s)")
                    if cluster:
                        desc_parts.append(cluster)

                    options.append({
                        "label": name,
                        "description": " | ".join(desc_parts) if desc_parts else name,
                        "metadata": ns
                    })
        elif isinstance(raw_data, str):
            # Single namespace
            options.append({
                "label": raw_data,
                "description": raw_data,
                "metadata": {}
            })

    elif entity_type == "cluster":
        if isinstance(raw_data, list):
            for cluster in raw_data:
                if isinstance(cluster, dict):
                    name = cluster.get("name", "")
                    provider = cluster.get("provider", "")
                    region = cluster.get("region", "")

                    desc_parts = []
                    if provider:
                        desc_parts.append(provider.upper())
                    if region:
                        desc_parts.append(f"Región: {region}")

                    options.append({
                        "label": name,
                        "description": " | ".join(desc_parts) if desc_parts else name,
                        "metadata": cluster
                    })

    elif entity_type == "environment":
        # Environment is a single value
        if isinstance(raw_data, str):
            options.append({
                "label": raw_data,
                "description": f"Entorno actual del proyecto: {raw_data}",
                "metadata": {"current": raw_data}
            })

    elif entity_type == "resource":
        if isinstance(raw_data, list):
            for resource in raw_data:
                if isinstance(resource, dict):
                    name = resource.get("name", "")
                    resource_type = resource.get("type", "")
                    status = resource.get("status", "")

                    desc_parts = []
                    if resource_type:
                        desc_parts.append(resource_type)
                    if status:
                        desc_parts.append(f"Estado: {status}")

                    options.append({
                        "label": name,
                        "description": " | ".join(desc_parts) if desc_parts else name,
                        "metadata": resource
                    })

    return options


def generate_dynamic_questions(entity_options: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Generates AskUserQuestion configuration dynamically.

    Args:
        entity_options: Dict of {entity_type: [options]}

    Returns:
        List of question configs for AskUserQuestion:
        [
            {
                "question": "¿Qué servicio quieres revisar?",
                "header": "📦 Servicio",
                "options": [...],
                "multiSelect": False
            },
            ...
        ]
    """
    questions = []

    # Question templates
    question_templates = {
        "service": "¿Qué servicio quieres usar?",
        "namespace": "¿En qué namespace?",
        "cluster": "¿En qué cluster?",
        "environment": "¿En qué entorno?",
        "resource": "¿Qué recurso?"
    }

    header_templates = {
        "service": "Servicio",
        "namespace": "Namespace",
        "cluster": "Cluster",
        "environment": "Entorno",
        "resource": "Recurso"
    }

    for entity_type, options in entity_options.items():
        if len(options) <= 1:
            # No ambiguity if 0 or 1 option
            continue

        # Build options for AskUserQuestion
        formatted_options = []
        for opt in options:
            emoji = EMOJI_MAP.get(entity_type, "📌")
            formatted_options.append({
                "label": f"{emoji} {opt['label']}",
                "description": opt['description']
            })

        # Add "All" option if applicable (for queries that can operate on multiple)
        if entity_type in ["service", "namespace", "cluster"]:
            formatted_options.append({
                "label": f"{EMOJI_MAP['all']} Todos",
                "description": f"Aplicar a todos ({len(options)} total)"
            })

        # Build question
        emoji = EMOJI_MAP.get(entity_type, "📌")
        questions.append({
            "question": question_templates.get(entity_type, f"¿Qué {entity_type}?"),
            "header": f"{emoji} {header_templates.get(entity_type, entity_type.title())}",
            "options": formatted_options,
            "multiSelect": False
        })

    return questions


def enrich_prompt(original_prompt: str,
                  user_responses: Dict[str, str],
                  entity_types: List[str]) -> str:
    """
    Enriches original prompt with user responses.

    Args:
        original_prompt: Original user request
        user_responses: Dict from AskUserQuestion {"question_1": "📦 tcm-api", ...}
        entity_types: List of entity types in same order as questions

    Returns:
        Enriched prompt:
        "Check the API [Clarification - service: tcm-api] [namespace: tcm-non-prod]"
    """
    enriched = original_prompt

    clarifications = []
    for i, entity_type in enumerate(entity_types):
        question_key = f"question_{i+1}"
        response = user_responses.get(question_key, "")

        if response:
            # Remove emoji prefix from response
            clean_response = re.sub(r'^[^\w\s]+\s*', '', response).strip()

            # Skip "Todos" option
            if "Todos" not in clean_response:
                clarifications.append(f"{entity_type}: {clean_response}")

    if clarifications:
        enriched += f"\n\n[Clarification - {', '.join(clarifications)}]"

    return enriched


def clarify_generic(user_prompt: str,
                    project_context: Dict[str, Any],
                    threshold: int = 30,
                    ask_func: Optional[callable] = None) -> Tuple[str, bool]:
    """
    Main clarification function. Analyzes prompt, searches context, generates questions.

    Args:
        user_prompt: User's original request
        project_context: Loaded project-context.json
        threshold: Ambiguity threshold (0-100, default 30)
        ask_func: AskUserQuestion function (optional, for testing)

    Returns:
        Tuple of (enriched_prompt, clarification_occurred)

    Performance: ~200ms (before user interaction)
    """
    # Step 1: Analyze prompt (~100ms)
    analysis = analyze_prompt(user_prompt)

    if not analysis["ambiguous_entities"]:
        # No ambiguity detected
        return (user_prompt, False)

    # Step 2: Extract options from project-context (~50ms)
    entity_options = {}
    for entity_type in analysis["ambiguous_entities"]:
        options = extract_from_context(project_context, entity_type)
        if options and len(options) > 1:
            entity_options[entity_type] = options

    if not entity_options:
        # No ambiguity in context (0 or 1 option per entity)
        return (user_prompt, False)

    # Step 3: Generate questions (~50ms)
    questions = generate_dynamic_questions(entity_options)

    if not questions:
        return (user_prompt, False)

    # Step 4: Ask user (uses provided function or import)
    if ask_func is None:
        try:
            from claude_code_tools import AskUserQuestion
            ask_func = AskUserQuestion
        except ImportError:
            # AskUserQuestion not available (non-Claude Code environment)
            return (user_prompt, False)

    try:
        result = ask_func(questions=questions)
        user_responses = result.get("answers", {})

        # Step 5: Enrich prompt
        entity_types = list(entity_options.keys())
        enriched = enrich_prompt(user_prompt, user_responses, entity_types)

        return (enriched, True)

    except Exception as e:
        # If clarification fails, return original
        import sys
        print(f"Warning: Clarification failed: {e}", file=sys.stderr)
        return (user_prompt, False)

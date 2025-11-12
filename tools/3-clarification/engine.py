"""
Intelligent Clarification Engine

Detects ambiguity in user prompts and asks targeted questions
before agent routing. Follows the approval_gate.py pattern.
"""

import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from difflib import get_close_matches


class ClarificationEngine:
    """
    Analyzes user prompts for ambiguity and generates intelligent
    clarification questions using project-context.json.

    Design Pattern: Modeled after approval_gate.py
    """

    def __init__(self, project_context_path: str = ".claude/project-context.json"):
        self.project_context_path = project_context_path
        self.project_context = self._load_project_context()
        self.clarification_log_path = ".claude/logs/clarifications.jsonl"
        self.config = self._load_config()
        self._ensure_log_directory()

    # ========================================================================
    # PUBLIC API (Orchestrator Entry Points)
    # ========================================================================

    def detect_ambiguity(
        self,
        user_prompt: str,
        command_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Analyze user prompt for ambiguity.

        Args:
            user_prompt: Original user request
            command_context: Optional context (e.g., Spec-Kit command type)

        Returns:
            {
                "needs_clarification": bool,
                "ambiguity_score": int (0-100),
                "ambiguity_points": List[Dict],  # What's ambiguous
                "suggested_questions": List[str]  # What to ask
            }
        """
        # Import patterns here to avoid circular dependency
        from .patterns import detect_all_ambiguities

        # Step 1: Detect all ambiguities using patterns
        ambiguities = detect_all_ambiguities(user_prompt, self.project_context)

        # Step 2: Filter by command context (if specified)
        if command_context and "command" in command_context:
            command_name = command_context["command"]
            if command_name in self.config["command_rules"]:
                command_rules = self.config["command_rules"][command_name]
                if not command_rules.get("enabled", True):
                    # Clarification disabled for this command
                    return {
                        "needs_clarification": False,
                        "ambiguity_score": 0,
                        "ambiguity_points": [],
                        "suggested_questions": []
                    }

                # Filter by allowed patterns
                allowed_patterns = command_rules.get("patterns", [])
                if allowed_patterns:
                    ambiguities = [
                        amb for amb in ambiguities
                        if amb["pattern"] in allowed_patterns
                    ]

        # Step 3: Calculate overall ambiguity score
        ambiguity_score = self._calculate_ambiguity_score(ambiguities)

        # Step 4: Get threshold from config
        threshold = self.config["global_settings"]["ambiguity_threshold"]

        # Step 5: Determine if clarification is needed
        needs_clarification = ambiguity_score > threshold

        return {
            "needs_clarification": needs_clarification,
            "ambiguity_score": ambiguity_score,
            "ambiguity_points": ambiguities,
            "suggested_questions": [a["suggested_question"] for a in ambiguities]
        }

    def generate_questions(
        self,
        ambiguity_analysis: Dict[str, Any],
        max_questions: int = 5
    ) -> Dict[str, Any]:
        """
        Generate AskUserQuestion configuration.

        Args:
            ambiguity_analysis: Output from detect_ambiguity()
            max_questions: Maximum number of questions (default 5)

        Returns:
            {
                "summary": str,  # Markdown summary for user
                "question_config": Dict,  # For AskUserQuestion tool
                "clarification_context": Dict  # For enrich_prompt()
            }
        """
        ambiguities = ambiguity_analysis["ambiguity_points"][:max_questions]

        # Step 1: Generate summary
        summary = self._generate_summary(ambiguities, ambiguity_analysis["ambiguity_score"])

        # Step 2: Build questions for AskUserQuestion
        questions = []
        for i, ambiguity in enumerate(ambiguities):
            question = self._build_question(ambiguity, i+1)
            questions.append(question)

        # Step 3: Build AskUserQuestion config
        question_config = {
            "questions": questions
        }

        # Step 4: Store context for enrich_prompt()
        clarification_context = {
            "ambiguity_analysis": ambiguity_analysis,
            "questions_asked": len(questions),
            "ambiguities": ambiguities
        }

        return {
            "summary": summary,
            "question_config": question_config,
            "clarification_context": clarification_context
        }

    def enrich_prompt(
        self,
        original_prompt: str,
        user_responses: Dict[str, Any],
        clarification_context: Dict[str, Any]
    ) -> str:
        """
        Merge user responses into original prompt.

        Example:
            Original: "Check the API"
            Response: {"question_1": "📦 tcm-api"}
            Enriched: "Check the API (tcm-api service in tcm-non-prod namespace)"
        """
        enriched = original_prompt

        # Extract responses
        for question_id, answer in user_responses.items():
            question_index = int(question_id.split("_")[1]) - 1

            if question_index >= len(clarification_context["ambiguities"]):
                continue

            ambiguity = clarification_context["ambiguities"][question_index]

            # Clean answer (remove emoji if present)
            clean_answer = self._clean_answer(answer)

            # Validate answer against available options
            validated_answer = self._validate_answer(clean_answer, ambiguity)

            # Append clarification to prompt
            clarification_note = f"\n\n[Clarification - {ambiguity['pattern']}]: {validated_answer}"
            enriched += clarification_note

        return enriched

    def log_clarification(
        self,
        original_prompt: str,
        enriched_prompt: str,
        ambiguity_analysis: Dict[str, Any],
        user_responses: Dict[str, Any]
    ):
        """Log clarification decision for audit trail."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "original_prompt": original_prompt,
            "enriched_prompt": enriched_prompt,
            "ambiguity_score": ambiguity_analysis["ambiguity_score"],
            "questions_asked": len(ambiguity_analysis["ambiguity_points"]),
            "patterns_detected": [a["pattern"] for a in ambiguity_analysis["ambiguity_points"]],
            "user_responses": user_responses
        }

        with open(self.clarification_log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    # ========================================================================
    # PRIVATE METHODS (Internal Logic)
    # ========================================================================

    def _load_project_context(self) -> Dict[str, Any]:
        """Load project-context.json."""
        if not os.path.exists(self.project_context_path):
            return {"sections": {}}

        with open(self.project_context_path, "r") as f:
            return json.load(f)

    def _load_config(self) -> Dict[str, Any]:
        """Load clarification_rules.json."""
        config_path = ".claude/config/clarification_rules.json"
        if not os.path.exists(config_path):
            # Return default config
            return {
                "global_settings": {
                    "enabled": True,
                    "ambiguity_threshold": 30,
                    "max_questions_per_cycle": 5
                },
                "command_rules": {},
                "question_templates": {},
                "emoji_map": {}
            }

        with open(config_path, "r") as f:
            return json.load(f)

    def _ensure_log_directory(self):
        """Ensure logs directory exists."""
        os.makedirs(os.path.dirname(self.clarification_log_path), exist_ok=True)

    def _calculate_ambiguity_score(self, ambiguities: List[Dict]) -> int:
        """Calculate overall ambiguity score (0-100)."""
        if not ambiguities:
            return 0
        # Weighted average of top 3 ambiguities
        top_weights = [a["weight"] for a in ambiguities[:3]]
        return int(sum(top_weights) / len(top_weights))

    def _generate_summary(self, ambiguities: List[Dict], ambiguity_score: int) -> str:
        """Generate human-readable summary of ambiguities."""
        lines = []
        lines.append("=" * 60)
        lines.append("🔍 CLARIFICACIÓN NECESARIA")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"**Score de ambigüedad:** {ambiguity_score}/100")
        lines.append("")
        lines.append("He detectado las siguientes ambigüedades en tu solicitud:")
        lines.append("")

        for i, amb in enumerate(ambiguities):
            lines.append(f"**{i+1}. {amb['ambiguity_reason']}**")
            options_preview = amb['available_options'][:5]
            if len(amb['available_options']) > 5:
                options_preview_str = ", ".join(options_preview) + f" (y {len(amb['available_options']) - 5} más)"
            else:
                options_preview_str = ", ".join(options_preview)
            lines.append(f"   Opciones disponibles: {options_preview_str}")
            lines.append("")

        lines.append("Por favor, responde las siguientes preguntas para continuar:")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _build_question(self, ambiguity: Dict[str, Any], question_num: int) -> Dict[str, Any]:
        """
        Build question with 3-4 rich options.

        This implements the optimized UX pattern:
        - Emoji for visual scanning
        - Short label (2-4 words)
        - Rich description with metadata
        - Max 4 options (3 specific + 1 catch-all)
        """
        # Select appropriate emoji based on pattern
        emoji_map = self.config.get("emoji_map", {})
        pattern_name = ambiguity["pattern"].split("_")[0]  # "service" from "service_ambiguity"
        emoji = emoji_map.get(pattern_name, "❓")

        # Build options with rich descriptions
        options = []
        available_options = ambiguity["available_options"][:3]  # Max 3 specific options

        for option_name in available_options:
            # Fetch metadata from project_context
            metadata = self._get_option_metadata(option_name, ambiguity)

            options.append({
                "label": f"{emoji} {option_name}",
                "description": metadata
            })

        # Add catch-all 4th option if there are more than 3 options
        if len(ambiguity["available_options"]) > 3:
            total_count = len(ambiguity["available_options"])
            resource_type = pattern_name  # "service", "namespace", "resource"

            options.append({
                "label": f"{emoji_map.get('all', '🌐')} Todos los {resource_type}s",
                "description": f"Aplicar a todos los recursos ({total_count} total)"
            })

        return {
            "question": ambiguity["suggested_question"],
            "header": f"{emoji} Pregunta {question_num}",
            "multiSelect": ambiguity.get("allow_multiple", False),
            "options": options
        }

    def _get_option_metadata(self, option_name: str, ambiguity: Dict[str, Any]) -> str:
        """
        Fetch rich metadata for option from project-context.json.

        This generates the detailed description shown under each option.
        """
        pattern = ambiguity["pattern"]

        # Service metadata
        if pattern == "service_ambiguity" and "services_metadata" in ambiguity:
            svc = ambiguity["services_metadata"].get(option_name, {})
            tech_stack = svc.get("tech_stack", "N/A")
            namespace = svc.get("namespace", "N/A")
            port = svc.get("port", "N/A")

            # Status is NOT stored in project-context (must be verified in real-time)
            # Only show static metadata
            return f"{tech_stack} | Namespace: {namespace} | Puerto: {port}"

        # Namespace metadata
        elif pattern == "namespace_ambiguity" and "namespace_metadata" in ambiguity:
            ns_meta = ambiguity["namespace_metadata"].get(option_name, {})
            services = ns_meta.get("services", [])
            service_count = ns_meta.get("service_count", 0)
            services_str = ", ".join(services[:3])
            if len(services) > 3:
                services_str += f" (y {len(services) - 3} más)"

            return f"Servicios: {services_str} | Total: {service_count} servicios"

        # Environment metadata
        elif pattern == "environment_ambiguity":
            current_env = ambiguity.get("current_environment", "unknown")
            if "Continuar" in option_name:
                return f"Proceder con el entorno actual ({current_env}). Operación segura."
            elif "Detener" in option_name:
                return f"Cancelar para configurar el proyecto correctamente primero"
            elif "Forzar" in option_name:
                return f"⚠️ Proceder en producción AUNQUE el contexto diga {current_env}. Solo si estás seguro."

        # Resource metadata
        elif pattern == "resource_ambiguity" and "resource_metadata" in ambiguity:
            res_meta = ambiguity["resource_metadata"].get(option_name, {})
            res_type = res_meta.get("type", "N/A")
            status = res_meta.get("status", "unknown")
            tier = res_meta.get("tier", "N/A")
            namespace = res_meta.get("namespace", "")

            parts = [res_type]
            if tier != "N/A":
                parts.append(f"Tier: {tier}")
            if namespace:
                parts.append(f"Namespace: {namespace}")
            parts.append(f"Estado: {status}")

            return " | ".join(parts)

        # Default fallback
        return f"Seleccionar: {option_name}"

    def _clean_answer(self, answer: str) -> str:
        """Remove emoji and extra formatting from user's answer."""
        # Remove common emoji
        emoji_list = ["📦", "🎯", "🔧", "⚠️", "🌐", "✅", "❌", "🗄️", "🔴"]
        cleaned = answer
        for emoji in emoji_list:
            cleaned = cleaned.replace(emoji, "")
        return cleaned.strip()

    def _validate_answer(self, answer: str, ambiguity: Dict[str, Any]) -> str:
        """
        Validate user's answer against available options.

        Implements fuzzy matching to handle variations like:
        - "tcm api" → "tcm-api"
        - "todos" → "Todos los servicios"
        """
        available_options = ambiguity["available_options"]

        # Exact match (after cleaning)
        if answer in available_options:
            return answer

        # Fuzzy match
        matches = get_close_matches(answer.lower(),
                                     [opt.lower() for opt in available_options],
                                     n=1, cutoff=0.6)

        if matches:
            # Find original option (with proper casing)
            for opt in available_options:
                if opt.lower() == matches[0]:
                    return opt

        # Check for "all" / "todos" keywords
        if any(keyword in answer.lower() for keyword in ["todos", "all", "ambos", "both"]):
            # User wants all options
            return f"Todos ({', '.join(available_options)})"

        # No match - return original answer
        return answer


# ============================================================================
# CONVENIENCE FUNCTIONS (Orchestrator API)
# ============================================================================

def request_clarification(
    user_prompt: str,
    command_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Main function to request clarification from user.

    This should be called by the orchestrator in Phase 0.

    Args:
        user_prompt: Original user request
        command_context: Optional context (e.g., {"command": "speckit.init"})

    Returns:
        {
            "needs_clarification": bool,
            "summary": str,  # Markdown summary (if clarification needed)
            "question_config": Dict,  # For AskUserQuestion tool
            "engine_instance": ClarificationEngine,  # For process_clarification()
            "clarification_context": Dict  # For enrich_prompt()
        }
    """
    engine = ClarificationEngine()
    ambiguity = engine.detect_ambiguity(user_prompt, command_context)

    if not ambiguity["needs_clarification"]:
        return {
            "needs_clarification": False,
            "enriched_prompt": user_prompt  # No changes
        }

    questions = engine.generate_questions(ambiguity)

    return {
        "needs_clarification": True,
        "ambiguity_score": ambiguity.get("ambiguity_score", 0),
        "ambiguity_points": ambiguity.get("ambiguity_points", []),
        "summary": questions["summary"],
        "question_config": questions["question_config"],
        "engine_instance": engine,
        "clarification_context": questions["clarification_context"],
        "original_prompt": user_prompt
    }


def process_clarification(
    engine_instance: ClarificationEngine,
    original_prompt: str,
    user_responses: Dict[str, Any],
    clarification_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Process user's clarification responses.

    Args:
        engine_instance: The ClarificationEngine instance from request_clarification
        original_prompt: Original user request
        user_responses: Response from AskUserQuestion
        clarification_context: Context from request_clarification

    Returns:
        {
            "enriched_prompt": str,  # Enriched prompt for agent_router.py
            "clarification_log": Dict  # Log entry
        }
    """
    enriched_prompt = engine_instance.enrich_prompt(
        original_prompt,
        user_responses,
        clarification_context
    )

    # Log the clarification
    engine_instance.log_clarification(
        original_prompt,
        enriched_prompt,
        clarification_context["ambiguity_analysis"],
        user_responses
    )

    return {
        "enriched_prompt": enriched_prompt,
        "clarification_log": {
            "timestamp": datetime.now().isoformat(),
            "original_prompt": original_prompt,
            "enriched_prompt": enriched_prompt
        }
    }

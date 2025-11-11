"""
User Interaction Helper

Provides a simple ask() wrapper over AskUserQuestion to enforce
consistent question format across all agent responses.

Philosophy: ALWAYS use ask() for user questions, NEVER plain text.
"""

from typing import List, Dict, Any, Optional


def ask(question: str,
        options: List[Dict[str, str]],
        header: str = "Pregunta",
        multi_select: bool = False) -> str:
    """
    Ultra-simple wrapper for AskUserQuestion.

    Forces consistent question format across orchestrator and agents.

    Args:
        question: The question to ask
        options: List of {"label": "...", "description": "..."}
        header: Short header (max 12 chars, usually emoji + text)
        multi_select: Allow multiple selections (default: False)

    Returns:
        User's selected option(s)

    Example:
        response = ask(
            "¿Proceder con implementación?",
            options=[
                {"label": "Sí", "description": "Ejecutar plan"},
                {"label": "No", "description": "Cancelar"}
            ],
            header="Confirmación"
        )

    Raises:
        ImportError: If AskUserQuestion not available
        ValueError: If options are invalid
    """
    # Validate inputs
    if not question or not isinstance(question, str):
        raise ValueError("question must be a non-empty string")

    if not options or not isinstance(options, list):
        raise ValueError("options must be a non-empty list")

    if len(options) < 2:
        raise ValueError("options must have at least 2 items")

    for opt in options:
        if not isinstance(opt, dict) or "label" not in opt:
            raise ValueError("each option must have 'label' key")

    if len(header) > 20:
        raise ValueError("header must be max 20 characters")

    # Import AskUserQuestion from Claude Code
    try:
        from claude_code_tools import AskUserQuestion
    except ImportError:
        raise ImportError(
            "AskUserQuestion not available. "
            "This function requires Claude Code environment."
        )

    # Build question config for AskUserQuestion
    question_config = {
        "question": question,
        "header": header,
        "options": options,
        "multiSelect": multi_select
    }

    try:
        # Call AskUserQuestion
        result = AskUserQuestion(questions=[question_config])

        # Extract answer
        if result and isinstance(result, dict):
            answers = result.get("answers", {})
            response = answers.get("question_1", "")
            return response

        return ""

    except Exception as e:
        raise RuntimeError(f"AskUserQuestion failed: {e}")


def ask_confirmation(message: str,
                    warning: bool = False) -> bool:
    """
    Ask user for yes/no confirmation.

    Args:
        message: Confirmation message
        warning: If True, shows warning icon (⚠️)

    Returns:
        True if user selects "Sí", False otherwise

    Example:
        if ask_confirmation("¿Estás seguro?", warning=True):
            # Proceed
            ...
    """
    header = "⚠️ Advertencia" if warning else "✓ Confirmación"

    response = ask(
        question=message,
        options=[
            {"label": "Sí", "description": "Continuar"},
            {"label": "No", "description": "Cancelar"}
        ],
        header=header,
        multi_select=False
    )

    return "Sí" in response


def ask_choice(question: str,
               choices: List[str],
               descriptions: Optional[List[str]] = None) -> str:
    """
    Ask user to select one from multiple choices.

    Args:
        question: The question
        choices: List of choice labels
        descriptions: Optional list of descriptions (same length as choices)

    Returns:
        Selected choice

    Example:
        cluster = ask_choice(
            "¿En qué cluster?",
            choices=["digital-prod", "b2b-prod", "ofertas-prod"],
            descriptions=["AWS Digital", "AWS B2B", "AWS Ofertas"]
        )
    """
    if not choices or len(choices) < 2:
        raise ValueError("choices must have at least 2 items")

    if descriptions and len(descriptions) != len(choices):
        raise ValueError("descriptions must match choices length")

    options = []
    for i, choice in enumerate(choices):
        desc = descriptions[i] if descriptions else choice
        options.append({"label": choice, "description": desc})

    response = ask(
        question=question,
        options=options,
        header="Seleccionar",
        multi_select=False
    )

    # Clean emoji prefix
    import re
    clean = re.sub(r'^[^\w\s]+\s*', '', response).strip()
    return clean


def ask_multiple(question: str,
                 options_list: List[Dict[str, str]]) -> List[str]:
    """
    Ask user to select multiple options.

    Args:
        question: The question
        options_list: List of {"label": "...", "description": "..."}

    Returns:
        List of selected options

    Example:
        selected = ask_multiple(
            "¿Qué servicios afecta?",
            options_list=[
                {"label": "tcm-api", "description": "API TCM"},
                {"label": "pg-api", "description": "API PG"},
                ...
            ]
        )
    """
    response = ask(
        question=question,
        options=options_list,
        header="Seleccionar",
        multi_select=True
    )

    # Parse multi-select response
    if response:
        # Response is comma-separated or newline-separated
        items = [item.strip() for item in response.split('\n') if item.strip()]
        # Clean emoji prefixes
        import re
        items = [re.sub(r'^[^\w\s]+\s*', '', item).strip() for item in items]
        return items

    return []

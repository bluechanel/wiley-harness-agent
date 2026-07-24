"""Assemble the system prompt from the instruction."""


def build_system_prompt(instruction: str | None = None) -> str | None:
    """Normalize the instruction into a system prompt.

    Returns None when the instruction is empty so callers can omit the field.
    Tool schemas are sent via the API ``tools`` parameter, not the prompt.
    """
    if instruction and instruction.strip():
        return instruction.strip()
    return None

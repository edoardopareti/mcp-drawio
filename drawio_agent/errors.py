class DiagramError(ValueError):
    """Raised for invalid operations; the message is returned to the LLM so it can correct itself."""

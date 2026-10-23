"""Notations (ArchiMate, UML, Azure, ...): allowed shape libraries, relationship types and agent guidance."""
from .model import Notation, RelationType
from .registry import NotationRegistry, UnknownNotationError
from .guide import NotationGuideBuilder

__all__ = ["Notation", "RelationType", "NotationRegistry", "UnknownNotationError", "NotationGuideBuilder"]

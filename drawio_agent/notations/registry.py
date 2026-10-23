"""Discovers notation definitions from the bundled folder and an optional user folder."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .model import Notation

log = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).resolve().parent / "definitions"
NONE_VALUES = {"", "none", "generic", "default"}


class UnknownNotationError(ValueError):
    pass


class NotationRegistry:
    """Notations by name. A user folder may add notations or override bundled ones (same name).

    Each notation is <name>.json (structure: libraries, relations, ...) plus an optional <name>.md
    (free-text guidance injected into the agent's system prompt). Editing the .md is the intended
    way to give the agent rules, constraints and domain knowledge for a notation.
    """

    def __init__(self, notations: dict[str, Notation]):
        self._notations = notations
        self._aliases = {alias.lower(): n.name for n in notations.values() for alias in [n.name, *n.aliases]}

    @classmethod
    def load(cls, *directories: Path | None) -> "NotationRegistry":
        found: dict[str, Notation] = {}
        for directory in (BUILTIN_DIR, *directories):
            if directory is None or not Path(directory).is_dir():
                continue
            for path in sorted(Path(directory).glob("*.json")):
                notation = cls._read(path)
                found[notation.name] = notation
        return cls(found)

    @staticmethod
    def _read(path: Path) -> Notation:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_comment", None)
        data.setdefault("name", path.stem)
        prompt_file = path.with_suffix(".md")
        if prompt_file.is_file() and not data.get("prompt"):
            data["prompt"] = prompt_file.read_text(encoding="utf-8").strip()
        return Notation.model_validate(data)

    def names(self) -> list[str]:
        return sorted(self._notations)

    def all(self) -> list[Notation]:
        return [self._notations[n] for n in self.names()]

    @staticmethod
    def is_none(name: str | None) -> bool:
        return name is None or name.strip().lower() in NONE_VALUES

    def resolve_name(self, name: str | None) -> str | None:
        """Canonical name, None for 'no notation'. Raises for unknown names."""
        if self.is_none(name):
            return None
        canonical = self._aliases.get(name.strip().lower())
        if canonical is None:
            raise UnknownNotationError(f"Unknown notation '{name}'. Available: {', '.join(self.names())} (or 'none').")
        return canonical

    def get(self, name: str | None) -> Notation | None:
        canonical = self.resolve_name(name)
        return self._notations[canonical] if canonical else None

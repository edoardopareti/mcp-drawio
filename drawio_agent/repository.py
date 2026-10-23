"""File-based persistence of diagrams: state, undo history and rendered output.

Each diagram lives in the workspace as:
  <name>.json          source of truth (model)
  <name>.history.json  undo stack
  <name>.drawio        rendered, editable draw.io file (rewritten after every change)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .errors import DiagramError
from .models import NAME_PATTERN, Diagram

HISTORY_LIMIT = 50
_NAME_RE = re.compile(NAME_PATTERN)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class DiagramRepository:
    def __init__(self, workspace: Path | str, history_limit: int = HISTORY_LIMIT):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.history_limit = history_limit

    # ---- naming ----
    @staticmethod
    def check_name(name: str) -> str:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise DiagramError(f"Invalid diagram name '{name}'. Use letters, digits, '_' or '-' (max 64 chars).")
        return name

    def state_path(self, name: str) -> Path:
        return self.workspace / f"{self.check_name(name)}.json"

    def history_path(self, name: str) -> Path:
        return self.workspace / f"{self.check_name(name)}.history.json"

    def drawio_path(self, name: str) -> Path:
        return self.workspace / f"{self.check_name(name)}.drawio"

    # ---- queries ----
    def exists(self, name: str) -> bool:
        return self.state_path(name).exists()

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.workspace.glob("*.json") if not p.name.endswith(".history.json"))

    def load(self, name: str) -> Diagram:
        path = self.state_path(name)
        if not path.exists():
            available = ", ".join(self.names()) or "none"
            raise DiagramError(f"Diagram '{name}' does not exist. Available diagrams: {available}.")
        return Diagram.model_validate_json(path.read_text(encoding="utf-8"))

    def load_or_none(self, name: str) -> Diagram | None:
        return self.load(name) if self.exists(name) else None

    def history(self, name: str) -> list[dict]:
        path = self.history_path(name)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    # ---- commands ----
    def save(self, diagram: Diagram, rendered: str, previous: Diagram | None) -> None:
        if previous is not None:
            history = self.history(diagram.name)
            history.append(previous.model_dump(mode="json"))
            atomic_write(self.history_path(diagram.name), json.dumps(history[-self.history_limit:]))
        atomic_write(self.state_path(diagram.name), diagram.model_dump_json(indent=2))
        atomic_write(self.drawio_path(diagram.name), rendered)

    def pop_history(self, name: str) -> tuple[Diagram, int]:
        history = self.history(name)
        if not history:
            raise DiagramError("Nothing to undo.")
        restored = Diagram.model_validate(history.pop())
        atomic_write(self.history_path(name), json.dumps(history))
        return restored, len(history)

    def save_without_history(self, diagram: Diagram, rendered: str) -> None:
        atomic_write(self.state_path(diagram.name), diagram.model_dump_json(indent=2))
        atomic_write(self.drawio_path(diagram.name), rendered)

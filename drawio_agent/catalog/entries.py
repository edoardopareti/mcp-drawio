"""Value objects of the shape catalogue."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ElementType = Literal["node", "container"]
LabelFormat = Literal["plain", "compartments"]
SourceKind = Literal["seed", "sidebar", "library", "harvest"]

KEY_PATTERN = r"^[a-z0-9][a-z0-9_]*\.[a-z0-9][a-z0-9_.]*$"


def slugify(text: str, max_len: int = 60) -> str:
    """'Application Component' -> 'application_component'."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (slug or "shape")[:max_len].rstrip("_")


class ShapeEntry(BaseModel):
    """One shape the agent can use. 'key' is what diagrams store; 'style' is what draw.io renders."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=KEY_PATTERN)
    name: str
    library: str
    category: str = "general"
    element_type: ElementType = "node"
    style: str
    width: float = 120
    height: float = 60
    keywords: list[str] = Field(default_factory=list)
    label_format: LabelFormat = "plain"
    source: SourceKind = "seed"

    def summary(self) -> dict:
        """Compact representation returned to the LLM (no style: it does not need it)."""
        return {"key": self.key, "name": self.name, "library": self.library, "category": self.category,
                "element_type": self.element_type}


class LibraryInfo(BaseModel):
    name: str
    shapes: int
    containers: int
    categories: list[str]
    sources: list[str]

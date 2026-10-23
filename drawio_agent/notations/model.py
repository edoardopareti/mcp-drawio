"""Declarative notation definitions, loaded from <name>.json + <name>.md files."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..models import Direction


class RelationType(BaseModel):
    """A typed connector of a notation (e.g. ArchiMate 'serving', UML 'generalization')."""
    model_config = ConfigDict(extra="forbid")

    description: str
    style: str = Field(description="draw.io edge style")
    default_label: str = ""
    # Optional constraints on endpoint shape categories (catalogue 'category'); empty = unrestricted.
    source_categories: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)


class Notation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(description="catalogue libraries whose shapes may be used")
    require_shapes: bool = True
    require_relations: bool = False
    default_relation: str | None = None
    default_direction: Direction = "left-to-right"
    default_group_shape: str | None = None
    inline_shape_limit: int = Field(default=0, description="list all shapes in the guide if there are at most this many")
    relations: dict[str, RelationType] = Field(default_factory=dict)
    prompt: str = Field(default="", description="agent guidance; loaded from <name>.md when present")

    def relation(self, name: str | None) -> RelationType | None:
        return self.relations.get(name) if name else None

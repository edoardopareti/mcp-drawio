"""Data model of a diagram. The model (not the XML) is the source of truth; XML is rendered from it."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal[
    "actor", "service", "agent", "database", "external", "process",
    "decision", "document", "queue", "storage", "note",
]
Direction = Literal["left-to-right", "top-to-bottom"]
LayoutMode = Literal["auto", "manual"]

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$"
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------- persisted model ----------------
class Node(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(default="", max_length=300)
    kind: NodeKind = "service"
    group: str | None = None
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    # Geometry is only used in manual layout mode (e.g. imported diagrams).
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    # Raw draw.io style preserved from imported files.
    style: str | None = None


class Edge(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    source: str
    target: str
    label: str = Field(default="", max_length=200)
    dashed: bool = False
    bidirectional: bool = False
    style: str | None = None


class Group(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(default="", max_length=150)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)


class Diagram(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    title: str = Field(default="", max_length=200)
    direction: Direction = "left-to-right"
    layout_mode: LayoutMode = "auto"
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)


# ---------------- tool inputs ----------------
class NodeInput(_Strict):
    id: str = Field(pattern=ID_PATTERN, description="Unique short snake_case id, e.g. 'api_gateway'")
    label: str = Field(max_length=300, description="Text shown in the shape. Use \\n for line breaks.")
    kind: NodeKind = Field(default="service", description="Shape type")
    group: str | None = Field(default=None, description="Id of the group/boundary containing the node. Created automatically if missing.")
    color: str | None = Field(default=None, pattern=COLOR_PATTERN, description="Fill colour as #RRGGBB")


class NodeUpdate(_Strict):
    id: str = Field(description="Id of the existing node to change")
    label: str | None = Field(default=None, max_length=300)
    kind: NodeKind | None = None
    group: str | None = Field(default=None, description="New group id. Use an empty string to take the node out of its group.")
    color: str | None = Field(default=None, description="#RRGGBB, or an empty string to reset to the default colour")


class EdgeInput(_Strict):
    source: str = Field(description="Id of the source node")
    target: str = Field(description="Id of the target node")
    label: str = Field(default="", max_length=200)
    dashed: bool = False
    bidirectional: bool = False
    id: str | None = Field(default=None, pattern=ID_PATTERN, description="Optional id; generated if omitted")


class EdgeUpdate(_Strict):
    id: str = Field(description="Id of the existing edge to change")
    source: str | None = None
    target: str | None = None
    label: str | None = Field(default=None, max_length=200)
    dashed: bool | None = None
    bidirectional: bool | None = None


class GroupInput(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(max_length=150)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)

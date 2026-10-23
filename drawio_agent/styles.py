"""Resolves the draw.io style, size and label format of every diagram element.

Precedence for nodes:  raw imported style  >  catalogue shape  >  generic 'kind'.
Precedence for groups: catalogue shape  >  notation default group shape  >  generic swimlane.
Precedence for edges:  raw imported style  >  notation relation (explicit or default)  >  generic arrow.
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass

from .catalog import ShapeCatalog, ShapeEntry
from .drawio_codec import has_label_below, set_style_value
from .models import Diagram, Edge, Group, Node
from .notations import Notation, NotationRegistry, UnknownNotationError

KIND_STYLES: dict[str, str] = {
    "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;",
    "service": "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "agent": "rounded=1;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;fontStyle=1;",
    "database": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;fillColor=#d5e8d4;strokeColor=#82b366;",
    "storage": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;fillColor=#f5f5f5;strokeColor=#666666;",
    "external": "rounded=1;whiteSpace=wrap;html=1;dashed=1;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;",
    "process": "rounded=1;whiteSpace=wrap;html=1;arcSize=40;fillColor=#fff2cc;strokeColor=#d6b656;",
    "decision": "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;",
    "document": "shape=document;whiteSpace=wrap;html=1;boundedLbl=1;fillColor=#ffe6cc;strokeColor=#d79b00;",
    "queue": "shape=process;whiteSpace=wrap;html=1;backgroundOutline=1;fillColor=#f8cecc;strokeColor=#b85450;",
    "note": "shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#fffbe6;strokeColor=#c9b458;align=left;spacingLeft=6;",
}
KIND_SIZES: dict[str, tuple[float, float]] = {
    "actor": (60, 80), "database": (140, 80), "storage": (140, 80), "decision": (150, 90),
    "document": (160, 70), "queue": (160, 60), "note": (180, 60),
}
DEFAULT_SIZE = (170.0, 64.0)
GROUP_STYLE = (
    "swimlane;startSize=32;html=1;rounded=1;arcSize=4;container=1;collapsible=0;"
    "fillColor=#f7f9fc;swimlaneFillColor=#fbfcfe;strokeColor=#8a9bb3;fontStyle=1;"
)
EDGE_STYLE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;endFill=1;"
TITLE_STYLE = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontSize=22;fontStyle=1;"

LABEL_BELOW_HEIGHT = 40.0   # room reserved under icons whose label is drawn below the shape
LABEL_BELOW_MIN_WIDTH = 130.0
COMPARTMENT_LINE_HEIGHT = 18.0
COMPARTMENT_SEPARATOR = "--"


@dataclass(frozen=True)
class Box:
    width: float
    height: float


class StyleResolver:
    def __init__(self, catalog: ShapeCatalog | None = None, notations: NotationRegistry | None = None):
        self.catalog = catalog or ShapeCatalog.load()
        self.notations = notations or NotationRegistry.load()

    # ---------------- lookups ----------------
    def notation_of(self, diagram: Diagram) -> Notation | None:
        try:
            return self.notations.get(diagram.notation)
        except UnknownNotationError:
            return None

    def shape_of(self, node: Node) -> ShapeEntry | None:
        return self.catalog.get(node.shape) if node.shape else None

    def group_shape_of(self, group: Group, notation: Notation | None) -> ShapeEntry | None:
        key = group.shape or (notation.default_group_shape if notation else None)
        return self.catalog.get(key) if key else None

    # ---------------- nodes ----------------
    def node_style(self, node: Node) -> str:
        entry = self.shape_of(node)
        style = node.style or (entry.style if entry else KIND_STYLES.get(node.kind, KIND_STYLES["service"]))
        if node.color:
            style = set_style_value(style, "fillColor", node.color)
        return style

    def node_label(self, node: Node) -> str:
        entry = self.shape_of(node)
        if entry is not None and entry.label_format == "compartments":
            return compartments_html(node.label)
        return plain_html(node.label)

    def node_shape_box(self, node: Node) -> Box:
        """Size of the drawn shape."""
        if node.width and node.height:
            return Box(node.width, node.height)
        entry = self.shape_of(node)
        if entry is None:
            return Box(*KIND_SIZES.get(node.kind, DEFAULT_SIZE))
        if entry.label_format == "compartments":
            return Box(entry.width, max(entry.height, compartments_height(node.label)))
        return Box(entry.width, entry.height)

    def node_layout_box(self, node: Node) -> Box:
        """Space the node needs in the layout (shape plus a label drawn below it, if any)."""
        shape = self.node_shape_box(node)
        if has_label_below(self.node_style(node)):
            return Box(max(shape.width, LABEL_BELOW_MIN_WIDTH), shape.height + LABEL_BELOW_HEIGHT)
        return shape

    # ---------------- groups ----------------
    def group_style(self, group: Group, notation: Notation | None) -> str:
        entry = self.group_shape_of(group, notation)
        style = entry.style if entry else GROUP_STYLE
        style = set_style_value(style, "container", "1")
        if group.color:
            style = set_style_value(style, "fillColor", group.color)
        return style

    # ---------------- edges ----------------
    def edge_style(self, edge: Edge, notation: Notation | None) -> str:
        relation = None
        if notation is not None:
            relation = notation.relation(edge.relation) or notation.relation(notation.default_relation)
        style = edge.style or (relation.style if relation else EDGE_STYLE)
        if edge.dashed:
            style = set_style_value(style, "dashed", "1")
        elif edge.style is None and relation is None:
            style = set_style_value(style, "dashed", None)
        if edge.bidirectional:
            style = set_style_value(set_style_value(style, "startArrow", "block"), "startFill", "1")
        return style

    def edge_label(self, edge: Edge, notation: Notation | None) -> str:
        relation = notation.relation(edge.relation) if notation else None
        return plain_html(edge.label or (relation.default_label if relation else ""))


# ---------------- label helpers ----------------
def plain_html(label: str) -> str:
    return html.escape(label).replace("\n", "<br>")


def split_compartments(label: str) -> list[list[str]]:
    sections: list[list[str]] = [[]]
    for line in label.split("\n"):
        if line.strip() == COMPARTMENT_SEPARATOR:
            sections.append([])
        else:
            sections[-1].append(line)
    return sections


def compartments_html(label: str) -> str:
    """UML-style compartments: centred bold header, then left-aligned sections separated by rules."""
    header, *rest = split_compartments(label)
    parts = ['<p style="margin:0px;margin-top:4px;text-align:center;">'
             + "<br>".join(f"<b>{html.escape(line)}</b>" if i == len(header) - 1 else html.escape(line)
                           for i, line in enumerate(header)) + "</p>"]
    for section in rest:
        parts.append('<hr size="1" style="border-style:solid;">')
        body = "<br>".join(html.escape(line) for line in section) or "&nbsp;"
        parts.append(f'<p style="margin:0px;margin-left:4px;">{body}</p>')
    return "".join(parts)


def compartments_height(label: str) -> float:
    sections = split_compartments(label)
    lines = sum(max(1, len(s)) for s in sections)
    return math.ceil(lines * COMPARTMENT_LINE_HEIGHT + 8 * len(sections) + 8)

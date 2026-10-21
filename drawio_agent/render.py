"""Render the diagram model to an uncompressed, editable draw.io (mxGraph) file."""
from __future__ import annotations

import html
from xml.etree import ElementTree as ET

from .layout import Geometry, compute_geometry
from .models import Diagram, Edge, Group, Node

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
GROUP_STYLE = (
    "swimlane;startSize=32;html=1;rounded=1;arcSize=4;container=1;collapsible=0;"
    "fillColor=#f7f9fc;swimlaneFillColor=#fbfcfe;strokeColor=#8a9bb3;fontStyle=1;"
)
EDGE_STYLE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;endFill=1;"
TITLE_STYLE = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontSize=22;fontStyle=1;"


def set_style_value(style: str, key: str, value: str | None) -> str:
    """Set, replace or (value=None) remove a key in a draw.io style string."""
    parts = [p for p in style.split(";") if p]
    parts = [p for p in parts if p.split("=", 1)[0] != key]
    if value is not None:
        parts.append(f"{key}={value}")
    return ";".join(parts) + ";"


def node_style(node: Node) -> str:
    style = node.style or KIND_STYLES.get(node.kind, KIND_STYLES["service"])
    if node.color:
        style = set_style_value(style, "fillColor", node.color)
    return style


def group_style(group: Group) -> str:
    style = GROUP_STYLE
    if group.color:
        style = set_style_value(style, "fillColor", group.color)
    return style


def edge_style(edge: Edge) -> str:
    style = edge.style or EDGE_STYLE
    style = set_style_value(style, "dashed", "1" if edge.dashed else None)
    if edge.bidirectional:
        style = set_style_value(set_style_value(style, "startArrow", "block"), "startFill", "1")
    elif edge.style is None:
        style = set_style_value(style, "startArrow", None)
    return style


def to_value(label: str) -> str:
    return html.escape(label).replace("\n", "<br>")


def render_xml(diagram: Diagram, geometry: Geometry | None = None) -> str:
    geo = geometry or compute_geometry(diagram)
    mxfile = ET.Element("mxfile", host="drawio-nl-agent", agent="drawio-nl-agent", type="device")
    page = ET.SubElement(mxfile, "diagram", id=f"page-{diagram.name}", name=diagram.title or diagram.name)
    model = ET.SubElement(
        page, "mxGraphModel", dx="1400", dy="900", grid="1", gridSize="10", guides="1", tooltips="1",
        connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth="1169", pageHeight="827",
        math="0", shadow="0",
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    def geometry_el(cell: ET.Element, x: float, y: float, w: float, h: float) -> None:
        ET.SubElement(cell, "mxGeometry", x=f"{x:.0f}", y=f"{y:.0f}", width=f"{w:.0f}", height=f"{h:.0f}", **{"as": "geometry"})

    if diagram.title:
        title = ET.SubElement(root, "mxCell", id="diagram-title", value=to_value(diagram.title), style=TITLE_STYLE, vertex="1", parent="1")
        geometry_el(title, 40, 20, 900, 40)

    for g in diagram.groups:
        x, y, w, h = geo.groups[g.id]
        cell = ET.SubElement(root, "mxCell", id=g.id, value=to_value(g.label), style=group_style(g), vertex="1", parent="1")
        geometry_el(cell, x, y, w, h)

    group_ids = {g.id for g in diagram.groups}
    for n in diagram.nodes:
        x, y, w, h = geo.nodes[n.id]
        parent = "1"
        if n.group in group_ids:
            parent = n.group
            gx, gy, _, _ = geo.groups[n.group]
            x, y = x - gx, y - gy  # children use coordinates relative to their container
        cell = ET.SubElement(root, "mxCell", id=n.id, value=to_value(n.label), style=node_style(n), vertex="1", parent=parent)
        geometry_el(cell, x, y, w, h)

    for e in diagram.edges:
        cell = ET.SubElement(
            root, "mxCell", id=e.id, value=to_value(e.label), style=edge_style(e),
            edge="1", parent="1", source=e.source, target=e.target,
        )
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})

    ET.indent(mxfile)
    return ET.tostring(mxfile, encoding="unicode")

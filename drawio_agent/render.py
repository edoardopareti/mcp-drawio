"""Render the diagram model to an uncompressed, editable draw.io (mxGraph) file."""
from __future__ import annotations

from xml.etree import ElementTree as ET

from .layout import Geometry, LayoutEngine
from .models import Diagram
from .styles import TITLE_STYLE, StyleResolver, plain_html


class DrawioRenderer:
    def __init__(self, resolver: StyleResolver | None = None, layout: LayoutEngine | None = None):
        self.resolver = resolver or StyleResolver()
        self.layout = layout or LayoutEngine(self.resolver)

    def render(self, diagram: Diagram, geometry: Geometry | None = None) -> str:
        geo = geometry or self.layout.compute(diagram)
        notation = self.resolver.notation_of(diagram)
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

        if diagram.title:
            title = ET.SubElement(root, "mxCell", id="diagram-title", value=plain_html(diagram.title),
                                  style=TITLE_STYLE, vertex="1", parent="1")
            self._geometry(title, 40, 20, 900, 40)

        groups = diagram.group_map()

        def container_of(parent_id: str | None) -> tuple[str, float, float]:
            """mxGraph parent id and its absolute origin (children use relative coordinates)."""
            if parent_id in groups and parent_id in geo.groups:
                gx, gy, _, _ = geo.groups[parent_id]
                return parent_id, gx, gy
            return "1", 0.0, 0.0

        for g in self._parents_first(diagram):
            x, y, w, h = geo.groups[g.id]
            parent, px, py = container_of(g.parent)
            cell = ET.SubElement(root, "mxCell", id=g.id, value=plain_html(g.label),
                                 style=self.resolver.group_style(g, notation), vertex="1", parent=parent)
            self._geometry(cell, x - px, y - py, w, h)

        for n in diagram.nodes:
            x, y, w, h = geo.nodes[n.id]
            parent, px, py = container_of(n.group)
            cell = ET.SubElement(root, "mxCell", id=n.id, value=self.resolver.node_label(n),
                                 style=self.resolver.node_style(n), vertex="1", parent=parent)
            self._geometry(cell, x - px, y - py, w, h)

        for e in diagram.edges:
            cell = ET.SubElement(
                root, "mxCell", id=e.id, value=self.resolver.edge_label(e, notation),
                style=self.resolver.edge_style(e, notation), edge="1", parent="1", source=e.source, target=e.target,
            )
            ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})

        ET.indent(mxfile)
        return ET.tostring(mxfile, encoding="unicode")

    @staticmethod
    def _geometry(cell: ET.Element, x: float, y: float, w: float, h: float) -> None:
        ET.SubElement(cell, "mxGeometry", x=f"{x:.0f}", y=f"{y:.0f}", width=f"{w:.0f}", height=f"{h:.0f}",
                      **{"as": "geometry"})

    @staticmethod
    def _parents_first(diagram: Diagram):
        ids = {g.id for g in diagram.groups}
        emitted: set[str] = set()
        pending = list(diagram.groups)
        while pending:
            progressed = False
            for g in list(pending):
                if g.parent not in ids or g.parent in emitted or g.parent == g.id:
                    emitted.add(g.id)
                    pending.remove(g)
                    progressed = True
                    yield g
            if not progressed:  # cycle (should be prevented by validation): emit the rest flat
                yield from pending
                return


def render_xml(diagram: Diagram, geometry: Geometry | None = None, resolver: StyleResolver | None = None) -> str:
    """Backward-compatible functional entry point."""
    return DrawioRenderer(resolver).render(diagram, geometry)

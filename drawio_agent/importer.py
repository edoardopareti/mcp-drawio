"""Import existing .drawio files (compressed or uncompressed) into the diagram model.

Only the first page is imported. Containers become (possibly nested) groups. When a shape catalogue
is given, imported styles are matched to catalogue keys so the agent can reason about them
(e.g. an imported ArchiMate application component gets shape='archimate3.application_component').
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .catalog import ShapeCatalog
from .drawio_codec import first_page_model, is_container_style, parse_style, strip_html
from .models import Diagram, Edge, Group, Node

_ID_BAD = re.compile(r"[^A-Za-z0-9_.:-]")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")

# Kept for backward compatibility with v3 imports.
load_graph_model = first_page_model


def infer_kind(style: str) -> str:
    s = style.lower()
    if "umlactor" in s:
        return "actor"
    if "cylinder" in s or "datastore" in s or "shape=database" in s:
        return "database"
    if s.startswith("rhombus") or ";rhombus" in s:
        return "decision"
    if "shape=document" in s:
        return "document"
    if "shape=note" in s:
        return "note"
    if "queue" in s or "shape=process" in s:
        return "queue"
    if s.startswith("text;"):
        return "note"
    if "dashed=1" in s:
        return "external"
    return "service"


@dataclass
class _Cell:
    label: str
    style: str
    parent: str | None
    vertex: bool
    edge: bool
    source: str | None
    target: str | None
    geom: tuple[float, float, float, float] | None


class DrawioImporter:
    def __init__(self, catalog: ShapeCatalog | None = None):
        self.catalog = catalog

    def load(self, path: Path, name: str) -> tuple[Diagram, dict]:
        cells = self._read_cells(first_page_model(path))
        layers = {cid for cid, c in cells.items() if not c.vertex and not c.edge}
        vertex_parents = {c.parent for c in cells.values() if c.vertex and c.parent not in layers}
        group_ids = {cid for cid, c in cells.items()
                     if c.vertex and (cid in vertex_parents or is_container_style(c.style))}

        ids = _IdMapper()
        title = ""
        group_cells: list[tuple[str, Group]] = []
        for cid, c in cells.items():
            if c.vertex and cid == "diagram-title":
                title = c.label
            elif cid in group_ids:
                fill = parse_style(c.style)[1].get("fillColor", "")
                group_cells.append((cid, Group(id=ids.map(cid), label=c.label[:150],
                                               color=fill if _COLOR.fullmatch(fill) else None,
                                               shape=self._match(c.style, "container"))))
        for cid, group in group_cells:  # parents resolved once every group id is mapped
            parent = self._container(cells, cid, layers, group_ids)
            group.parent = ids.get(parent) if parent else None
        groups = [g for _, g in group_cells]

        nodes: list[Node] = []
        matched = 0
        for cid, c in cells.items():
            if not c.vertex or cid == "diagram-title" or cid in group_ids:
                continue
            ax, ay = self._absolute(cells, cid, layers)
            w, h = (c.geom[2], c.geom[3]) if c.geom else (None, None)
            parent = self._container(cells, cid, layers, group_ids)
            shape = self._match(c.style, "node")
            matched += shape is not None
            nodes.append(Node(
                id=ids.map(cid), label=c.label[:1000], kind=infer_kind(c.style), shape=shape,
                group=ids.get(parent) if parent else None,
                x=ax, y=ay, width=w or None, height=h or None, style=c.style or None,
            ))

        endpoints = {n.id for n in nodes} | {g.id for g in groups}
        edges: list[Edge] = []
        skipped = 0
        for cid, c in cells.items():
            if not c.edge:
                continue
            s, t = ids.get(c.source or ""), ids.get(c.target or "")
            if s not in endpoints or t not in endpoints:
                skipped += 1
                continue
            edges.append(Edge(id=ids.map(cid), source=s, target=t, label=c.label[:200],
                              dashed="dashed=1" in c.style, style=c.style or None))

        diagram = Diagram(name=name, title=title[:200], layout_mode="manual", nodes=nodes, edges=edges, groups=groups)
        return diagram, {"skipped_edges": skipped, "matched_shapes": matched}

    # ---- helpers ----
    def _match(self, style: str, element_type: str) -> str | None:
        if self.catalog is None or not style:
            return None
        entry = self.catalog.match_style(style)
        return entry.key if entry is not None and entry.element_type == element_type else None

    @staticmethod
    def _read_cells(model: ET.Element) -> dict[str, _Cell]:
        root = model.find("root")
        if root is None:
            raise ValueError("Missing <root> in graph model")
        cells: dict[str, _Cell] = {}
        for el in root:
            if el.tag in ("UserObject", "object"):
                cell = el.find("mxCell")
                if cell is None:
                    continue
                label = el.get("label", "")
            elif el.tag == "mxCell":
                cell, label = el, el.get("value", "")
            else:
                continue
            cid = el.get("id")
            if not cid:
                continue
            g = cell.find("mxGeometry")
            geom = None
            if g is not None and g.get("relative") != "1":
                geom = tuple(float(g.get(k, "0") or 0) for k in ("x", "y", "width", "height"))
            cells[cid] = _Cell(strip_html(label or ""), cell.get("style", "") or "", cell.get("parent"),
                               cell.get("vertex") == "1", cell.get("edge") == "1",
                               cell.get("source"), cell.get("target"), geom)
        return cells

    @staticmethod
    def _absolute(cells: dict[str, _Cell], cid: str, layers: set[str]) -> tuple[float, float]:
        x = y = 0.0
        cur: str | None = cid
        while cur in cells and cur not in layers:
            if cells[cur].geom:
                x += cells[cur].geom[0]
                y += cells[cur].geom[1]
            cur = cells[cur].parent
        return x, y

    @staticmethod
    def _container(cells: dict[str, _Cell], cid: str, layers: set[str], group_ids: set[str]) -> str | None:
        parent = cells[cid].parent
        while parent in cells and parent not in layers:
            if parent in group_ids:
                return parent
            parent = cells[parent].parent
        return None


class _IdMapper:
    """Maps arbitrary draw.io cell ids to valid, unique model ids."""

    def __init__(self):
        self._used: set[str] = set()
        self._map: dict[str, str] = {}

    def get(self, old: str) -> str | None:
        return self._map.get(old)

    def map(self, old: str) -> str:
        base = _ID_BAD.sub("_", old).strip("_.:-") or "cell"
        if not base[0].isalnum():
            base = "c" + base
        base = base[:70]
        cand, k = base, 2
        while cand in self._used or cand in {"0", "1", "diagram-title"}:
            cand, k = f"{base}_{k}", k + 1
        self._used.add(cand)
        self._map[old] = cand
        return cand


def import_drawio(path: Path, name: str, catalog: ShapeCatalog | None = None) -> tuple[Diagram, dict]:
    """Backward-compatible functional entry point."""
    return DrawioImporter(catalog).load(path, name)

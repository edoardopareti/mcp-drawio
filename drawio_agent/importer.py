"""Import existing .drawio files (compressed or uncompressed) into the diagram model."""
from __future__ import annotations

import base64
import html
import re
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .models import Diagram, Edge, Group, Node

_ID_BAD = re.compile(r"[^A-Za-z0-9_.:-]")


def _decode_model(diagram_el: ET.Element) -> ET.Element:
    model = diagram_el.find("mxGraphModel")
    if model is not None:
        return model
    text = (diagram_el.text or "").strip()
    if not text:
        raise ValueError("The diagram page is empty")
    raw = zlib.decompress(base64.b64decode(text), -15).decode("utf-8")
    return ET.fromstring(unquote(raw))


def load_graph_model(path: Path) -> ET.Element:
    root = ET.parse(path).getroot()
    if root.tag == "mxGraphModel":
        return root
    if root.tag == "mxfile":
        page = root.find("diagram")
        if page is None:
            raise ValueError("No <diagram> page found in the file")
        return _decode_model(page)
    raise ValueError(f"Unsupported root element <{root.tag}>")


def strip_html(value: str) -> str:
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(div|p)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


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


def _style_map(style: str) -> dict[str, str]:
    out = {}
    for part in style.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def import_drawio(path: Path, name: str) -> tuple[Diagram, dict]:
    model = load_graph_model(path)
    root = model.find("root")
    if root is None:
        raise ValueError("Missing <root> in graph model")

    cells: dict[str, dict] = {}
    for el in root:
        if el.tag in ("UserObject", "object"):
            inner = el.find("mxCell")
            if inner is None:
                continue
            cid, label, cell = el.get("id"), el.get("label", ""), inner
        elif el.tag == "mxCell":
            cid, label, cell = el.get("id"), cell_value(el), el
        else:
            continue
        if not cid:
            continue
        g = cell.find("mxGeometry")
        geom = None
        if g is not None and g.get("relative") != "1":
            geom = tuple(float(g.get(k, "0") or 0) for k in ("x", "y", "width", "height"))
        cells[cid] = {
            "label": strip_html(label or ""), "style": cell.get("style", "") or "",
            "parent": cell.get("parent"), "vertex": cell.get("vertex") == "1", "edge": cell.get("edge") == "1",
            "source": cell.get("source"), "target": cell.get("target"), "geom": geom,
        }

    layers = {cid for cid, c in cells.items() if not c["vertex"] and not c["edge"]}
    vertex_parents = {c["parent"] for c in cells.values() if c["vertex"] and c["parent"] not in layers}
    group_ids = {
        cid for cid, c in cells.items()
        if c["vertex"] and (cid in vertex_parents or "swimlane" in c["style"] or "container=1" in c["style"] or c["style"].startswith("group"))
    }

    def absolute(cid: str) -> tuple[float, float]:
        x = y = 0.0
        cur = cid
        while cur in cells and cur not in layers:
            geom = cells[cur]["geom"]
            if geom:
                x += geom[0]
                y += geom[1]
            cur = cells[cur]["parent"]
        return x, y

    used: set[str] = set()
    id_map: dict[str, str] = {}

    def new_id(old: str) -> str:
        base = _ID_BAD.sub("_", old).strip("_.:-") or "cell"
        if not base[0].isalnum():
            base = "c" + base
        base = base[:70]
        cand, k = base, 2
        while cand in used:
            cand, k = f"{base}_{k}", k + 1
        used.add(cand)
        id_map[old] = cand
        return cand

    title = ""
    groups: list[Group] = []
    nodes: list[Node] = []
    for cid, c in cells.items():
        if not c["vertex"]:
            continue
        if cid == "diagram-title":
            title = c["label"]
            continue
        if cid in group_ids:
            fill = _style_map(c["style"]).get("fillColor", "")
            groups.append(Group(id=new_id(cid), label=c["label"][:150],
                                color=fill if re.fullmatch(r"#[0-9A-Fa-f]{6}", fill) else None))

    for cid, c in cells.items():
        if not c["vertex"] or cid == "diagram-title" or cid in group_ids:
            continue
        ax, ay = absolute(cid)
        w, h = (c["geom"][2], c["geom"][3]) if c["geom"] else (None, None)
        parent, group = c["parent"], None
        while parent in cells and parent not in layers:  # nearest container ancestor
            if parent in group_ids:
                group = id_map[parent]
                break
            parent = cells[parent]["parent"]
        nodes.append(Node(
            id=new_id(cid), label=c["label"][:300], kind=infer_kind(c["style"]), group=group,
            x=ax, y=ay, width=w or None, height=h or None, style=c["style"] or None,
        ))

    node_ids = {n.id for n in nodes}
    edges: list[Edge] = []
    skipped = 0
    for cid, c in cells.items():
        if not c["edge"]:
            continue
        s, t = id_map.get(c["source"] or ""), id_map.get(c["target"] or "")
        if s not in node_ids or t not in node_ids:
            skipped += 1
            continue
        edges.append(Edge(id=new_id(cid), source=s, target=t, label=c["label"][:200],
                          dashed="dashed=1" in c["style"], style=c["style"] or None))

    diagram = Diagram(name=name, title=title[:200], layout_mode="manual", nodes=nodes, edges=edges, groups=groups)
    return diagram, {"skipped_edges": skipped}


def cell_value(el: ET.Element) -> str:
    return el.get("value", "") or ""

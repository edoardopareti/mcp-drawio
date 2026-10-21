"""Deterministic layout: layered (Sugiyama-style) placement with groups laid out as blocks."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import Diagram, Node

Box = tuple[float, float, float, float]  # x, y, width, height (absolute)

NODE_SIZES: dict[str, tuple[float, float]] = {
    "actor": (60, 80),
    "database": (140, 80),
    "storage": (140, 80),
    "decision": (150, 90),
    "document": (160, 70),
    "queue": (160, 60),
    "note": (180, 60),
}
DEFAULT_SIZE = (170, 64)

MARGIN = 40
TITLE_HEIGHT = 60
GROUP_PAD = 30
GROUP_HEADER = 32
NODE_MAIN_GAP = 90   # gap between layers (along the flow direction)
NODE_CROSS_GAP = 40  # gap between siblings within a layer
UNIT_MAIN_GAP = 110
UNIT_CROSS_GAP = 60
EMPTY_GROUP_SIZE = (220, 120)


@dataclass
class Geometry:
    nodes: dict[str, Box] = field(default_factory=dict)
    groups: dict[str, Box] = field(default_factory=dict)


def node_size(node: Node, use_stored: bool) -> tuple[float, float]:
    if use_stored and node.width and node.height:
        return node.width, node.height
    return NODE_SIZES.get(node.kind, DEFAULT_SIZE)


def layered(
    items: dict[str, tuple[float, float]],
    edges: list[tuple[str, str]],
    horizontal: bool,
    main_gap: float,
    cross_gap: float,
) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    """Place items in layers following edge direction. Returns top-left positions and total size."""
    ids = list(items)
    if not ids:
        return {}, (0.0, 0.0)

    adj: dict[str, list[str]] = {i: [] for i in ids}
    for s, t in edges:
        if s != t and s in adj and t in adj and t not in adj[s]:
            adj[s].append(t)

    # Break cycles: drop DFS back edges.
    state: dict[str, int] = {}
    dag: dict[str, list[str]] = {i: [] for i in ids}

    def dfs(u: str) -> None:
        state[u] = 1
        for v in adj[u]:
            if state.get(v) == 1:
                continue
            dag[u].append(v)
            if not state.get(v):
                dfs(v)
        state[u] = 2

    for i in ids:
        if not state.get(i):
            dfs(i)

    # Longest-path ranking.
    indeg = {i: 0 for i in ids}
    for u in ids:
        for v in dag[u]:
            indeg[v] += 1
    rank = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    while queue:
        u = queue.pop(0)
        for v in dag[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)

    layers: dict[int, list[str]] = defaultdict(list)
    for i in ids:
        layers[rank[i]].append(i)
    ranks = sorted(layers)

    preds: dict[str, list[str]] = defaultdict(list)
    for u in ids:
        for v in dag[u]:
            preds[v].append(u)

    # Barycenter ordering to reduce crossings.
    def index_map() -> dict[str, float]:
        return {n: float(k) for r in ranks for k, n in enumerate(layers[r])}

    for _ in range(4):
        pos = index_map()
        for r in ranks[1:]:
            layers[r].sort(key=lambda n: (sum(pos[p] for p in preds[n]) / len(preds[n])) if preds[n] else pos[n])
            pos.update({n: float(k) for k, n in enumerate(layers[r])})
        for r in reversed(ranks[:-1]):
            layers[r].sort(key=lambda n: (sum(pos[s] for s in dag[n]) / len(dag[n])) if dag[n] else pos[n])
            pos.update({n: float(k) for k, n in enumerate(layers[r])})

    def main(i: str) -> float:
        w, h = items[i]
        return w if horizontal else h

    def cross(i: str) -> float:
        w, h = items[i]
        return h if horizontal else w

    layer_main = {r: max(main(i) for i in layers[r]) for r in ranks}
    layer_cross = {r: sum(cross(i) for i in layers[r]) + cross_gap * (len(layers[r]) - 1) for r in ranks}
    max_cross = max(layer_cross.values())

    positions: dict[str, tuple[float, float]] = {}
    main_offset = 0.0
    for r in ranks:
        c = (max_cross - layer_cross[r]) / 2
        for i in layers[r]:
            m = main_offset + (layer_main[r] - main(i)) / 2
            positions[i] = (m, c) if horizontal else (c, m)
            c += cross(i) + cross_gap
        main_offset += layer_main[r] + main_gap
    total_main = main_offset - main_gap
    size = (total_main, max_cross) if horizontal else (max_cross, total_main)
    return positions, size


def _origin(diagram: Diagram) -> tuple[float, float]:
    return MARGIN, MARGIN + (TITLE_HEIGHT if diagram.title else 0)


def auto_geometry(diagram: Diagram) -> Geometry:
    horizontal = diagram.direction == "left-to-right"
    ox, oy = _origin(diagram)
    group_ids = [g.id for g in diagram.groups]
    members: dict[str, list[Node]] = {g: [] for g in group_ids}
    unit_of: dict[str, str] = {}
    for n in diagram.nodes:
        if n.group in members:
            members[n.group].append(n)
            unit_of[n.id] = n.group
        else:
            unit_of[n.id] = n.id

    edge_pairs = [(e.source, e.target) for e in diagram.edges]

    # 1) Lay out each group's members internally.
    internal: dict[str, dict[str, tuple[float, float]]] = {}
    unit_sizes: dict[str, tuple[float, float]] = {}
    for gid in group_ids:
        mem = members[gid]
        if not mem:
            unit_sizes[gid] = EMPTY_GROUP_SIZE
            internal[gid] = {}
            continue
        sizes = {n.id: node_size(n, False) for n in mem}
        inner_edges = [(s, t) for s, t in edge_pairs if unit_of.get(s) == gid and unit_of.get(t) == gid]
        pos, (w, h) = layered(sizes, inner_edges, horizontal, NODE_MAIN_GAP, NODE_CROSS_GAP)
        internal[gid] = pos
        unit_sizes[gid] = (w + 2 * GROUP_PAD, h + GROUP_HEADER + 2 * GROUP_PAD)

    # 2) Ungrouped nodes are units of their own; order units by first appearance.
    ordered_units: list[str] = []
    for n in diagram.nodes:
        u = unit_of[n.id]
        if u not in ordered_units:
            ordered_units.append(u)
        if u == n.id:
            unit_sizes[u] = node_size(n, False)
    for gid in group_ids:
        if gid not in ordered_units:
            ordered_units.append(gid)

    meta_edges = []
    for s, t in edge_pairs:
        us, ut = unit_of.get(s), unit_of.get(t)
        if us and ut and us != ut:
            meta_edges.append((us, ut))

    outer, _ = layered({u: unit_sizes[u] for u in ordered_units}, meta_edges, horizontal, UNIT_MAIN_GAP, UNIT_CROSS_GAP)

    geo = Geometry()
    nodes_by_id = {n.id: n for n in diagram.nodes}
    for u, (ux, uy) in outer.items():
        ax, ay = ox + ux, oy + uy
        if u in members:
            w, h = unit_sizes[u]
            geo.groups[u] = (ax, ay, w, h)
            for nid, (ix, iy) in internal[u].items():
                nw, nh = node_size(nodes_by_id[nid], False)
                geo.nodes[nid] = (ax + GROUP_PAD + ix, ay + GROUP_HEADER + GROUP_PAD + iy, nw, nh)
        else:
            nw, nh = unit_sizes[u]
            geo.nodes[u] = (ax, ay, nw, nh)
    return geo


def manual_geometry(diagram: Diagram) -> Geometry:
    """Use stored coordinates; place nodes without coordinates below the existing content."""
    geo = Geometry()
    ox, oy = _origin(diagram)
    placed = [n for n in diagram.nodes if n.x is not None and n.y is not None]
    bottom = max((n.y + node_size(n, True)[1] for n in placed), default=oy)
    cursor_x, row_y = ox, bottom + 80
    for n in diagram.nodes:
        w, h = node_size(n, True)
        if n.x is not None and n.y is not None:
            geo.nodes[n.id] = (n.x, n.y, w, h)
        else:
            geo.nodes[n.id] = (cursor_x, row_y, w, h)
            cursor_x += w + NODE_CROSS_GAP
    _groups_from_members(diagram, geo, ox, row_y + 120)
    return geo


def _groups_from_members(diagram: Diagram, geo: Geometry, empty_x: float, empty_y: float) -> None:
    for g in diagram.groups:
        boxes = [geo.nodes[n.id] for n in diagram.nodes if n.group == g.id]
        if not boxes:
            geo.groups[g.id] = (empty_x, empty_y, *EMPTY_GROUP_SIZE)
            empty_x += EMPTY_GROUP_SIZE[0] + UNIT_CROSS_GAP
            continue
        x0 = min(b[0] for b in boxes) - GROUP_PAD
        y0 = min(b[1] for b in boxes) - GROUP_PAD - GROUP_HEADER
        x1 = max(b[0] + b[2] for b in boxes) + GROUP_PAD
        y1 = max(b[1] + b[3] for b in boxes) + GROUP_PAD
        geo.groups[g.id] = (x0, y0, x1 - x0, y1 - y0)


def compute_geometry(diagram: Diagram) -> Geometry:
    return auto_geometry(diagram) if diagram.layout_mode == "auto" else manual_geometry(diagram)

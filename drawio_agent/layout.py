"""Deterministic layout: layered (Sugiyama-style) placement, with (nested) groups laid out as blocks.

The engine only needs sizes, which it gets from a StyleResolver, so it works for any shape.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import Diagram
from .styles import StyleResolver

Rect = tuple[float, float, float, float]  # x, y, width, height (absolute)

MARGIN = 40
TITLE_HEIGHT = 60
GROUP_PAD = 30
GROUP_HEADER = 32
NODE_MAIN_GAP = 90   # gap between layers (along the flow direction)
NODE_CROSS_GAP = 40  # gap between siblings within a layer
UNIT_MAIN_GAP = 110
UNIT_CROSS_GAP = 60
EMPTY_GROUP_SIZE = (220.0, 120.0)


@dataclass
class Geometry:
    """Absolute rectangles of the drawn shapes (nodes) and containers (groups)."""
    nodes: dict[str, Rect] = field(default_factory=dict)
    groups: dict[str, Rect] = field(default_factory=dict)


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

    # Break cycles: drop DFS back edges (iterative, safe for large diagrams).
    state: dict[str, int] = {}
    dag: dict[str, list[str]] = {i: [] for i in ids}
    for root in ids:
        if state.get(root):
            continue
        stack = [(root, iter(adj[root]))]
        state[root] = 1
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            if v is None:
                state[u] = 2
                stack.pop()
                continue
            if state.get(v) == 1:
                continue
            dag[u].append(v)
            if not state.get(v):
                state[v] = 1
                stack.append((v, iter(adj[v])))

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
    pos = {n: float(k) for r in ranks for k, n in enumerate(layers[r])}
    for _ in range(4):
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


class _Containment:
    """Answers 'which direct child of container C contains element X' for nodes and nested groups."""

    def __init__(self, diagram: Diagram):
        self.group_ids = {g.id for g in diagram.groups}
        self.parent: dict[str, str | None] = {}
        for g in diagram.groups:
            self.parent[g.id] = g.parent if g.parent in self.group_ids and g.parent != g.id else None
        for n in diagram.nodes:
            self.parent[n.id] = n.group if n.group in self.group_ids else None
        # Defensive: break containment cycles (validation should prevent them).
        for gid in self.group_ids:
            seen, cur = {gid}, self.parent[gid]
            while cur is not None:
                if cur in seen:
                    self.parent[gid] = None
                    break
                seen.add(cur)
                cur = self.parent[cur]

    def children(self, container: str | None, order: list[str]) -> list[str]:
        return [x for x in order if self.parent.get(x, "__none__") == container]

    def unit_in(self, element: str, container: str | None) -> str | None:
        cur: str | None = element
        while cur is not None:
            if self.parent.get(cur) == container:
                return cur
            cur = self.parent.get(cur)
        return None


class LayoutEngine:
    def __init__(self, resolver: StyleResolver | None = None):
        self.resolver = resolver or StyleResolver()

    def compute(self, diagram: Diagram) -> Geometry:
        return self._auto(diagram) if diagram.layout_mode == "auto" else self._manual(diagram)

    @staticmethod
    def _origin(diagram: Diagram) -> tuple[float, float]:
        return MARGIN, MARGIN + (TITLE_HEIGHT if diagram.title else 0)

    # ---------------- automatic ----------------
    def _auto(self, diagram: Diagram) -> Geometry:
        horizontal = diagram.direction == "left-to-right"
        tree = _Containment(diagram)
        nodes = diagram.node_map()
        order = [n.id for n in diagram.nodes] + [g.id for g in diagram.groups]
        edge_pairs = [(e.source, e.target) for e in diagram.edges]
        layout_box = {n.id: self.resolver.node_layout_box(n) for n in diagram.nodes}

        sizes: dict[str, tuple[float, float]] = {}
        local: dict[str, dict[str, tuple[float, float]]] = {}  # container -> child -> offset

        def layout_container(container: str | None) -> tuple[float, float]:
            children = tree.children(container, order)
            for child in children:
                if child in tree.group_ids:
                    w, h = layout_container(child)
                    sizes[child] = (w + 2 * GROUP_PAD, h + GROUP_HEADER + 2 * GROUP_PAD) if w else EMPTY_GROUP_SIZE
                else:
                    box = layout_box[child]
                    sizes[child] = (box.width, box.height)
            meta = []
            for s, t in edge_pairs:
                us, ut = tree.unit_in(s, container), tree.unit_in(t, container)
                if us and ut and us != ut:
                    meta.append((us, ut))
            nested = container is not None
            positions, size = layered({c: sizes[c] for c in children}, meta, horizontal,
                                      NODE_MAIN_GAP if nested else UNIT_MAIN_GAP,
                                      NODE_CROSS_GAP if nested else UNIT_CROSS_GAP)
            local[container or ""] = positions
            return size

        layout_container(None)

        geo = Geometry()
        ox, oy = self._origin(diagram)

        def place(container: str | None, ax: float, ay: float) -> None:
            for child, (cx, cy) in local[container or ""].items():
                x, y = ax + cx, ay + cy
                if child in tree.group_ids:
                    geo.groups[child] = (x, y, *sizes[child])
                    place(child, x + GROUP_PAD, y + GROUP_HEADER + GROUP_PAD)
                else:
                    shape = self.resolver.node_shape_box(nodes[child])
                    lw = sizes[child][0]
                    geo.nodes[child] = (x + (lw - shape.width) / 2, y, shape.width, shape.height)

        place(None, ox, oy)
        return geo

    # ---------------- manual ----------------
    def _manual(self, diagram: Diagram) -> Geometry:
        """Use stored coordinates; place nodes without coordinates below the existing content."""
        geo = Geometry()
        ox, oy = self._origin(diagram)
        boxes = {n.id: self.resolver.node_shape_box(n) for n in diagram.nodes}
        placed = [n for n in diagram.nodes if n.x is not None and n.y is not None]
        bottom = max((n.y + boxes[n.id].height for n in placed), default=oy)
        cursor_x, row_y = ox, bottom + 80
        for n in diagram.nodes:
            box = boxes[n.id]
            if n.x is not None and n.y is not None:
                geo.nodes[n.id] = (n.x, n.y, box.width, box.height)
            else:
                geo.nodes[n.id] = (cursor_x, row_y, box.width, box.height)
                cursor_x += self.resolver.node_layout_box(n).width + NODE_CROSS_GAP
        self._groups_from_members(diagram, geo, ox, row_y + 160)
        return geo

    @staticmethod
    def _groups_from_members(diagram: Diagram, geo: Geometry, empty_x: float, empty_y: float) -> None:
        tree = _Containment(diagram)
        depth = {}
        for g in diagram.groups:
            d, cur = 0, tree.parent[g.id]
            while cur is not None:
                d, cur = d + 1, tree.parent[cur]
            depth[g.id] = d
        for g in sorted(diagram.groups, key=lambda g: -depth[g.id]):  # innermost first
            members = [geo.nodes[n.id] for n in diagram.nodes if tree.parent[n.id] == g.id]
            members += [geo.groups[c.id] for c in diagram.groups if tree.parent[c.id] == g.id and c.id in geo.groups]
            if not members:
                geo.groups[g.id] = (empty_x, empty_y, *EMPTY_GROUP_SIZE)
                empty_x += EMPTY_GROUP_SIZE[0] + UNIT_CROSS_GAP
                continue
            x0 = min(b[0] for b in members) - GROUP_PAD
            y0 = min(b[1] for b in members) - GROUP_PAD - GROUP_HEADER
            x1 = max(b[0] + b[2] for b in members) + GROUP_PAD
            y1 = max(b[1] + b[3] for b in members) + GROUP_PAD
            geo.groups[g.id] = (x0, y0, x1 - x0, y1 - y0)


def compute_geometry(diagram: Diagram, resolver: StyleResolver | None = None) -> Geometry:
    """Backward-compatible functional entry point."""
    return LayoutEngine(resolver).compute(diagram)

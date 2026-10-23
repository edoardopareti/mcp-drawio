"""Diagram validation: structural integrity for the whole diagram, notation rules for changed elements.

Notation rules are enforced only on elements touched by the current operation. This keeps legacy or
imported content editable after a notation is applied; remaining violations are reported as
'notation_issues' by get_diagram so the agent can fix them on request.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from pydantic import ValidationError

from .catalog import ShapeCatalog
from .errors import DiagramError
from .models import Diagram, Edge, Group, Node
from .notations import Notation, NotationRegistry, UnknownNotationError

RESERVED_IDS = {"0", "1", "diagram-title"}


@dataclass
class Touched:
    nodes: set[str] = field(default_factory=set)
    edges: set[str] = field(default_factory=set)
    groups: set[str] = field(default_factory=set)

    @classmethod
    def everything(cls, d: Diagram) -> "Touched":
        return cls({n.id for n in d.nodes}, {e.id for e in d.edges}, {g.id for g in d.groups})


class DiagramValidator:
    def __init__(self, catalog: ShapeCatalog, notations: NotationRegistry):
        self.catalog, self.notations = catalog, notations

    # ---------------- public API ----------------
    def validate(self, d: Diagram, touched: Touched | None = None) -> None:
        errors = list(self._structural(d))
        if not errors:
            errors += list(self._notation_checks(d, touched or Touched()))
        if errors:
            raise DiagramError(" ".join(errors[:8]) + (f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""))

    def issues(self, d: Diagram) -> list[str]:
        try:
            return list(self._notation_checks(d, Touched.everything(d)))
        except DiagramError as exc:
            return [str(exc)]

    def notation(self, d: Diagram) -> Notation | None:
        try:
            return self.notations.get(d.notation)
        except UnknownNotationError as exc:
            raise DiagramError(str(exc)) from exc

    # ---------------- structure ----------------
    def _structural(self, d: Diagram) -> Iterator[str]:
        try:
            Diagram.model_validate(d.model_dump(mode="json"))  # re-check field constraints after in-place edits
        except ValidationError as exc:
            yield f"Invalid diagram content: {exc}"
            return
        seen: set[str] = set()
        for kind, items in (("group", d.groups), ("node", d.nodes), ("edge", d.edges)):
            for item in items:
                if item.id in seen or item.id in RESERVED_IDS:
                    yield (f"Duplicate or reserved id '{item.id}' ({kind}). "
                           "Ids must be unique across nodes, edges and groups.")
                seen.add(item.id)
        node_ids, groups = {n.id for n in d.nodes}, d.group_map()
        for n in d.nodes:
            if n.group is not None and n.group not in groups:
                yield f"Node '{n.id}' references unknown group '{n.group}'."
        for g in d.groups:
            if g.parent is not None and g.parent not in groups:
                yield f"Group '{g.id}' references unknown parent group '{g.parent}'."
        yield from self._cycles(d)
        endpoints = node_ids | set(groups)
        for e in d.edges:
            missing = [x for x in (e.source, e.target) if x not in endpoints]
            if missing:
                yield (f"Edge '{e.id}' references unknown node(s): {', '.join(missing)}. "
                       f"Existing nodes: {', '.join(sorted(node_ids)) or 'none'}.")

    @staticmethod
    def _cycles(d: Diagram) -> Iterator[str]:
        parents = {g.id: g.parent for g in d.groups}
        for gid in parents:
            seen, cur = {gid}, parents.get(gid)
            while cur is not None:
                if cur in seen:
                    yield f"Group '{gid}' is (indirectly) nested inside itself."
                    break
                seen.add(cur)
                cur = parents.get(cur)

    # ---------------- notation ----------------
    def _notation_checks(self, d: Diagram, touched: Touched) -> Iterator[str]:
        notation = self.notation(d)
        for n in d.nodes:
            if n.id in touched.nodes:
                yield from self._check_node(n, notation)
        for g in d.groups:
            if g.id in touched.groups:
                yield from self._check_group(g, notation)
        nodes = d.node_map()
        for e in d.edges:
            if e.id in touched.edges:
                yield from self._check_edge(e, notation, nodes)

    def _shape_problem(self, key: str, notation: Notation | None, element_type: str, owner: str) -> str | None:
        libraries = notation.libraries if notation else None
        entry = self.catalog.get(key)
        if entry is None:
            suggestions = self.catalog.suggest(key, libraries)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return f"{owner}: unknown shape '{key}'.{hint} Use search_shapes to find valid keys."
        if notation is not None and entry.library not in notation.libraries:
            return (f"{owner}: shape '{key}' (library '{entry.library}') is not part of the {notation.title} "
                    f"notation. Allowed libraries: {', '.join(notation.libraries)}.")
        if entry.element_type != element_type:
            return f"{owner}: shape '{key}' is a {entry.element_type} shape; a {element_type} shape is required."
        return None

    def _check_node(self, n: Node, notation: Notation | None) -> Iterator[str]:
        if n.shape:
            problem = self._shape_problem(n.shape, notation, "node", f"Node '{n.id}'")
            if problem:
                yield problem
        elif notation is not None and notation.require_shapes and not n.style:
            yield (f"Node '{n.id}' needs a 'shape' from the {notation.title} notation "
                   f"(libraries: {', '.join(notation.libraries)}); use search_shapes to find one.")

    def _check_group(self, g: Group, notation: Notation | None) -> Iterator[str]:
        if g.shape:
            problem = self._shape_problem(g.shape, notation, "container", f"Group '{g.id}'")
            if problem:
                yield problem

    def _check_edge(self, e: Edge, notation: Notation | None, nodes: dict[str, Node]) -> Iterator[str]:
        if e.relation and notation is None:
            yield f"Edge '{e.id}': relation '{e.relation}' requires a diagram notation (none is set)."
            return
        if notation is None:
            return
        if e.relation:
            relation = notation.relation(e.relation)
            if relation is None:
                yield (f"Edge '{e.id}': '{e.relation}' is not a relationship type of {notation.title}. "
                       f"Valid: {', '.join(notation.relations)}.")
                return
            yield from self._check_categories(e, relation.source_categories, nodes.get(e.source), "source")
            yield from self._check_categories(e, relation.target_categories, nodes.get(e.target), "target")
        elif notation.require_relations and not e.style:
            yield (f"Edge '{e.id}' needs a 'relation' of the {notation.title} notation. "
                   f"Valid: {', '.join(notation.relations)}.")

    def _check_categories(self, e: Edge, allowed: list[str], node: Node | None, end: str) -> Iterator[str]:
        if not allowed or node is None or not node.shape:
            return
        entry = self.catalog.get(node.shape)
        if entry is None:
            return
        text = f"{entry.category} {entry.name}".lower()
        if not any(a.lower() in text for a in allowed):
            yield (f"Edge '{e.id}': relation '{e.relation}' requires the {end} to be one of "
                   f"{', '.join(allowed)}; '{node.id}' is a {entry.name}.")

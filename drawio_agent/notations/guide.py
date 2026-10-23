"""Builds the notation guide text given to the agent: the hand-written prompt plus generated facts."""
from __future__ import annotations

from ..catalog import ShapeCatalog
from .model import Notation


class NotationGuideBuilder:
    def __init__(self, catalog: ShapeCatalog):
        self.catalog = catalog

    def build(self, notation: Notation) -> str:
        sections = [f"# Notation: {notation.title}", notation.prompt.strip()]
        sections.append(self._rules(notation))
        sections.append(self._relations(notation))
        sections.append(self._containers(notation))
        sections.append(self._shapes(notation))
        return "\n\n".join(s for s in sections if s)

    def _rules(self, n: Notation) -> str:
        libs = ", ".join(n.libraries)
        lines = ["## Tool usage for this notation",
                 f"- Only shapes from these catalogue libraries are accepted: {libs}.",
                 "- Set the 'shape' field of every new node to a catalogue key"
                 + (" (mandatory: nodes without a shape are rejected)." if n.require_shapes else ".")]
        if n.relations:
            lines.append("- Set the 'relation' field of every edge to one of the relationship types below"
                         + (" (mandatory)." if n.require_relations else
                            f" (if omitted, '{n.default_relation}' is used)." if n.default_relation else "."))
        lines.append("- Use search_shapes to find shape keys; never invent keys.")
        if n.default_group_shape:
            lines.append(f"- Groups without a shape are drawn as '{n.default_group_shape}'.")
        return "\n".join(lines)

    @staticmethod
    def _relations(n: Notation) -> str:
        if not n.relations:
            return ""
        lines = ["## Relationship types (edge 'relation' values, edge goes source -> target)"]
        for name, rel in n.relations.items():
            label = f" Default label: {rel.default_label}." if rel.default_label else ""
            lines.append(f"- {name}: {rel.description}{label}")
        return "\n".join(lines)

    def _containers(self, n: Notation) -> str:
        containers = [e for e in self.catalog.in_libraries(n.libraries) if e.element_type == "container"]
        if not containers:
            return ""
        lines = ["## Container shapes (use as group 'shape'; groups can be nested with 'parent')"]
        lines += [f"- {e.key}: {e.name}" for e in containers]
        return "\n".join(lines)

    def _shapes(self, n: Notation) -> str:
        nodes = [e for e in self.catalog.in_libraries(n.libraries) if e.element_type == "node"]
        if not nodes:
            return "## Shapes\nNo shapes of this notation are in the catalogue. Ask the user to run build_catalog.py."
        if len(nodes) > n.inline_shape_limit:
            cats = sorted({e.category for e in nodes})
            return (f"## Shapes\n{len(nodes)} shapes available in categories: {', '.join(cats)}. "
                    "Look them up with search_shapes (e.g. query='key vault').")
        lines = ["## Shapes (key: name [category])"]
        lines += [f"- {e.key}: {e.name} [{e.category}]" for e in nodes]
        return "\n".join(lines)

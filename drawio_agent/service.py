"""Application service: the diagram operations exposed as MCP tools.

Responsibilities are delegated to collaborators:
  DiagramRepository  persistence and undo history
  DiagramValidator   structural and notation rules
  DrawioRenderer     model -> .drawio (with LayoutEngine and StyleResolver)
  DrawioImporter     .drawio -> model
  ShapeCatalog / NotationRegistry / NotationGuideBuilder   shapes and notations
"""
from __future__ import annotations

import re
from pathlib import Path

from .catalog import ShapeCatalog
from .errors import DiagramError
from .importer import DrawioImporter
from .layout import LayoutEngine
from .models import (
    Diagram, Edge, EdgeInput, EdgeUpdate, Group, GroupInput, Node, NodeInput, NodeUpdate,
)
from .notations import NotationGuideBuilder, NotationRegistry, UnknownNotationError
from .render import DrawioRenderer
from .repository import DiagramRepository
from .styles import StyleResolver
from .validation import DiagramValidator, Touched

__all__ = ["DiagramService", "DiagramError"]

MAX_SEARCH_RESULTS = 50
_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def _humanize(identifier: str) -> str:
    return re.sub(r"[_\-.:]+", " ", identifier).strip().title()


class DiagramService:
    def __init__(self, workspace: Path | str, import_dirs: list[Path] | None = None, *,
                 catalog: ShapeCatalog | None = None, notations: NotationRegistry | None = None):
        self.catalog = catalog if catalog is not None else ShapeCatalog.load()
        self.notations = notations if notations is not None else NotationRegistry.load()
        self.repo = DiagramRepository(workspace)
        self.validator = DiagramValidator(self.catalog, self.notations)
        resolver = StyleResolver(self.catalog, self.notations)
        self.layout = LayoutEngine(resolver)
        self.renderer = DrawioRenderer(resolver, self.layout)
        self.importer = DrawioImporter(self.catalog)
        self.guides = NotationGuideBuilder(self.catalog)
        roots = [self.repo.workspace, Path.cwd().resolve()] + [Path(p).expanduser().resolve() for p in (import_dirs or [])]
        self.import_roots = list(dict.fromkeys(roots))

    # ---------------- infrastructure ----------------
    @property
    def workspace(self) -> Path:
        return self.repo.workspace

    def drawio_path(self, name: str) -> Path:
        return self.repo.drawio_path(name)

    def _load(self, name: str) -> Diagram:
        return self.repo.load(name)

    def _canonical_notation(self, name: str | None) -> str | None:
        try:
            return self.notations.resolve_name(name)
        except UnknownNotationError as exc:
            raise DiagramError(str(exc)) from exc

    def _commit(self, d: Diagram, previous: Diagram | None, message: str, touched: Touched | None = None) -> dict:
        self.validator.validate(d, touched)
        self.repo.save(d, self.renderer.render(d), previous)
        return self._summary(d, message)

    def _summary(self, d: Diagram, message: str) -> dict:
        return {
            "diagram": d.name, "message": message, "file": str(self.drawio_path(d.name)),
            "nodes": len(d.nodes), "edges": len(d.edges), "groups": len(d.groups),
            "layout_mode": d.layout_mode, "notation": d.notation,
        }

    def _ensure_group(self, d: Diagram, group_id: str | None, touched: Touched) -> None:
        if group_id and group_id not in {g.id for g in d.groups}:
            d.groups.append(Group(id=group_id, label=_humanize(group_id)))
            touched.groups.add(group_id)

    @staticmethod
    def _next_edge_id(d: Diagram) -> str:
        taken = {e.id for e in d.edges} | {n.id for n in d.nodes} | {g.id for g in d.groups}
        k = len(d.edges) + 1
        while f"e{k}" in taken:
            k += 1
        return f"e{k}"

    # ---------------- read operations ----------------
    def list_diagrams(self) -> dict:
        items = []
        for name in self.repo.names():
            try:
                d = self._load(name)
                items.append({"diagram": name, "title": d.title, "notation": d.notation, "nodes": len(d.nodes),
                              "edges": len(d.edges), "file": str(self.drawio_path(name))})
            except Exception as exc:  # corrupted file: report, do not crash
                items.append({"diagram": name, "error": str(exc)})
        return {"workspace": str(self.workspace), "diagrams": items}

    def get_diagram(self, diagram: str) -> dict:
        d = self._load(diagram)
        data = d.model_dump(mode="json", exclude_none=True, exclude={
            "nodes": {"__all__": {"x", "y", "width", "height", "style"}},
            "edges": {"__all__": {"style"}},
        })
        data["file"] = str(self.drawio_path(diagram))
        data["undo_steps_available"] = len(self.repo.history(diagram))
        if d.notation:
            issues = self.validator.issues(d)
            if issues:
                data["notation_issues"] = issues[:20]
        return data

    # ---------------- shapes & notations ----------------
    def list_notations(self) -> dict:
        return {"notations": [{
            "name": n.name, "title": n.title, "description": n.description, "aliases": n.aliases,
            "libraries": n.libraries, "relations": list(n.relations),
            "shapes": len([e for e in self.catalog.in_libraries(n.libraries) if e.element_type == "node"]),
        } for n in self.notations.all()]}

    def get_notation_guide(self, notation: str) -> dict:
        n = self.notations.get(self._canonical_notation(notation)) if notation else None
        if n is None:
            raise DiagramError("Give a notation name. Available: " + ", ".join(self.notations.names()))
        return {"notation": n.name, "title": n.title, "guide": self.guides.build(n)}

    def _libraries_for(self, notation: str | None, library: str | None) -> list[str] | None:
        n = self.notations.get(self._canonical_notation(notation)) if notation else None
        if library and n is not None and library not in n.libraries:
            raise DiagramError(f"Library '{library}' is not part of the {n.title} notation "
                               f"(allowed: {', '.join(n.libraries)}).")
        if library:
            if library not in {lib.name for lib in self.catalog.libraries()}:
                raise DiagramError(f"Unknown library '{library}'. Use list_shape_libraries.")
            return [library]
        return n.libraries if n else None

    def search_shapes(self, query: str, notation: str | None = None, library: str | None = None,
                      element_type: str | None = None, category: str | None = None, limit: int = 10) -> dict:
        libraries = self._libraries_for(notation, library)
        types = [element_type] if element_type else None
        limit = max(1, min(limit, MAX_SEARCH_RESULTS))
        results = self.catalog.search(query, libraries=libraries, element_types=types, category=category, limit=limit)
        out = {"query": query, "results": [e.summary() for e in results]}
        if not results:
            out["hint"] = "No match. Try a shorter or more generic query, another category, or list_shape_libraries."
        return out

    def list_shape_libraries(self, notation: str | None = None) -> dict:
        libraries = self._libraries_for(notation, None)
        return {"libraries": [lib.model_dump() for lib in self.catalog.libraries(libraries)],
                "total_shapes": len(self.catalog.in_libraries(libraries))}

    # ---------------- lifecycle ----------------
    def create_diagram(self, diagram: str, title: str = "", direction: str | None = None, overwrite: bool = False,
                       notation: str | None = None) -> dict:
        self.repo.check_name(diagram)
        notation_name = self._canonical_notation(notation)
        previous = self.repo.load_or_none(diagram)
        if previous and not overwrite:
            raise DiagramError(f"Diagram '{diagram}' already exists. Edit it, choose another name, or set overwrite=true.")
        n = self.notations.get(notation_name)
        d = Diagram(name=diagram, title=title, notation=notation_name,
                    direction=direction or (n.default_direction if n else "left-to-right"))
        return self._commit(d, previous, f"Created diagram '{diagram}'" + (f" ({n.title})." if n else "."))

    def import_drawio(self, path: str, diagram: str | None = None, overwrite: bool = False) -> dict:
        src = Path(path).expanduser().resolve()
        if src.suffix.lower() not in {".drawio", ".xml"}:
            raise DiagramError("Only .drawio or .xml files can be imported.")
        if not any(src.is_relative_to(root) for root in self.import_roots):
            raise DiagramError(f"Import path not allowed. Allowed folders: {', '.join(map(str, self.import_roots))}. "
                               "Add more with DRAWIO_IMPORT_DIRS.")
        if not src.is_file():
            raise DiagramError(f"File not found: {src}")
        name = diagram or re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_-")[:64] or "imported"
        self.repo.check_name(name)
        previous = self.repo.load_or_none(name)
        if previous and not overwrite:
            raise DiagramError(f"Diagram '{name}' already exists. Choose another name or set overwrite=true.")
        try:
            d, stats = self.importer.load(src, name)
        except Exception as exc:
            raise DiagramError(f"Could not parse {src.name}: {exc}") from exc
        msg = f"Imported {src.name} as '{name}' (positions preserved, layout_mode=manual)."
        if stats["matched_shapes"]:
            msg += f" Recognised {stats['matched_shapes']} catalogue shape(s)."
        d.notation = self._infer_notation(d)
        if d.notation:
            msg += f" Detected notation: {d.notation}."
        if stats["skipped_edges"]:
            msg += f" Skipped {stats['skipped_edges']} edge(s) not connected to two shapes."
        return self._commit(d, previous, msg)

    def _infer_notation(self, d: Diagram) -> str | None:
        """A notation is detected when most nodes are catalogue shapes of exactly one notation."""
        shapes = [self.catalog.get(n.shape) for n in d.nodes if n.shape]
        libraries = {e.library for e in shapes if e is not None}
        if not d.nodes or len(shapes) * 2 < len(d.nodes) or not libraries:
            return None
        fitting = [n.name for n in self.notations.all() if libraries <= set(n.libraries)]
        return fitting[0] if len(fitting) == 1 else None

    def replace_diagram(self, diagram: str, nodes: list[NodeInput], edges: list[EdgeInput] | None = None,
                        groups: list[GroupInput] | None = None, title: str | None = None,
                        direction: str | None = None, notation: str | None = None) -> dict:
        previous = self.repo.load_or_none(self.repo.check_name(diagram))
        notation_name = self._canonical_notation(notation) if notation is not None else (
            previous.notation if previous else None)
        n = self.notations.get(notation_name)
        d = Diagram(name=diagram, notation=notation_name,
                    title=title if title is not None else (previous.title if previous else ""),
                    direction=direction or (previous.direction if previous else
                                            (n.default_direction if n else "left-to-right")))
        touched = Touched()
        d.groups = [self._new_group(g) for g in (groups or [])]
        for node in nodes:
            self._ensure_group(d, node.group, touched)
            d.nodes.append(Node(**node.model_dump()))
        for e in edges or []:
            d.edges.append(self._new_edge(d, e))
        return self._commit(d, previous, f"Replaced the content of '{diagram}'.", Touched.everything(d))

    # ---------------- nodes ----------------
    def add_nodes(self, diagram: str, nodes: list[NodeInput]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        touched = Touched()
        existing = {n.id for n in d.nodes}
        for n in nodes:
            if n.id in existing:
                raise DiagramError(f"Node '{n.id}' already exists; use update_nodes to change it.")
            self._ensure_group(d, n.group, touched)
            d.nodes.append(Node(**n.model_dump()))
            existing.add(n.id)
            touched.nodes.add(n.id)
        return self._commit(d, prev, f"Added {len(nodes)} node(s): {', '.join(n.id for n in nodes)}.", touched)

    def update_nodes(self, diagram: str, updates: list[NodeUpdate]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        touched = Touched()
        by_id = d.node_map()
        for u in updates:
            node = by_id.get(u.id)
            if node is None:
                raise DiagramError(f"Node '{u.id}' does not exist. Existing nodes: {', '.join(sorted(by_id))}.")
            if u.label is not None:
                node.label = u.label
            if u.kind is not None and u.kind != node.kind:
                node.kind = u.kind
                if not u.shape:
                    node.shape = None
                self._reset_appearance(node)
            if u.shape is not None and (u.shape or None) != node.shape:
                node.shape = u.shape or None
                self._reset_appearance(node)
            if u.group is not None:
                node.group = u.group or None
                self._ensure_group(d, node.group, touched)
            if u.color is not None:
                if u.color and not _COLOR_RE.fullmatch(u.color):
                    raise DiagramError(f"Invalid colour '{u.color}'; use #RRGGBB.")
                node.color = u.color or None
            touched.nodes.add(node.id)
        return self._commit(d, prev, f"Updated node(s): {', '.join(u.id for u in updates)}.", touched)

    @staticmethod
    def _reset_appearance(node: Node) -> None:
        """A new shape type drops the imported style and size (position is kept)."""
        node.style = None
        node.width = node.height = None

    def remove_nodes(self, diagram: str, ids: list[str]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        unknown = set(ids) - {n.id for n in d.nodes}
        if unknown:
            raise DiagramError(f"Unknown node id(s): {', '.join(sorted(unknown))}.")
        drop = set(ids)
        removed_edges = [e.id for e in d.edges if e.source in drop or e.target in drop]
        d.nodes = [n for n in d.nodes if n.id not in drop]
        d.edges = [e for e in d.edges if e.id not in removed_edges]
        msg = f"Removed node(s): {', '.join(ids)}."
        if removed_edges:
            msg += f" Also removed connected edge(s): {', '.join(removed_edges)}."
        return self._commit(d, prev, msg)

    # ---------------- edges ----------------
    def _new_edge(self, d: Diagram, e: EdgeInput) -> Edge:
        data = e.model_dump()
        data["id"] = data["id"] or self._next_edge_id(d)
        return Edge(**data)

    def add_edges(self, diagram: str, edges: list[EdgeInput]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        touched = Touched()
        added = []
        for e in edges:
            edge = self._new_edge(d, e)
            d.edges.append(edge)
            touched.edges.add(edge.id)
            added.append(f"{edge.id} ({e.source}->{e.target})")
        return self._commit(d, prev, f"Added edge(s): {', '.join(added)}.", touched)

    def update_edges(self, diagram: str, updates: list[EdgeUpdate]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        touched = Touched()
        by_id = {e.id: e for e in d.edges}
        for u in updates:
            edge = by_id.get(u.id)
            if edge is None:
                raise DiagramError(f"Edge '{u.id}' does not exist. Existing edges: {', '.join(sorted(by_id)) or 'none'}.")
            for attr in ("source", "target", "label", "dashed", "bidirectional"):
                value = getattr(u, attr)
                if value is not None:
                    setattr(edge, attr, value)
            if u.relation is not None:
                edge.relation = u.relation or None
                edge.style = None  # the relation defines the look
            touched.edges.add(edge.id)
        return self._commit(d, prev, f"Updated edge(s): {', '.join(u.id for u in updates)}.", touched)

    def remove_edges(self, diagram: str, ids: list[str]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        unknown = set(ids) - {e.id for e in d.edges}
        if unknown:
            raise DiagramError(f"Unknown edge id(s): {', '.join(sorted(unknown))}.")
        d.edges = [e for e in d.edges if e.id not in set(ids)]
        return self._commit(d, prev, f"Removed edge(s): {', '.join(ids)}.")

    # ---------------- groups ----------------
    @staticmethod
    def _new_group(g: GroupInput) -> Group:
        data = g.model_dump()
        data["parent"] = data["parent"] or None
        return Group(**data)

    def upsert_groups(self, diagram: str, groups: list[GroupInput]) -> dict:
        """Create groups, or update existing ones. Optional fields change only when explicitly provided."""
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        touched = Touched()
        by_id = d.group_map()
        for g in groups:
            if g.id in by_id:
                group = by_id[g.id]
                group.label = g.label
                for attr in ("color", "shape", "parent"):
                    if attr in g.model_fields_set:
                        setattr(group, attr, getattr(g, attr) or None)
            else:
                d.groups.append(self._new_group(g))
            touched.groups.add(g.id)
        return self._commit(d, prev, f"Saved group(s): {', '.join(g.id for g in groups)}.", touched)

    def remove_groups(self, diagram: str, ids: list[str], remove_members: bool = False) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        groups = d.group_map()
        unknown = set(ids) - set(groups)
        if unknown:
            raise DiagramError(f"Unknown group id(s): {', '.join(sorted(unknown))}.")
        drop = set(ids)
        if remove_members:  # also remove nested groups, recursively
            changed = True
            while changed:
                nested = {g.id for g in d.groups if g.parent in drop} - drop
                changed = bool(nested)
                drop |= nested
            gone = {n.id for n in d.nodes if n.group in drop}
            d.nodes = [n for n in d.nodes if n.id not in gone]
        else:  # members and nested groups move to the removed group's parent
            def surviving_parent(gid: str | None) -> str | None:
                while gid in drop:
                    gid = groups[gid].parent
                return gid
            for n in d.nodes:
                if n.group in drop:
                    n.group = surviving_parent(n.group)
            for g in d.groups:
                if g.parent in drop:
                    g.parent = surviving_parent(g.parent)
            gone = set()
        d.groups = [g for g in d.groups if g.id not in drop]
        d.edges = [e for e in d.edges if not ({e.source, e.target} & (gone | drop))]
        return self._commit(d, prev, f"Removed group(s): {', '.join(ids)}"
                            + (" and their members." if remove_members else "; members kept."))

    # ---------------- diagram-level ----------------
    def set_diagram_properties(self, diagram: str, title: str | None = None, direction: str | None = None,
                               layout_mode: str | None = None, notation: str | None = None) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        if title is not None:
            d.title = title
        if notation is not None:
            d.notation = self._canonical_notation(notation)
        if direction is not None:
            d.direction = direction
            d.layout_mode = "auto"  # a new direction only makes sense with automatic layout
        if layout_mode == "manual" and d.layout_mode == "auto":
            geo = self.layout.compute(d)  # freeze the current automatic positions
            for n in d.nodes:
                n.x, n.y, n.width, n.height = geo.nodes[n.id]
        if layout_mode is not None:
            d.layout_mode = layout_mode
        result = self._commit(d, prev, "Updated diagram properties.")
        if notation is not None and d.notation:
            issues = self.validator.issues(d)
            if issues:
                result["notation_issues"] = issues[:20]
                result["message"] += (f" {len(issues)} existing element(s) do not follow the {d.notation} "
                                      "notation yet (see notation_issues).")
        return result

    def relayout(self, diagram: str) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        d.layout_mode = "auto"
        for n in d.nodes:
            n.x = n.y = None
            if n.style is None:
                n.width = n.height = None
        return self._commit(d, prev, "Re-arranged the diagram with automatic layout.")

    def undo(self, diagram: str) -> dict:
        self._load(diagram)
        restored, remaining = self.repo.pop_history(diagram)
        self.repo.save_without_history(restored, self.renderer.render(restored))
        return self._summary(restored, f"Undid the last change ({remaining} more undo step(s) available).")

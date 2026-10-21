"""Stateful diagram operations used by the MCP server.

Each diagram lives in the workspace as:
  <name>.json          source of truth (model)
  <name>.history.json  undo stack
  <name>.drawio        rendered, editable draw.io file (rewritten after every change)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .importer import import_drawio as _import_drawio
from .layout import compute_geometry
from .models import (
    NAME_PATTERN, Diagram, Edge, EdgeInput, EdgeUpdate, Group, GroupInput, Node, NodeInput, NodeUpdate,
)
from .render import render_xml

HISTORY_LIMIT = 50
_NAME_RE = re.compile(NAME_PATTERN)


class DiagramError(ValueError):
    """Raised for invalid operations; the message is returned to the LLM so it can correct itself."""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _humanize(identifier: str) -> str:
    return re.sub(r"[_\-.:]+", " ", identifier).strip().title()


class DiagramService:
    def __init__(self, workspace: Path | str, import_dirs: list[Path] | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        roots = [self.workspace, Path.cwd().resolve()] + [Path(p).expanduser().resolve() for p in (import_dirs or [])]
        self.import_roots = list(dict.fromkeys(roots))

    # ---------------- storage ----------------
    def _check_name(self, name: str) -> str:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise DiagramError(f"Invalid diagram name '{name}'. Use letters, digits, '_' or '-' (max 64 chars).")
        return name

    def _state_path(self, name: str) -> Path:
        return self.workspace / f"{self._check_name(name)}.json"

    def _history_path(self, name: str) -> Path:
        return self.workspace / f"{self._check_name(name)}.history.json"

    def drawio_path(self, name: str) -> Path:
        return self.workspace / f"{self._check_name(name)}.drawio"

    def _exists(self, name: str) -> bool:
        return self._state_path(name).exists()

    def _load(self, name: str) -> Diagram:
        path = self._state_path(name)
        if not path.exists():
            available = ", ".join(self._names()) or "none"
            raise DiagramError(f"Diagram '{name}' does not exist. Available diagrams: {available}.")
        return Diagram.model_validate_json(path.read_text(encoding="utf-8"))

    def _names(self) -> list[str]:
        return sorted(p.stem for p in self.workspace.glob("*.json") if not p.name.endswith(".history.json"))

    def _read_history(self, name: str) -> list[dict]:
        path = self._history_path(name)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def _validate(self, d: Diagram) -> None:
        try:
            Diagram.model_validate(d.model_dump(mode="json"))  # re-check field constraints after in-place edits
        except ValidationError as exc:
            raise DiagramError(f"Invalid diagram content: {exc}") from exc
        seen: set[str] = set()
        for kind, items in (("group", d.groups), ("node", d.nodes), ("edge", d.edges)):
            for item in items:
                if item.id in seen or item.id in {"0", "1", "diagram-title"}:
                    raise DiagramError(f"Duplicate or reserved id '{item.id}' ({kind}). Ids must be unique across nodes, edges and groups.")
                seen.add(item.id)
        node_ids = {n.id for n in d.nodes}
        group_ids = {g.id for g in d.groups}
        for n in d.nodes:
            if n.group is not None and n.group not in group_ids:
                raise DiagramError(f"Node '{n.id}' references unknown group '{n.group}'.")
        for e in d.edges:
            missing = [x for x in (e.source, e.target) if x not in node_ids]
            if missing:
                raise DiagramError(f"Edge '{e.id}' references unknown node(s): {', '.join(missing)}. Existing nodes: {', '.join(sorted(node_ids)) or 'none'}.")

    def _commit(self, d: Diagram, previous: Diagram | None, message: str) -> dict:
        self._validate(d)
        if previous is not None:
            history = self._read_history(d.name)
            history.append(previous.model_dump(mode="json"))
            _atomic_write(self._history_path(d.name), json.dumps(history[-HISTORY_LIMIT:]))
        _atomic_write(self._state_path(d.name), d.model_dump_json(indent=2))
        _atomic_write(self.drawio_path(d.name), render_xml(d))
        return self._summary(d, message)

    def _summary(self, d: Diagram, message: str) -> dict:
        return {
            "diagram": d.name, "message": message, "file": str(self.drawio_path(d.name)),
            "nodes": len(d.nodes), "edges": len(d.edges), "groups": len(d.groups), "layout_mode": d.layout_mode,
        }

    def _ensure_group(self, d: Diagram, group_id: str | None) -> None:
        if group_id and group_id not in {g.id for g in d.groups}:
            d.groups.append(Group(id=group_id, label=_humanize(group_id)))

    def _next_edge_id(self, d: Diagram) -> str:
        taken = {e.id for e in d.edges} | {n.id for n in d.nodes} | {g.id for g in d.groups}
        k = len(d.edges) + 1
        while f"e{k}" in taken:
            k += 1
        return f"e{k}"

    # ---------------- read operations ----------------
    def list_diagrams(self) -> dict:
        items = []
        for name in self._names():
            try:
                d = self._load(name)
                items.append({"diagram": name, "title": d.title, "nodes": len(d.nodes), "edges": len(d.edges),
                              "file": str(self.drawio_path(name))})
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
        data["undo_steps_available"] = len(self._read_history(diagram))
        return data

    # ---------------- lifecycle ----------------
    def create_diagram(self, diagram: str, title: str = "", direction: str = "left-to-right", overwrite: bool = False) -> dict:
        self._check_name(diagram)
        previous = self._load(diagram) if self._exists(diagram) else None
        if previous and not overwrite:
            raise DiagramError(f"Diagram '{diagram}' already exists. Edit it, choose another name, or set overwrite=true.")
        d = Diagram(name=diagram, title=title, direction=direction)
        return self._commit(d, previous, f"Created diagram '{diagram}'.")

    def import_drawio(self, path: str, diagram: str | None = None, overwrite: bool = False) -> dict:
        src = Path(path).expanduser().resolve()
        if src.suffix.lower() not in {".drawio", ".xml"}:
            raise DiagramError("Only .drawio or .xml files can be imported.")
        if not any(src.is_relative_to(root) for root in self.import_roots):
            raise DiagramError(f"Import path not allowed. Allowed folders: {', '.join(map(str, self.import_roots))}. Add more with DRAWIO_IMPORT_DIRS.")
        if not src.is_file():
            raise DiagramError(f"File not found: {src}")
        name = diagram or re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_-")[:64] or "imported"
        self._check_name(name)
        previous = self._load(name) if self._exists(name) else None
        if previous and not overwrite:
            raise DiagramError(f"Diagram '{name}' already exists. Choose another name or set overwrite=true.")
        try:
            d, stats = _import_drawio(src, name)
        except DiagramError:
            raise
        except Exception as exc:
            raise DiagramError(f"Could not parse {src.name}: {exc}") from exc
        msg = f"Imported {src.name} as '{name}' (positions preserved, layout_mode=manual)."
        if stats["skipped_edges"]:
            msg += f" Skipped {stats['skipped_edges']} edge(s) not connected to two shapes."
        return self._commit(d, previous, msg)

    def replace_diagram(self, diagram: str, nodes: list[NodeInput], edges: list[EdgeInput] | None = None,
                        groups: list[GroupInput] | None = None, title: str | None = None, direction: str | None = None) -> dict:
        previous = self._load(diagram) if self._exists(diagram) else None
        d = Diagram(name=self._check_name(diagram),
                    title=title if title is not None else (previous.title if previous else ""),
                    direction=direction or (previous.direction if previous else "left-to-right"))
        d.groups = [Group(**g.model_dump()) for g in (groups or [])]
        for n in nodes:
            self._ensure_group(d, n.group)
            d.nodes.append(Node(**n.model_dump()))
        for e in edges or []:
            data = e.model_dump()
            data["id"] = data["id"] or self._next_edge_id(d)
            d.edges.append(Edge(**data))
        return self._commit(d, previous, f"Replaced the content of '{diagram}'.")

    # ---------------- nodes ----------------
    def add_nodes(self, diagram: str, nodes: list[NodeInput]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        existing = {n.id for n in d.nodes}
        for n in nodes:
            if n.id in existing:
                raise DiagramError(f"Node '{n.id}' already exists; use update_nodes to change it.")
            self._ensure_group(d, n.group)
            d.nodes.append(Node(**n.model_dump()))
            existing.add(n.id)
        return self._commit(d, prev, f"Added {len(nodes)} node(s): {', '.join(n.id for n in nodes)}.")

    def update_nodes(self, diagram: str, updates: list[NodeUpdate]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        by_id = {n.id: n for n in d.nodes}
        for u in updates:
            node = by_id.get(u.id)
            if node is None:
                raise DiagramError(f"Node '{u.id}' does not exist. Existing nodes: {', '.join(sorted(by_id))}.")
            if u.label is not None:
                node.label = u.label
            if u.kind is not None and u.kind != node.kind:
                node.kind, node.style = u.kind, None
                node.width = node.height = None
            if u.group is not None:
                node.group = u.group or None
                self._ensure_group(d, node.group)
            if u.color is not None:
                if u.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", u.color):
                    raise DiagramError(f"Invalid colour '{u.color}'; use #RRGGBB.")
                node.color = u.color or None
        return self._commit(d, prev, f"Updated node(s): {', '.join(u.id for u in updates)}.")

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
    def add_edges(self, diagram: str, edges: list[EdgeInput]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        added = []
        for e in edges:
            data = e.model_dump()
            data["id"] = data["id"] or self._next_edge_id(d)
            d.edges.append(Edge(**data))
            added.append(f"{data['id']} ({e.source}->{e.target})")
        return self._commit(d, prev, f"Added edge(s): {', '.join(added)}.")

    def update_edges(self, diagram: str, updates: list[EdgeUpdate]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        by_id = {e.id: e for e in d.edges}
        for u in updates:
            edge = by_id.get(u.id)
            if edge is None:
                raise DiagramError(f"Edge '{u.id}' does not exist. Existing edges: {', '.join(sorted(by_id)) or 'none'}.")
            for attr in ("source", "target", "label", "dashed", "bidirectional"):
                value = getattr(u, attr)
                if value is not None:
                    setattr(edge, attr, value)
        return self._commit(d, prev, f"Updated edge(s): {', '.join(u.id for u in updates)}.")

    def remove_edges(self, diagram: str, ids: list[str]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        unknown = set(ids) - {e.id for e in d.edges}
        if unknown:
            raise DiagramError(f"Unknown edge id(s): {', '.join(sorted(unknown))}.")
        d.edges = [e for e in d.edges if e.id not in set(ids)]
        return self._commit(d, prev, f"Removed edge(s): {', '.join(ids)}.")

    # ---------------- groups ----------------
    def upsert_groups(self, diagram: str, groups: list[GroupInput]) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        by_id = {g.id: g for g in d.groups}
        for g in groups:
            if g.id in by_id:
                by_id[g.id].label = g.label
                by_id[g.id].color = g.color
            else:
                d.groups.append(Group(**g.model_dump()))
        return self._commit(d, prev, f"Saved group(s): {', '.join(g.id for g in groups)}.")

    def remove_groups(self, diagram: str, ids: list[str], remove_members: bool = False) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        unknown = set(ids) - {g.id for g in d.groups}
        if unknown:
            raise DiagramError(f"Unknown group id(s): {', '.join(sorted(unknown))}.")
        drop = set(ids)
        if remove_members:
            gone = {n.id for n in d.nodes if n.group in drop}
            d.nodes = [n for n in d.nodes if n.id not in gone]
            d.edges = [e for e in d.edges if e.source not in gone and e.target not in gone]
        else:
            for n in d.nodes:
                if n.group in drop:
                    n.group = None
        d.groups = [g for g in d.groups if g.id not in drop]
        return self._commit(d, prev, f"Removed group(s): {', '.join(ids)}" + (" and their members." if remove_members else "; members kept."))

    # ---------------- diagram-level ----------------
    def set_diagram_properties(self, diagram: str, title: str | None = None, direction: str | None = None,
                               layout_mode: str | None = None) -> dict:
        d = self._load(diagram)
        prev = d.model_copy(deep=True)
        if title is not None:
            d.title = title
        if direction is not None:
            d.direction = direction
            d.layout_mode = "auto"  # a new direction only makes sense with automatic layout
        if layout_mode == "manual" and d.layout_mode == "auto":
            geo = compute_geometry(d)  # freeze the current automatic positions
            for n in d.nodes:
                n.x, n.y, n.width, n.height = geo.nodes[n.id]
        if layout_mode is not None:
            d.layout_mode = layout_mode
        return self._commit(d, prev, "Updated diagram properties.")

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
        history = self._read_history(diagram)
        if not history:
            raise DiagramError("Nothing to undo.")
        restored = Diagram.model_validate(history.pop())
        _atomic_write(self._history_path(diagram), json.dumps(history))
        _atomic_write(self._state_path(diagram), restored.model_dump_json(indent=2))
        _atomic_write(self.drawio_path(diagram), render_xml(restored))
        return self._summary(restored, f"Undid the last change ({len(history)} more undo step(s) available).")

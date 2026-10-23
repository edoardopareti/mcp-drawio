"""
MCP server exposing stateful draw.io diagram editing tools.

Run:  python -m drawio_agent.server                         (stdio, used by the agent and by VS Code)
      python -m drawio_agent.server --transport streamable-http --port 8000
"""
from __future__ import annotations

import argparse
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .models import EdgeInput, EdgeUpdate, GroupInput, NodeInput, NodeUpdate
from .service import DiagramService

_cfg = load_config()
service = DiagramService(_cfg.workspace, _cfg.import_dirs)

mcp = FastMCP(
    "drawio-diagrams",
    instructions=(
        "Create and incrementally edit draw.io diagrams. Every diagram is identified by 'diagram' (its name). "
        "Read the current state with get_diagram before editing, reuse existing ids, prefer small edits "
        "(add/update/remove) over replace_diagram. Every change rewrites the .drawio file and can be undone."
    ),
    log_level="WARNING",
)

Direction = Literal["left-to-right", "top-to-bottom"]


@mcp.tool()
def list_diagrams() -> dict:
    """List all diagrams in the workspace with their size and .drawio file path."""
    return service.list_diagrams()


@mcp.tool()
def get_diagram(diagram: str) -> dict:
    """Return the full current content of a diagram: title, direction, groups, nodes (id, label, kind, group, color) and edges (id, source, target, label)."""
    return service.get_diagram(diagram)


@mcp.tool()
def create_diagram(diagram: str, title: str = "", direction: Direction = "left-to-right", overwrite: bool = False) -> dict:
    """Create a new empty diagram. 'diagram' is a short snake_case name used as file name. Use left-to-right for architectures, top-to-bottom for workflows."""
    return service.create_diagram(diagram, title, direction, overwrite)


@mcp.tool()
def import_drawio(path: str, diagram: str | None = None, overwrite: bool = False) -> dict:
    """Import an existing .drawio file so it can be edited. Original positions and styles are preserved (manual layout)."""
    return service.import_drawio(path, diagram, overwrite)


@mcp.tool()
def add_nodes(diagram: str, nodes: list[NodeInput]) -> dict:
    """Add one or more shapes. Ids must be new and unique. Setting 'group' places the node inside that boundary (auto-created if missing)."""
    return service.add_nodes(diagram, nodes)


@mcp.tool()
def update_nodes(diagram: str, updates: list[NodeUpdate]) -> dict:
    """Change label, kind (shape), group or colour of existing nodes. Only provided fields change. group='' removes a node from its group; color='' resets the colour."""
    return service.update_nodes(diagram, updates)


@mcp.tool()
def remove_nodes(diagram: str, ids: list[str]) -> dict:
    """Delete nodes by id. Edges connected to them are deleted too."""
    return service.remove_nodes(diagram, ids)


@mcp.tool()
def add_edges(diagram: str, edges: list[EdgeInput]) -> dict:
    """Add connectors between existing nodes (source -> target), with optional label, dashed line or arrows on both ends."""
    return service.add_edges(diagram, edges)


@mcp.tool()
def update_edges(diagram: str, updates: list[EdgeUpdate]) -> dict:
    """Change label, endpoints, dashed or bidirectional flags of existing edges by edge id."""
    return service.update_edges(diagram, updates)


@mcp.tool()
def remove_edges(diagram: str, ids: list[str]) -> dict:
    """Delete edges by edge id (see get_diagram for ids)."""
    return service.remove_edges(diagram, ids)


@mcp.tool()
def upsert_groups(diagram: str, groups: list[GroupInput]) -> dict:
    """Create or rename/recolour groups (boundaries, layers, zones). Assign nodes to them via add_nodes/update_nodes."""
    return service.upsert_groups(diagram, groups)


@mcp.tool()
def remove_groups(diagram: str, ids: list[str], remove_members: bool = False) -> dict:
    """Delete groups. By default their nodes are kept and simply ungrouped; remove_members=true deletes them too."""
    return service.remove_groups(diagram, ids, remove_members)


@mcp.tool()
def set_diagram_properties(diagram: str, title: str | None = None, direction: Direction | None = None,
                           layout_mode: Literal["auto", "manual"] | None = None) -> dict:
    """Change the title, flow direction (re-layouts automatically) or layout mode. 'manual' freezes current positions."""
    return service.set_diagram_properties(diagram, title, direction, layout_mode)


@mcp.tool()
def relayout(diagram: str) -> dict:
    """Discard stored positions and arrange the whole diagram automatically (useful after importing or many edits)."""
    return service.relayout(diagram)


@mcp.tool()
def replace_diagram(diagram: str, nodes: list[NodeInput], edges: list[EdgeInput] | None = None,
                    groups: list[GroupInput] | None = None, title: str | None = None,
                    direction: Direction | None = None) -> dict:
    """Replace the whole content of a diagram in one call (creates it if missing). Use only for a full redesign or a large initial diagram."""
    return service.replace_diagram(diagram, nodes, edges, groups, title, direction)


@mcp.tool()
def undo(diagram: str) -> dict:
    """Revert the last change made to the diagram."""
    return service.undo(diagram)


def main() -> None:
    parser = argparse.ArgumentParser(description="draw.io MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio")
    parser.add_argument(
        "--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

"""Tool exposure and argument policy: what the LLM sees and how its calls are completed/constrained."""
from __future__ import annotations

from .schema import tool_name, tool_params

# Tools only offered to the LLM when a notation is active (generic mode keeps the v3 tool set).
NOTATION_TOOLS = frozenset({"search_shapes", "list_shape_libraries", "list_notations", "get_notation_guide"})
_SCOPED_SEARCH = frozenset({"search_shapes", "list_shape_libraries"})
_CREATING = frozenset({"create_diagram", "replace_diagram"})


class ToolPolicy:
    def __init__(self, tools: list[dict]):
        self._tools = tools
        self._takes_diagram = {tool_name(t) for t in tools if "diagram" in tool_params(t)}

    def visible_tools(self, notation: str | None) -> list[dict]:
        if notation:
            return self._tools
        return [t for t in self._tools if tool_name(t) not in NOTATION_TOOLS]

    def complete(self, name: str, args: dict, *, active: str | None, notation: str | None,
                 notation_forced: bool) -> dict:
        """Fill in implicit arguments and enforce the active notation. Returns a new dict."""
        args = dict(args)
        if name in self._takes_diagram and "diagram" not in args and active:
            args["diagram"] = active
        if notation:
            if name in _SCOPED_SEARCH:
                args["notation"] = notation  # the agent may only browse its notation's shapes
            elif name in _CREATING and (notation_forced or not args.get("notation")):
                args["notation"] = notation
        if notation_forced and name == "set_diagram_properties" and "notation" in args:
            args["notation"] = notation or "none"  # the user's --notation choice wins
        return args

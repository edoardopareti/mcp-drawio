"""Conversion of MCP tool descriptions into OpenAI function-calling specs."""
from __future__ import annotations

import copy
from typing import Any


def inline_refs(schema: dict) -> dict:
    """Inline local $ref/$defs so the schema works with every OpenAI-compatible endpoint."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            return node
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"].split("/")[-1]
                target = copy.deepcopy(defs.get(ref, {}))
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**target, **extra}, depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items() if k not in ("$defs", "definitions")}
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    out = resolve(schema)
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def mcp_tool_to_openai(name: str, description: str | None, input_schema: dict) -> dict:
    """Convert an MCP tool description to an OpenAI-compatible function spec."""
    return {"type": "function", "function": {
        "name": name,
        "description": (description or "").strip()[:1024],
        "parameters": inline_refs(input_schema or {}),
    }}


def tool_name(tool: dict) -> str:
    return tool["function"]["name"]


def tool_params(tool: dict) -> set[str]:
    return set(tool["function"]["parameters"].get("properties", {}))

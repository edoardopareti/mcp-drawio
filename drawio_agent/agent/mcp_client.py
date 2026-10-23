"""MCP connection: spawn the local server over stdio or connect to a streamable-HTTP endpoint."""
from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager

from ..config import PROJECT_ROOT, Config
from .core import ToolCaller


@asynccontextmanager
async def connect_mcp(cfg: Config):
    from mcp import ClientSession, StdioServerParameters

    if cfg.mcp_server_url:
        # The remote server must expose this project's tools (it is not compatible with draw.io's official MCP).
        try:
            from mcp.client.streamable_http import streamablehttp_client as http_client
        except ImportError:  # newer SDK naming
            from mcp.client.streamable_http import streamable_http_client as http_client
        async with http_client(cfg.mcp_server_url) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        from mcp.client.stdio import stdio_client

        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "DRAWIO_WORKSPACE": str(cfg.workspace.resolve())}
        if cfg.shape_catalog:
            env["SHAPE_CATALOG"] = str(cfg.shape_catalog.resolve())
        if cfg.notations_dir:
            env["NOTATIONS_DIR"] = str(cfg.notations_dir.resolve())
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "drawio_agent.server", "--transport", "stdio"],
            env=env, cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def make_tool_caller(session) -> ToolCaller:
    """Return a function that calls an MCP tool and returns (result_text, is_error)."""
    async def call(name: str, args: dict) -> tuple[str, bool]:
        try:
            result = await session.call_tool(name, args)
        except Exception as exc:  # transport/protocol errors are reported to the model, not raised
            return f"{type(exc).__name__}: {exc}", True
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        if not text and getattr(result, "structuredContent", None) is not None:
            text = json.dumps(result.structuredContent)
        return text, bool(result.isError)
    return call

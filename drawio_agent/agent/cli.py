"""Command line entry point of the agent."""
from __future__ import annotations

import argparse
import asyncio

from ..config import load_config
from .core import ChatAgent, NotationError
from .mcp_client import connect_mcp, make_tool_caller
from .repl import Repl, print_tool
from .schema import mcp_tool_to_openai


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create and edit draw.io diagrams in natural language")
    p.add_argument("--diagram", help="diagram to open at start-up")
    p.add_argument("--notation", help="constrain the agent to a notation: archimate, uml, azure (or 'none'). "
                                      "Without it the agent follows the diagram's own notation, or is generic.")
    return p


async def amain() -> None:
    args = build_parser().parse_args()
    from ..llm import create_llm_client

    cfg = load_config()  # Read configuration from .env and environment variables
    llm, model = create_llm_client(cfg)
    async with connect_mcp(cfg) as session:
        listed = await session.list_tools()
        tools = [mcp_tool_to_openai(t.name, t.description, t.inputSchema) for t in listed.tools]
        if not tools:
            print("No tools available from the MCP server. Check the server logs.")
            return
        agent = ChatAgent(llm, model, tools, make_tool_caller(session), temperature=cfg.temperature,
                          max_tool_rounds=cfg.max_tool_rounds, on_tool=print_tool)
        print(f"Connected to MCP server ({len(tools)} tools). Workspace: {cfg.workspace.resolve()}")
        if args.notation:
            try:
                print(await agent.set_notation(args.notation))
            except NotationError as exc:
                print(f"Error: {exc}")
                return
        await Repl(agent).run(args.diagram)


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass

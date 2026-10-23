"""Interactive natural-language agent: MCP client + LLM tool-calling loop.

Run:  python -m drawio_agent.agent [--diagram NAME] [--notation archimate|uml|azure]
"""
from .core import ChatAgent, NotationError, ToolCaller
from .policy import NOTATION_TOOLS, ToolPolicy
from .prompts import PromptComposer
from .schema import inline_refs, mcp_tool_to_openai

__all__ = [
    "ChatAgent", "NotationError", "ToolCaller", "ToolPolicy", "NOTATION_TOOLS", "PromptComposer",
    "inline_refs", "mcp_tool_to_openai", "main",
]


def main() -> None:  # lazy import: the CLI pulls in the MCP SDK and the OpenAI client
    from .cli import main as _main

    _main()

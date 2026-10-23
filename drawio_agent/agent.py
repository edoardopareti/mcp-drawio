"""Interactive natural-language agent: an MCP client + LLM tool-calling loop with conversation memory.

Run:  python -m drawio_agent.agent [--diagram NAME]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from .config import PROJECT_ROOT, Config, load_config

SYSTEM_PROMPT = """You are a diagramming assistant. You create and edit draw.io diagrams ONLY through the provided tools.

Working rules:
- Work on the ACTIVE diagram unless the user explicitly names another one. Its current content is given to you in context at every turn.
- If there is no active diagram and the user asks for a diagram, call create_diagram first (short snake_case name), then add content.
- Prefer incremental edits (add_nodes, update_nodes, remove_nodes, add_edges, update_edges, remove_edges, upsert_groups). Use replace_diagram only for a brand-new large diagram or a complete redesign.
- Reuse existing node/edge ids exactly as they appear in the current state. New ids are short snake_case.
- Batch related changes into as few tool calls as possible (the list arguments accept many items).
- Use groups for layers, boundaries, zones or swimlanes the user mentions.
- Pick the node kind that best matches the concept: actor, service, agent, database, storage, external, process, decision, document, queue, note.
- Do not invent components or relationships the user did not ask for. If a request is ambiguous in a way that changes the result materially, ask one short question instead of guessing.
- "Undo"/"go back" requests: call undo. Layout complaints ("messy", "rearrange"): call relayout or change direction.
- If a tool returns an error, correct the arguments and retry, or explain the problem.
- After making changes, answer with a short summary of what changed. Never print XML."""

HELP = """Commands:
  /new <name> [title]   create a diagram and make it active
  /open <name>          switch the active diagram
  /import <path> [name] import an existing .drawio file and make it active
  /list                 list diagrams in the workspace
  /show                 print the active diagram's content
  /undo                 undo the last change
  /layout               re-arrange automatically
  /view                 open the .drawio file with the default application
  /reset                clear the conversation memory (diagrams are kept)
  /help                 this help
  /exit                 quit
Anything else is sent to the agent, e.g. "add a Redis cache between the API and the database"."""

ToolCaller = Callable[[str, dict], Awaitable[tuple[str, bool]]]


# ---------------- MCP schema -> OpenAI tool ----------------
def inline_refs(schema: dict) -> dict:
    """
    Inline local $ref/$defs so the schema
    works with every OpenAI-compatible endpoint.
    """
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


def mcp_tool_to_openai(
        name: str,
        description: str | None,
        input_schema: dict) -> dict:
    """
    Convert an MCP tool description to an OpenAI-compatible function spec.
    """
    return {"type": "function", "function": {
        "name": name,
        "description": (description or "").strip()[:1024],
        "parameters": inline_refs(input_schema or {}),
    }}


# ---------------- the agent ----------------
class ChatAgent:
    def __init__(self, llm: Any, model: str, tools: list[dict], call_tool: ToolCaller, *,
                 temperature: float | None = None, max_tool_rounds: int = 12, max_turns_kept: int = 20,
                 on_tool: Callable[[str, dict, str, bool], None] | None = None):
        self.llm, self.model, self.tools, self.call_tool = llm, model, tools, call_tool
        self.temperature, self.max_tool_rounds, self.max_turns_kept = temperature, max_tool_rounds, max_turns_kept
        self.on_tool = on_tool
        self.history: list[dict] = []
        self.active: str | None = None
        self._takes_diagram = {
            t["function"]["name"] for t in tools if "diagram" in t["function"]["parameters"].get("properties", {})
        }

    async def _context(self) -> str:
        if not self.active:
            return "There is no active diagram yet."
        text, err = await self.call_tool("get_diagram", {"diagram": self.active})
        if err:
            return f"The active diagram '{self.active}' could not be loaded: {text}"
        return f"Active diagram: '{self.active}'. Current state (JSON):\n{text}"

    def _track_active(self, tool: str, result_text: str) -> None:
        if tool == "list_diagrams":
            return
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict) and isinstance(data.get("diagram"), str):
            self.active = data["diagram"]

    def _trim(self) -> None:
        user_idx = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(user_idx) > self.max_turns_kept:
            self.history = self.history[user_idx[-self.max_turns_kept]:]

    def _create(self, messages: list[dict]):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": self.tools,
            "tool_choice": "auto"}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return self.llm.chat.completions.create(**kwargs)

    async def ask(self, text: str) -> str:
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        try:
            for _ in range(self.max_tool_rounds):
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": await self._context()}
                    ] + self.history
                response = await asyncio.to_thread(self._create, messages)
                msg = response.choices[0].message
                tool_calls = list(msg.tool_calls or [])
                assistant: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if tool_calls:
                    assistant["tool_calls"] = [
                        {"id": tc.id,
                         "type": "function",
                         "function": {
                             "name": tc.function.name,
                             "arguments": tc.function.arguments or "{}"
                             }
                         } for tc in tool_calls]
                self.history.append(assistant)
                if not tool_calls:
                    self._trim()
                    return msg.content or "(no answer)"
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                    except Exception as exc:
                        result, is_error = f"Invalid JSON arguments: {exc}", True
                        args = {}
                    else:
                        if name in self._takes_diagram and "diagram" not in args and self.active:
                            args["diagram"] = self.active
                        result, is_error = await self.call_tool(name, args)
                        if not is_error:
                            self._track_active(name, result)
                    if self.on_tool:
                        self.on_tool(name, args, result, is_error)
                    self.history.append(
                        {"role": "tool",
                         "tool_call_id": tc.id,
                         "content": ("ERROR: " + result) if is_error else result})
            self._trim()
            return f"Stopped after {self.max_tool_rounds} tool rounds. The changes made so far are saved; tell me how to continue."
        except Exception:
            del self.history[start:]  # keep the conversation consistent if the LLM call failed
            raise

    def reset(self) -> None:
        self.history.clear()


# ---------------- MCP connection ----------------
@asynccontextmanager
async def connect_mcp(cfg: Config):
    """
    Connect to the MCP server,
    either via a URL or by spawning a local stdio server
    """
    from mcp import ClientSession, StdioServerParameters

    if cfg.mcp_server_url:
        # Connect to an existing MCP server via streamable HTTP.
        # NOTE: This MCPP server must expose the same tools
        # as the local stdio server, otherwise the agent will fail.
        # So, currently, the agent only works with the local stdio server,
        # but not with official MCP servers provided by draw.io.
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
        # Spawn a local MCP server and connect to it via stdio.
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "drawio_agent.server", "--transport", "stdio"],
            env={**os.environ,
                 "PYTHONPATH": str(PROJECT_ROOT),
                 "DRAWIO_WORKSPACE": str(cfg.workspace.resolve())},
            cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def make_tool_caller(session) -> ToolCaller:
    """
    Return a function that calls an MCP tool and returns (result_text, is_error).
    """
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


# ---------------- REPL ----------------
def _open_file(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _print_tool(name: str, args: dict, result: str, is_error: bool) -> None:
    shown = {k: v for k, v in args.items() if k != "diagram"}
    brief = json.dumps(shown, ensure_ascii=False)
    if len(brief) > 140:
        brief = brief[:137] + "..."
    if is_error:
        print(f"   x {name} {brief}\n     {result[:300]}")
    else:
        print(f"   - {name} {brief}")


async def _command(agent: ChatAgent, line: str) -> bool:
    """Handle a /command. Returns False when the user wants to quit."""
    parts = line.split()
    cmd, rest = parts[0].lower(), parts[1:]

    async def run(tool: str, args: dict) -> dict | None:
        text, err = await agent.call_tool(tool, args)
        if err:
            print(f"Error: {text}")
            return None
        agent._track_active(tool, text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(text)
            return None

    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/reset":
        agent.reset()
        print("Conversation memory cleared.")
    elif cmd == "/list":
        data = await run("list_diagrams", {})
        if data:
            for d in data["diagrams"]:
                mark = "*" if d.get("diagram") == agent.active else " "
                print(f" {mark} {d.get('diagram')}: {d.get('title') or ''} ({d.get('nodes', '?')} nodes, {d.get('edges', '?')} edges)")
            if not data["diagrams"]:
                print("No diagrams yet.")
    elif cmd == "/new" and rest:
        data = await run("create_diagram", {"diagram": rest[0], "title": " ".join(rest[1:])})
        if data:
            print(f"Active diagram: {agent.active} -> {data['file']}")
    elif cmd == "/open" and rest:
        data = await run("get_diagram", {"diagram": rest[0]})
        if data:
            agent.active = data["name"]
            print(f"Active diagram: {agent.active} ({len(data.get('nodes', []))} nodes) -> {data['file']}")
    elif cmd == "/import" and rest:
        args = {"path": rest[0]}
        if len(rest) > 1:
            args["diagram"] = rest[1]
        data = await run("import_drawio", args)
        if data:
            print(f"{data['message']}\nActive diagram: {agent.active}")
    elif cmd in ("/show", "/undo", "/layout", "/view"):
        if not agent.active:
            print("No active diagram. Use /new, /open or just describe a diagram.")
            return True
        if cmd == "/show":
            data = await run("get_diagram", {"diagram": agent.active})
            if data:
                print(json.dumps(data, indent=2, ensure_ascii=False))
        elif cmd == "/undo":
            data = await run("undo", {"diagram": agent.active})
            if data:
                print(data["message"])
        elif cmd == "/layout":
            data = await run("relayout", {"diagram": agent.active})
            if data:
                print(data["message"])
        else:
            data = await run("get_diagram", {"diagram": agent.active})
            if data:
                _open_file(data["file"])
                print(f"Opened {data['file']}")
    else:
        print("Unknown or incomplete command. Type /help.")
    return True


async def repl(agent: ChatAgent, initial: str | None) -> None:
    """
    Read-eval-print loop for the agent.
    """

    print("draw.io natural-language agent. Type /help for commands, /exit to quit.")

    if initial:
        await _command(agent, f"/open {initial}")
    while True:
        try:
            line = (await asyncio.to_thread(input, f"\n[{agent.active or 'no diagram'}] you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.startswith("/"):
            if not await _command(agent, line):
                return
            continue
        try:
            answer = await agent.ask(line)
        except Exception as exc:
            print(f"LLM error: {type(exc).__name__}: {exc}")
            continue
        print(f"\nagent> {answer}")
        if agent.active:
            text, err = await agent.call_tool("get_diagram", {"diagram": agent.active})
            if not err:
                print(f"       file: {json.loads(text)['file']}")


async def amain() -> None:
    """
    Main entry point for the draw.io agent.
    """

    parser = argparse.ArgumentParser(
        description="Create and edit draw.io diagrams in natural language")

    parser.add_argument(
        "--diagram", help="diagram to open at start-up")

    args = parser.parse_args()

    from .llm import create_llm_client

    cfg = load_config()  # Read configuration from .env and environment variables
    llm, model = create_llm_client(cfg)  # Create an LLM client based on the configuration
    async with connect_mcp(cfg) as session:
        listed = await session.list_tools()  # Get the list of available tools from the MCP server
        tools = [
            mcp_tool_to_openai(
                t.name, t.description, t.inputSchema) for t in listed.tools]  # Convert MCP tools to OpenAI-compatible function specifications
        if not tools:
            print("No tools available from the MCP server. Check the server logs.")
            return
        agent = ChatAgent(
            llm, model,
            tools, make_tool_caller(session),
            temperature=cfg.temperature, max_tool_rounds=cfg.max_tool_rounds,
            on_tool=_print_tool)
        print(
            f"Connected to MCP server ({len(tools)} tools). Workspace: {cfg.workspace.resolve()}")
        await repl(agent, args.diagram)


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

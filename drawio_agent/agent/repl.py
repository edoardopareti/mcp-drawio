"""Interactive command line: slash commands plus free text sent to the agent."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable

from .core import ChatAgent, NotationError

HELP = """Commands:
  /new <name> [title]   create a diagram (with the active notation) and make it active
  /open <name>          switch the active diagram
  /import <path> [name] import an existing .drawio file and make it active
  /list                 list diagrams in the workspace
  /show                 print the active diagram's content
  /undo                 undo the last change
  /layout               re-arrange automatically
  /view                 open the .drawio file with the default application
  /notation [name]      show or set the notation (archimate, uml, azure, none)
  /notations            list the available notations
  /shapes <query>       search the shape catalogue (within the active notation)
  /libraries            list the shape libraries
  /reset                clear the conversation memory (diagrams are kept)
  /help                 this help
  /exit                 quit
Anything else is sent to the agent, e.g. "add a Redis cache between the API and the database"."""


def open_file(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def print_tool(name: str, args: dict, result: str, is_error: bool) -> None:
    shown = {k: v for k, v in args.items() if k != "diagram"}
    brief = json.dumps(shown, ensure_ascii=False)
    if len(brief) > 140:
        brief = brief[:137] + "..."
    if is_error:
        print(f"   x {name} {brief}\n     {result[:300]}")
    else:
        print(f"   - {name} {brief}")


class Repl:
    def __init__(self, agent: ChatAgent):
        self.agent = agent
        self._commands: dict[str, Callable[[list[str]], Awaitable[None]]] = {
            "/help": self._help, "/reset": self._reset, "/list": self._list, "/new": self._new,
            "/open": self._open, "/import": self._import, "/show": self._show, "/undo": self._undo,
            "/layout": self._layout, "/view": self._view, "/notation": self._notation,
            "/notations": self._notations, "/shapes": self._shapes, "/libraries": self._libraries,
        }

    # ---------------- helpers ----------------
    async def _run(self, tool: str, args: dict) -> dict | None:
        text, err = await self.agent.call_tool(tool, args)
        if err:
            print(f"Error: {text}")
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(text)
            return None

    async def _activate(self, name: str) -> None:
        message = await self.agent.activate(name)
        if message:
            print(message)

    def _require_active(self) -> bool:
        if not self.agent.active:
            print("No active diagram. Use /new, /open or just describe a diagram.")
        return bool(self.agent.active)

    def prompt(self) -> str:
        notation = f"|{self.agent.notation}" if self.agent.notation else ""
        return f"\n[{self.agent.active or 'no diagram'}{notation}] you> "

    # ---------------- commands ----------------
    async def _help(self, _: list[str]) -> None:
        print(HELP)

    async def _reset(self, _: list[str]) -> None:
        self.agent.reset()
        print("Conversation memory cleared.")

    async def _list(self, _: list[str]) -> None:
        data = await self._run("list_diagrams", {})
        if not data:
            return
        for d in data["diagrams"]:
            mark = "*" if d.get("diagram") == self.agent.active else " "
            notation = f" [{d['notation']}]" if d.get("notation") else ""
            print(f" {mark} {d.get('diagram')}{notation}: {d.get('title') or ''} "
                  f"({d.get('nodes', '?')} nodes, {d.get('edges', '?')} edges)")
        if not data["diagrams"]:
            print("No diagrams yet.")

    async def _new(self, rest: list[str]) -> None:
        if not rest:
            print("Usage: /new <name> [title]")
            return
        args = {"diagram": rest[0], "title": " ".join(rest[1:])}
        if self.agent.notation:
            args["notation"] = self.agent.notation
        data = await self._run("create_diagram", args)
        if data:
            await self._activate(data["diagram"])
            print(f"Active diagram: {self.agent.active} -> {data['file']}")

    async def _open(self, rest: list[str]) -> None:
        if not rest:
            print("Usage: /open <name>")
            return
        data = await self._run("get_diagram", {"diagram": rest[0]})
        if data:
            await self._activate(data["name"])
            print(f"Active diagram: {self.agent.active} ({len(data.get('nodes', []))} nodes) -> {data['file']}")

    async def _import(self, rest: list[str]) -> None:
        if not rest:
            print("Usage: /import <path> [name]")
            return
        args = {"path": rest[0]}
        if len(rest) > 1:
            args["diagram"] = rest[1]
        data = await self._run("import_drawio", args)
        if data:
            print(data["message"])
            await self._activate(data["diagram"])
            print(f"Active diagram: {self.agent.active}")

    async def _show(self, _: list[str]) -> None:
        if self._require_active() and (data := await self._run("get_diagram", {"diagram": self.agent.active})):
            print(json.dumps(data, indent=2, ensure_ascii=False))

    async def _undo(self, _: list[str]) -> None:
        if self._require_active() and (data := await self._run("undo", {"diagram": self.agent.active})):
            print(data["message"])

    async def _layout(self, _: list[str]) -> None:
        if self._require_active() and (data := await self._run("relayout", {"diagram": self.agent.active})):
            print(data["message"])

    async def _view(self, _: list[str]) -> None:
        if self._require_active() and (data := await self._run("get_diagram", {"diagram": self.agent.active})):
            open_file(data["file"])
            print(f"Opened {data['file']}")

    async def _notation(self, rest: list[str]) -> None:
        if not rest:
            state = self.agent.notation_state
            origin = " (chosen by you)" if state.forced else " (from the diagram)" if state.name else ""
            print(f"Notation: {state.title or 'generic (no notation)'}{origin}")
            return
        try:
            print(await self.agent.set_notation(rest[0]))
        except NotationError as exc:
            print(f"Error: {exc}")

    async def _notations(self, _: list[str]) -> None:
        data = await self._run("list_notations", {})
        for n in (data or {}).get("notations", []):
            print(f"  {n['name']:<10} {n['title']}: {n['shapes']} shapes, {len(n['relations'])} relation types "
                  f"(libraries: {', '.join(n['libraries'])})")

    async def _shapes(self, rest: list[str]) -> None:
        if not rest:
            print("Usage: /shapes <query>")
            return
        args = {"query": " ".join(rest), "limit": 15}
        if self.agent.notation:
            args["notation"] = self.agent.notation
        data = await self._run("search_shapes", args)
        for r in (data or {}).get("results", []):
            print(f"  {r['key']:<50} {r['name']} [{r['category']}]" + (" (container)" if r["element_type"] == "container" else ""))
        if data and not data.get("results"):
            print("  no match")

    async def _libraries(self, _: list[str]) -> None:
        args = {"notation": self.agent.notation} if self.agent.notation else {}
        data = await self._run("list_shape_libraries", args)
        for lib in (data or {}).get("libraries", []):
            print(f"  {lib['name']:<22} {lib['shapes']:>4} shapes {lib['containers']:>3} containers "
                  f"(sources: {', '.join(lib['sources'])})")

    # ---------------- loop ----------------
    async def handle_command(self, line: str) -> bool:
        """Handle a /command. Returns False when the user wants to quit."""
        parts = line.split()
        cmd, rest = parts[0].lower(), parts[1:]
        if cmd in ("/exit", "/quit"):
            return False
        handler = self._commands.get(cmd)
        if handler is None:
            print("Unknown command. Type /help.")
        else:
            await handler(rest)
        return True

    async def run(self, initial_diagram: str | None = None) -> None:
        print("draw.io natural-language agent. Type /help for commands, /exit to quit.")
        if initial_diagram:
            await self.handle_command(f"/open {initial_diagram}")
        while True:
            try:
                line = (await asyncio.to_thread(input, self.prompt())).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.startswith("/"):
                if not await self.handle_command(line):
                    return
                continue
            try:
                answer = await self.agent.ask(line)
            except Exception as exc:
                print(f"LLM error: {type(exc).__name__}: {exc}")
                continue
            print(f"\nagent> {answer}")
            if self.agent.active:
                text, err = await self.agent.call_tool("get_diagram", {"diagram": self.agent.active})
                if not err:
                    print(f"       file: {json.loads(text)['file']}")

"""The conversational agent: LLM tool-calling loop with memory, active diagram and active notation."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .policy import ToolPolicy
from .prompts import PromptComposer

ToolCaller = Callable[[str, dict], Awaitable[tuple[str, bool]]]
ToolObserver = Callable[[str, dict, str, bool], None]

_NONE = {"", "none", "generic", "default"}


class NotationError(ValueError):
    pass


@dataclass
class NotationState:
    """Effective notation of the agent. 'forced' means chosen by the user (--notation or /notation)."""
    name: str | None = None
    title: str | None = None
    guide: str | None = None
    forced: bool = False


class ChatAgent:
    def __init__(self, llm: Any, model: str, tools: list[dict], call_tool: ToolCaller, *,
                 temperature: float | None = None, max_tool_rounds: int = 12, max_turns_kept: int = 20,
                 on_tool: ToolObserver | None = None, prompts: PromptComposer | None = None):
        self.llm, self.model, self.call_tool = llm, model, call_tool
        self.temperature, self.max_tool_rounds, self.max_turns_kept = temperature, max_tool_rounds, max_turns_kept
        self.on_tool = on_tool
        self.prompts = prompts or PromptComposer()
        self.policy = ToolPolicy(tools)
        self.history: list[dict] = []
        self.active: str | None = None
        self.notation_state = NotationState()

    # ---------------- properties ----------------
    @property
    def tools(self) -> list[dict]:
        return self.policy.visible_tools(self.notation)

    @property
    def notation(self) -> str | None:
        return self.notation_state.name

    def system_prompt(self) -> str:
        s = self.notation_state
        return self.prompts.compose(s.title, s.guide)

    # ---------------- tool helpers ----------------
    async def _call_json(self, name: str, args: dict) -> dict:
        text, err = await self.call_tool(name, args)
        if err:
            raise NotationError(text)
        return json.loads(text)

    async def _load_guide(self, notation: str) -> NotationState:
        data = await self._call_json("get_notation_guide", {"notation": notation})
        return NotationState(name=data["notation"], title=data.get("title"), guide=data.get("guide"))

    # ---------------- notation management ----------------
    async def set_notation(self, notation: str | None) -> str:
        """User choice (CLI flag or /notation). Applies to the agent and to the active diagram."""
        if notation is None or notation.strip().lower() in _NONE:
            self.notation_state = NotationState(forced=True)
        else:
            state = await self._load_guide(notation)
            state.forced = True
            self.notation_state = state
        message = f"Notation: {self.notation_state.title or 'generic (no notation)'}."
        if self.active:
            message += " " + await self._apply_forced_notation()
        return message

    async def _apply_forced_notation(self) -> str:
        data = await self._call_json("get_diagram", {"diagram": self.active})
        if data.get("notation") == self.notation:
            return ""
        result = await self._call_json("set_diagram_properties",
                                       {"diagram": self.active, "notation": self.notation or "none"})
        return result.get("message", "")

    async def sync_notation(self) -> str:
        """Called when the active diagram changes: enforce a forced notation or adopt the diagram's own."""
        if not self.active:
            return ""
        if self.notation_state.forced:
            return await self._apply_forced_notation()
        data = await self._call_json("get_diagram", {"diagram": self.active})
        diagram_notation = data.get("notation")
        if diagram_notation == self.notation:
            return ""
        self.notation_state = await self._load_guide(diagram_notation) if diagram_notation else NotationState()
        return f"Using the diagram's notation: {self.notation_state.title or 'generic'}."

    async def activate(self, diagram: str) -> str:
        self.active = diagram
        try:
            return await self.sync_notation()
        except NotationError as exc:
            return f"Notation not applied: {exc}"

    # ---------------- conversation ----------------
    async def _context(self) -> str:
        if not self.active:
            return "There is no active diagram yet."
        text, err = await self.call_tool("get_diagram", {"diagram": self.active})
        if err:
            return f"The active diagram '{self.active}' could not be loaded: {text}"
        return f"Active diagram: '{self.active}'. Current state (JSON):\n{text}"

    def _diagram_from_result(self, tool: str, result_text: str) -> str | None:
        if tool in ("list_diagrams", "list_notations", "get_notation_guide", "search_shapes", "list_shape_libraries"):
            return None
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return None
        name = data.get("diagram") if isinstance(data, dict) else None
        return name if isinstance(name, str) else None

    async def track_result(self, tool: str, result_text: str) -> None:
        """Follow the diagram a tool worked on; re-sync the notation when the active diagram changes."""
        name = self._diagram_from_result(tool, result_text)
        if name and name != self.active:
            await self.activate(name)

    def _trim(self) -> None:
        user_idx = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(user_idx) > self.max_turns_kept:
            self.history = self.history[user_idx[-self.max_turns_kept]:]

    def _create(self, messages: list[dict]):
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "tools": self.tools, "tool_choice": "auto"}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return self.llm.chat.completions.create(**kwargs)

    async def _run_tool_call(self, tc) -> tuple[str, dict, str, bool]:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except Exception as exc:
            return name, {}, f"Invalid JSON arguments: {exc}", True
        args = self.policy.complete(name, args, active=self.active, notation=self.notation,
                                    notation_forced=self.notation_state.forced)
        result, is_error = await self.call_tool(name, args)
        if not is_error:
            await self.track_result(name, result)
        return name, args, result, is_error

    async def ask(self, text: str) -> str:
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        try:
            for _ in range(self.max_tool_rounds):
                messages = [{"role": "system", "content": self.system_prompt()},
                            {"role": "system", "content": await self._context()}] + self.history
                response = await asyncio.to_thread(self._create, messages)
                msg = response.choices[0].message
                tool_calls = list(msg.tool_calls or [])
                assistant: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if tool_calls:
                    assistant["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                        "name": tc.function.name, "arguments": tc.function.arguments or "{}"}} for tc in tool_calls]
                self.history.append(assistant)
                if not tool_calls:
                    self._trim()
                    return msg.content or "(no answer)"
                for tc in tool_calls:
                    name, args, result, is_error = await self._run_tool_call(tc)
                    if self.on_tool:
                        self.on_tool(name, args, result, is_error)
                    self.history.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": ("ERROR: " + result) if is_error else result})
            self._trim()
            return (f"Stopped after {self.max_tool_rounds} tool rounds. "
                    "The changes made so far are saved; tell me how to continue.")
        except Exception:
            del self.history[start:]  # keep the conversation consistent if the LLM call failed
            raise

    def reset(self) -> None:
        self.history.clear()

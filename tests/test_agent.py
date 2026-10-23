"""Conversational loop with a scripted fake LLM and the real diagram service."""
import asyncio
import json
from types import SimpleNamespace

from conftest import make_dispatcher

from drawio_agent.agent import NOTATION_TOOLS, ChatAgent, PromptComposer, inline_refs, mcp_tool_to_openai
from drawio_agent.models import NodeInput

V3_SYSTEM_PROMPT = """You are a diagramming assistant. You create and edit draw.io diagrams ONLY through the provided tools.

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


def tool(name, props):
    return mcp_tool_to_openai(name, name, {"type": "object", "properties": props})


D = {"diagram": {"type": "string"}}
TOOLS = [tool(n, D) for n in ("get_diagram", "create_diagram", "add_nodes", "update_nodes", "add_edges",
                              "remove_nodes", "undo", "set_diagram_properties", "replace_diagram")]
TOOLS += [tool(n, {"notation": {"type": "string"}, "query": {"type": "string"}}) for n in sorted(NOTATION_TOOLS)]


def tc(i, name, args):
    return SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLLM:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content, calls = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def run(coro):
    return asyncio.run(coro)


def test_generic_mode_is_backward_compatible(svc, dispatcher):
    call, _ = dispatcher
    llm = FakeLLM([("ok", None)])
    agent = ChatAgent(llm, "m", TOOLS, call)
    assert PromptComposer().compose() == V3_SYSTEM_PROMPT
    run(agent.ask("hi"))
    request = llm.requests[0]
    assert request["messages"][0]["content"] == V3_SYSTEM_PROMPT
    assert not {t["function"]["name"] for t in request["tools"]} & NOTATION_TOOLS


def test_multi_turn_conversation(svc, dispatcher):
    call, calls = dispatcher
    llm = FakeLLM([
        (None, [tc(1, "create_diagram", {"diagram": "shop", "title": "Shop"})]),
        (None, [tc(2, "add_nodes", {"nodes": [{"id": "api", "label": "API"}, {"id": "db", "label": "DB", "kind": "database"}]}),
                tc(3, "add_edges", {"edges": [{"source": "api", "target": "db", "label": "reads"}]})]),
        ("Created the shop diagram.", None),
        (None, [tc(4, "add_edges", {"edges": [{"source": "api", "target": "cache"}]})]),
        (None, [tc(5, "add_nodes", {"nodes": [{"id": "cache", "label": "Redis", "kind": "database"}]}),
                tc(6, "add_edges", {"edges": [{"source": "api", "target": "cache"}]})]),
        ("Added a Redis cache.", None),
        (None, [tc(7, "undo", {})]),
        ("Undone.", None),
    ])
    agent = ChatAgent(llm, "m", TOOLS, call)
    assert run(agent.ask("Draw an API reading a DB")) == "Created the shop diagram."
    assert agent.active == "shop" and agent.notation is None
    assert run(agent.ask("add a redis cache used by the api")) == "Added a Redis cache."
    edits = [c for c in calls if c[0] != "get_diagram"]
    assert edits[-1] == ("add_edges", {"edges": [{"source": "api", "target": "cache"}], "diagram": "shop"})
    errors = [m for m in agent.history if m["role"] == "tool" and m["content"].startswith("ERROR")]
    assert len(errors) == 1 and "cache" in errors[0]["content"]
    assert "Active diagram: 'shop'" in llm.requests[3]["messages"][1]["content"]
    run(agent.ask("undo that"))
    assert len(svc.get_diagram("shop")["edges"]) == 1
    for i, m in enumerate(agent.history):  # valid tool-call ordering
        if m["role"] == "tool":
            prev = next(x for x in reversed(agent.history[:i]) if x["role"] == "assistant")
            assert any(t["id"] == m["tool_call_id"] for t in prev["tool_calls"])


def test_forced_notation_constrains_prompt_tools_and_arguments(svc, dispatcher):
    call, calls = dispatcher
    llm = FakeLLM([
        (None, [tc(1, "search_shapes", {"query": "key vault", "notation": "uml"}),
                tc(2, "create_diagram", {"diagram": "az", "notation": "uml"})]),
        (None, [tc(3, "add_nodes", {"nodes": [{"id": "kv", "label": "Key Vault", "shape": "azure2.key_vaults"}]})]),
        ("Done.", None),
    ])
    agent = ChatAgent(llm, "m", TOOLS, call)
    run(agent.set_notation("azure"))
    assert agent.notation == "azure" and agent.notation_state.forced
    run(agent.ask("draw a key vault"))
    first = llm.requests[0]
    assert "Azure architecture" in first["messages"][0]["content"]
    assert "Do not use the generic 'kind' field" in first["messages"][0]["content"]
    assert NOTATION_TOOLS <= {t["function"]["name"] for t in first["tools"]}
    sent = {name: args for name, args in calls if name in ("search_shapes", "create_diagram")}
    assert sent["search_shapes"]["notation"] == "azure" and sent["create_diagram"]["notation"] == "azure"
    assert svc.get_diagram("az")["notation"] == "azure"


def test_forced_notation_is_applied_to_opened_diagrams(svc, dispatcher):
    call, _ = dispatcher
    svc.create_diagram("legacy")
    svc.add_nodes("legacy", [NodeInput(id="a", label="A")])
    agent = ChatAgent(FakeLLM([]), "m", TOOLS, call)
    run(agent.set_notation("archimate"))
    message = run(agent.activate("legacy"))
    assert "do not follow the archimate notation" in message
    assert svc.get_diagram("legacy")["notation"] == "archimate"
    run(agent.set_notation("none"))
    assert agent.notation is None and "notation" not in svc.get_diagram("legacy")


def test_agent_adopts_the_diagram_notation_when_not_forced(svc, dispatcher):
    call, _ = dispatcher
    svc.create_diagram("cls", notation="uml")
    svc.create_diagram("plain")
    agent = ChatAgent(FakeLLM([]), "m", TOOLS, call)
    run(agent.activate("cls"))
    assert agent.notation == "uml" and not agent.notation_state.forced
    assert "uml.class" in agent.system_prompt()
    run(agent.activate("plain"))
    assert agent.notation is None and agent.system_prompt() == V3_SYSTEM_PROMPT


def test_failed_llm_call_rolls_back_history(svc, dispatcher):
    call, _ = dispatcher

    class Boom(FakeLLM):
        def create(self, **kwargs):
            raise RuntimeError("network down")

    agent = ChatAgent(Boom([]), "m", TOOLS, call)
    try:
        run(agent.ask("hello"))
    except RuntimeError:
        pass
    assert agent.history == []


def test_inline_refs():
    schema = {"type": "object", "properties": {"nodes": {"type": "array", "items": {"$ref": "#/$defs/N"}}},
              "$defs": {"N": {"type": "object", "properties": {"id": {"type": "string"}}}}}
    out = inline_refs(schema)
    assert "$defs" not in out and out["properties"]["nodes"]["items"]["properties"]["id"]["type"] == "string"

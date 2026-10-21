"""End-to-end test of the conversational loop with a scripted fake LLM and the real diagram service."""
import asyncio
import json
from types import SimpleNamespace

from drawio_agent.agent import ChatAgent, inline_refs, mcp_tool_to_openai
from drawio_agent import models
from drawio_agent.service import DiagramError

def dispatcher(svc):
    convert = {
        "add_nodes": ("nodes", models.NodeInput), "update_nodes": ("updates", models.NodeUpdate),
        "add_edges": ("edges", models.EdgeInput), "update_edges": ("updates", models.EdgeUpdate),
        "upsert_groups": ("groups", models.GroupInput),
    }
    calls = []

    async def call(name, args):
        calls.append((name, dict(args)))
        args = dict(args)
        if name in convert:
            key, model = convert[name]
            args[key] = [model(**x) for x in args[key]]
        try:
            return json.dumps(getattr(svc, name)(**args)), False
        except DiagramError as exc:
            return str(exc), True
    return call, calls


def tool(name, props):
    return mcp_tool_to_openai(name, name, {"type": "object", "properties": props})


TOOLS = [tool(n, {"diagram": {"type": "string"}}) for n in
         ("get_diagram", "create_diagram", "add_nodes", "update_nodes", "add_edges", "remove_nodes", "undo")]


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


def test_multi_turn_conversation(svc):
    call, calls = dispatcher(svc)
    llm = FakeLLM([
        # turn 1: create + populate
        (None, [tc(1, "create_diagram", {"diagram": "shop", "title": "Shop"})]),
        (None, [tc(2, "add_nodes", {"nodes": [{"id": "api", "label": "API"}, {"id": "db", "label": "DB", "kind": "database"}]}),
                tc(3, "add_edges", {"edges": [{"source": "api", "target": "db", "label": "reads"}]})]),
        ("Created the shop diagram.", None),
        # turn 2: edit with a first wrong call, then a correction (diagram arg omitted -> injected)
        (None, [tc(4, "add_edges", {"edges": [{"source": "api", "target": "cache"}]})]),
        (None, [tc(5, "add_nodes", {"nodes": [{"id": "cache", "label": "Redis", "kind": "database"}]}),
                tc(6, "add_edges", {"edges": [{"source": "api", "target": "cache"}]})]),
        ("Added a Redis cache.", None),
        # turn 3: undo
        (None, [tc(7, "undo", {})]),
        ("Undone.", None),
    ])
    agent = ChatAgent(llm, "m", TOOLS, call)

    assert asyncio.run(agent.ask("Draw an API reading a DB")) == "Created the shop diagram."
    assert agent.active == "shop"
    assert asyncio.run(agent.ask("add a redis cache used by the api")) == "Added a Redis cache."
    edits = [c for c in calls if c[0] != "get_diagram"]  # get_diagram = state refresh before each LLM round
    assert edits[-1] == ("add_edges", {"edges": [{"source": "api", "target": "cache"}], "diagram": "shop"})
    errors = [m for m in agent.history if m["role"] == "tool" and m["content"].startswith("ERROR")]
    assert len(errors) == 1 and "cache" in errors[0]["content"]
    d = svc.get_diagram("shop")
    assert {n["id"] for n in d["nodes"]} == {"api", "db", "cache"} and len(d["edges"]) == 2

    # the current state is provided to the model on each turn
    ctx = llm.requests[3]["messages"][1]["content"]
    assert "Active diagram: 'shop'" in ctx and '"api"' in ctx

    asyncio.run(agent.ask("undo that"))
    assert len(svc.get_diagram("shop")["edges"]) == 1

    # history keeps the whole conversation in valid tool-call order
    roles = [m["role"] for m in agent.history]
    assert roles.count("user") == 3
    for i, m in enumerate(agent.history):
        if m["role"] == "tool":
            prev_assistant = next(x for x in reversed(agent.history[:i]) if x["role"] == "assistant")
            assert any(t["id"] == m["tool_call_id"] for t in prev_assistant["tool_calls"])


def test_failed_llm_call_rolls_back_history(svc):
    call, _ = dispatcher(svc)

    class Boom(FakeLLM):
        def create(self, **kwargs):
            raise RuntimeError("network down")

    agent = ChatAgent(Boom([]), "m", TOOLS, call)
    try:
        asyncio.run(agent.ask("hello"))
    except RuntimeError:
        pass
    assert agent.history == []


def test_inline_refs():
    schema = {"type": "object", "properties": {"nodes": {"type": "array", "items": {"$ref": "#/$defs/N"}}},
              "$defs": {"N": {"type": "object", "properties": {"id": {"type": "string"}}}}}
    out = inline_refs(schema)
    assert "$defs" not in out and out["properties"]["nodes"]["items"]["properties"]["id"]["type"] == "string"

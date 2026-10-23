import json

import pytest

from drawio_agent import models
from drawio_agent.service import DiagramError, DiagramService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DiagramService(tmp_path / "ws")


_CONVERT = {
    "add_nodes": ("nodes", models.NodeInput), "update_nodes": ("updates", models.NodeUpdate),
    "add_edges": ("edges", models.EdgeInput), "update_edges": ("updates", models.EdgeUpdate),
    "upsert_groups": ("groups", models.GroupInput),
}


def make_dispatcher(svc):
    """A ToolCaller backed by the real service (what the MCP server does, minus the transport)."""
    calls = []

    async def call(name, args):
        calls.append((name, dict(args)))
        args = dict(args)
        if name in _CONVERT:
            key, model = _CONVERT[name]
            args[key] = [model(**x) for x in args[key]]
        try:
            return json.dumps(getattr(svc, name)(**args)), False
        except DiagramError as exc:
            return str(exc), True
    return call, calls


@pytest.fixture
def dispatcher(svc):
    return make_dispatcher(svc)

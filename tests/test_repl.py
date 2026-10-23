import asyncio

from conftest import make_dispatcher
from test_agent import TOOLS, FakeLLM

from drawio_agent.agent import ChatAgent
from drawio_agent.agent.repl import Repl


def test_repl_commands(svc, capsys):
    call, _ = make_dispatcher(svc)
    repl = Repl(ChatAgent(FakeLLM([]), "m", TOOLS, call))

    async def script():
        for line in ["/notations", "/notation uml", "/new classes Domain model", "/shapes class",
                     "/libraries", "/list", "/notation", "/notation bogus", "/unknown"]:
            assert await repl.handle_command(line)
        assert not await repl.handle_command("/exit")

    asyncio.run(script())
    out = capsys.readouterr().out
    assert "archimate" in out and "Notation: UML 2.5." in out
    assert svc.get_diagram("classes")["notation"] == "uml"
    assert "uml.class" in out
    assert "* classes [uml]: Domain model" in out
    assert "(chosen by you)" in out and "Unknown notation 'bogus'" in out and "Unknown command" in out
    assert repl.prompt().strip() == "[classes|uml] you>"

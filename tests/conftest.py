import pytest
from drawio_agent.service import DiagramService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DiagramService(tmp_path / "ws")

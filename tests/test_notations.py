import json

import pytest

from drawio_agent.catalog import ShapeCatalog
from drawio_agent.notations import NotationGuideBuilder, NotationRegistry, UnknownNotationError


def test_builtin_notations_and_aliases():
    reg = NotationRegistry.load()
    assert reg.names() == ["archimate", "azure", "uml"]
    assert reg.resolve_name("ArchiMate3") == "archimate" and reg.resolve_name("none") is None
    assert "ArchiMate 3.2" in reg.get("archimate").prompt  # loaded from archimate.md
    with pytest.raises(UnknownNotationError):
        reg.resolve_name("bpmn")


def test_user_folder_adds_and_overrides(tmp_path):
    (tmp_path / "c4.json").write_text(json.dumps({"title": "C4", "libraries": ["c4"], "relations": {
        "uses": {"description": "uses", "style": "endArrow=open;"}}}))
    (tmp_path / "c4.md").write_text("Use the C4 model.")
    (tmp_path / "azure.json").write_text(json.dumps({"title": "Azure (company)", "libraries": ["azure2", "company"]}))
    reg = NotationRegistry.load(tmp_path)
    assert reg.get("c4").prompt == "Use the C4 model." and reg.get("c4").name == "c4"
    assert reg.get("azure").title == "Azure (company)"


def test_guide_lists_small_catalogues_and_points_to_search_for_large_ones():
    reg, guides = NotationRegistry.load(), NotationGuideBuilder(ShapeCatalog.load())
    archimate = guides.build(reg.get("archimate"))
    assert "- archimate3.business_actor: Business Actor [Business]" in archimate
    assert "assignment:" in archimate and "archimate_containers.grouping" in archimate
    azure = guides.build(reg.get("azure"))
    assert "Look them up with search_shapes" in azure and "azure_boundaries.subnet_boundary" in azure

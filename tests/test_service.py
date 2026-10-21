import base64
import zlib
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import pytest

from drawio_agent.models import EdgeInput, EdgeUpdate, GroupInput, NodeInput, NodeUpdate
from drawio_agent.service import DiagramError


def build(svc):
    svc.create_diagram("arch", "NIS2 platform")
    svc.add_nodes("arch", [
        NodeInput(id="user", label="User", kind="actor"),
        NodeInput(id="web", label="Web app", group="ui"),
        NodeInput(id="orch", label="Orchestrator", kind="agent", group="core"),
        NodeInput(id="code", label="Code agent", kind="agent", group="core"),
        NodeInput(id="db", label="Evidence DB", kind="database"),
    ])
    svc.add_edges("arch", [
        EdgeInput(source="user", target="web", label="uses"),
        EdgeInput(source="web", target="orch"),
        EdgeInput(source="orch", target="code", label="delegates"),
        EdgeInput(source="code", target="db", label="stores evidence", dashed=True),
    ])


def parse(path):
    root = ET.parse(path).getroot()
    cells = root.find("diagram/mxGraphModel/root")
    return {c.get("id"): c for c in cells}


def test_incremental_editing_and_rendering(svc):
    build(svc)
    d = svc.get_diagram("arch")
    assert {g["id"] for g in d["groups"]} == {"ui", "core"}  # auto-created
    cells = parse(d["file"])
    assert cells["orch"].get("parent") == "core"
    assert cells["e4"].get("source") == "code" and "dashed=1" in cells["e4"].get("style")

    svc.update_nodes("arch", [NodeUpdate(id="web", label="Portal", color="#ffcc00"), NodeUpdate(id="db", group="")])
    svc.upsert_groups("arch", [GroupInput(id="core", label="Agent core")])
    svc.update_edges("arch", [EdgeUpdate(id="e2", label="requests", bidirectional=True)])
    cells = parse(svc.drawio_path("arch"))
    assert cells["web"].get("value") == "Portal" and "fillColor=#ffcc00" in cells["web"].get("style")
    assert cells["core"].get("value") == "Agent core"
    assert "startArrow=block" in cells["e2"].get("style")


def test_remove_cascades_and_undo(svc):
    build(svc)
    out = svc.remove_nodes("arch", ["code"])
    assert "e3" in out["message"] and "e4" in out["message"]
    assert svc.get_diagram("arch")["edges"][-1]["id"] == "e2"
    svc.undo("arch")
    assert len(svc.get_diagram("arch")["nodes"]) == 5
    assert len(svc.get_diagram("arch")["edges"]) == 4


def test_errors_are_explicit(svc):
    build(svc)
    with pytest.raises(DiagramError, match="unknown node"):
        svc.add_edges("arch", [EdgeInput(source="web", target="ghost")])
    with pytest.raises(DiagramError, match="already exists"):
        svc.add_nodes("arch", [NodeInput(id="web", label="dup")])
    with pytest.raises(DiagramError, match="does not exist"):
        svc.get_diagram("nope")
    with pytest.raises(DiagramError, match="Invalid diagram name"):
        svc.create_diagram("../evil")
    # failed operations do not change the state
    assert len(svc.get_diagram("arch")["edges"]) == 4


def test_no_overlaps_in_auto_layout(svc):
    build(svc)
    from drawio_agent.layout import compute_geometry
    from drawio_agent.models import Diagram
    d = Diagram.model_validate_json((svc.workspace / "arch.json").read_text())
    for direction in ("left-to-right", "top-to-bottom"):
        d.direction = direction
        geo = compute_geometry(d)
        boxes = list(geo.nodes.values())
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                assert not overlap
        g = list(geo.groups.values())
        a, b = g
        assert not (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def test_import_roundtrip_and_manual_edit(svc):
    build(svc)
    src = svc.drawio_path("arch")
    copy = svc.workspace / "copy.drawio"
    copy.write_text(src.read_text())
    out = svc.import_drawio(str(copy), "copy")
    d = svc.get_diagram("copy")
    assert out["layout_mode"] == "manual"
    assert d["title"] == "NIS2 platform"
    assert {n["id"] for n in d["nodes"]} == {"user", "web", "orch", "code", "db"}
    assert len(d["edges"]) == 4
    assert next(n for n in d["nodes"] if n["id"] == "orch")["group"] == "core"
    svc.add_nodes("copy", [NodeInput(id="cache", label="Cache", kind="database")])
    svc.relayout("copy")
    assert svc.get_diagram("copy")["layout_mode"] == "auto"


def test_import_compressed_file(svc, tmp_path):
    model = ('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
             '<mxCell id="a 1" value="&lt;b&gt;Alpha&lt;/b&gt;" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="10" y="10" width="120" height="60" as="geometry"/></mxCell>'
             '<mxCell id="b" value="Beta" style="shape=cylinder3;" vertex="1" parent="1"><mxGeometry x="300" y="10" width="120" height="60" as="geometry"/></mxCell>'
             '<mxCell id="c" value="x" edge="1" parent="1" source="a 1" target="b"><mxGeometry relative="1" as="geometry"/></mxCell>'
             '</root></mxGraphModel>')
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = base64.b64encode(comp.compress(quote(model, safe="").encode()) + comp.flush()).decode()
    f = tmp_path / "legacy.drawio"
    f.write_text(f'<mxfile><diagram id="p" name="Page-1">{data}</diagram></mxfile>')
    svc.import_drawio(str(f))
    d = svc.get_diagram("legacy")
    labels = {n["label"]: n for n in d["nodes"]}
    assert labels["Alpha"]["id"] == "a_1" and labels["Beta"]["kind"] == "database"
    assert d["edges"][0]["source"] == "a_1"


def test_import_path_restriction(svc, tmp_path):
    outside = Path(tmp_path.anchor) / "definitely_not_allowed_dir" / "x.drawio"
    with pytest.raises(DiagramError, match="not allowed"):
        svc.import_drawio(str(outside))


def test_replace_and_direction(svc):
    svc.replace_diagram("flow", [NodeInput(id="a", label="Start", kind="process"),
                                 NodeInput(id="b", label="OK?", kind="decision")],
                        [EdgeInput(source="a", target="b")], title="Flow", direction="top-to-bottom")
    d = svc.get_diagram("flow")
    assert d["direction"] == "top-to-bottom" and d["edges"][0]["id"] == "e1"
    svc.set_diagram_properties("flow", layout_mode="manual")
    svc.set_diagram_properties("flow", direction="left-to-right")
    assert svc.get_diagram("flow")["layout_mode"] == "auto"

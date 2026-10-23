"""Diagram service with notations: shapes, relations, rendering and validation."""
from xml.etree import ElementTree as ET

import pytest

from drawio_agent.layout import compute_geometry
from drawio_agent.models import Diagram, EdgeInput, GroupInput, NodeInput, NodeUpdate
from drawio_agent.service import DiagramError
from drawio_agent.styles import LABEL_BELOW_HEIGHT


def cells(path):
    return {c.get("id"): c for c in ET.parse(path).getroot().find("diagram/mxGraphModel/root")}


@pytest.fixture
def ea(svc):
    svc.create_diagram("ea", "EA", notation="archimate3")  # alias resolves to 'archimate'
    svc.add_nodes("ea", [
        NodeInput(id="crm", label="CRM", shape="archimate3.application_component"),
        NodeInput(id="svc", label="Customer Data Service", shape="archimate3.application_service"),
        NodeInput(id="proc", label="Handle Claim", shape="archimate3.business_process"),
        NodeInput(id="cust", label="Customer", shape="archimate3.data_object"),
    ])
    svc.add_edges("ea", [
        EdgeInput(source="crm", target="svc", relation="realization"),
        EdgeInput(source="svc", target="proc", relation="serving"),
        EdgeInput(source="crm", target="cust", relation="access_write"),
    ])
    return svc


def test_notation_defaults_and_rendering(ea):
    d = ea.get_diagram("ea")
    assert d["notation"] == "archimate" and d["direction"] == "top-to-bottom"
    c = cells(ea.drawio_path("ea"))
    assert "shape=mxgraph.archimate3.application" in c["crm"].get("style") and "appType=comp" in c["crm"].get("style")
    assert "dashed=1" in c["e1"].get("style") and "endFill=0" in c["e1"].get("style")  # realization
    assert "endArrow=open" in c["e2"].get("style") and "dashed" not in c["e2"].get("style")  # serving
    assert "notation_issues" not in d


@pytest.mark.parametrize("node, match", [
    (NodeInput(id="x", label="x"), "needs a 'shape'"),
    (NodeInput(id="x", label="x", shape="azure2.key_vaults"), "not part of the ArchiMate"),
    (NodeInput(id="x", label="x", shape="archimate3.app_component"), "Did you mean: archimate3.application_component"),
    (NodeInput(id="x", label="x", shape="archimate_containers.grouping"), "container shape"),
])
def test_node_rules(ea, node, match):
    with pytest.raises(DiagramError, match=match):
        ea.add_nodes("ea", [node])


def test_edge_rules(ea):
    with pytest.raises(DiagramError, match="needs a 'relation'"):
        ea.add_edges("ea", [EdgeInput(source="crm", target="proc")])
    with pytest.raises(DiagramError, match="not a relationship type"):
        ea.add_edges("ea", [EdgeInput(source="crm", target="proc", relation="uses")])


def test_group_shapes(ea):
    ea.upsert_groups("ea", [GroupInput(id="app_layer", label="Application", shape="archimate_containers.layer_band")])
    ea.update_nodes("ea", [NodeUpdate(id="crm", group="app_layer"), NodeUpdate(id="svc", group="auto_group")])
    c = cells(ea.drawio_path("ea"))
    assert "fillColor=#f5f5f5" in c["app_layer"].get("style")
    assert "dashPattern=8 4" in c["auto_group"].get("style")  # notation default group shape
    with pytest.raises(DiagramError, match="is a node shape"):
        ea.upsert_groups("ea", [GroupInput(id="g", label="g", shape="archimate3.node")])


def test_rules_only_apply_to_touched_elements(svc):
    svc.create_diagram("legacy")
    svc.add_nodes("legacy", [NodeInput(id="a", label="A"), NodeInput(id="b", label="B")])
    svc.add_edges("legacy", [EdgeInput(source="a", target="b")])
    out = svc.set_diagram_properties("legacy", notation="archimate")
    assert len(out["notation_issues"]) == 3  # 2 nodes without shape + 1 edge without relation
    svc.add_nodes("legacy", [NodeInput(id="c", label="C", shape="archimate3.node")])  # untouched legacy is fine
    svc.update_nodes("legacy", [NodeUpdate(id="a", shape="archimate3.node")])
    assert len(svc.get_diagram("legacy")["notation_issues"]) == 2
    with pytest.raises(DiagramError):
        svc.update_nodes("legacy", [NodeUpdate(id="b", label="B2")])  # touching b requires fixing it
    svc.set_diagram_properties("legacy", notation="none")
    assert "notation" not in svc.get_diagram("legacy")


def test_uml_compartments_and_category_rules(svc):
    svc.create_diagram("cls", notation="uml")
    label = "Order\n--\n- id: UUID\n- total: Money\n--\n+ checkout(): Invoice"
    svc.add_nodes("cls", [NodeInput(id="order", label=label, shape="uml.class"),
                          NodeInput(id="base", label="Entity", shape="uml.abstract_class"),
                          NodeInput(id="uc1", label="Pay", shape="uml.use_case")])
    svc.add_edges("cls", [EdgeInput(source="order", target="base", relation="generalization")])
    c = cells(svc.drawio_path("cls"))
    value = c["order"].get("value")
    assert value.count("<hr") == 2 and "<b>Order</b>" in value and "checkout" in value
    assert float(c["order"].find("mxGeometry").get("height")) > 90  # grows with the compartments
    with pytest.raises(DiagramError, match="requires the source"):
        svc.add_edges("cls", [EdgeInput(source="order", target="uc1", relation="include")])
    svc.add_nodes("cls", [NodeInput(id="uc2", label="Login", shape="uml.use_case")])
    svc.add_edges("cls", [EdgeInput(source="uc1", target="uc2", relation="include")])
    assert "«include»" in cells(svc.drawio_path("cls"))["e2"].get("value")


def test_azure_icons_default_relation_and_label_space(svc):
    svc.create_diagram("az", notation="azure")
    svc.upsert_groups("az", [GroupInput(id="vnet", label="vnet-prod", shape="azure_boundaries.virtual_network_boundary")])
    svc.add_nodes("az", [NodeInput(id="app", label="App Service", shape="azure2.app_services", group="vnet"),
                         NodeInput(id="kv", label="Key Vault", shape="azure2.key_vaults", group="vnet")])
    svc.add_edges("az", [EdgeInput(source="app", target="kv", label="secrets")])  # relation optional in azure
    c = cells(svc.drawio_path("az"))
    assert "img/lib/azure2/security/Key_Vaults.svg" in c["kv"].get("style")
    assert "strokeColor=#333333" in c["e1"].get("style")  # default 'flow' relation style
    d = Diagram.model_validate_json((svc.workspace / "az.json").read_text())
    resolver = svc.renderer.resolver
    app = d.node_map()["app"]
    assert resolver.node_shape_box(app).height == 64  # the icon keeps its size ...
    assert resolver.node_layout_box(app).height == 64 + LABEL_BELOW_HEIGHT  # ... and room is left for its label
    geo = compute_geometry(d, resolver)
    a, k = geo.nodes["app"], geo.nodes["kv"]
    assert a[0] + a[2] <= k[0] or k[0] + k[2] <= a[0] or abs(a[1] - k[1]) >= 64 + LABEL_BELOW_HEIGHT


def test_shape_search_and_libraries(svc):
    res = svc.search_shapes("database", notation="azure")["results"]
    assert res and all(r["library"].startswith("azure") for r in res)
    assert svc.search_shapes("vnet", notation="azure", element_type="container")["results"][0]["key"] == \
        "azure_boundaries.virtual_network_boundary"
    with pytest.raises(DiagramError, match="not part of"):
        svc.search_shapes("x", notation="uml", library="azure2")
    libs = {lib["name"] for lib in svc.list_shape_libraries("archimate")["libraries"]}
    assert libs == {"archimate3", "archimate_containers"}
    assert {n["name"] for n in svc.list_notations()["notations"]} == {"archimate", "uml", "azure"}
    guide = svc.get_notation_guide("archimate")["guide"]
    assert "archimate3.application_component" in guide and "serving" in guide
    assert "search_shapes" in svc.get_notation_guide("azure")["guide"]
    with pytest.raises(DiagramError, match="Unknown notation"):
        svc.create_diagram("x", notation="bpmn")


def test_import_recognises_catalogue_shapes(ea):
    copy = ea.workspace / "copy.drawio"
    copy.write_text(ea.drawio_path("ea").read_text())
    out = ea.import_drawio(str(copy), "copy")
    assert "Recognised 4 catalogue shape(s)" in out["message"]
    shapes = {n["id"]: n.get("shape") for n in ea.get_diagram("copy")["nodes"]}
    assert shapes == {"crm": "archimate3.application_component", "svc": "archimate3.application_service",
                      "proc": "archimate3.business_process", "cust": "archimate3.data_object"}
    assert "Detected notation: archimate" in out["message"]
    assert ea.get_diagram("copy")["notation"] == "archimate"

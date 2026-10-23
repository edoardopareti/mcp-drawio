"""Shape catalogue: search, sources and builder."""
import base64
import json
import zlib
from urllib.parse import quote

import pytest

from drawio_agent.catalog import ShapeCatalog, UnknownShapeError
from drawio_agent.catalog.builder import CatalogBuilder
from drawio_agent.catalog.cli import main as build_main
from drawio_agent.catalog.jsparse import SidebarScanner
from drawio_agent.catalog.sources import DiagramHarvestSource, MxLibrarySource, SidebarSource

# Mirrors the structure of draw.io's Sidebar-*.js files.
SIDEBAR_JS = r"""
(function()
{
	Sidebar.prototype.addArchimate3ApplicationPalette = function()
	{
		var w = 50;
		var h = 50;
		var s = 'html=1;outlineConnect=0;whiteSpace=wrap;fillColor=#99ffff;shape=mxgraph.archimate3.application;';
		var dt = 'archimate3 application ';
		var sb = this;
		var fns = [
			this.createVertexTemplateEntry(s + 'appType=comp;archiType=square;', w * 3, h * 1.5, '', 'Application Component', null, null, dt + 'component'),
			this.createVertexTemplateEntry(s + "appType=serv;archiType=rounded;", 150, 75, '', 'Application Service', null, null, this.getTagsForStencil('x', 'y').join(' ')),
			this.createVertexTemplateEntry(mxConstants.UNKNOWN + 'x', 10, 10, '', 'Unresolvable'),
			this.addEntry(dt + 'complex', function() { return sb.createVertexTemplateFromCells([], 1, 1, 'x'); })
		];
		this.addPaletteFunctions('archimate3Application', 'Archimate 3.0 / Application', false, fns);
	};
	Sidebar.prototype.addAzure2ComputePalette = function()
	{
		var s = 'image;aspect=fixed;html=1;points=[];align=center;fontSize=12;image=img/lib/azure2/';
		this.addPaletteFunctions('azure2Compute', 'Azure / Compute', false, [
			this.createVertexTemplateEntry(s + 'compute/Function_Apps.svg;', 68, 60, '', 'Function Apps', false, null, 'azure function'),
		]);
	};
})();
"""


def test_seed_catalogue_search_and_suggestions():
    cat = ShapeCatalog.load()
    assert {"archimate3", "uml", "azure2", "azure_boundaries"} <= {lib.name for lib in cat.libraries()}
    assert cat.search("application component", libraries=["archimate3"])[0].key == "archimate3.application_component"
    assert cat.search("postgres")[0].key == "azure2.azure_database_for_postgresql"
    assert cat.search("subnet", element_types=["container"])[0].key == "azure_boundaries.subnet_boundary"
    with pytest.raises(UnknownShapeError, match="application_component"):
        cat.require("archimate3.aplication_component")


def test_sidebar_scanner_evaluates_static_definitions():
    result = SidebarScanner().scan(SIDEBAR_JS)
    by_title = {e.title: e for e in result.entries}
    assert set(by_title) == {"Application Component", "Application Service", "Function Apps"}
    comp = by_title["Application Component"]
    assert comp.style.endswith("appType=comp;archiType=square;") and (comp.width, comp.height) == (150, 75)
    assert comp.category == "Application" and comp.tags == "archimate3 application component"
    assert by_title["Function Apps"].category == "Compute"
    assert result.skipped == 1


def test_sidebar_source_and_library_override_seeds(tmp_path):
    js = tmp_path / "Sidebar-ArchiMate3.js"
    js.write_text(SIDEBAR_JS)
    catalog, report = CatalogBuilder().add(SidebarSource(js)).build()
    archimate = [e for e in catalog.entries() if e.library == "archimate3"]
    assert {e.source for e in archimate} == {"sidebar"}  # seeds of the library were replaced
    assert "archimate3" in report.superseded_seed_libraries
    assert catalog.get("archimate_containers.grouping") is not None  # other seed libraries kept
    assert catalog.get("archimate3.function_apps").category == "Compute"


def _compressed(xml: str) -> str:
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(comp.compress(quote(xml, safe="").encode()) + comp.flush()).decode()


def test_mxlibrary_source(tmp_path):
    cell = ('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Hub" style="image;html=1;image=img/lib/azure2/networking/Virtual_Networks.svg;" '
            'vertex="1" parent="1"><mxGeometry width="64" height="64" as="geometry"/></mxCell></root></mxGraphModel>')
    items = [{"xml": _compressed(cell), "w": 64, "h": 64, "title": "Hub VNet"},
             {"xml": cell, "w": 64, "h": 64, "title": ""},
             {"data": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "w": 40, "h": 40, "title": "Custom Icon"},
             {"xml": "not valid", "title": "Broken"}]
    lib = tmp_path / "company.xml"
    lib.write_text("<mxlibrary>" + json.dumps(items) + "</mxlibrary>")
    entries = list(MxLibrarySource(lib, "company").collect())
    assert [e.name for e in entries] == ["Hub VNet", "Hub", "Custom Icon"]
    assert "image=data:image/svg+xml,PHN2" in entries[2].style  # ';base64,' would break the style string
    assert entries[0].key == "company.hub_vnet"


def test_harvest_merges_known_styles_and_adds_new_ones(tmp_path):
    seed_style = ShapeCatalog.load().get("archimate3.application_component").style
    model = ('<mxfile><diagram id="p" name="P"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
             f'<mxCell id="a" value="Billing" style="{seed_style}fontSize=14;" vertex="1" parent="1"><mxGeometry width="150" height="75" as="geometry"/></mxCell>'
             '<mxCell id="b" value="Gateway" style="shape=hexagon;fillColor=#ffcc00;" vertex="1" parent="1"><mxGeometry width="120" height="60" as="geometry"/></mxCell>'
             '<mxCell id="t" value="free text" style="text;html=1;" vertex="1" parent="1"><mxGeometry width="50" height="20" as="geometry"/></mxCell>'
             '</root></mxGraphModel></diagram></mxfile>')
    folder = tmp_path / "diagrams"
    folder.mkdir()
    (folder / "one.drawio").write_text(model)
    catalog, report = CatalogBuilder().add(DiagramHarvestSource(folder, "company")).build()
    assert report.merged_harvest == 1
    assert "billing" in catalog.get("archimate3.application_component").keywords
    harvested = [e for e in catalog.entries() if e.library == "company"]
    assert len(harvested) == 1 and harvested[0].name == "Hexagon"


def test_cli_builds_a_catalogue_the_server_can_load(tmp_path):
    js = tmp_path / "Sidebar-Azure2.js"
    js.write_text(SIDEBAR_JS)
    out = tmp_path / "shapes.json"
    assert build_main(["--sidebar", f"{js}", "--output", str(out)]) == 0
    loaded = ShapeCatalog.load([out])
    assert loaded.get("azure2.function_apps").source == "sidebar"
    assert loaded.get("azure2.key_vaults") is not None  # bundled seeds still available at runtime
    assert build_main(["--sidebar", str(tmp_path / "missing.js"), "--output", str(out)]) == 2

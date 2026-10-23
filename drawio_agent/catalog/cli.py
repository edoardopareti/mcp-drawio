"""Command line interface of the catalogue builder (see build_catalog.py at the project root)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import PROJECT_ROOT, load_config
from .builder import DOWNLOADABLE, CatalogBuilder, SidebarDownloader
from .sources import DiagramHarvestSource, MxLibrarySource, SidebarSource


def _path_and_library(value: str) -> tuple[Path, str | None]:
    """'path/to/file.js' or 'path/to/file.js=library' (also works with Windows drive letters)."""
    path, sep, library = value.rpartition("=")
    return (Path(path).expanduser(), library or None) if sep else (Path(value).expanduser(), None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_catalog.py",
        description="Build the shape catalogue used by the draw.io agent from several sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  # draw.io's own ArchiMate 3, Azure and UML 2.5 libraries (downloaded once, then cached)
  python build_catalog.py --download archimate3 azure2 uml25

  # offline: sidebars from a local clone of https://github.com/jgraph/drawio
  python build_catalog.py --drawio-repo ~/src/drawio --sidebars ArchiMate3 Azure2 UML25

  # an icon library exported from draw.io and the conventions used in your diagrams
  python build_catalog.py --download archimate3 --library ~/libs/company-azure.xml=azure2 \\
                          --harvest ~/architecture=archimate3
""")
    p.add_argument("--output", type=Path, default=None, help="catalogue file (default: SHAPE_CATALOG or ./catalog/shapes.json)")
    p.add_argument("--download", nargs="+", metavar="LIB", choices=sorted(DOWNLOADABLE),
                   help=f"download draw.io sidebar definitions: {', '.join(sorted(DOWNLOADABLE))}")
    p.add_argument("--refresh", action="store_true", help="re-download sidebars even if cached")
    p.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "catalog" / "sources")
    p.add_argument("--drawio-repo", type=Path, help="local clone of jgraph/drawio")
    p.add_argument("--sidebars", nargs="+", default=["ArchiMate3", "Azure2", "UML25"], metavar="NAME",
                   help="sidebar names to read from --drawio-repo (default: ArchiMate3 Azure2 UML25)")
    p.add_argument("--sidebar", action="append", default=[], metavar="FILE[=LIB]", help="a Sidebar-*.js file")
    p.add_argument("--library", action="append", default=[], metavar="FILE[=LIB]",
                   help="a draw.io library file (.xml, <mxlibrary>)")
    p.add_argument("--harvest", action="append", default=[], metavar="PATH[=LIB]",
                   help="a .drawio file or a folder of diagrams whose shapes are collected (default LIB: harvested)")
    p.add_argument("--no-seeds", action="store_true", help="do not include the bundled seed shapes")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    output = args.output or load_config().shape_catalog

    builder = CatalogBuilder(include_seeds=not args.no_seeds)
    if args.download:
        try:
            paths = SidebarDownloader(args.cache_dir).fetch(args.download, refresh=args.refresh)
        except OSError as exc:  # network blocked, proxy, ...
            print(f"error: download failed ({exc}). Clone https://github.com/jgraph/drawio and use --drawio-repo, "
                  "or pass the files with --sidebar.", file=sys.stderr)
            return 2
        for path in paths:
            builder.add(SidebarSource(path))
    if args.drawio_repo:
        folder = args.drawio_repo / "src" / "main" / "webapp" / "js" / "diagramly" / "sidebar"
        for name in args.sidebars:
            path = folder / f"Sidebar-{name}.js"
            if not path.is_file():
                print(f"error: {path} not found", file=sys.stderr)
                return 2
            builder.add(SidebarSource(path))
    for value in args.sidebar:
        path, lib = _path_and_library(value)
        builder.add(SidebarSource(path, lib))
    for value in args.library:
        path, lib = _path_and_library(value)
        builder.add(MxLibrarySource(path, lib))
    for value in args.harvest:
        path, lib = _path_and_library(value)
        builder.add(DiagramHarvestSource(path, lib or "harvested"))

    missing = [s.path for s in builder.sources if not Path(s.path).exists()]
    if missing:
        print("error: not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2

    catalog, report = builder.build()
    catalog.save(output)
    print(f"Catalogue written to {output}")
    print("\n".join(report.lines()))
    return 0

"""Merges catalogue sources into one ShapeCatalog, applying precedence rules."""
from __future__ import annotations

import logging
import urllib.request
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..drawio_codec import style_signature
from .catalog import SEEDS_DIR, ShapeCatalog
from .entries import ShapeEntry
from .sources import CatalogSource, SeedSource

log = logging.getLogger(__name__)

DRAWIO_SIDEBAR_URL = "https://raw.githubusercontent.com/jgraph/drawio/dev/src/main/webapp/js/diagramly/sidebar/{name}"
DOWNLOADABLE = {
    "archimate3": "Sidebar-ArchiMate3.js",
    "azure2": "Sidebar-Azure2.js",
    "uml25": "Sidebar-UML25.js",
    "c4": "Sidebar-C4.js",
    "bpmn": "Sidebar-BPMN.js",
    "kubernetes": "Sidebar-Kubernetes.js",
}


@dataclass
class BuildReport:
    per_source: dict[str, int] = field(default_factory=dict)
    superseded_seed_libraries: list[str] = field(default_factory=list)
    merged_harvest: int = 0
    total: int = 0
    by_library: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [f"  {name}: {count} entries" for name, count in self.per_source.items()]
        if self.superseded_seed_libraries:
            out.append(f"  seeds replaced by authoritative sources: {', '.join(self.superseded_seed_libraries)}")
        if self.merged_harvest:
            out.append(f"  harvested styles merged into existing shapes: {self.merged_harvest}")
        out.append(f"  total: {self.total} shapes in {len(self.by_library)} libraries")
        out += [f"    {lib}: {n}" for lib, n in sorted(self.by_library.items())]
        return out


class CatalogBuilder:
    """Precedence (lowest to highest): seeds < sidebar and library sources (later sources win on equal keys).

    * A library produced by a sidebar or library source replaces the seed entries of that library,
      because draw.io's own definitions are exact while seeds are a curated approximation.
    * Harvested styles that match an existing shape (same identity signature) only enrich that
      shape's keywords; new styles become entries of the harvest library.
    """

    def __init__(self, include_seeds: bool = True):
        self.include_seeds = include_seeds
        self.sources: list[CatalogSource] = []

    def add(self, source: CatalogSource) -> "CatalogBuilder":
        self.sources.append(source)
        return self

    def build(self) -> tuple[ShapeCatalog, BuildReport]:
        report = BuildReport()
        seeds: list[ShapeEntry] = []
        if self.include_seeds:
            for path in sorted(SEEDS_DIR.glob("*.json")):
                src = SeedSource(path)
                items = list(src.collect())
                report.per_source[src.description] = len(items)
                seeds += items

        authoritative = [s for s in self.sources if s.kind in ("sidebar", "library")]
        harvests = [s for s in self.sources if s.kind == "harvest"]
        others = [s for s in self.sources if s not in authoritative and s not in harvests]

        primary: dict[str, ShapeEntry] = {}
        produced_libraries: set[str] = set()
        for src in [*others, *authoritative]:
            items = self._collect(src, report)
            produced_libraries |= {e.library for e in items if src.kind in ("sidebar", "library")}
            primary.update({e.key: e for e in items})

        merged: dict[str, ShapeEntry] = {}
        for e in seeds:
            if e.library in produced_libraries:
                continue
            merged[e.key] = e
        report.superseded_seed_libraries = sorted({e.library for e in seeds} & produced_libraries)
        merged.update(primary)

        by_signature = {style_signature(e.style): key for key, e in merged.items()}
        for src in harvests:
            for e in self._collect(src, report):
                sig = style_signature(e.style)
                if sig in by_signature:
                    target = merged[by_signature[sig]]
                    extra = [k for k in e.keywords if k not in target.keywords]
                    merged[target.key] = target.model_copy(update={"keywords": target.keywords + extra[:10]})
                    report.merged_harvest += 1
                elif e.key not in merged:
                    merged[e.key] = e
                    by_signature[sig] = e.key

        catalog = ShapeCatalog(merged.values())
        report.total = len(catalog)
        report.by_library = dict(Counter(e.library for e in catalog.entries()))
        return catalog, report

    @staticmethod
    def _collect(src: CatalogSource, report: BuildReport) -> list[ShapeEntry]:
        try:
            items = list(src.collect())
        except Exception as exc:
            log.error("Source %s failed: %s", src.description, exc)
            items = []
        report.per_source[src.description] = len(items)
        return items


class SidebarDownloader:
    """Fetches draw.io sidebar definitions from the public jgraph/drawio repository into a local cache.

    This is the only network access in the project and it happens at build time, never at runtime.
    """

    def __init__(self, cache_dir: Path, url_template: str = DRAWIO_SIDEBAR_URL, timeout: float = 30):
        self.cache_dir, self.url_template, self.timeout = cache_dir, url_template, timeout

    def fetch(self, libraries: Iterable[str], refresh: bool = False) -> list[Path]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for lib in libraries:
            name = DOWNLOADABLE.get(lib)
            if name is None:
                raise ValueError(f"Unknown downloadable library '{lib}'. Known: {', '.join(sorted(DOWNLOADABLE))}")
            target = self.cache_dir / name
            if refresh or not target.exists():
                url = self.url_template.format(name=name)
                log.info("Downloading %s", url)
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310 (fixed https URL)
                    target.write_bytes(resp.read())
            paths.append(target)
        return paths

"""Catalogue sources. Each source turns some input into ShapeEntry objects.

Sources (Strategy pattern, all implement CatalogSource.collect):
  SeedSource           curated JSON shipped with the package (fallback, lowest priority)
  SidebarSource        draw.io's own library definitions (Sidebar-*.js) - authoritative
  MxLibrarySource      draw.io library files (<mxlibrary>[...]</mxlibrary>, File > Open Library)
  DiagramHarvestSource styles actually used in existing .drawio diagrams
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ..drawio_codec import (
    is_container_style, iter_page_models, parse_model_text, parse_style, strip_html, style_signature,
)
from .entries import ElementType, LabelFormat, ShapeEntry, SourceKind, slugify
from .jsparse import SidebarScanner

log = logging.getLogger(__name__)

# draw.io sidebar file -> library name used in shape keys and notation definitions.
KNOWN_SIDEBARS = {
    "Sidebar-ArchiMate3.js": "archimate3",
    "Sidebar-ArchiMate.js": "archimate",
    "Sidebar-Azure2.js": "azure2",
    "Sidebar-Azure.js": "azure",
    "Sidebar-UML25.js": "uml25",
    "Sidebar-C4.js": "c4",
    "Sidebar-BPMN.js": "bpmn",
    "Sidebar-AWS4.js": "aws4",
    "Sidebar-Kubernetes.js": "kubernetes",
}
DEFAULT_SIZE = (120.0, 60.0)


def library_name_for_sidebar(path: Path) -> str:
    return KNOWN_SIDEBARS.get(path.name) or slugify(path.stem.removeprefix("Sidebar-"))


def humanize(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.sub(r"[_\-]+", " ", text).strip().title()


def _name_from_style(style: str) -> str:
    _, values = parse_style(style)
    if "image" in values:
        return humanize(Path(values["image"].split("?")[0]).stem)
    shape = values.get("shape", "")
    base = humanize(shape.rsplit(".", 1)[-1]) if shape else ""
    for k in ("appType", "techType", "motivType"):
        if k in values:
            return f"{base} {humanize(values[k])}".strip()
    return base


def _category_from_style(style: str) -> str | None:
    image = parse_style(style)[1].get("image", "")
    m = re.search(r"img/lib/[^/]+/([^/]+)/", image)
    return humanize(m.group(1)) if m else None


def _keywords(*texts: str) -> list[str]:
    words: list[str] = []
    for text in texts:
        for w in re.split(r"[\s,]+", text or ""):
            w = w.strip().lower()
            if len(w) > 1 and w not in words:
                words.append(w)
    return words[:25]


@dataclass
class EntryFactory:
    """Builds unique, valid ShapeEntry objects for one library."""
    library: str
    source: SourceKind
    _used: set[str] = field(default_factory=set)

    def make(self, name: str, style: str, width: float | None, height: float | None, *,
             category: str | None = None, keywords: Iterable[str] = (), element_type: ElementType | None = None,
             label_format: LabelFormat = "plain", key: str | None = None) -> ShapeEntry:
        name = (name or _name_from_style(style) or "Shape").strip()
        base = key or f"{slugify(self.library)}.{slugify(name)}"
        candidate, n = base, 2
        while candidate in self._used:
            candidate, n = f"{base}_{n}", n + 1
        self._used.add(candidate)
        return ShapeEntry(
            key=candidate, name=name, library=self.library, source=self.source,
            category=category or _category_from_style(style) or "General",
            element_type=element_type or ("container" if is_container_style(style) else "node"),
            style=style, width=width or DEFAULT_SIZE[0], height=height or DEFAULT_SIZE[1],
            keywords=list(keywords), label_format=label_format,
        )


class CatalogSource(ABC):
    kind: SourceKind

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def collect(self) -> Iterator[ShapeEntry]: ...


# ---------------- seeds ----------------
class SeedSource(CatalogSource):
    """Curated seed files: {"library": ..., "defaults": {...}, "entries": [{name, style, ...}]}."""
    kind = "seed"

    def __init__(self, path: Path):
        self.path = path

    @property
    def description(self) -> str:
        return f"seed {self.path.name}"

    def collect(self) -> Iterator[ShapeEntry]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        factory = EntryFactory(data["library"], "seed")
        defaults = data.get("defaults", {})
        for item in data["entries"]:
            merged = {**defaults, **item}
            style = merged.get("style_prefix", "") + merged["style"]
            yield factory.make(
                merged["name"], style, merged.get("width"), merged.get("height"),
                category=merged.get("category"), keywords=merged.get("keywords", []),
                element_type=merged.get("element_type"), label_format=merged.get("label_format", "plain"),
                key=merged.get("key"))


# ---------------- draw.io sidebars ----------------
class SidebarSource(CatalogSource):
    kind = "sidebar"

    def __init__(self, path: Path, library: str | None = None):
        self.path, self.library = path, library or library_name_for_sidebar(path)

    @property
    def description(self) -> str:
        return f"sidebar {self.path.name} -> {self.library}"

    def collect(self) -> Iterator[ShapeEntry]:
        result = SidebarScanner().scan(self.path.read_text(encoding="utf-8", errors="replace"))
        if result.skipped:
            log.info("%s: skipped %d entries that cannot be evaluated statically", self.path.name, result.skipped)
        factory = EntryFactory(self.library, "sidebar")
        for call in result.entries:
            name = call.title or strip_html(call.value) or _name_from_style(call.style)
            yield factory.make(name, call.style, call.width, call.height, category=call.category,
                               keywords=_keywords(call.tags, name))


# ---------------- mxlibrary files ----------------
class MxLibrarySource(CatalogSource):
    kind = "library"

    def __init__(self, path: Path, library: str | None = None):
        self.path, self.library = path, library or slugify(path.stem)

    @property
    def description(self) -> str:
        return f"library {self.path.name} -> {self.library}"

    def collect(self) -> Iterator[ShapeEntry]:
        text = self.path.read_text(encoding="utf-8", errors="replace").strip()
        m = re.search(r"<mxlibrary[^>]*>(.*)</mxlibrary>", text, re.DOTALL)
        items = json.loads(m.group(1) if m else text)
        factory = EntryFactory(self.library, "library")
        for item in items:
            try:
                entry = self._entry(factory, item)
            except Exception as exc:  # one broken item must not spoil the library
                log.warning("%s: skipped item %r: %s", self.path.name, item.get("title"), exc)
                continue
            if entry is not None:
                yield entry

    def _entry(self, factory: EntryFactory, item: dict) -> ShapeEntry | None:
        title = strip_html(item.get("title") or "")
        width, height = item.get("w"), item.get("h")
        if item.get("data"):  # image library item
            data = re.sub(r";base64,", ",", item["data"], count=1)  # ';' is a style separator in draw.io
            style = ("shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;"
                     f"image={data};")
            return factory.make(title or "Image", style, width, height, keywords=_keywords(title))
        if not item.get("xml"):
            return None
        model = parse_model_text(item["xml"])
        cell = self._main_vertex(model)
        if cell is None:
            return None
        style = cell.get("style", "")
        name = title or strip_html(cell.get("value", "")) or _name_from_style(style)
        return factory.make(name, style, width, height, keywords=_keywords(title, name))

    @staticmethod
    def _main_vertex(model: ET.Element) -> ET.Element | None:
        root = model.find("root") if model.tag == "mxGraphModel" else model
        if root is None:
            return None
        cells = [c.find("mxCell") if c.tag in ("UserObject", "object") else c for c in root]
        vertices = [c for c in cells if c is not None and c.get("vertex") == "1" and c.get("style")]
        top = [c for c in vertices if c.get("parent") in (None, "1")]
        return (top or vertices or [None])[0]


# ---------------- existing diagrams ----------------
class DiagramHarvestSource(CatalogSource):
    """Collects every distinct vertex style used in existing diagrams (your own conventions)."""
    kind = "harvest"

    def __init__(self, path: Path, library: str = "harvested"):
        self.path, self.library = path, library

    @property
    def description(self) -> str:
        return f"harvest {self.path} -> {self.library}"

    def _files(self) -> list[Path]:
        if self.path.is_file():
            return [self.path]
        return sorted(p for ext in ("*.drawio", "*.xml") for p in self.path.rglob(ext))

    def collect(self) -> Iterator[ShapeEntry]:
        seen: dict[str, dict] = {}
        for file in self._files():
            try:
                models = iter_page_models(file)
            except Exception as exc:
                log.warning("harvest: cannot read %s: %s", file, exc)
                continue
            for model in models:
                root = model.find("root")
                for el in root if root is not None else []:
                    cell = el.find("mxCell") if el.tag in ("UserObject", "object") else el
                    if cell is None or cell.get("vertex") != "1" or not cell.get("style"):
                        continue
                    style = cell.get("style")
                    tokens, _ = parse_style(style)
                    if tokens[:1] == ["text"]:
                        continue  # free text, not a shape
                    label = strip_html(el.get("label") or cell.get("value") or "")
                    sig = style_signature(style) or style
                    info = seen.setdefault(sig, {"style": style, "labels": [], "size": None})
                    if label and label not in info["labels"]:
                        info["labels"].append(label)
                    geo = cell.find("mxGeometry")
                    if info["size"] is None and geo is not None and geo.get("width"):
                        info["size"] = (float(geo.get("width")), float(geo.get("height", 0) or 0) or None)
        factory = EntryFactory(self.library, "harvest")
        for info in seen.values():
            name = _name_from_style(info["style"]) or (info["labels"][0][:40] if info["labels"] else "")
            width, height = info["size"] or (None, None)
            yield factory.make(name, info["style"], width, height, keywords=_keywords(*info["labels"][:8]))

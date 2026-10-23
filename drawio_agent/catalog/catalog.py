"""In-memory shape catalogue with keyword search and style matching."""
from __future__ import annotations

import difflib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from ..drawio_codec import parse_style, style_signature
from .entries import ElementType, LibraryInfo, ShapeEntry

log = logging.getLogger(__name__)

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
CATALOG_FORMAT_VERSION = 1

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class UnknownShapeError(KeyError):
    def __init__(self, key: str, suggestions: list[str]):
        super().__init__(key)
        self.key, self.suggestions = key, suggestions

    def __str__(self) -> str:
        hint = f" Did you mean: {', '.join(self.suggestions)}?" if self.suggestions else ""
        return f"Unknown shape '{self.key}'.{hint} Use search_shapes to find valid keys."


class _SearchIndex:
    """Weighted token index. Name matches weigh more than keywords, which weigh more than categories."""
    WEIGHTS = (("name", 3.0), ("keywords", 2.0), ("category", 1.0), ("key", 1.0))

    def __init__(self, entries: Iterable[ShapeEntry]):
        self._fields: dict[str, dict[str, set[str]]] = {}
        for e in entries:
            self._fields[e.key] = {
                "name": set(tokenize(e.name)),
                "keywords": {t for k in e.keywords for t in tokenize(k)},
                "category": set(tokenize(e.category)),
                "key": set(tokenize(e.key)),
            }

    def score(self, entry: ShapeEntry, query_tokens: list[str], query_text: str) -> float:
        fields = self._fields[entry.key]
        total = 0.0
        for qt in query_tokens:
            best = 0.0
            for field, weight in self.WEIGHTS:
                tokens = fields[field]
                if qt in tokens:
                    best = max(best, weight)
                elif len(qt) >= 3 and any(t.startswith(qt) for t in tokens):
                    best = max(best, weight * 0.6)
            total += best
        if not total:
            return 0.0
        matched = sum(1 for qt in query_tokens if any(qt in f or any(t.startswith(qt) for t in f)
                                                      for f in fields.values()))
        total *= matched / len(query_tokens)  # favour entries matching every word
        if entry.name.lower() == query_text:
            total += 5.0
        return total


class ShapeCatalog:
    def __init__(self, entries: Iterable[ShapeEntry] = ()):
        self._entries: dict[str, ShapeEntry] = {}
        for e in entries:
            self._entries[e.key] = e  # later entries override earlier ones
        self._index = _SearchIndex(self._entries.values())
        self._by_signature: dict[str, list[ShapeEntry]] = defaultdict(list)
        for e in self._entries.values():
            self._by_signature[style_signature(e.style)].append(e)

    # ---------------- construction ----------------
    @staticmethod
    def read_file(path: Path) -> list[ShapeEntry]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [ShapeEntry.model_validate(item) for item in data.get("entries", [])]

    @classmethod
    def load(cls, extra_files: Iterable[Path | None] = (), include_seeds: bool = True) -> "ShapeCatalog":
        """Seeds first, then each catalogue file in order (later keys override earlier ones)."""
        entries: list[ShapeEntry] = []
        if include_seeds:
            from .sources import SeedSource  # local import: sources depend on entries only

            for seed in sorted(SEEDS_DIR.glob("*.json")):
                entries.extend(SeedSource(seed).collect())
        for path in extra_files:
            if path is None:
                continue
            if Path(path).is_file():
                entries.extend(cls.read_file(Path(path)))
            else:
                log.info("Shape catalogue %s not found; using bundled seeds only", path)
        return cls(entries)

    def save(self, path: Path) -> None:
        payload = {"version": CATALOG_FORMAT_VERSION,
                   "entries": [e.model_dump(mode="json") for e in self.entries()]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---------------- lookups ----------------
    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def entries(self) -> list[ShapeEntry]:
        return sorted(self._entries.values(), key=lambda e: e.key)

    def get(self, key: str) -> ShapeEntry | None:
        return self._entries.get(key)

    def require(self, key: str, libraries: Iterable[str] | None = None) -> ShapeEntry:
        entry = self._entries.get(key)
        if entry is None:
            raise UnknownShapeError(key, self.suggest(key, libraries))
        return entry

    def in_libraries(self, libraries: Iterable[str] | None) -> list[ShapeEntry]:
        allowed = set(libraries) if libraries is not None else None
        return [e for e in self.entries() if allowed is None or e.library in allowed]

    def suggest(self, key: str, libraries: Iterable[str] | None = None, limit: int = 5) -> list[str]:
        pool = self.in_libraries(libraries)
        by_key = difflib.get_close_matches(key, [e.key for e in pool], n=limit, cutoff=0.5)
        if len(by_key) >= limit:
            return by_key
        words = " ".join(key.split(".")[-1:]).replace("_", " ")
        by_name = [e.key for e in self.search(words, libraries=libraries, limit=limit)]
        return list(dict.fromkeys(by_key + by_name))[:limit]

    def search(self, query: str, libraries: Iterable[str] | None = None,
               element_types: Iterable[ElementType] | None = None, category: str | None = None,
               limit: int = 10) -> list[ShapeEntry]:
        types = set(element_types) if element_types else None
        pool = [e for e in self.in_libraries(libraries)
                if (types is None or e.element_type in types)
                and (category is None or category.lower() in e.category.lower())]
        tokens = tokenize(query)
        if not tokens:
            return pool[:limit]
        scored = [(self._index.score(e, tokens, query.strip().lower()), e) for e in pool]
        scored = [(s, e) for s, e in scored if s > 0]
        scored.sort(key=lambda se: (-se[0], len(se[1].name), se[1].key))
        return [e for _, e in scored[:limit]]

    def match_style(self, style: str, libraries: Iterable[str] | None = None) -> ShapeEntry | None:
        """Find the catalogue shape a raw draw.io style corresponds to (used when importing)."""
        candidates = self._by_signature.get(style_signature(style), [])
        if libraries is not None:
            allowed = set(libraries)
            candidates = [c for c in candidates if c.library in allowed]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        # Same identity (e.g. ArchiMate business vs application service differ only by colour):
        # pick the candidate sharing the most style settings, if it is a clear winner.
        _, wanted = parse_style(style)
        ranked = sorted(((sum(1 for k, v in parse_style(c.style)[1].items() if wanted.get(k) == v), c)
                         for c in candidates), key=lambda sc: -sc[0])
        return ranked[0][1] if ranked[0][0] > ranked[1][0] else None

    def libraries(self, names: Iterable[str] | None = None) -> list[LibraryInfo]:
        grouped: dict[str, list[ShapeEntry]] = defaultdict(list)
        for e in self.in_libraries(names):
            grouped[e.library].append(e)
        return [LibraryInfo(
            name=lib, shapes=sum(1 for e in items if e.element_type == "node"),
            containers=sum(1 for e in items if e.element_type == "container"),
            categories=sorted({e.category for e in items}), sources=sorted({e.source for e in items}),
        ) for lib, items in sorted(grouped.items())]

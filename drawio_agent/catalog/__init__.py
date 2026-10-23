"""Shape catalogue: searchable draw.io shapes (ArchiMate, UML, Azure, ...) referenced by key from diagrams."""
from .entries import ElementType, LabelFormat, LibraryInfo, ShapeEntry, slugify
from .catalog import ShapeCatalog, UnknownShapeError

__all__ = [
    "ElementType", "LabelFormat", "LibraryInfo", "ShapeEntry", "ShapeCatalog", "UnknownShapeError", "slugify",
]

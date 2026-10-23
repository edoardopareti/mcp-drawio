"""Low-level helpers for the draw.io file format: page decoding and style strings.

Shared by the importer, the renderer and the catalogue builder so that the format
knowledge lives in a single place.
"""
from __future__ import annotations

import base64
import html
import re
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

# ---------------- compressed payloads ----------------


def decompress(text: str) -> str:
    """Decode a draw.io compressed payload (base64 + raw deflate + URI encoding)."""
    raw = zlib.decompress(base64.b64decode(text.strip()), -15).decode("utf-8")
    return unquote(raw)


def parse_model_text(text: str) -> ET.Element:
    """Parse an mxGraphModel given as plain XML or as a compressed payload."""
    text = text.strip()
    if not text.startswith("<"):
        text = decompress(text)
    return ET.fromstring(text)


def page_model(diagram_el: ET.Element) -> ET.Element:
    """Return the mxGraphModel of a <diagram> page, decoding it if compressed."""
    model = diagram_el.find("mxGraphModel")
    if model is not None:
        return model
    text = (diagram_el.text or "").strip()
    if not text:
        raise ValueError("The diagram page is empty")
    return parse_model_text(text)


def iter_page_models(path: Path) -> list[ET.Element]:
    """Return the mxGraphModel of every page in a .drawio/.xml file."""
    root = ET.parse(path).getroot()
    if root.tag == "mxGraphModel":
        return [root]
    if root.tag == "mxfile":
        pages = root.findall("diagram")
        if not pages:
            raise ValueError("No <diagram> page found in the file")
        return [page_model(p) for p in pages]
    raise ValueError(f"Unsupported root element <{root.tag}>")


def first_page_model(path: Path) -> ET.Element:
    return iter_page_models(path)[0]


# ---------------- labels ----------------


def strip_html(value: str) -> str:
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(div|p)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


# ---------------- style strings ----------------


def parse_style(style: str) -> tuple[list[str], dict[str, str]]:
    """Split a style into bare tokens (e.g. 'ellipse', 'image') and key=value pairs."""
    tokens: list[str] = []
    values: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            values[key] = value
        else:
            tokens.append(part)
    return tokens, values


def style_value(style: str, key: str) -> str | None:
    return parse_style(style)[1].get(key)


def set_style_value(style: str, key: str, value: str | None) -> str:
    """Set, replace or (value=None) remove a key in a draw.io style string."""
    parts = [p for p in (style or "").split(";") if p]
    parts = [p for p in parts if p.split("=", 1)[0] != key]
    if value is not None:
        parts.append(f"{key}={value}")
    return ";".join(parts) + ";"


# Keys that identify *what* a shape is, as opposed to how it is decorated.
_IDENTITY_KEYS = ("shape", "image", "appType", "archiType", "techType", "motivType", "prIcon", "direction")


def style_signature(style: str) -> str:
    """A stable identity of a style, used to match/deduplicate shapes regardless of colours or fonts."""
    tokens, values = parse_style(style)
    parts = [t for t in tokens if t not in {"html"}][:1]
    parts += [f"{k}={values[k]}" for k in _IDENTITY_KEYS if k in values]
    return ";".join(parts)


def is_container_style(style: str) -> bool:
    tokens, values = parse_style(style)
    return "swimlane" in tokens or values.get("container") == "1" or "group" in tokens


def has_label_below(style: str) -> bool:
    tokens, values = parse_style(style)
    return values.get("verticalLabelPosition") == "bottom" or (bool(tokens) and tokens[0] == "image")

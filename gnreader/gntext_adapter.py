"""Adapter around the schema lane's gntext extractor.

We deliberately do not re-parse rich text or Yjs here.  gntext owns the
text/paragraph extraction; this module only converts its records into the
renderer's TextBox model and gives geometry defaults for full-page nexus
documents (which have no item bbox).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .model import TextBox


@lru_cache(maxsize=1)
def _pycrdt_ok() -> bool:
    try:
        import pycrdt  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _extract_fn():
    try:
        from gntext import extract_text
    except Exception:
        return None
    return extract_text


def available() -> bool:
    return _extract_fn() is not None


def _nexus_runs(data: bytes) -> dict[str, list[str]]:
    """Layout-only supplement: the text stays from gntext, but Yjs children are
    the paragraph/list boundaries that make a full-page text document readable."""
    out: dict[str, list[str]] = {}
    try:
        from gntext import schema_pb2 as S
        from gntext.items import split_records
        from pycrdt import Doc, XmlFragment
    except Exception:
        return out
    for rec in split_records(data):
        m = S.AnyNotesItem()
        try:
            m.ParseFromString(rec)
        except Exception:
            continue
        if m.WhichOneof("Item") != "nexus" or not m.nexus.yjsData:
            continue
        try:
            doc = Doc()
            doc.apply_update(bytes(m.nexus.yjsData))
            frag = doc.get("content", type=XmlFragment)
            runs = []
            for ch in list(frag.children):
                try:
                    t = str(ch.to_py())
                except Exception:
                    continue
                if t:
                    runs.append(t)
            out[m.nexus.id] = runs
        except Exception:
            continue
    return out


def extract(records, include_nexus: bool = True) -> list[dict[str, Any]]:
    """Return gntext items, or raise ImportError for the caller."""
    fn = _extract_fn()
    if fn is None:
        raise ImportError("gntext is not installed; install the gnreader distribution")
    items = fn(records, include_nexus=include_nexus)
    if include_nexus and isinstance(records, (bytes, bytearray)):
        runs = _nexus_runs(bytes(records))
        if runs:
            for d in items:
                if d.get("kind") == "nexusText":
                    d["runs"] = runs.get(d.get("id"), [])
    return items


def _z(v):
    if not v:
        return (0, 0, 0)
    try:
        return max(tuple(int(x) for x in e[:3]) for e in v)
    except Exception:
        return (0, 0, 0)


def _rgba(c, default=(0, 0, 0, 255)):
    if not isinstance(c, dict):
        return default
    vals = [c.get(k, (default[i] / 255.0)) for i, k in enumerate(("r", "g", "b", "a"))]
    # gntext returns normalized floats; tolerate 0..255 if a future extractor changes.
    if max(vals[:3]) > 1.5:
        return tuple(max(0, min(255, round(x))) for x in vals)
    return tuple(max(0, min(255, round(float(x) * 255.0))) for x in vals)


def _font_size(style: dict | None, box: dict | None = None) -> float:
    v = (style or {}).get("fontSize")
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if 1.0 < v < 300.0:
        return v
    # Unstyled shape labels in the corpus are one line in a ~29 px box.
    if box and box.get("h"):
        h = float(box["h"])
        if h <= 40:
            return max(10.0, min(32.0, h * 0.69))
    return 20.0


def _bbox(d: dict):
    b = d.get("bbox")
    if not isinstance(b, dict):
        return None
    try:
        return {k: float(b.get(k, 0.0)) for k in ("x", "y", "w", "h")}
    except (TypeError, ValueError):
        return None


def _padding(raw) -> tuple[float, float, float, float]:
    if not isinstance(raw, dict):
        return (0.0, 0.0, 0.0, 0.0)
    g = lambda k: float(raw.get(k) or 0.0)      # noqa: E731
    return (g("left"), g("top"), g("right"), g("bottom"))


def to_textbox(d: dict) -> TextBox | None:
    kind = d.get("kind") or "text"
    text = d.get("text") or ""
    if not text.strip() and kind not in ("stickyNote",):
        return None
    bbox = _bbox(d)
    if bbox is None:
        if kind != "nexusText":
            return None
        # nexus is a full-page text document; the renderer substitutes the page
        # text area.  Zero marks "use page area".
        bbox = {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    style = d.get("style") or {}
    color = _rgba(style.get("color") if isinstance(style, dict) else None)
    font_size = _font_size(style if isinstance(style, dict) else None, bbox)
    font_family = (style.get("fontFamily") if isinstance(style, dict) else "") or ""
    italic = bool(style.get("italic")) if isinstance(style, dict) else False
    weight = style.get("fontWeight") if isinstance(style, dict) else None
    bold = bool(isinstance(weight, (int, float)) and 500 <= weight < 1000)
    runs = d.get("runs") or []
    lines = runs if runs else (text.split("\n") if text else [])
    paragraphs = d.get("paragraphs") or []
    # gntext exposes rich-text runs as paragraph entries for some shape labels
    # (a code line can become one entry per word).  Merge those token runs into
    # flow text so the renderer does not put every token on its own line.
    if len(paragraphs) > 1 and kind not in ("shapeText", "stickyNote"):
        # (shape text / sticky notes keep their runs; the renderer lays them out)
        texts = [str(p.get("text") or "") for p in paragraphs if isinstance(p, dict)]
        short = sum(1 for t in texts if len(t.strip()) <= 12)
        if short >= max(3, int(0.6 * len(texts))):
            inline = next((p.get("inline") for p in paragraphs
                           if isinstance(p, dict) and p.get("inline")), {}) or {}
            block = next((p.get("block") for p in paragraphs
                          if isinstance(p, dict) and p.get("block")), {}) or {}
            joined = " ".join(t.strip() for t in texts if t.strip())
            paragraphs = [{"text": joined, "inline": inline, "block": block}]
            lines = joined.split("\n")
    # Preserve leading blank paragraphs for nexus documents; they are real layout.
    if not paragraphs and lines:
        paragraphs = [{"text": ln, "inline": {}, "block": {}} for ln in lines]
    return TextBox(
        id=d.get("id") or "",
        x=bbox["x"], y=bbox["y"], width=bbox["w"], height=bbox["h"],
        lines=lines, color=color, font_size=font_size,
        rotation=((d.get("transform") or {}).get("rotation") or 0.0),
        scale=((d.get("transform") or {}).get("scale") or 1.0),
        record=int(d.get("draw_index") or 0),
        z=_z(d.get("z")),
        kind=kind, paragraphs=paragraphs, font_family=font_family,
        italic=italic, bold=bold,
        alignment=1 if any((p.get("block") or {}).get("textAlignment") == 1
                           for p in paragraphs if isinstance(p, dict)) else 0,
        fill_color=_rgba(style.get("color") if kind == "stickyNote" and isinstance(style, dict) else None,
                         (0, 0, 0, 0)) if kind == "stickyNote" else (0, 0, 0, 0),
        full_page=(kind == "nexusText"), runs=runs,
        padding=_padding(d.get("padding")),
    )

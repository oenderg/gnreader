"""Extract typed text and text-bearing items from Goodnotes page records.

Input forms accepted by extract_text():
  * bytes: a backup `notes/<uuid>` file (varint-length-delimited stream)
  * list[bytes]: already split protobuf records
  * list[(descriptor_bytes, item_bytes)]: output of
    gnstore.get_page_records(page_id)

Output: list of dicts with text, geometry, style and z-order.  Text content is
returned to the caller only; the module never logs it.
"""
import struct

from . import schema_pb2 as S
from .richtext import parse_rich_text, rtf_to_text

SHAPE_TEXT = "shapeText"
TEXTBOX = "textBox"
TEXTBOX_V2 = "textBoxV2"
TEXT_FRAGMENT = "textFragment"
NEXUS_TEXT = "nexusText"


def varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def split_records(data: bytes):
    out = []
    i = 0
    while i < len(data):
        ln, i = varint(data, i)
        if i + ln > len(data):
            break
        out.append(data[i:i + ln])
        i += ln
    return out


def _normalize(records):
    if isinstance(records, (bytes, bytearray)):
        return [(None, r) for r in split_records(bytes(records))]
    out = []
    for r in records:
        if isinstance(r, (bytes, bytearray)):
            out.append((None, bytes(r)))
        elif isinstance(r, tuple) and len(r) == 2:
            out.append((bytes(r[0]) if r[0] else None, bytes(r[1])))
        else:
            raise TypeError("records must be bytes, list[bytes] or list[(descriptor, item)]")
    return out


def _point(p):
    if p is None or not p.ListFields():
        return None
    return {"x": p.x, "y": p.y}


def _size(s):
    if s is None or not s.ListFields():
        return None
    return {"w": s.width, "h": s.height}


def _rect(r):
    if r is None or not r.ListFields():
        return None
    o = _point(r.origin) or {"x": 0.0, "y": 0.0}
    s = _size(r.size) or {"w": 0.0, "h": 0.0}
    return {"x": o["x"], "y": o["y"], "w": s["w"], "h": s["h"]}


def _z(pos):
    if pos is None:
        return None
    els = []
    for e in pos.elements:
        els.append([int(e.session), int(e.batchId), int(e.batchIndex)])
    return els or None


def _transform(t):
    if t is None or not t.ListFields():
        return None
    return {"a": t.a, "b": t.b, "c": t.c, "d": t.d, "tx": t.tx, "ty": t.ty}


def _shape_transform(t):
    if t is None or not t.ListFields():
        return None
    v = t.translation
    return {"translation": {"x": v.x, "y": v.y}, "rotation": t.rotation, "scale": t.scale}


def _color_msg(c):
    if c is None or not c.ListFields():
        return None
    return [c.r, c.g, c.b, c.a]


def _style(inline):
    if not inline:
        return None
    st = {}
    # proto3 scalars have no presence for bool/float; copy non-defaults
    if inline.underline:
        st["underline"] = True
    if inline.strike:
        st["strike"] = True
    if inline.fontItalic:
        st["italic"] = True
    if inline.link:
        st["link"] = inline.link
    if inline.fontFamily:
        st["fontFamily"] = inline.fontFamily
    if inline.fontSize:
        st["fontSize"] = inline.fontSize
    if inline.fontWeight:
        st["fontWeight"] = inline.fontWeight
    if inline.fontWidth:
        st["fontWidth"] = inline.fontWidth
    if inline.HasField("color"):
        st["color"] = _color_msg(inline.color)
    if inline.HasField("highlight"):
        st["highlight"] = _color_msg(inline.highlight)
    return st or None


def _item_common(it, kind, text, paragraphs=None, bbox=None, bbox_source=None,
                 extra_style=None):
    d = {
        "kind": kind,
        "id": getattr(it, "id", None),
        "deleted": bool(getattr(it, "deleted", False)),
        "text": text,
        "paragraphs": paragraphs or [],
        "bbox": bbox,
        "bbox_source": bbox_source,
        "z": None,
        "style": extra_style,
    }
    return d


def _text_box_v1(it):
    chunks = []
    for c in it.content:
        if c.text:
            try:
                chunks.append(c.text.decode("utf-8"))
            except UnicodeDecodeError:
                chunks.append(c.text.decode("utf-8", "replace"))
    text = "".join(chunks)
    d = _item_common(it, TEXTBOX, text,
                     bbox=_rect(it.rect) or _rect(it.textRect),
                     bbox_source="rect")
    d["id"] = it.id
    d["z"] = _z(it.position)
    d["transform"] = _transform(it.transform if it.HasField("transform") else None)
    d["textRect"] = _rect(it.textRect)
    d["glyph_count"] = len(it.glyphRects)
    d["alignment"] = int(it.alignment)
    d["style"] = {"borderColor": _color_msg(it.borderColor if it.HasField("borderColor") else None),
                  "borderWidth": it.borderWidth, "padding": it.padding}
    return d


def _text_box_v2(it):
    raw = bytes(it.rtfData)
    text = rtf_to_text(raw)
    d = _item_common(it, TEXTBOX_V2, text,
                     bbox=_rect(it.rect) or _rect(it.textRect),
                     bbox_source="rect")
    d["id"] = it.id
    d["z"] = _z(it.position)
    d["transform"] = _transform(it.textRectTransform)
    d["textRect"] = _rect(it.textRect)
    d["glyph_count"] = len(it.glyphRects)
    d["style"] = {"preset": it.preset, "padding": it.padding,
                  "cornerRadius": it.cornerRadius}
    return d


def _text_fragment(it):
    raw = bytes(it.rtfData)
    text = rtf_to_text(raw)
    d = _item_common(it, TEXT_FRAGMENT, text,
                     bbox=None, bbox_source=None)
    d["id"] = it.id
    d["z"] = _z(it.position)
    d["size"] = _size(it.textSize)
    d["style"] = {"textPresetId": it.textPresetId.value if it.HasField("textPresetId") else None,
                  "indentationLevel": it.indentationLevel.value if it.HasField("indentationLevel") else None}
    return d


def _shape_text(it):
    """SerializedShapeNotesItem with RichTextRaw text."""
    if not it.HasField("text"):
        return None
    rt = parse_rich_text(it.text.SerializeToString())
    if not rt["text"].strip() and not rt["paragraphs"]:
        return None
    tr = _shape_transform(it.transform if it.HasField("transform") else None)
    bbox = None
    if rt.get("size"):
        bbox = {"x": tr["translation"]["x"] if tr else 0.0,
                "y": tr["translation"]["y"] if tr else 0.0,
                "w": rt["size"]["width"], "h": rt["size"]["height"]}
    # first non-empty paragraph's inline attrs as the item style
    style = None
    for p in rt["paragraphs"]:
        if p.get("inline"):
            style = p["inline"]
            break
    if style and rt["paragraphs"] and rt["paragraphs"][0].get("inline"):
        style = rt["paragraphs"][0]["inline"]
    d = _item_common(it, SHAPE_TEXT, rt["text"], paragraphs=rt["paragraphs"],
                     bbox=bbox, bbox_source="shape.transform+text.size" if bbox else None,
                     extra_style=style)
    d["id"] = it.id
    d["z"] = _z(it.position) if it.HasField("position") else None
    d["transform"] = tr
    d["text_size"] = rt.get("size")
    d["padding"] = rt.get("padding")
    d["shape_kind"] = int(it.kind)
    d["text_element_id"] = it.elementID
    return d


def _sticky_text(it):
    """AnyNotesItem.stickyNote -> SerializedStickyNotesItem.richText.

    Older recovered schemas typed this oneof member as `StickyNote`; the bytes
    written by Goodnotes are the serialized sticky item, so fall back to a
    re-parse when the generated class has no richText field.
    """
    sn = it
    if not hasattr(sn, "richText"):
        sn = S.SerializedStickyNotesItem()
        try:
            sn.ParseFromString(it.SerializeToString())
        except Exception:
            return None
    if not sn.HasField("richText"):
        return None
    rt = parse_rich_text(sn.richText.SerializeToString())
    if not rt["text"].strip() and not rt["paragraphs"]:
        return None
    tr = _shape_transform(sn.transform) if sn.HasField("transform") else None
    bbox = None
    if rt.get("size") and tr:
        bbox = {"x": tr["translation"]["x"], "y": tr["translation"]["y"],
                "w": rt["size"]["width"], "h": rt["size"]["height"]}
    d = {"kind": "stickyNote", "id": sn.id, "deleted": bool(sn.deleted),
         "text": rt["text"], "paragraphs": rt["paragraphs"], "bbox": bbox,
         "bbox_source": "sticky.transform+richText.size" if bbox else None,
         "z": _z(sn.position) if sn.HasField("position") else None,
         "transform": tr,
         "text_size": rt.get("size"),
         "padding": rt.get("padding"),
         "style": {"color": _color_msg(sn.color) if sn.HasField("color") else None},
         "author": sn.authorName or None,
         "author_id": sn.authorId or None,
         "expanded": bool(sn.expanded),
         "show_author": bool(sn.showAuthor)}
    return d


def _nexus_text(it):
    data = bytes(it.yjsData)
    if not data:
        return None
    try:
        from pycrdt import Doc, XmlFragment
    except Exception as e:  # optional dependency
        return {"kind": NEXUS_TEXT, "id": it.id, "text": None,
                "error": "pycrdt not installed: %s" % e,
                "z": _z(getattr(it, "position", None))}
    try:
        doc = Doc()
        doc.apply_update(data)
        frag = doc.get("content", type=XmlFragment)
        parts = []
        _collect_xml(frag, parts)
        text = "".join(parts)
    except Exception as e:
        return {"kind": NEXUS_TEXT, "id": it.id, "text": None,
                "error": "yjs decode failed: %s" % e,
                "z": _z(getattr(it, "position", None))}
    return {"kind": NEXUS_TEXT, "id": it.id, "text": text, "paragraphs": [],
            "bbox": None, "bbox_source": None,
            "z": _z(getattr(it, "position", None)),
            "style": None, "yjs_bytes": len(data)}


def _collect_xml(node, out):
    """Append the text of a Y.XmlFragment / XmlElement tree (pycrdt)."""
    children = getattr(node, "children", None)
    if children is None:
        try:
            out.append(str(node.to_py()) if hasattr(node, "to_py") else str(node))
        except Exception:
            pass
        return
    for ch in list(children):
        tag = getattr(ch, "tagName", None)
        sub = getattr(ch, "children", None)
        if sub is not None:
            _collect_xml(ch, out)
        else:
            try:
                out.append(str(ch.to_py()) if hasattr(ch, "to_py") else str(ch))
            except Exception:
                pass
        if tag in ("paragraph", "heading", "listItem"):
            out.append("\n")


def extract_text(records, include_nexus=True):
    """Return text-bearing items (shape text, text boxes, text fragments, Yjs docs).

    Result order is the input order (draw order for gnstore.get_page_records);
    each item also carries ``draw_index`` and its NotesZIndex in ``z``.
    """
    result = []
    for idx, (_desc, item_bytes) in enumerate(_normalize(records)):
        if not item_bytes:
            continue
        msg = S.AnyNotesItem()
        try:
            msg.ParseFromString(item_bytes)
        except Exception:
            continue
        which = msg.WhichOneof("Item")
        if which is None:
            continue
        try:
            if which == "shape":
                d = _shape_text(msg.shape)
                if d:
                    result.append(d)
            elif which == "text":
                d = _text_box_v1(msg.text); d["draw_index"] = idx; result.append(d)
            elif which == "text2":
                d = _text_box_v2(msg.text2); d["draw_index"] = idx; result.append(d)
            elif which == "textFragment":
                d = _text_fragment(msg.textFragment); d["draw_index"] = idx; result.append(d)
            elif which == "stickyNote":
                d = _sticky_text(msg.stickyNote)
                if d:
                    d["draw_index"] = idx
                    result.append(d)
            elif which == "nexus" and include_nexus:
                d = _nexus_text(msg.nexus)
                if d:
                    d["draw_index"] = idx
                    result.append(d)
        except Exception as e:
            result.append({"kind": which, "error": "%s: %s" % (type(e).__name__, e)})
    for d in result:
        b = d.get("bbox")
        if b:
            d["position"] = {"x": b["x"], "y": b["y"]}
    return result


def item_kinds(records):
    """Census helper: {oneof member: count} for one page stream."""
    counts = {}
    for _desc, item_bytes in _normalize(records):
        msg = S.AnyNotesItem()
        try:
            msg.ParseFromString(item_bytes)
        except Exception:
            continue
        w = msg.WhichOneof("Item")
        if w:
            counts[w] = counts.get(w, 0) + 1
    return counts

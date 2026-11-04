"""Decode a .goodnotes zip into Notebook/Page/Stroke objects."""
from __future__ import annotations

import io
import os
import struct
import zipfile

import pypdfium2 as pdfium

from . import bv4
from .model import (FillShapeItem, ImageItem, LineItem, Notebook, Page, ShapeItem,
                    StickyNoteItem, Stroke, TextBox)
from . import gntext_adapter

PENCIL_SIG = "vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)"
CONSTANT_SIG = "vuA(v)A(S(uu))A(S(uuuu))vA(f)"
VARIABLE_SIG = "vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)"
ERASER_SIG = "vS(vvvvvvvv)S(uuuuuu)"
# Page-4 sticker body: constant width without the trailing lineCap/shape arrays.
CONSTANT_SHORT_SIG = "vuA(v)A(S(uu))A(S(uuuu))"

KNOWN_SIGS = {PENCIL_SIG: "pencil", CONSTANT_SIG: "constant",
              VARIABLE_SIG: "variable", ERASER_SIG: "eraser"}


def read_records(data: bytes):
    """Yield (offset, length, payload) for a varint-length-delimited stream."""
    i = 0
    n = len(data)
    while i < n:
        start = i
        val = 0
        shift = 0
        while True:
            if i >= n:
                raise ValueError("truncated record length at %d" % start)
            b = data[i]
            i += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 70:
                raise ValueError("record length too long at %d" % start)
        if i + val > n:
            raise ValueError("record at %d length %d exceeds file" % (start, val))
        yield start, val, data[i:i + val]
        i += val


def read_fields(payload: bytes):
    """Yield (field_no, wire_type, value) for one protobuf message."""
    i = 0
    n = len(payload)
    while i < n:
        key = 0
        shift = 0
        while True:
            if i >= n:
                raise ValueError("truncated field key")
            b = payload[i]
            i += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        fn, wt = key >> 3, key & 7
        if wt == 0:
            val = 0
            shift = 0
            while True:
                if i >= n:
                    raise ValueError("truncated varint")
                b = payload[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield fn, wt, val
        elif wt == 1:
            if i + 8 > n:
                raise ValueError("truncated fixed64")
            yield fn, wt, payload[i:i + 8]
            i += 8
        elif wt == 2:
            val = 0
            shift = 0
            while True:
                if i >= n:
                    raise ValueError("truncated length")
                b = payload[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if i + val > n:
                raise ValueError("field %d length %d exceeds message" % (fn, val))
            yield fn, wt, payload[i:i + val]
            i += val
        elif wt == 5:
            if i + 4 > n:
                raise ValueError("truncated fixed32")
            yield fn, wt, payload[i:i + 4]
            i += 4
        else:
            raise ValueError("field %d unsupported wire type %d" % (fn, wt))


def _message(raw: bytes) -> dict[int, object]:
    return {fn: v for fn, wt, v in read_fields(raw)}


def _uuid(raw: bytes | None) -> str:
    if not isinstance(raw, bytes):
        return ""
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return ""


def _text(raw: bytes | None) -> str:
    if not isinstance(raw, bytes):
        return ""
    return raw.decode("utf-8", "replace")


def _uuid_prev(value: str) -> str:
    """notes container id = page id + 1 (hex carry, store lane)."""
    try:
        digits = value.replace("-", "")
        n = int(digits, 16) - 1
        h = "%032x" % n
        return ("%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])).upper()
    except (ValueError, TypeError):
        return ""


def _color(raw: bytes | None) -> tuple[int, int, int, int]:
    if not isinstance(raw, bytes):
        return (0, 0, 0, 255)
    vals = {}
    try:
        for fn, wt, v in read_fields(raw):
            if wt == 5 and len(v) == 4:
                vals[fn] = struct.unpack("<f", v)[0]
    except ValueError:
        return (0, 0, 0, 255)
    # Colour message: 1=red (optional, defaults to 0), 2=green, 3=blue, 4=alpha.
    # A black-ish pen has no field 1; a highlighter has all four.
    r, g, b, a = vals.get(1, 0), vals.get(2, 0), vals.get(3, 0), vals.get(4, 1)
    return tuple(max(0, min(255, round(c * 255))) for c in (r, g, b, a))


def _float32(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, bytes) and len(raw) == 4:
        return struct.unpack("<f", raw)[0]
    return default


def _rgba_message(raw: object) -> tuple[int, int, int, int]:
    if not isinstance(raw, bytes):
        return (0, 0, 0, 255)
    m = _message(raw)
    vals = [_float32(m.get(k), 0.0) for k in (1, 2, 3)]
    a = _float32(m.get(4), 1.0)
    return tuple(max(0, min(255, round(v * 255))) for v in (*vals, a))


def extract_rich_text(raw: bytes) -> list[str]:
    """Pull plain text lines out of a RichTextRaw `bv41` payload."""
    try:
        data = bv4.decompress(raw) if raw.startswith(b"bv41") else raw
    except bv4.Bv4Error:
        return []
    lines: list[str] = []

    def walk(msg: bytes, depth: int = 0):
        try:
            fs = list(read_fields(msg))
        except ValueError:
            return
        for fn, wt, v in fs:
            if wt != 2:
                continue
            if depth < 2 and fn in (1, 2):
                # top level: paragraph messages in field 1; their field 1 is text
                try:
                    sub = list(read_fields(v))
                except ValueError:
                    sub = []
                if sub and any(a == 1 and b == 2 for a, b, _ in sub):
                    for a, b, c in sub:
                        if a == 1 and b == 2:
                            try:
                                text = c.decode("utf-8")
                            except UnicodeDecodeError:
                                continue
                            if text:
                                lines.append(text)
                elif depth == 0:
                    walk(v, depth + 1)
    walk(data)
    if not lines:
        # fallback: printable runs from the decompressed protobuf
        import re
        lines = [m.decode("utf-8", "replace") for m in re.findall(rb"[ -~]{4,}", data)]
    return lines


def _decode_shape(inner: dict[int, object], record: int) -> TextBox | None:
    """Decode a SerializedShapeNotesItem; currently only kind=1 text boxes."""
    if inner.get(9) != 1:              # kind 1 = text box in every sample
        return None
    text_raw = inner.get(32)
    if not isinstance(text_raw, bytes):
        return None
    text_msg = _message(text_raw)
    raw_msg = text_msg.get(1)
    if not isinstance(raw_msg, bytes):
        return None
    raw_data = _message(raw_msg).get(2)
    if not isinstance(raw_data, bytes):
        return None
    lines = extract_rich_text(raw_data)
    if not lines:
        return None
    size = _message(text_msg.get(2)) if isinstance(text_msg.get(2), bytes) else {}
    width = _float32(size.get(1), 0.0)
    height = _float32(size.get(2), 0.0)
    transform = _message(inner.get(20)) if isinstance(inner.get(20), bytes) else {}
    vec = _message(transform.get(1)) if isinstance(transform.get(1), bytes) else {}
    x = _float32(vec.get(1), 0.0)
    y = _float32(vec.get(2), 0.0)
    rotation = _float32(transform.get(2), 0.0)
    scale = _float32(transform.get(3), 1.0)
    color = (0, 0, 0, 255)
    font_size = 24.0
    attrs = text_msg.get(5)
    if isinstance(attrs, bytes):
        attrs_m = _message(attrs)
        inline = attrs_m.get(1)
        if isinstance(inline, bytes):
            inline_m = _message(inline)
            if isinstance(inline_m.get(3), bytes):
                color = _rgba_message(inline_m[3])
            font_size = _float32(inline_m.get(40), 24.0)
    return TextBox(_uuid(inner.get(1)), x, y, width, height, lines, color, font_size,
                   rotation, scale, record)


def _decode_eraser(inner: dict[int, object], record: int, report) -> Stroke | None:
    """Best-effort PEraserStroke decode (no eraser items exist in the backups
    scanned, so this path is only exercised by live records)."""
    blob = inner.get(2)
    if not isinstance(blob, bytes) or not blob.startswith(b"bv41"):
        report(record, "eraser without bv41", signature="")
        return None
    try:
        tpl = bv4.decode_tpl(bv4.decompress(blob))
    except (bv4.Bv4Error, ValueError) as exc:
        report(record, "eraser decode: %s" % exc, signature="")
        return None
    if tpl.signature != ERASER_SIG:
        report(record, "unknown eraser signature", signature=tpl.signature)
        return None
    vals = tpl.values
    points = vals[2] if len(vals) > 2 and isinstance(vals[2], list) else []
    # GUESS: six floats are three (x, y) points; frame is eight u16 (bbox).
    pts = [(float(points[i]), float(points[i + 1]), 20.0)
           for i in range(0, len(points) - 1, 2)]
    commands = []
    if pts:
        commands.append({"type": "moveTo", "points": [pts[0]]})
        if len(pts) >= 3:
            commands.append({"type": "quadCurveTo", "points": [pts[1], pts[2]]})
    return Stroke(_uuid(inner.get(1)), "eraser", (0, 0, 0, 255), commands,
                  thickness=20.0, record=record, signature=tpl.signature)


def _zindex(msg: dict[int, object], field: int = 7) -> tuple[int, int, int]:
    """NotesZIndex: repeated element {session, batchId, batchIndex}; higher = on top."""
    raw = msg.get(field)
    if not isinstance(raw, bytes):
        return (0, 0, 0)
    try:
        elements = [v for fn, wt, v in read_fields(raw) if fn == 1 and wt == 2]
    except ValueError:
        return (0, 0, 0)
    best = (0, 0, 0)
    for element in elements:
        try:
            em = {fn: v for fn, wt, v in read_fields(element)}
        except ValueError:
            continue
        z = (int(em.get(1, 0)), int(em.get(2, 0)), int(em.get(3, 0)))
        if z > best:
            best = z
    return best


def _point(raw) -> tuple[float, float]:
    if not isinstance(raw, bytes) or not raw:
        return (0.0, 0.0)
    try:
        m = _message(raw)
    except (ValueError, KeyError, IndexError):
        return (0.0, 0.0)
    return (_float32(m.get(1), 0.0), _float32(m.get(2), 0.0))


def _decode_fill(inner: dict[int, object], record: int) -> FillShapeItem | None:
    raws = [v for fn, wt, v in read_fields(inner.get(4)) if fn == 4 and wt == 2]         if isinstance(inner.get(4), bytes) else []
    if not raws and isinstance(inner.get(4), bytes):
        raws = [inner[4]]
    tx, ty = _point(inner.get(6)) if isinstance(inner.get(6), bytes) else (0.0, 0.0)
    shapes = []
    for raw_shape in raws:
        try:
            sm_fields = list(read_fields(raw_shape))
            sm = {fn: v for fn, wt, v in sm_fields}
        except (ValueError, KeyError, IndexError):
            continue
        has_msg = lambda n: any(fn == n and wt == 2 for fn, wt, _v in sm_fields)
        has_fixed = lambda n: any(fn == n and wt == 5 for fn, wt, _v in sm_fields)
        # Observed live layout: field 4 is an Ellipse{center,radius,rotation}.
        if has_msg(1) and has_msg(2) and has_fixed(3):
            cx, cy = _point(sm[1])
            rm = _message(sm[2]) if sm[2] else {}
            rx = _float32(rm.get(1), 0.0)
            ry = _float32(rm.get(2), 0.0)
            shapes.append({"type": "ellipse", "cx": cx + tx, "cy": cy + ty,
                           "rx": rx, "ry": ry,
                           "rotation": _float32(sm[3], 0.0)})
            continue
        # Rect{origin,size}
        if has_msg(1) and has_msg(2):
            x, y = _point(sm[1])
            sz = _message(sm[2]) if sm[2] else {}
            shapes.append({"type": "rect", "x": x + tx, "y": y + ty,
                           "w": _float32(sz.get(1), 0.0),
                           "h": _float32(sz.get(2), 0.0)})
            continue
        # Wrapper / PenStrokeShape variants: unwrap one level.
        nested = []
        for fn, wt, v in sm_fields:
            if wt == 2:
                try:
                    sub = list(read_fields(v))
                except Exception:
                    continue
                nested.append((fn, sub))
        for fn, sub in nested:
            subm = {a: c for a, b, c in sub}
            if fn == 4 and any(a == 1 for a, b, c in sub):   # ellipse
                cx, cy = _point(subm.get(1))
                rm = _message(subm.get(2)) if isinstance(subm.get(2), bytes) else {}
                shapes.append({"type": "ellipse", "cx": cx + tx, "cy": cy + ty,
                               "rx": _float32(rm.get(1), 0.0),
                               "ry": _float32(rm.get(2), 0.0),
                               "rotation": 0.0})
            elif fn == 3:                                     # rectangle
                x, y = _point(subm.get(1))
                sz = _message(subm.get(2)) if isinstance(subm.get(2), bytes) else {}
                shapes.append({"type": "rect", "x": x + tx, "y": y + ty,
                               "w": _float32(sz.get(1), 0.0),
                               "h": _float32(sz.get(2), 0.0)})
            elif fn == 1 and any(a == 1 for a, b, c in sub):  # polyline
                pts = [_point(c) for a, b, c in sub if a == 1 and b == 2]
                if len(pts) >= 3:
                    shapes.append({"type": "polygon",
                                   "points": [(x + tx, y + ty) for x, y in pts]})
    if not shapes:
        return None
    return FillShapeItem(_uuid(inner.get(1)), shapes, _color(inner.get(7)),
                         record=record)


def _decode_math(inner: dict[int, object], attachments: dict[str, str],
                 record: int) -> ImageItem | None:
    aid = _uuid(inner.get(4))
    rect = _message(inner.get(2)) if isinstance(inner.get(2), bytes) else {}
    origin = _message(rect.get(1)) if isinstance(rect.get(1), bytes) else {}
    size = _message(rect.get(2)) if isinstance(rect.get(2), bytes) else {}
    data_string = inner.get(9)
    if isinstance(data_string, bytes):
        try:
            data_string = data_string.decode("utf-8", "replace")
        except Exception:
            data_string = ""
    return ImageItem(_uuid(inner.get(1)),
                     _float32(origin.get(1), 0.0), _float32(origin.get(2), 0.0),
                     _float32(size.get(1), 0.0), _float32(size.get(2), 0.0),
                     attachments.get(aid), attachment_id=aid, kind="math",
                     data_string=data_string if isinstance(data_string, str) else "",
                     record=record)


def _decode_image(inner: dict[int, object], attachments: dict[str, str],
                  record: int) -> ImageItem | None:
    aid = _uuid(inner.get(4))
    member = attachments.get(aid)
    rect = _message(inner.get(2)) if isinstance(inner.get(2), bytes) else {}
    origin = _message(rect.get(1)) if isinstance(rect.get(1), bytes) else {}
    size = _message(rect.get(2)) if isinstance(rect.get(2), bytes) else {}
    rotation = 0.0
    center_x = center_y = 0.0
    actual_w = actual_h = 0.0
    if isinstance(inner.get(3), bytes):
        actual = _message(inner.get(3))
        center = _message(actual.get(1)) if isinstance(actual.get(1), bytes) else {}
        actual_size = _message(actual.get(2)) if isinstance(actual.get(2), bytes) else {}
        rotation = _float32(actual.get(3), 0.0)
        center_x = _float32(center.get(1), 0.0)
        center_y = _float32(center.get(2), 0.0)
        actual_w = _float32(actual_size.get(1), 0.0)
        actual_h = _float32(actual_size.get(2), 0.0)
    return ImageItem(_uuid(inner.get(1)),
                     _float32(origin.get(1), 0.0), _float32(origin.get(2), 0.0),
                     _float32(size.get(1), 0.0), _float32(size.get(2), 0.0),
                     member, attachment_id=aid, rotation=rotation, record=record,
                     center_x=center_x, center_y=center_y,
                     actual_width=actual_w, actual_height=actual_h)


def _stroke_style(raw) -> tuple[float, bool, tuple[int, int, int, int]]:
    """SerializedShapeStroke: 1 width, 2 pattern (solid/dashed), 3 shading{1 color}."""
    if not isinstance(raw, bytes):
        return (0.0, False, (0, 0, 0, 255))
    try:
        m = _message(raw)
    except ValueError:
        return (0.0, False, (0, 0, 0, 255))
    width = _float32(m.get(1), 0.0)
    dashed = False
    if isinstance(m.get(2), bytes):
        try:
            dashed = 2 in _message(m[2])
        except ValueError:
            pass
    color = (0, 0, 0, 255)
    if isinstance(m.get(3), bytes):
        try:
            shading = _message(m[3])
            color = _color(shading.get(1))
        except ValueError:
            pass
    return (width, dashed, color)


def _line_end(raw) -> dict[str, object]:
    """SerializedLineNotesItem_LineEnd: 1 point | 2 LineConnection{anchor,angle}."""
    if not isinstance(raw, bytes):
        return {"kind": "none"}
    try:
        m = _message(raw)
    except ValueError:
        return {"kind": "none"}
    if isinstance(m.get(1), bytes):
        x, y = _point(m[1])
        return {"kind": "point", "x": x, "y": y}
    if isinstance(m.get(2), bytes):
        try:
            c = _message(m[2])
            anchor = ""
            if isinstance(c.get(1), bytes):
                anchor = _uuid(_message(c[1]).get(1))
            return {"kind": "connection", "anchor": anchor,
                    "angle": _float32(c.get(2), 0.0)}
        except ValueError:
            pass
    return {"kind": "none"}


def _decode_line(inner: dict[int, object], record: int) -> LineItem | None:
    geometry = ""
    start = {"kind": "none"}
    end = {"kind": "none"}
    mids: list[tuple[float, float]] = []
    start_direction = 0
    rotation = None
    if isinstance(inner.get(20), bytes):          # hobby
        geometry = "hobby"
        g = list(read_fields(inner[20]))
    elif isinstance(inner.get(21), bytes):        # elbow
        geometry = "elbow"
        g = list(read_fields(inner[21]))
    else:
        return None
    start = _line_end(next((v for fn, wt, v in g if fn == 1 and wt == 2), None))
    end = _line_end(next((v for fn, wt, v in g if fn == 3 and wt == 2), None))
    mids = [_point(v) for fn, wt, v in g if fn == 2 and wt == 2]
    if geometry == "elbow":
        start_direction = next((int(v) for fn, wt, v in g if fn == 4 and wt == 0), 0)
        rot_raw = next((v for fn, wt, v in g if fn == 5 and wt == 2), None)
        if isinstance(rot_raw, bytes):
            try:
                r = _message(rot_raw)
                ax, ay = _point(r.get(2))
                rotation = (_float32(r.get(1), 0.0), ax, ay)
            except ValueError:
                rotation = None
    width, dashed, color = _stroke_style(inner.get(32))
    return LineItem(
        id=_uuid(inner.get(1)), geometry=geometry, start=start, end=end,
        middle_points=mids, width=width, color=color, dashed=dashed,
        start_arrow=int(inner.get(30, 0)) if isinstance(inner.get(30), int) else 0,
        end_arrow=int(inner.get(31, 0)) if isinstance(inner.get(31), int) else 0,
        start_direction=start_direction, rotation=rotation, record=record,
    )


def _sizing(raw) -> tuple[str, float, float]:
    """SerializeShapeSizingStrategy variant -> (name, width, height)."""
    if not isinstance(raw, bytes):
        return ("", 0.0, 0.0)
    try:
        m = _message(raw)
    except ValueError:
        return ("", 0.0, 0.0)
    for fn, name in ((1, "fixedWidthFixedHeight"), (2, "fixedWidthAutoHeight"),
                     (3, "autoWidthAutoHeight")):
        if isinstance(m.get(fn), bytes):
            try:
                v = _message(m[fn])
            except ValueError:
                continue
            if fn == 1:
                return (name, _float32(v.get(1), 0.0), _float32(v.get(2), 0.0))
            if fn == 2:
                return (name, _float32(v.get(1), 0.0), _float32(v.get(2), 0.0))
            return (name, _float32(v.get(1), 0.0), _float32(v.get(3), 0.0))
    return ("", 0.0, 0.0)


def _decode_shape_item(inner: dict[int, object], record: int) -> ShapeItem | None:
    if not inner.get(1):
        return None
    sizing, width, height = _sizing(inner.get(21))
    rotation = scale = 0.0
    x = y = 0.0
    if isinstance(inner.get(20), bytes):
        try:
            tr = _message(inner[20])
            x, y = _point(tr.get(1))
            rotation = _float32(tr.get(2), 0.0)
            scale = _float32(tr.get(3), 1.0)
        except ValueError:
            pass
    geometry = ""
    if isinstance(inner.get(22), bytes):
        try:
            gm = _message(inner[22])
            geometry = ("roundedRectangle" if 1 in gm else
                        "ellipse" if 2 in gm else "hobbyPath" if 3 in gm else "")
        except ValueError:
            pass
    fill = (0, 0, 0, 0)
    if isinstance(inner.get(30), bytes):
        try:
            shading = _message(_message(inner[30]).get(1))
            fill = _color(shading.get(1))
        except ValueError:
            pass
    stroke_w, dashed, stroke_color = _stroke_style(inner.get(31))
    return ShapeItem(
        id=_uuid(inner.get(1)), kind=int(inner.get(9, 0)) if isinstance(inner.get(9), int) else 0,
        geometry=geometry, x=x, y=y, width=width, height=height,
        fill_color=fill, stroke_width=stroke_w, stroke_color=stroke_color,
        dashed=dashed, rotation=rotation, scale=scale or 1.0, sizing=sizing,
        has_text=isinstance(inner.get(32), bytes), record=record,
    )


def _decode_sticky(inner: dict[int, object], record: int, text: str = "") -> StickyNoteItem | None:
    if not inner.get(1):
        return None
    sizing, width, height = _sizing(inner.get(21))
    x = y = 0.0
    if isinstance(inner.get(20), bytes):
        try:
            tr = _message(inner[20])
            x, y = _point(tr.get(1))
        except ValueError:
            pass
    color = (255, 255, 255, 255)
    if isinstance(inner.get(30), bytes):
        color = _color(inner[30])
    return StickyNoteItem(
        id=_uuid(inner.get(1)), x=x, y=y, width=width, height=height,
        color=color, text=text, author_id=_uuid(inner.get(32)),
        author_name=_text(inner.get(33)),
        expanded=bool(inner.get(40, 0)), show_author=bool(inner.get(41, 0)),
        record=record,
    )


def _decode_stroke(inner: dict[int, object], record: int, report) -> Stroke | None:
    sid = _uuid(inner.get(1))
    blob = inner.get(2)
    if not isinstance(blob, bytes) or not blob.startswith(b"bv41"):
        report(record, "stroke without bv41 blob", signature="")
        return None
    try:
        raw = bv4.decompress(blob)
        tpl = bv4.decode_tpl(raw)
    except (bv4.Bv4Error, ValueError) as exc:
        report(record, "decode failed: %s" % exc, signature="")
        return None
    kind = KNOWN_SIGS.get(tpl.signature)
    color = _color(inner.get(4))
    brush_type = int(inner.get(3, 0)) if isinstance(inner.get(3), int) else 0
    tape = None
    if isinstance(inner.get(20), bytes):        # TapeStyle
        try:
            ts = _message(inner[20])
            if isinstance(ts.get(2), bytes):     # PatternTape
                pt = _message(ts[2])
                ax, ay = _point(pt.get(3))
                tape = {"style": "pattern", "attachment_id": _uuid(pt.get(1)),
                        "rotation": _float32(pt.get(2), 0.0),
                        "rotation_anchor": [ax, ay],
                        "thickness": _float32(pt.get(4), 0.0),
                        "scale": _float32(pt.get(5), 1.0)}
            elif isinstance(ts.get(1), bytes):   # SolidTape
                tape = {"style": "solid"}
        except ValueError:
            tape = None
    tx = ty = 0.0
    trans = inner.get(6)
    if isinstance(trans, bytes) and trans:
        try:
            tm = _message(trans)
            tx = _float32(tm.get(1), 0.0)
            ty = _float32(tm.get(2), 0.0)
        except ValueError:
            pass

    def translated(commands):
        if tx == 0.0 and ty == 0.0:
            return commands
        for c in commands:
            c["points"] = [(p[0] + tx, p[1] + ty) + tuple(p[2:]) for p in c["points"]]
        return commands
    if tpl.signature == CONSTANT_SHORT_SIG:
        # Sticker/highlighter body: constant width, no lineCap/shape arrays.
        v = tpl.values
        thickness, cmdtypes, moves, quads = v[1], v[2], v[3], v[4]
        commands = []
        mi = qi = 0
        for t in cmdtypes:
            if t == 0 and mi < len(moves):
                p = moves[mi]; mi += 1
                commands.append({"type": "moveTo", "points": [(p[0], p[1], thickness)]})
            elif t == 1 and qi < len(quads):
                p = quads[qi]; qi += 1
                commands.append({"type": "quadCurveTo",
                                 "points": [(p[0], p[1], thickness), (p[2], p[3], thickness)]})
            else:
                report(record, "unknown constant-short command %r" % (t,),
                       signature=tpl.signature)
        return Stroke(sid, "constant", color, translated(commands), thickness=thickness,
                      record=record, signature=tpl.signature, line_cap=1,
                      signature_kind="constant_short", brush_type=brush_type, tape=tape)
    if kind == "pencil" and tape:
        # Tape: version-2 pencil data with commands 4/5 (move + quad); expose the
        # centreline, keep commands empty so the current renderer leaves it alone.
        v = tpl.values
        tape_commands = []
        mi = qi = 0
        for t in v[1]:
            if t == 4:
                p = v[2][mi:mi + 4]; mi += 4
                if len(p) == 4:
                    tape_commands.append({"type": "moveTo",
                                          "points": [(p[0], p[1], p[2])],
                                          "aux": [p[3]]})
            elif t == 5:
                p = v[3][qi:qi + 9]; qi += 9
                if len(p) == 9:
                    tape_commands.append({"type": "quadCurveTo",
                                          "points": [(p[0], p[1], p[2]), (p[3], p[4], p[5])],
                                          "aux": list(p[6:9])})
            else:
                report(record, "unknown tape command %r" % (t,), signature=tpl.signature)
        outline = {"counts": v[4], "types": v[5], "move": v[6],
                   "cubic": v[8], "arc": v[9], "clockwise": v[10]}
        return Stroke(sid, "pencil", color, [], record=record, signature=tpl.signature,
                      thickness=(tape or {}).get("thickness"),
                      outline=outline, brush_type=brush_type, tape=tape,
                      tape_commands=tape_commands)
    if kind == "pencil":
        v = tpl.values
        cmdtypes, moves, quads = v[1], v[2], v[3]
        commands = []
        mi = qi = 0
        for t in cmdtypes:
            if t == 0:
                p = moves[mi:mi + 3]; mi += 3
                if len(p) == 3:
                    commands.append({"type": "moveTo", "points": [tuple(p)]})
            elif t == 1:
                p = quads[qi:qi + 6]; qi += 6
                if len(p) == 6:
                    commands.append({"type": "quadCurveTo",
                                     "points": [tuple(p[0:3]), tuple(p[3:6])]})
            else:
                report(record, "unknown pencil command %r" % (t,), signature=tpl.signature)
        outline = {"counts": v[4], "types": v[5], "move": v[6],
                   "cubic": v[8], "arc": v[9], "clockwise": v[10]}
        if tx or ty:
            outline["move"] = [c + (tx if i % 2 == 0 else ty)
                               for i, c in enumerate(outline["move"])]
            outline["cubic"] = [c + (tx if i % 2 == 0 else ty)
                                for i, c in enumerate(outline["cubic"])]
            arc = []
            for i, c in enumerate(outline["arc"]):
                if i % 5 == 0:
                    c += tx
                elif i % 5 == 1:
                    c += ty
                arc.append(c)
            outline["arc"] = arc
        grain = None
        if brush_type == 5 and isinstance(v[3], list) and v[3]:
            grain = {"brush_type": 5, "source": "pencil-11f"}
        return Stroke(sid, "pencil", color, translated(commands), record=record,
                      signature=tpl.signature, outline=outline,
                      brush_type=brush_type, tape=tape, grain=grain)
    if kind == "constant":
        v = tpl.values
        thickness, cmdtypes, moves, quads = v[1], v[2], v[3], v[4]
        commands = []
        mi = qi = 0
        for t in cmdtypes:
            if t == 0 and mi < len(moves):
                p = moves[mi]; mi += 1
                commands.append({"type": "moveTo",
                                 "points": [(p[0], p[1], thickness)]})
            elif t == 1 and qi < len(quads):
                p = quads[qi]; qi += 1
                commands.append({"type": "quadCurveTo",
                                 "points": [(p[0], p[1], thickness), (p[2], p[3], thickness)]})
            else:
                report(record, "unknown/overrun constant command %r" % (t,),
                       signature=tpl.signature)
        line_cap = int(v[5]) if len(v) > 5 and isinstance(v[5], (int, float)) else 0
        return Stroke(sid, "constant", color, translated(commands), thickness=thickness,
                      record=record, signature=tpl.signature, line_cap=line_cap,
                      brush_type=brush_type, tape=tape)
    if kind == "variable":
        v = tpl.values
        thickness, cmdtypes, moves, quads = v[1], v[2], v[3], v[4]
        commands = []
        mi = qi = 0
        for t in cmdtypes:
            if t == 0:
                if mi < len(moves):
                    p = moves[mi]; mi += 1
                    # 5-float point = x, y, angle, angle, unused
                    commands.append({"type": "moveTo",
                                     "points": [(p[0], p[1], thickness)]})
            elif t == 1:
                if qi < len(quads):
                    q = quads[qi]; qi += 1
                    # 11 floats in the wild: 1 unused/padding float, then two
                    # 5-float points (control, end).  Verified on a live page.
                    if len(q) == 11:
                        q = q[1:]
                    if len(q) >= 10:
                        commands.append({"type": "quadCurveTo",
                                         "points": [(q[0], q[1], thickness),
                                                    (q[5], q[6], thickness)]})
            else:
                report(record, "unknown variable command %r" % (t,), signature=tpl.signature)
        grain = None
        if brush_type == 5:
            params = [(round(q[3], 6), round(q[4], 6), round(q[10], 6))
                      for q in quads if isinstance(q, list) and len(q) == 11]
            if params:
                grain = {"brush_type": 5,
                         "param_a": params[0][0], "param_b": params[0][1],
                         "param_c_start": min(p[2] for p in params),
                         "param_c_end": max(p[2] for p in params),
                         "vertices": len(params)}
        return Stroke(sid, "variable", color, translated(commands), thickness=thickness,
                      record=record, signature=tpl.signature, brush_type=brush_type,
                      tape=tape, grain=grain)
    if kind == "eraser":
        return Stroke(sid, "eraser", color, [], record=record, signature=tpl.signature,
                      brush_type=brush_type, tape=tape)
    report(record, "unknown signature", signature=tpl.signature)
    return Stroke(sid, "unknown", color, [], record=record, signature=tpl.signature,
                  brush_type=brush_type, tape=tape)


def _read_events(z: zipfile.ZipFile):
    pages = {}      # page event uuid -> (background uuid, label)
    backgrounds = {}  # background uuid -> (attachment id, canvas w, canvas h, attachment page, template)
    deleted_pages = set()
    document_id = ""
    if "index.events.pb" not in z.namelist():
        return pages, backgrounds, deleted_pages, document_id
    for _off, _ln, rec in read_records(z.read("index.events.pb")):
        try:
            m = _message(rec)
        except ValueError:
            continue
        types = [fn for fn in m if fn != 1]
        if types == [54]:
            inner = _message(m[54])
            page_uuid = _uuid(inner.get(2))
            bg = ""
            label = ""
            f3 = inner.get(3)
            if isinstance(f3, bytes):
                bg = _uuid(_message(f3).get(1))
            f4 = inner.get(4)
            if isinstance(f4, bytes):
                label = _uuid(_message(f4).get(1))
            if page_uuid:
                pages[page_uuid] = (bg, label)
        elif types == [56]:
            inner = _message(m[56])
            page_uuid = _uuid(inner.get(2))
            val = inner.get(3, 0)
            if isinstance(val, bytes):
                try:
                    val = _message(val).get(1, 0)
                except ValueError:
                    val = 0
            if page_uuid and val:
                deleted_pages.add(page_uuid)
        elif types == [30]:
            inner = _message(m[30])
            document_id = _uuid(inner.get(1)) or document_id
        elif types == [2]:
            inner = _message(m[2])
            bg_uuid = _uuid(inner.get(2))
            attachment = _uuid(inner.get(4))
            w = h = 0.0
            f8 = inner.get(8)
            if isinstance(f8, bytes):
                try:
                    f8m = _message(f8)
                    if 1 in f8m and len(f8m[1]) == 4:
                        w = struct.unpack("<f", f8m[1])[0]
                    if 2 in f8m and len(f8m[2]) == 4:
                        h = struct.unpack("<f", f8m[2])[0]
                except ValueError:
                    pass
            page_no = inner.get(5, 1)
            template = _uuid(inner.get(9))
            if bg_uuid:
                backgrounds[bg_uuid] = (attachment, w, h, max(0, int(page_no) - 1), template)
    return pages, backgrounds, deleted_pages, document_id


def _add_text_items(page: Page, data: bytes, report=None) -> tuple[set[str], dict[str, dict]]:
    """Use gntext (schema lane) for shape/textbox/sticky/nexus extraction."""
    ids: set[str] = set()
    by_id: dict[str, dict] = {}
    if not data:
        return ids, by_id
    try:
        items = gntext_adapter.extract(data)
    except Exception as exc:
        page.unknown_items.append({"record": None, "reason":
                                   "gntext unavailable/failed: %s: %s" % (type(exc).__name__, exc)})
        return ids
    for d in items:
        if not isinstance(d, dict) or d.get("deleted"):
            continue
        item_id = d.get("id") or ""
        if item_id:
            ids.add(item_id)
            by_id[item_id] = d
        box = gntext_adapter.to_textbox(d)
        if box is not None:
            page.texts.append(box)
            page.text_items += 1
            if d.get("kind") == "nexusText":
                page.unknown_items.append({
                    "record": int(d.get("draw_index") or 0), "field": 13,
                    "reason": "nexus text rendered with approximate layout",
                })
        elif d.get("text") or d.get("error"):
            if d.get("kind") == "stickyNote":
                # The StickyNoteItem renderer draws its paper, body and author.
                continue
            page.unknown_items.append({
                "record": int(d.get("draw_index") or 0),
                "field": {"shapeText": 21, "nexusText": 13, "stickyNote": 20}.get(d.get("kind"), 0),
                "reason": "text item not rendered: %s" % (d.get("error") or d.get("kind")),
            })
    return ids, by_id


def open_records(data: bytes, width: float = 1024.0, height: float = 1366.0,
                 template: str = "", paper_width: float = 595.28,
                 paper_height: float = 841.89) -> Page:
    """Decode a live page's record bytes (descriptor + item pairs)."""
    page = Page(index=0, uuid="", canvas_width=width, canvas_height=height,
                template=template, paper_width=paper_width, paper_height=paper_height,
                background_kind="template" if template else "blank")

    def report(record: int, reason: str, signature: str = ""):
        page.unknown_items.append({"record": record, "reason": reason,
                                   "signature": signature})

    records = list(read_records(data))
    text_ids, text_data = _add_text_items(page, data)
    deleted_ids = set()
    for _off, _ln, rec in records:
        try:
            fs = list(read_fields(rec))
        except ValueError:
            continue
        if len(fs) == 1:
            continue
        d = {fn: v for fn, wt, v in fs}
        if d.get(3):
            deleted_ids.add(_uuid(d.get(1)))
    for record_no, (_off, _ln, rec) in enumerate(records):
        try:
            fields = list(read_fields(rec))
        except ValueError as exc:
            page.unknown_items.append({"record": record_no, "reason": "protobuf: %s" % exc})
            continue
        if len(fields) != 1 or fields[0][1] != 2:
            continue
        fn, _wt, val = fields[0]
        try:
            item_id = _uuid(_message(val).get(1))
        except ValueError:
            item_id = ""
        if item_id and item_id in deleted_ids:
            page.deleted_items += 1
            continue
        if fn == 7:
            inner = _message(val)
            stroke = _decode_stroke(inner, record_no, report)
            if stroke is not None:
                stroke.z = _zindex(inner)
                page.strokes.append(stroke)
        elif fn == 22:
            try:
                inner = _message(val)
            except ValueError:
                inner = None
            if inner is not None:
                line = _decode_line(inner, record_no)
                if line is not None:
                    line.z = _zindex(inner, field=7)
                    page.lines.append(line)
                else:
                    page.unknown_items.append({"record": record_no, "field": 22,
                                               "reason": "line not decoded"})
        elif fn == 21:
            shm = _message(val)
            sid = _uuid(shm.get(1))
            shape = _decode_shape_item(shm, record_no)
            if shape is not None:
                shape.z = _zindex(shm, field=7)
                page.shapes.append(shape)
            if shape is not None and shape.geometry in ("ellipse", "roundedRectangle"):
                continue
            if sid in text_ids:
                continue
            page.unknown_items.append({"record": record_no, "field": 21,
                                       "reason": "shape geometry not rendered"})
        elif fn == 10:
            inner = _message(val)
            stroke = _decode_eraser(inner, record_no, report)
            if stroke is not None:
                page.strokes.append(stroke)
        elif fn == 9:
            try:
                fill = _decode_fill(_message(val), record_no)
            except ValueError:
                fill = None
            if fill is not None:
                fill.z = _zindex(_message(val), field=3)
                page.fills.append(fill)
            else:
                page.unknown_items.append({"record": record_no, "field": 9,
                                           "reason": "fillShape not decoded"})
        elif fn == 11:
            try:
                item = _decode_math(_message(val), {}, record_no)
            except ValueError:
                item = None
            if item is not None:
                item.z = _zindex(_message(val), field=5)
                page.images.append(item)
            else:
                page.unknown_items.append({"record": record_no, "field": 11,
                                           "reason": "math item not decoded"})
        elif fn == 13:
            if _uuid(_message(val).get(1)) in text_ids:
                continue
            page.unknown_items.append({"record": record_no, "field": 13,
                                       "reason": "nexus/Yjs text not rendered"})
        elif fn == 1:
            try:
                image = _decode_image(_message(val), {}, record_no)
            except ValueError:
                image = None
            if image is not None:
                image.z = _zindex(_message(val), field=5)
                page.images.append(image)
            else:
                page.unknown_items.append({"record": record_no, "field": 1,
                                           "reason": "image not decoded"})
        elif fn == 20:
            try:
                inner = _message(val)
            except ValueError:
                inner = None
            sid = _uuid(inner.get(1)) if inner else ""
            sticky = _decode_sticky(inner, record_no,
                                    text=(text_data.get(sid) or {}).get("text") or "") if inner else None
            if sticky is not None:
                sticky.z = _zindex(inner, field=7)
                page.stickies.append(sticky)
            elif sid not in text_ids:
                page.unknown_items.append({"record": record_no, "field": 20,
                                           "reason": "sticky note not decoded"})
        elif fn in (5, 8, 12):
            try:
                sid = _uuid(_message(val).get(1))
            except ValueError:
                sid = ""
            if sid in text_ids:
                continue
            page.unknown_items.append({"record": record_no, "field": fn,
                                       "reason": "text item not rendered"})
        else:
            page.unknown_items.append({"record": record_no, "field": fn,
                                       "reason": "item type not decoded"})
    return page


def open_notebook(path: str) -> Notebook:
    title = os.path.basename(path)
    for suffix in (".goodnotes",):
        if title.endswith(suffix):
            title = title[:-len(suffix)]
    nb = Notebook(path=os.path.abspath(path), title=title)

    def report(record: int, reason: str, signature: str = ""):
        nb.unknown.append({"page": None, "record": record, "reason": reason,
                           "signature": signature})

    paper_sizes = {}

    def paper_size(member: str | None) -> tuple[float, float]:
        if not member:
            return 595.28, 841.89
        if member not in paper_sizes:
            try:
                doc = pdfium.PdfDocument(io.BytesIO(z.read(member)))
                paper_sizes[member] = doc[0].get_size()
            except Exception:
                paper_sizes[member] = (595.28, 841.89)
        return paper_sizes[member]

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        pages_ev, backgrounds, deleted_pages, document_id = _read_events(z)
        nb.document_id = document_id

        # attachment id -> member path
        attachments = {}
        if "index.attachments.pb" in names:
            for _off, _ln, rec in read_records(z.read("index.attachments.pb")):
                try:
                    m = _message(rec)
                except ValueError:
                    continue
                aid, member = _uuid(m.get(1)), _uuid(m.get(2))
                if aid and member:
                    attachments[aid] = member
        nb.attachments = attachments

        # page order
        order = []
        if "index.notes.pb" in names:
            for _off, _ln, rec in read_records(z.read("index.notes.pb")):
                try:
                    m = _message(rec)
                except ValueError:
                    continue
                u = _uuid(m.get(1))
                if u:
                    order.append(u)
        # fallback: filenames
        if not order:
            order = sorted(n[len("notes/"):] for n in names if n.startswith("notes/"))

        deleted_count = 0
        live_order = []
        for u in order:
            ev = _uuid_prev(u)
            if ev in deleted_pages or u in deleted_pages:
                deleted_count += 1
                continue
            live_order.append(u)
        nb.deleted_pages = deleted_count
        for idx, page_uuid in enumerate(live_order):
            page = Page(index=idx, uuid=page_uuid, document_id=document_id)
            ev_uuid = _uuid_prev(page_uuid)
            if ev_uuid not in pages_ev:
                ev_uuid = next((u for u in pages_ev if u[:-1] == page_uuid[:-1]), None)
            bg_uuid, label = pages_ev.get(ev_uuid, ("", ""))
            page.label = label
            info = backgrounds.get(bg_uuid)
            if info:
                aid, w, h, att_page, template = info
                page.canvas_width, page.canvas_height = w, h
                page.attachment = attachments.get(aid)
                page.attachment_page = att_page
                page.template = template
                if template:
                    page.paper_width, page.paper_height = paper_size(page.attachment)
                if template:
                    page.background_kind = "template"
                elif page.attachment:
                    page.background_kind = "pdf"
                else:
                    page.background_kind = "blank"
            else:
                page.background_kind = "blank"
            member = "notes/" + page_uuid
            if member not in names:
                nb.unknown.append({"page": idx, "record": None,
                                   "reason": "notes member missing", "signature": ""})
                nb.pages.append(page)
                continue
            data = z.read(member)
            if not data:
                nb.pages.append(page)
                continue
            text_ids, text_data = _add_text_items(page, data)
            try:
                records = list(read_records(data))
            except ValueError as exc:
                nb.unknown.append({"page": idx, "record": None,
                                   "reason": "record framing: %s" % exc, "signature": ""})
                nb.pages.append(page)
                continue
            deleted_ids = set()
            for _off, _ln, _rec in records:
                try:
                    _fs = list(read_fields(_rec))
                except ValueError:
                    continue
                if len(_fs) == 1:
                    continue
                _d = {fn: v for fn, wt, v in _fs}
                if _d.get(3):
                    deleted_ids.add(_uuid(_d.get(1)))
            for record_no, (_off, _ln, rec) in enumerate(records):
                try:
                    fields = list(read_fields(rec))
                except ValueError as exc:
                    nb.unknown.append({"page": idx, "record": record_no,
                                       "reason": "protobuf: %s" % exc, "signature": ""})
                    continue
                if len(fields) != 1:
                    continue
                fn, wt, val = fields[0]
                if wt != 2:
                    continue
                item_id = ""
                try:
                    item_id = _uuid(_message(val).get(1))
                except ValueError:
                    pass
                if item_id and item_id in deleted_ids:
                    page.deleted_items += 1
                    continue
                if fn == 7:
                    try:
                        inner = _message(val)
                    except ValueError as exc:
                        nb.unknown.append({"page": idx, "record": record_no,
                                           "reason": "item message: %s" % exc, "signature": ""})
                        continue
                    stroke = _decode_stroke(inner, record_no,
                                            lambda r, why, signature="": nb.unknown.append(
                                                {"page": idx, "record": r, "reason": why,
                                                 "signature": signature}))
                    if stroke is not None:
                        stroke.z = _zindex(inner)
                        page.strokes.append(stroke)
                elif fn == 22:
                    try:
                        inner = _message(val)
                    except ValueError:
                        inner = None
                    if inner is not None:
                        line = _decode_line(inner, record_no)
                        if line is not None:
                            line.z = _zindex(inner, field=7)
                            page.lines.append(line)
                        else:
                            page.unknown_items.append({"record": record_no, "field": 22,
                                                       "reason": "line not decoded"})
                elif fn == 21:
                    try:
                        inner = _message(val)
                    except ValueError:
                        continue
                    sid = _uuid(inner.get(1))
                    shape = _decode_shape_item(inner, record_no)
                    if shape is not None:
                        shape.z = _zindex(inner, field=7)
                        page.shapes.append(shape)
                    if shape is not None and shape.geometry in ("ellipse", "roundedRectangle"):
                        continue
                    if sid in text_ids:
                        continue
                    page.unknown_items.append({"record": record_no, "field": 21,
                                               "reason": "shape geometry not rendered"})
                elif fn == 10:
                    try:
                        inner = _message(val)
                    except ValueError:
                        continue
                    eraser = _decode_eraser(inner, record_no,
                                            lambda r, why, signature="": nb.unknown.append(
                                                {"page": idx, "record": r, "reason": why,
                                                 "signature": signature}))
                    if eraser is not None:
                        eraser.z = _zindex(inner)
                        page.strokes.append(eraser)
                elif fn == 13:
                    try:
                        sid = _uuid(_message(val).get(1))
                    except ValueError:
                        sid = ""
                    if sid in text_ids:
                        continue
                    page.unknown_items.append({"record": record_no, "field": 13,
                                               "reason": "nexus/Yjs text not rendered"})
                elif fn == 1:
                    try:
                        inner = _message(val)
                    except ValueError:
                        continue
                    image = _decode_image(inner, attachments, record_no)
                    if image is not None:
                        image.z = _zindex(inner, field=5)
                        page.images.append(image)
                    else:
                        page.unknown_items.append({"record": record_no, "field": 1,
                                                   "reason": "image/page variant not decoded"})
                elif fn == 20:
                    try:
                        inner = _message(val)
                    except ValueError:
                        inner = None
                    sid = _uuid(inner.get(1)) if inner else ""
                    sticky = _decode_sticky(inner, record_no,
                                            text=(text_data.get(sid) or {}).get("text") or "") if inner else None
                    if sticky is not None:
                        sticky.z = _zindex(inner, field=7)
                        page.stickies.append(sticky)
                    elif sid not in text_ids:
                        page.unknown_items.append({"record": record_no, "field": 20,
                                                   "reason": "sticky note not decoded"})
                elif fn in (5, 8, 12):
                    try:
                        sid = _uuid(_message(val).get(1))
                    except ValueError:
                        sid = ""
                    if sid in text_ids:
                        continue
                    page.unknown_items.append({"record": record_no, "field": fn,
                                               "reason": "text item not rendered"})
                elif fn == 9:
                    try:
                        inner = _message(val)
                    except ValueError:
                        continue
                    fill = _decode_fill(inner, record_no)
                    if fill is not None:
                        fill.z = _zindex(inner, field=3)
                        page.fills.append(fill)
                    else:
                        page.unknown_items.append({"record": record_no, "field": 9,
                                                   "reason": "fill shape not decoded"})
                elif fn == 11:
                    try:
                        inner = _message(val)
                    except ValueError:
                        continue
                    item = _decode_math(inner, attachments, record_no)
                    if item is not None:
                        item.z = _zindex(inner, field=5)
                        page.images.append(item)
                    else:
                        page.unknown_items.append({"record": record_no, "field": 11,
                                                   "reason": "math item not decoded"})
                elif fn == 20:
                    page.unknown_items.append({"record": record_no, "field": 20,
                                               "reason": "sticky note not decoded"})
                else:
                    page.unknown_items.append({"record": record_no, "field": fn,
                                               "reason": "unknown item type"})
            nb.pages.append(page)
    return nb

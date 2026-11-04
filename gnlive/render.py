"""Live page renderer: gnstore page records + live attachment backgrounds + gnreader strokes."""
import io, os, sqlite3

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

import gnstore
from gnreader.decode import _message, _decode_stroke, open_records
from gnreader.render import _apply_highlighter, _draw_stroke, _draw_text

LIVE_FTS = os.path.expanduser(
    "~/Library/Containers/com.goodnotesapp.x/Data/Library/Databases/fts.sqlite")
DEFAULT_MAX_SIDE = 1600


def _strip(desc, item):
    return desc, item


def decode_strokes(records):
    """[(descriptor, item)] -> (strokes in list order, stats)."""
    strokes = []
    stats = {"items": len(records), "pencil": 0, "constant": 0, "variable": 0,
             "text_or_other": 0, "errors": 0}
    for idx, (_desc, item) in enumerate(records):
        try:
            top = _message(item)
        except Exception:
            stats["errors"] += 1
            continue
        inner = None
        if isinstance(top.get(7), bytes):
            try:
                inner = _message(top[7])
            except Exception:
                inner = None
        if inner is not None and not (isinstance(inner.get(2), bytes)
                                      and inner[2].startswith(b"bv41")):
            # newer wrapper: bv41 at <item>.f32.f1.f2
            try:
                f32 = _message(inner.get(32, b""))
                f1 = _message(f32.get(1, b""))
                blob = f1.get(2)
                if isinstance(blob, bytes) and blob.startswith(b"bv41"):
                    inner = {1: inner.get(1, b""), 2: blob, 4: inner.get(4)}
                else:
                    inner = None
            except Exception:
                inner = None
        if inner is None and isinstance(top.get(21), bytes):
            # live shape/text variant: bv41 usually at f21.f32.f1.f2
            try:
                mid = _message(top[21])
                f32 = _message(mid.get(32, b""))
                f1 = _message(f32.get(1, b""))
                blob = f1.get(2)
                if isinstance(blob, bytes) and blob.startswith(b"bv41"):
                    inner = {1: mid.get(1, b""), 2: blob, 4: mid.get(4)}
            except Exception:
                inner = None
        if inner is None:
            stats["text_or_other"] += 1
            continue
        try:
            s = _decode_stroke(inner, idx, lambda *a, **k: None)
        except Exception:
            stats["errors"] += 1
            continue
        if not s:
            stats["text_or_other"] += 1
            continue
        strokes.append(s)
        stats[s.kind] = stats.get(s.kind, 0) + 1
    return strokes, stats


def _background_image(bg, width, height):
    if not bg or not bg.get("attachment_id") or not bg.get("exists"):
        return Image.new("RGB", (width, height), "white")
    try:
        doc = pdfium.PdfDocument(bg["path"])
        idx = min(max(1, int(bg.get("attachment_page") or 1)) - 1, len(doc) - 1)
        page = doc[idx]
        w, h = page.get_size()
        img = page.render(scale=max(width / w, height / h)).to_pil().convert("RGB")
        return img.resize((width, height), Image.LANCZOS)
    except Exception:
        pass
    try:
        img = Image.open(bg["path"]).convert("RGB")
        return img.resize((width, height), Image.LANCZOS)
    except Exception:
        return Image.new("RGB", (width, height), "white")


_ITEM_NAMES = {
    1: "image", 3: "fill ellipse", 4: "fill path", 5: "legacy text",
    6: "legacy variable-width line", 8: "text v2", 9: "fill shape",
    10: "eraser", 11: "math", 12: "text fragment", 13: "nexus/Yjs text",
    20: "sticky note", 21: "shape", 22: "line",
}


def _frame_records(records):
    def varint(n):
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                return bytes(out)
    return b"".join(varint(len(d)) + d + varint(len(i)) + i for d, i in records)


def _page_warnings(page, bg):
    """Archive-decoder warnings -> structured render warnings."""
    counts = {}
    for u in page.unknown_items:
        field = u.get("field")
        if field in _ITEM_NAMES:
            name = _ITEM_NAMES[field]
        else:
            name = u.get("reason") or "unknown item"
        counts[name] = counts.get(name, 0) + 1
    erasers = sum(1 for s in page.strokes if s.kind == "eraser")
    if erasers:
        counts["eraser effect"] = erasers
    if bg and bg.get("attachment_id") and not bg.get("exists"):
        counts["missing background file"] = 1
    return [{"kind": k, "count": v} for k, v in sorted(counts.items())]


def render_page(notebook, page_number, max_side=DEFAULT_MAX_SIDE, fresh=True,
                snapshot=None, supersample=2):
    """Render one live page -> (PNG bytes, info dict).

    `notebook` is an id, exact name, or unique substring. `page_number` is
    1-based over non-deleted live pages.
    """
    if fresh:
        gnstore.snapshot(fresh=True, quiet=True)
    nb = gnstore.get_notebook(notebook, snapshot=snapshot)
    pages = [p for p in nb["pages"] if not p["deleted"]]
    if not 1 <= page_number <= len(pages):
        raise IndexError(f"page {page_number} out of range 1..{len(pages)}")
    page = pages[page_number - 1]
    bg = gnstore.get_page_background(page["page_id"], snapshot=snapshot)
    cw, ch = bg.get("canvas_width"), bg.get("canvas_height")
    if not cw or not ch:
        cw, ch = 1091.34, 1543.46
    scale = max_side / max(cw, ch)
    W, H = max(1, round(cw * scale)), max(1, round(ch * scale))

    base = _background_image(bg, W, H)
    records = gnstore.get_page_records(page["page_id"], snapshot=snapshot)
    # Decode and (z-)order objects with the archive renderer's own decoder so
    # text boxes, variable-width strokes, exact outlines and tombstones behave
    # exactly like gnreader, not like a private copy of it.
    page_obj = open_records(_frame_records(records), width=cw, height=ch)
    warnings = _page_warnings(page_obj, bg)

    layer = Image.new("RGBA", (W * supersample, H * supersample), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")

    def flush_layer():
        """Composite ink drawn so far onto the page background."""
        nonlocal base, layer, draw
        if layer.getbbox() is None:
            return
        if supersample != 1:
            small = layer.resize((W, H), Image.LANCZOS)
        else:
            small = layer
        base = Image.alpha_composite(base.convert("RGBA"), small).convert("RGB")
        layer = Image.new("RGBA", (W * supersample, H * supersample), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")

    drawable = 0
    for obj in page_obj.objects():
        if hasattr(obj, "commands"):           # Stroke
            if obj.kind in ("pencil", "constant", "variable") and obj.commands:
                if obj.kind == "constant" and 0 < obj.color[3] < 255:
                    # Highlighter: multiply over the flattened page (paper,
                    # background and ink below it), then keep drawing above.
                    flush_layer()
                    base = _apply_highlighter(base, obj, scale, supersample)
                else:
                    _draw_stroke(draw, obj, scale, supersample)
                drawable += 1
        elif hasattr(obj, "lines"):            # TextBox
            _draw_text(draw, obj, scale, supersample)
        # ImageItem has no live attachment resolver in open_records yet; its
        # absence is already reported in page_obj.unknown_items.
    flush_layer()
    out = base
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    kinds = [s.kind for s in page_obj.strokes]
    info = {
        "notebook": nb["name"], "notebook_id": nb["id"],
        "page_number": page_number, "page_id": page["page_id"],
        "page_index": page["page_index"], "modified": page["modified"],
        "canvas": [round(cw, 2), round(ch, 2)], "size": [W, H],
        "background": {"attachment_id": bg.get("attachment_id"),
                       "attachment_page": bg.get("attachment_page"),
                       "exists": bg.get("exists")},
        "items": len(records), "strokes_total": len(page_obj.strokes),
        "pencil": kinds.count("pencil"), "constant": kinds.count("constant"),
        "variable": kinds.count("variable"), "eraser": kinds.count("eraser"),
        "texts": len(page_obj.texts), "text_or_other": len(page_obj.texts),
        "drawable": drawable, "errors": 0,
        "warnings": warnings,
        "warning_count": sum(w["count"] for w in warnings),
    }
    return buf.getvalue(), info


def _fts_rows(page_id):
    if not os.path.exists(LIVE_FTS):
        return []
    con = sqlite3.connect(f"file:{LIVE_FTS}?mode=ro", uri=True)
    try:
        out = []
        for table, text_col in (("annotation_text_content", "text"),
                                ("textbox_text_content", "text")):
            try:
                rows = con.execute(
                    f"select {text_col} from {table} where page_id=?", (page_id,)).fetchall()
            except sqlite3.Error:
                continue
            out.extend((table, r[0]) for r in rows if r[0])
        return out
    finally:
        con.close()


def page_text_hint(page_id, snapshot=None):
    """OCR / typed text for a page, explicitly labelled unreliable."""
    rows = _fts_rows(page_id)
    ocr = [t for src, t in rows if src == "annotation_text_content"]
    typed = [t for src, t in rows if src == "textbox_text_content"]
    return {
        "page_id": page_id,
        "text": "\n".join(t for _s, t in rows) if rows else "",
        "ocr": "\n".join(ocr),
        "typed": "\n".join(typed),
        "source": sorted({s for s, _t in rows}),
        "reliable": False,
        "note": "OCR from Goodnotes' MyScript index; may be wrong, incomplete, or stale.",
    }

"""Highlighter and container regression tests (no private data required).

Private-page tests locate frozen pages through ``GNREADER_TEST_CORPUS`` and
skip when the corpus is absent. The synthetic tests always run.
"""
import io
import os
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageDraw, ImageFilter

from gnreader import bv4
from gnreader.decode import open_records, read_records
from gnreader.model import Stroke
from gnreader.render import _apply_highlighter, _draw_stroke, _paths

HERE = Path(__file__).resolve().parent
CORPUS = Path(os.environ.get("GNREADER_TEST_CORPUS", HERE / "corpus"))
V1 = CORPUS / "frozen-page-v1.bin"
V2 = CORPUS / "frozen-page-v2.bin"
IPAD = CORPUS / "ipad-reference.png"
W, H = 1091, 1543


def _need(path: Path):
    if not path.is_file():
        pytest.skip(f"private corpus file absent: {path.name}")


def _page(path: Path):
    _need(path)
    return open_records(path.read_bytes(), width=W, height=H)


def _highlighters(page):
    return [s for s in page.strokes
            if s.kind == "constant" and 0 < s.color[3] < 255]


def _bar_mask(stroke):
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    for path in _paths(stroke):
        if len(path) >= 2:
            md.line([(p[0], p[1]) for p in path], fill=255,
                    width=max(1, round(stroke.thickness or 12)), joint="curve")
    return np.asarray(mask) > 0


def _erode(mask, radius=2):
    im = Image.fromarray((mask * 255).astype("uint8"))
    im = im.filter(ImageFilter.MinFilter(2 * radius + 1))
    return np.asarray(im) > 0


def _composite(page, objects=None, include_highlighters=True):
    """Render like the live path: transparent layer flushed at each highlighter."""
    if objects is None:
        objects = list(page.objects())
    base = Image.new("RGB", (W, H), (255, 255, 255))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")

    def flush():
        nonlocal base, layer, draw
        if layer.getbbox() is None:
            return
        base = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")

    for obj in objects:
        if not hasattr(obj, "commands"):
            continue
        if obj.kind not in ("pencil", "constant", "variable") or not obj.commands:
            continue
        if obj.kind == "constant" and 0 < obj.color[3] < 255:
            if not include_highlighters:
                continue
            flush()
            base = _apply_highlighter(base, obj, 1.0, 1)
        else:
            _draw_stroke(draw, obj, 1.0, 1)
    flush()
    return np.asarray(base).astype(np.int16)


def _factor(stroke):
    a = stroke.color[3] / 255.0
    return np.array([(1.0 - a) + a * (c / 255.0) for c in stroke.color[:3]])


def _line_stroke(color, y, x0, x1, width):
    return Stroke("synthetic", "constant", color, [
        {"type": "moveTo", "points": [(x0, y, width)]},
        {"type": "quadCurveTo",
         "points": [((x0 + x1) / 2.0, y, width), (x1, y, width)]},
    ], thickness=width)


def test_highlighter_above_ink_preserves_formula_and_paper():
    page = _page(V1)
    hl = _highlighters(page)[0]
    mask = _bar_mask(hl)
    inner = _erode(mask, 2)
    base = _composite(page, include_highlighters=False)
    final = _composite(page)
    ink = inner & (base.mean(2) < 100)
    paper = inner & (base.min(2) > 245)
    outside_ink = (~mask) & (base.mean(2) < 100)
    assert ink.sum() > 100
    assert paper.sum() > 100
    assert outside_ink.sum() > 100
    assert abs(final[ink].mean(axis=1).min()
               - base[outside_ink].mean(axis=1).min()) < 12
    assert final[ink].mean() < 60
    got = final[paper].mean(axis=0) / 255.0
    assert np.allclose(got, _factor(hl), atol=0.02)


def test_highlighter_below_ink_keeps_ink_and_bar():
    page = _page(V1)
    hl = _highlighters(page)[0]
    mask = _bar_mask(hl)
    inner = _erode(mask, 2)
    objects = [hl] + [o for o in page.objects() if o is not hl]
    base = _composite(page, include_highlighters=False)
    final = _composite(page, objects=objects)
    ink = inner & (base.mean(2) < 100)
    paper = inner & (base.min(2) > 245)
    assert ink.sum() > 100 and paper.sum() > 100
    assert final[ink].mean() < 60
    got = final[paper].mean(axis=0) / 255.0
    assert np.allclose(got, _factor(hl), atol=0.02)


def test_synthetic_purple_pen_under_green_highlighter_multiplies():
    under = (111, 68, 163, 255)
    base = Image.new("RGB", (120, 80), (255, 255, 255))
    draw = ImageDraw.Draw(base)
    _draw_stroke(draw, _line_stroke(under, 40, 10, 110, 24), 1.0, 1)

    hl = _line_stroke((92, 255, 42, 128), 40, 20, 100, 24)
    out = np.asarray(_apply_highlighter(base, hl, 1.0, 1)).astype(float)
    under_px = np.asarray(base)[40, 60].astype(float)
    got = out[40, 60]
    expected = under_px * _factor(hl)
    assert np.abs(got - expected).max() <= 2
    assert got[1] >= under_px[1] - 1
    assert got[0] < under_px[0] and got[2] < under_px[2]


def test_alpha_factor_matches_ipad_p3_targets():
    # Display P3 screenshot values converted to sRGB.
    for rgba, target in (((255, 255, 54, 128), (1.000, 1.000, 0.601)),
                         ((92, 255, 42, 128), (0.673, 1.000, 0.575))):
        stroke = Stroke("s", "constant", rgba, [], thickness=24.0)
        assert np.abs(_factor(stroke) - np.array(target)).max() <= 6 / 255.0


def test_ipad_p3_screenshot_factors_match_rgba():
    _need(IPAD)
    im = Image.open(IPAD)
    src = ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"]))
    dst = ImageCms.createProfile("sRGB")

    def conv(rgb):
        tile = Image.new("RGB", (1, 1), rgb)
        out = ImageCms.profileToProfile(tile, src, dst, outputMode="RGB")
        return np.asarray(out.getpixel((0, 0)), dtype=float)

    paper = conv((248, 247, 234))
    for bar, rgba in ((conv((248, 247, 154)), (255, 255, 54, 128)),
                      (conv((185, 245, 147)), (92, 255, 42, 128))):
        stroke = Stroke("s", "constant", rgba, [], thickness=24.0)
        got = bar / paper
        assert np.abs(got - _factor(stroke)).max() <= 6 / 255.0, (rgba, got)


def test_frozen_v2_colour_relationships():
    page = _page(V2)
    hls = _highlighters(page)
    assert len(hls) == 5
    base = _composite(page, include_highlighters=False)
    final = _composite(page)
    for hl in hls:
        mask = _bar_mask(hl)
        inner = _erode(mask, 2)
        f = _factor(hl)
        paper = inner & (base.min(2) > 250)
        assert paper.sum() > 50
        got = final[paper].mean(axis=0) / 255.0
        assert np.allclose(got, f, atol=6 / 255.0), (hl.color, got, f)
        ink = inner & (base.mean(2) < 120)
        assert ink.sum() > 20
        expected = base[ink].astype(float) * f
        assert np.abs(final[ink] - expected).max() <= 6, hl.color
        black = inner & (base.max(2) <= 2)
        if black.sum() > 10:
            assert final[black].max() <= 4


def test_bv4_lz4_and_stored_roundtrip():
    import lz4.block
    payload = (b"tpl\x00" + struct.pack("<I", 8) + b"vA(u)\x00"
               + struct.pack("<H", 2) + struct.pack("<I", 1) + struct.pack("<f", 1.5))
    comp = lz4.block.compress(payload, store_size=False)
    frame = b"bv41" + struct.pack("<II", len(payload), len(comp)) + comp + b"bv4$"
    assert bv4.decompress(frame) == payload
    stored = b"bv4-" + struct.pack("<I", len(payload)) + payload + b"bv4$"
    assert bv4.decompress(stored) == payload


def test_varint_record_stream():
    records = [b"first", b"", b"third-record"]
    raw = b"".join(bytes([len(r)]) + r for r in records)
    got = [(off, ln, payload) for off, ln, payload in read_records(raw)]
    assert [p for _o, _l, p in got] == records
    assert got[0][1] == 5 and got[2][1] == len(records[2])


def test_signature_grammar_roundtrip():
    fields = bv4.parse_signature("vA(v)A(u)S(uu)")
    assert [f.describe() for f in fields] == ["v", "A(v)", "A(u)", "S(uu)"]
    payload = (struct.pack("<H", 2)
               + struct.pack("<I", 1) + struct.pack("<H", 55)
               + struct.pack("<I", 1) + struct.pack("<f", 1.5)
               + struct.pack("<f", 2.5) + struct.pack("<f", 3.5))
    vals, pos = bv4.read_values(fields, payload)
    assert pos == len(payload)
    assert vals == [2, [55], [1.5], [2.5, 3.5]]

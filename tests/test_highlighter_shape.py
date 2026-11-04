"""Regression: highlighter bar shape (round caps, no even-odd holes).

Frozen live page fixture has 24 translucent constant-width strokes.  Goodnotes stores the cap in ``ConstantWidthStroke`` payload
field 6; every highlighter on this page has value 1
(``PenStrokeLineStyle_LineCap_round``).  Short scribbled highlights cross
themselves, so Pillow's even-odd polyline fill leaves thin weak slivers unless
the coverage mask also stamps a disc at every flattened point.
"""
import os

import pytest

from gnreader.decode import open_records
from gnreader.render import _highlighter_mask, _paths

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.environ.get("GNREADER_TEST_CORPUS", os.path.join(HERE, "tests", "corpus"))
V3 = os.path.join(CORPUS, "highlighter_shape_v3", "page-01.bin")
W, H = 1091, 1543


def _page():
    if not os.path.isfile(V3):
        pytest.skip("frozen v3 highlighter page absent")
    return open_records(open(V3, "rb").read(), width=W, height=H)


def _highlighter(page, record):
    for stroke in page.strokes:
        if stroke.record == record and stroke.kind == "constant":
            return stroke
    pytest.fail("no constant stroke at record %d" % record)


def _points(stroke):
    return [p for path in _paths(stroke) for p in path]


def _mask(stroke):
    return _highlighter_mask(stroke, (W, H), 1.0, 2)


def test_constant_stroke_line_cap_field_is_decoded():
    page = _page()
    hls = [s for s in page.strokes
           if s.kind == "constant" and 0 < s.color[3] < 255]
    assert len(hls) == 24
    assert {s.line_cap for s in hls} == {1}


def test_round_caps_on_straight_bar():
    page = _page()
    stroke = _highlighter(page, 639)          # horizontal 84 x 24 pt bar
    assert stroke.line_cap == 1
    pts = _points(stroke)
    x0, y0 = pts[0][0], pts[0][1]
    x1 = pts[-1][0]
    r = (stroke.thickness or 24.0) / 2.0
    mask = _mask(stroke)

    def px(x, y):
        return mask.getpixel((round(x), round(y)))

    # Full semicircular ends: the stored endpoint is the cap centre, so the
    # mask must extend half a thickness past it, and the end corners stay paper.
    assert px(x0, y0) > 200
    assert px(x1, y0) > 200
    assert px(x0 - r + 2, y0) > 200
    assert px(x1 + r - 2, y0) > 200
    assert px(x0 - 0.8 * r, y0 + 0.8 * r) < 128
    assert px(x1 + 0.8 * r, y0 + 0.8 * r) < 128
    assert px(x0 - r - 3, y0) < 128


@pytest.mark.parametrize("record", [637, 657, 465])
def test_self_crossing_highlighter_has_no_white_slivers(record):
    page = _page()
    stroke = _highlighter(page, record)
    pts = _points(stroke)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    # Central body of a short horizontal mark, away from the round edges.
    box = (int(min(xs) + 6), int(min(ys) - 6),
           int(max(xs) - 6), int(max(ys) + 6))
    crop = _mask(stroke).crop(box)
    assert crop.size[0] > 2 and crop.size[1] > 2
    assert crop.getextrema()[0] == 255, (
        "coverage mask has a weak/white sliver inside record %d" % record)

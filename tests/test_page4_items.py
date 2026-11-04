"""Decode regression for the frozen page-4 item groups.

The page contains the five groups the renderer did not draw on 2026-11-04:
sticker colour body, sticky note, ellipse + 3 arrows, tape, grainy thick pencil.
These assertions cover the *decoded* model, not rendering.
"""
import os

import pytest

from gnreader.decode import open_records

HERE = os.path.dirname(__file__)
CORPUS = os.environ.get("GNREADER_TEST_CORPUS", os.path.join(HERE, "corpus"))
PAGE = os.path.join(CORPUS, "page4_items_p4", "page-04.bin")
W, H = 1091.3466796875, 1543.4649658203125

pytestmark = pytest.mark.skipif(
    not os.path.isfile(PAGE),
    reason="frozen page-4 corpus absent (private fixture not shipped)",
)


def pair(item):
    # page-04.bin alternates descriptor/item records -> item record = 2*pair+1
    return (item.record - 1) // 2


def page():
    return open_records(open(PAGE, "rb").read(), width=W, height=H)


def test_page4_lines():
    p = page()
    assert [pair(x) for x in p.lines] == [1, 2, 3]
    a, b, c = p.lines
    assert [x.geometry for x in p.lines] == ["elbow", "elbow", "hobby"]
    assert all(x.width == 3.0 and x.color == (30, 27, 27, 255) for x in p.lines)
    assert all(x.start_arrow == 0 and x.end_arrow == 1 for x in p.lines)
    assert all(x.end["kind"] == "connection" for x in p.lines[:2])
    anchor = p.shapes[0].id
    assert a.end["anchor"] == anchor and abs(a.end["angle"] + 0.544656) < 1e-4
    assert b.end["anchor"] == anchor and abs(b.end["angle"] - 4.712389) < 1e-4
    assert c.start["kind"] == "connection" and c.start["anchor"] == anchor
    assert c.start["angle"] == 0.0 and c.end["kind"] == "point"
    assert abs(a.start["x"] - 757.7075) < 1e-3
    assert abs(b.start["y"] - 295.6862) < 1e-3
    assert len(b.middle_points) == 1
    assert len(c.middle_points) == 4
    assert [x.z[0] for x in p.lines] == [5, 4, 3]


def test_page4_ellipse():
    p = page()
    assert len(p.shapes) == 1
    s = p.shapes[0]
    assert pair(s) == 22 and len(s.id) == 36 and s.id.count("-") == 4
    assert s.kind == 0 and s.geometry == "ellipse"
    assert (round(s.x, 2), round(s.y, 2)) == (571.97, 52.33)
    assert (round(s.width, 2), round(s.height, 2)) == (141.06, 125.05)
    assert s.sizing == "fixedWidthAutoHeight"
    assert s.fill_color == (30, 27, 27, 26)       # 10% dark grey
    assert s.stroke_width == 3.0 and s.stroke_color == (30, 27, 27, 255)
    assert s.z[0] == 2


def test_page4_sticky():
    p = page()
    assert len(p.stickies) == 1
    s = p.stickies[0]
    assert pair(s) == 0
    assert (round(s.x, 2), round(s.y, 2)) == (302.98, 86.04)
    assert (round(s.width, 2), round(s.height, 2)) == (219.0, 200.62)
    assert s.color == (250, 231, 120, 255)
    assert len(s.text) == 4
    assert len(s.author_name) == 5
    assert s.expanded and s.show_author
    assert s.z[0] == 1


def test_page4_sticker_body():
    p = page()
    body = [s for s in p.strokes if s.signature_kind == "constant_short"]
    assert [pair(s) for s in body] == [4, 5, 6, 7, 8, 9, 10]
    assert all(s.kind == "constant" for s in body)
    assert all(s.line_cap == 1 for s in body)
    assert all(s.commands for s in body)
    by_pair = {pair(s): s for s in body}
    assert {4: 14.064, 5: 14.064, 6: 14.064}[4] == round(by_pair[4].thickness, 3)
    assert all(round(by_pair[i].thickness, 3) == 14.064 for i in (4, 5, 6))
    assert all(round(by_pair[i].thickness, 3) == 11.433 for i in (7, 8, 10))
    assert round(by_pair[9].thickness, 3) == 2.858
    assert by_pair[4].color == (255, 97, 0, 128)       # orange, alpha 0.5
    assert by_pair[7].color == (255, 231, 133, 128)    # yellow
    assert by_pair[10].color == (205, 255, 78, 128)    # yellow-green
    assert by_pair[9].color == (0, 0, 0, 255)


def test_page4_tape():
    p = page()
    tape = [s for s in p.strokes if s.tape]
    assert len(tape) == 1
    t = tape[0]
    assert pair(t) == 26 and t.kind == "pencil"
    assert t.tape["style"] == "pattern"
    assert len(t.tape["attachment_id"]) == 36 and t.tape["attachment_id"].count("-") == 4
    assert t.tape["thickness"] == 18.0 and t.tape["scale"] == 1.0
    assert t.tape["rotation"] == 0.0
    assert len(t.tape_commands) == 41                   # 1 move + 40 quad
    assert t.commands == []                             # not rendered yet
    pts = [p for c in t.tape_commands for p in c["points"]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    assert (round(min(xs), 1), round(min(ys), 1)) == (231.1, 392.4)
    assert (round(max(xs), 1), round(max(ys), 1)) == (491.0, 438.7)


def test_page4_grain():
    p = page()
    grain = [s for s in p.strokes if s.grain]
    assert [pair(s) for s in grain] == [23, 24, 25]
    assert all(s.brush_type == 5 for s in grain)
    thin = [s for s in grain if pair(s) in (23, 24)]
    thick = next(s for s in grain if pair(s) == 25)
    assert all(round(s.thickness, 3) == 0.520 for s in thin)
    assert round(thick.thickness, 3) == 7.795
    assert round(thick.grain["param_a"], 5) == 0.08533
    assert round(thick.grain["param_b"], 5) == 0.92483
    assert thick.grain["vertices"] == 71


def test_page4_warnings():
    p = page()
    assert p.unknown_items == []
    assert len(p.strokes) == 63
    assert len(p.lines) == 3 and len(p.shapes) == 1 and len(p.stickies) == 1

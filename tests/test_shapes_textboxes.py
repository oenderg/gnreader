"""Triangle/diamond (hobbyPath), pill/circle and square text boxes, text-box
padding and fill, and a run-split hyperlink line.  Needs the private frozen page
``corpus/page3_shapes_p3/`` (skipped when absent)."""
import io
import os

import pytest
from PIL import Image

from gnreader.decode import open_records
from gnreader.render import render_records

HERE = os.path.dirname(__file__)
PAGE = os.path.join(HERE, "..", "corpus", "page3_shapes_p3", "page-03.bin")
W, H = 1091.35, 1543.46


@pytest.fixture(scope="module")
def page():
    if not os.path.isfile(PAGE):
        pytest.skip("frozen shapes page absent")
    return open_records(open(PAGE, "rb").read(), width=W, height=H)


def test_hobby_path_vertices(page):
    paths = [s for s in page.shapes if s.geometry == "hobbyPath"]
    assert len(paths) == 2
    tri, dia = sorted(paths, key=lambda s: len(s.path))
    assert tri.closed and dia.closed
    assert [(round(x, 2), round(y, 2)) for x, y, _r in tri.path] == [(0.5, 0.0), (0.0, 1.0), (1.0, 1.0)]
    assert [(round(x, 2), round(y, 2)) for x, y, _r in dia.path] == [
        (0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)]
    assert all(r == 5.0 for s in paths for _x, _y, r in s.path)


def test_rounded_rectangle_radius(page):
    rr = {round(s.corner_radius): s for s in page.shapes if s.geometry == "roundedRectangle"}
    assert set(rr) == {0, 7, 80}


def test_text_box_is_padded_and_filled(page):
    boxes = [s for s in page.shapes if s.kind == 1]
    lime = next(s for s in boxes if s.fill_color == (173, 255, 79, 255))
    assert (round(lime.width), round(lime.height)) == (108, 49)       # 88x29 + 2*10 padding
    white = next(s for s in boxes if s is not lime)
    assert white.fill_color[3] == 0                                    # alpha absent = transparent
    tb = next(t for t in page.texts if t.id == lime.id)
    assert round(tb.x - lime.x) == 10 and round(tb.y - lime.y) == 10
    assert tb.z > lime.z


def test_page_renders_without_warnings(page, tmp_path):
    out = tmp_path / "p.png"
    result = render_records(open(PAGE, "rb").read(), str(out), width=W, height=H,
                            supersample=1, return_info=True)
    im = Image.open(out).convert("RGB")
    lime = next(s for s in page.shapes if s.fill_color == (173, 255, 79, 255))
    px = im.getpixel((int(lime.x + 4), int(lime.y + lime.height - 4)))
    assert px[1] > 220 and px[2] < 140                                 # lime fill drawn
    assert result.warnings == []


def test_rich_text_runs_are_decoded(page):
    box = max((t for t in page.texts if t.kind == "shapeText"), key=lambda t: len(t.paragraphs))
    runs = {p["text"]: p for p in box.paragraphs}
    assert "font size 24\n" in runs and "fontSize" not in (runs["font size 24\n"]["inline"] or {})
    assert runs["font size small\n"]["inline"]["fontSize"] == 13.0
    assert runs["technical font"]["inline"]["fontFamily"] == "Courier New"
    assert runs["bold "]["inline"]["fontWeight"] == -30                 # bold marker
    assert runs["italic "]["inline"]["italic"] is True
    assert runs["underscore "]["inline"]["underline"] is True
    assert runs["strike"]["inline"]["strike"] is True
    assert runs["bullet\n"]["block"]["listStyle"] == {"type": 0}
    assert runs["numberered\n"]["block"]["listStyle"]["type"] == 1
    assert runs["alight on right"]["block"]["textAlignment"] == 3


def test_rich_text_box_height_is_twelve_lines_at_1_2(page):
    box = max((t for t in page.texts if t.kind == "shapeText"), key=lambda t: len(t.paragraphs))
    assert round(box.height) == 201          # 24 pt line + 11 x 13 pt lines, at 1.2 line height

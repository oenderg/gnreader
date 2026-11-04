"""Regression: draw the five previously-missed page-4 item groups.

Frozen page ``corpus/page4_items_p4/`` contains a sticker colour body (7
constant-short strokes), one sticky note, one ellipse + 3 connector lines, one
pattern tape, and one thick grainy pencil.  The tape test injects a small
synthetic pattern tile so it does not depend on the private live attachment.
"""
import io
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gnreader.decode import open_records
from gnreader.render import render_records

HERE = os.path.dirname(__file__)
CORPUS = os.environ.get("GNREADER_TEST_CORPUS", os.path.join(HERE, "corpus"))
PAGE = os.path.join(CORPUS, "page4_items_p4", "page-04.bin")
W, H = 1091.3466796875, 1543.4649658203125


def _pattern_png() -> bytes:
    im = Image.new("RGBA", (24, 24), (203, 217, 129, 255))
    d = ImageDraw.Draw(im)
    d.line([(4, 4), (20, 20)], fill=(34, 89, 86, 255), width=3)
    d.line([(20, 4), (4, 20)], fill=(34, 89, 86, 255), width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    if not os.path.isfile(PAGE):
        pytest.skip("frozen page-4 corpus absent")
    out = tmp_path_factory.mktemp("p4") / "page.png"
    pattern = _pattern_png()
    result = render_records(
        open(PAGE, "rb").read(), str(out), width=W, height=H, supersample=1,
        attachment_loader=lambda _aid: pattern, return_info=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(int), result


def _dark(a, box, limit=100):
    x0, y0, x1, y1 = box
    return int((a[y0:y1, x0:x1].max(2) < limit).sum())


def test_sticker_colour_body_is_drawn(rendered):
    a, _ = rendered
    body = a[70:181, 98:209]
    orange = ((body[:, :, 0] > 200) & (body[:, :, 1] > 70)
              & (body[:, :, 1] < 190) & (body[:, :, 2] < 120))
    assert orange.sum() > 3000
    # Black seams/dots remain above the body.
    assert _dark(a, (95, 68, 212, 182)) > 200


def test_sticky_paper_body_and_author(rendered):
    a, _ = rendered
    paper = a[86:287, 303:522]
    yellow = ((paper[:, :, 0] > 220) & (paper[:, :, 1] > 200)
              & (paper[:, :, 2] > 60) & (paper[:, :, 2] < 180))
    assert yellow.sum() > 10000
    assert _dark(a, (310, 90, 420, 145), 120) > 50     # body text
    assert _dark(a, (310, 250, 420, 285), 140) > 10    # author line


def test_ellipse_fill_stroke_and_three_arrows(rendered):
    a, _ = rendered
    assert tuple(a[115, 642]) != (255, 255, 255)
    assert sum(a[115, 642]) < 740                       # grey fill
    assert _dark(a, (570, 50, 715, 180)) > 200          # ellipse stroke
    assert _dark(a, (700, 140, 765, 275)) > 100         # elbow 1
    assert _dark(a, (565, 170, 650, 300)) > 100         # elbow 2
    assert _dark(a, (700, 75, 915, 185)) > 100          # hobby loop


def test_tape_pattern_is_tiled(rendered):
    a, info = rendered
    tape = a[380:480, 215:520]
    green = ((tape[:, :, 0] > 160) & (tape[:, :, 0] < 235)
             & (tape[:, :, 1] > 190) & (tape[:, :, 2] > 60)
             & (tape[:, :, 2] < 180))
    dark = (tape.max(2) < 160)
    assert green.sum() > 3000
    assert dark.sum() > 100
    assert info.stats["tape"] == 1


def test_thick_pencil_is_grainy_not_solid(rendered):
    a, _ = rendered
    thick = a[280:375, 826:973]
    m = thick.max(2) < 200
    assert m.sum() > 1000
    assert thick[m].std() > 15.0
    thin = a[330:372, 578:673]
    mt = thin.max(2) < 200
    assert mt.sum() > 50
    assert thin[mt].std() < 10.0


def test_page4_render_counts_and_warnings(rendered):
    _, result = rendered
    assert result.warnings == []
    assert result.stats["objects"] == 68
    assert result.stats["lines"] == 3
    assert result.stats["shapes"] == 1
    assert result.stats["stickies"] == 1
    assert result.stats["tape"] == 1

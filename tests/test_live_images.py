"""Regression: live f1 images are drawn in z-order by gnlive.

Uses frozen page records under work/archive/corpus/live_images_p1/. The image
attachment itself is private and is not shipped; the test skips when it is not
present locally.
"""
import io
import math
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
CORPUS = Path(os.environ.get("GNREADER_TEST_CORPUS", HERE / "corpus"))
FROZEN = CORPUS / "live_images_p1" / "page-01.bin"

import gnlive  # noqa: E402
from gnreader import live_attachments  # noqa: E402
from gnreader.decode import open_records  # noqa: E402
from gnreader.model import FillShapeItem, ImageItem, Page  # noqa: E402
from gnreader.render import _draw_image  # noqa: E402


def _page():
    if not FROZEN.is_file():
        pytest.skip("frozen live-image page records absent")
    return open_records(FROZEN.read_bytes(), width=1091.3466796875,
                        height=1543.4649658203125)


def _image_bytes(page):
    images = [im for im in page.images if im.attachment_id]
    if not images:
        pytest.skip("no image item in frozen page")
    for im in images:
        try:
            return im, live_attachments.load(im.attachment_id)
        except FileNotFoundError:
            continue
    pytest.skip("live image attachment absent (private file not shipped)")


def _fake_live(monkeypatch, page, records):
    nb = {"id": "frozen", "name": "frozen", "page_count": 1,
          "pages": [{"page_id": "frozen-page", "page_index": 1,
                     "deleted": False, "modified": None}]}
    bg = {"canvas_width": page.canvas_width, "canvas_height": page.canvas_height,
          "attachment_id": "", "attachment_page": 1, "exists": False}
    monkeypatch.setattr(gnlive.render.gnstore, "get_notebook",
                        lambda *a, **k: nb)
    monkeypatch.setattr(gnlive.render.gnstore, "get_page_background",
                        lambda *a, **k: bg)
    monkeypatch.setattr(gnlive.render.gnstore, "get_page_records",
                        lambda *a, **k: records)
    monkeypatch.setattr(gnlive.render.gnstore, "snapshot",
                        lambda *a, **k: None)


def _records():
    # Use the frozen descriptor/item pairs, the same shape gnstore returns.
    d = FROZEN.parent / "records"
    desc = sorted(d.glob("*.desc.bin"))
    item = sorted(d.glob("*.item.bin"))
    if not desc or len(desc) != len(item):
        pytest.skip("frozen descriptor/item pairs absent")
    return [(a.read_bytes(), b.read_bytes()) for a, b in zip(desc, item)]


def _render(page, records, monkeypatch):
    _fake_live(monkeypatch, page, records)
    png, info = gnlive.render_page("frozen", 1, max_side=1600,
                                   fresh=False, supersample=1)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")), info


def test_live_image_is_drawn_and_warns_when_missing(monkeypatch):
    page = _page()
    image, data = _image_bytes(page)
    records = _records()

    monkeypatch.setattr(gnlive.render.live_attachments, "load",
                        lambda aid: data)
    final, info = _render(page, records, monkeypatch)
    assert info["images"] >= 1
    assert info["images_drawn"] >= 1
    assert not [w for w in info["warnings"] if "image" in w["kind"]]

    with monkeypatch.context() as m:
        m.setattr(gnlive.render, "_draw_image", lambda *a, **k: None)
        blank, _info0 = _render(page, records, m)

    scale = 1600 / max(page.canvas_width, page.canvas_height)
    x0 = round(image.x * scale)
    y0 = round(image.y * scale)
    x1 = round((image.x + image.width) * scale)
    y1 = round((image.y + image.height) * scale)
    x0, y0 = max(0, x0 + 5), max(0, y0 + 5)
    x1, y1 = min(final.shape[1], x1 - 5), min(final.shape[0], y1 - 5)
    diff = np.abs(final[y0:y1, x0:x1].astype(int)
                  - blank[y0:y1, x0:x1].astype(int)).mean()
    assert diff > 10, f"image region changed by only {diff:.1f}"

    with monkeypatch.context() as m:
        def missing(_aid):
            raise FileNotFoundError(_aid)
        m.setattr(gnlive.render.live_attachments, "load", missing)
        _png, info2 = _render(page, records, m)
    assert info2["images_drawn"] == 0
    assert {"kind": "image missing", "count": 1} in info2["warnings"]


def test_undrawn_fill_shape_is_warned():
    page = Page(index=0, uuid="p", canvas_width=100, canvas_height=100)
    page.fills.append(FillShapeItem("f", [{"type": "rect",
                                           "x": 1, "y": 1, "w": 2, "h": 2}]))
    warnings = gnlive.render._page_warnings(page, {})
    assert {"kind": "fill shape", "count": 1} in warnings



def test_rotated_image_uses_actual_rect_centre(tmp_path):
    data = io.BytesIO()
    Image.new("RGB", (20, 10), (220, 20, 20)).save(data, format="PNG")
    item = ImageItem("rot", 0, 0, 48, 37, None, attachment_id="a",
                     rotation=math.pi / 2, center_x=50, center_y=50,
                     actual_width=20, actual_height=10)
    layer = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    _draw_image(layer, None, item, 1.0, 1, attachment_lambda(data.getvalue()))
    box = layer.getbbox()
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs((x1 - x0) - 10) <= 2, box
    assert abs((y1 - y0) - 20) <= 2, box
    assert abs((x0 + x1) / 2 - 50) <= 2
    assert abs((y0 + y1) / 2 - 50) <= 2


def attachment_lambda(data):
    return lambda _aid: data

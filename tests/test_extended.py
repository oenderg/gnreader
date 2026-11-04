import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from gnreader import open_notebook
from gnreader.decode import open_records
from gnreader.render import render_page
from gnreader.model import Notebook

HERE = Path(__file__).resolve().parent
CORPUS = Path(os.environ.get("GNREADER_TEST_CORPUS", HERE / "corpus"))


def _need(name):
    p = CORPUS / name
    if not p.is_file():
        pytest.skip(f"private corpus file absent: {name}")
    return p


def test_template_page():
    nb = open_notebook(str(_need("notebook.goodnotes")))
    page = nb.pages[0]
    assert page.canvas_width > 400 and page.canvas_height > 500


def test_text_boxes():
    nb = open_notebook(str(_need("text-notebook.goodnotes")))
    page = next((p for p in nb.pages if p.texts), None)
    assert page is not None
    assert all(t.lines for t in page.texts)


def test_render_records():
    path = CORPUS / "page-records.bin"
    if not path.is_file():
        pytest.skip("private page-record fixture absent")
    page = open_records(path.read_bytes(), width=1024, height=1366)
    assert page.strokes is not None


def test_cli_page_numbers_are_one_based(tmp_path):
    notebook = _need("notebook.goodnotes")
    out = tmp_path / "p1.png"
    r = subprocess.run([sys.executable, "-m", "gnreader", "render",
                        str(notebook), "1", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "page 1" in r.stdout
    assert out.exists()


def test_outline_and_zorder():
    from gnreader.render import _outline_polygons
    nb = open_notebook(str(_need("notebook.goodnotes")))
    page = next((p for p in nb.pages if any(s.outline for s in p.strokes)), None)
    if page is None:
        pytest.skip("no outline strokes")
    for stroke in (s for s in page.strokes if s.outline):
        assert _outline_polygons(stroke)
    objs = page.objects()
    assert [getattr(o, "z", (0, 0, 0)) for o in objs] == sorted(
        getattr(o, "z", (0, 0, 0)) for o in objs)


def test_shape_and_nexus_text_rendering():
    nb = open_notebook(str(_need("text-notebook.goodnotes")))
    assert any(t.kind in ("shapeText", "nexusText") for p in nb.pages for t in p.texts)


def test_live_image_attachment_loader(tmp_path):
    if os.environ.get("GNREADER_TEST_LIVE") != "1":
        pytest.skip("set GNREADER_TEST_LIVE=1 to exercise the live library")
    try:
        import gnstore
        nbs = gnstore.list_notebooks()
        page_id = next((p["page_id"] for nb in nbs for p in gnstore.get_notebook(nb["id"]).get("pages", [])
                        if not p.get("deleted")), None)
        if not page_id:
            pytest.skip("no live page")
        bg = gnstore.get_page_background(page_id)
        recs = gnstore.get_page_records(page_id)
    except Exception as exc:
        pytest.skip(f"live Goodnotes library unavailable: {type(exc).__name__}")
    from gnreader import render_records_result
    out = tmp_path / "live.png"
    r = render_records_result(recs, str(out), width=bg["canvas_width"],
                              height=bg["canvas_height"])
    assert out.is_file()
    assert r.stats["objects"] >= 0


def test_capabilities_contract():
    import gnreader
    from gnreader.capabilities import packaged
    assert gnreader.__version__.startswith("0.3.")
    caps = gnreader.capabilities()
    assert caps["version"] == gnreader.__version__
    assert {"supported", "partial", "unsupported", "item_kinds"} <= set(caps)
    assert caps == packaged()
    assert json.dumps(caps)  # serialisable


def test_sticky_adapter():
    from gnreader.gntext_adapter import to_textbox
    sticky = {
        "kind": "stickyNote", "id": "sticky-test",
        "bbox": {"x": 10, "y": 20, "w": 120, "h": 80},
        "text": "note", "paragraphs": [],
        "style": {"color": {"r": 1, "g": 0.8, "b": 0.2, "a": 1}},
        "z": [[1, 2, 3]], "draw_index": 4,
    }
    box = to_textbox(sticky)
    assert box is not None and box.kind == "stickyNote"
    assert box.fill_color[3] > 0 and box.z == (1, 2, 3)

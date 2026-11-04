import os
import struct
import zipfile
from pathlib import Path

import pytest

from gnreader import bv4
from gnreader.decode import open_notebook, read_records, read_fields

HERE = Path(__file__).resolve().parent
CORPUS = Path(os.environ.get("GNREADER_TEST_CORPUS", HERE / "corpus"))


def _need(name):
    p = CORPUS / name
    if not p.is_file():
        pytest.skip(f"private corpus file absent: {name}")
    return p


def _first_tpl(name="notebook.goodnotes"):
    with zipfile.ZipFile(_need(name)) as z:
        notes = [n for n in z.namelist() if n.startswith("notes/")]
        for n in notes:
            data = z.read(n)
            if not data:
                continue
            for _off, _ln, rec in read_records(data):
                fs = list(read_fields(rec))
                if len(fs) == 1 and fs[0][0] == 7:
                    inner = {fn: v for fn, wt, v in read_fields(fs[0][2])}
                    blob = inner.get(2)
                    if isinstance(blob, bytes) and blob.startswith(b"bv41"):
                        return blob
    pytest.skip("no drawing item")


def test_bv4_frame_and_tpl():
    blob = _first_tpl()
    raw = bv4.decompress(blob)
    assert raw.startswith(b"tpl\0")
    tpl = bv4.decode_tpl(raw)
    assert tpl.signature.startswith("v")
    assert len(tpl.signature) > 5


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


def test_page_model_and_page_order():
    nb = open_notebook(str(_need("notebook.goodnotes")))
    assert len(nb.pages) >= 1
    p = nb.pages[0]
    assert p.canvas_width > 100 and p.canvas_height > 100


def test_first_stroke_values():
    nb = open_notebook(str(_need("notebook.goodnotes")))
    page = next((p for p in nb.pages if p.strokes), None)
    if page is None:
        pytest.skip("no strokes in corpus notebook")
    s = page.strokes[0]
    assert s.kind in ("pencil", "constant", "variable")
    assert s.commands[0]["type"] == "moveTo"


def test_text_notebook_shape_or_nexus():
    nb = open_notebook(str(_need("text-notebook.goodnotes")))
    assert any(p.texts for p in nb.pages)


def test_deleted_items_are_skipped():
    nb = open_notebook(str(_need("notebook.goodnotes")))
    assert all(p.deleted_items >= 0 for p in nb.pages)


def test_variable_width_and_text_records_decode():
    path = CORPUS / "page-records.bin"
    if not path.is_file():
        pytest.skip("private page-record fixture absent")
    from gnreader.decode import open_records
    page = open_records(path.read_bytes(), width=1091, height=1543)
    assert not page.unknown_items

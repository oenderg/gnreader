import io
import os
import zipfile
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image

from gnreader import open_notebook
from gnreader.render import render_page

HERE = Path(__file__).resolve().parent
CORPUS = Path(os.environ.get("GNREADER_TEST_CORPUS", HERE / "corpus"))


def test_render_page_matches_pdf_mask(tmp_path):
    notebook = CORPUS / "notebook.goodnotes"
    reference = CORPUS / "reference.pdf"
    if not notebook.is_file() or not reference.is_file():
        pytest.skip("private notebook/reference PDF absent")
    nb = open_notebook(str(notebook))
    page = next((p for p in nb.pages if p.strokes and p.attachment), None)
    if page is None:
        pytest.skip("no renderable page with an attachment")
    out = tmp_path / "page.png"
    render_page(nb, page, str(out))

    pdf = pdfium.PdfDocument(str(reference))
    ref_page = pdf[min(page.index, len(pdf) - 1)]
    with zipfile.ZipFile(notebook) as z:
        att = pdfium.PdfDocument(io.BytesIO(z.read(page.attachment)))
    ours = np.asarray(Image.open(out).convert("L"))
    h, w = ours.shape
    bg = np.asarray(att[0].render(scale=page.canvas_width / att[0].get_size()[0])
                    .to_pil().convert("L").resize((w, h)))
    ref_img = ref_page.render(scale=page.canvas_width / ref_page.get_size()[0]).to_pil() \
        .convert("L").resize((w, h))
    ref = np.asarray(ref_img)
    ref_ink = (ref < 200) & ~(bg < 200)
    our_ink = (ours < 200) & ~(bg < 200)
    inter = (ref_ink & our_ink).sum()
    union = (ref_ink | our_ink).sum()
    assert union > 0
    assert inter / union > 0.50

"""gnlive public API."""
__version__ = "0.1.0"

from .render import render_page, page_text_hint, decode_strokes
import gnstore


def list_notebooks():
    """Live notebooks with name, id, folder path, page count, modified."""
    return gnstore.list_notebooks()


def find(query):
    """Case-insensitive match on notebook name, folder path, or id."""
    q = query.lower()
    out = []
    for nb in gnstore.list_notebooks():
        hay = " ".join(str(nb.get(k) or "") for k in ("name", "folder_path", "id")).lower()
        if q in hay:
            out.append(nb)
    return out


__all__ = ["list_notebooks", "find", "render_page", "page_text_hint", "decode_strokes"]

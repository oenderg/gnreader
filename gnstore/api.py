"""Public Python API for gnstore.

Example:
    import gnstore
    for nb in gnstore.list_notebooks():
        print(nb["name"], nb["page_count"])
    recs = gnstore.get_page_records("PAGE-ID")
    # recs: [(descriptor_bytes, item_bytes), ...] in draw order (bottom -> top)
"""
import os, datetime

from .catalog import Catalog
from .pageio import page_records, page_bytes, item_position, bump
from .store import DEFAULT_SNAPSHOT, LIVE

LIVE_LIBRARY = os.path.dirname(LIVE)  # .../Data/Library

__all__ = [
    "list_notebooks", "get_notebook", "get_page_info", "get_page_records",
    "get_page_bytes", "get_page_background", "get_attachment", "get_attachment_info",
    "page_container", "DEFAULT_SNAPSHOT",
]


def _catalog(snapshot):
    path = os.path.join(snapshot or DEFAULT_SNAPSHOT, "projection.sqlite")
    if not os.path.exists(path):
        path = None
    return Catalog(path)


def _iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).isoformat(sep=" ", timespec="seconds") if ms else None


def _enrich(cat, doc):
    if "folder" not in doc:
        doc = {**doc, "folder": cat.placements.get(doc["id"], (None,))[0]}
    return doc


def _notebook_dict(cat, doc):
    doc = _enrich(cat, doc)
    folder = doc.get("folder")
    return {
        "id": doc["id"],
        "name": doc["name"],
        "folder_id": folder,
        "folder_path": cat.folder_path(folder) if folder else "",
        "page_count": doc["page_count"],
        "created": _iso(doc.get("created")),
        "modified": _iso(doc.get("updated")),
        "deleted": bool(doc.get("deleted")),
        "document_type": doc.get("type"),
    }


def list_notebooks(snapshot=None, include_deleted=False):
    """List notebooks (name, id, folder path, page count, modified)."""
    cat = _catalog(snapshot)
    return [_notebook_dict(cat, d) for d in cat.notebooks(include_deleted=include_deleted)]


def get_notebook(notebook, snapshot=None):
    """Notebook metadata by id, exact name, or unique name substring."""
    cat = _catalog(snapshot)
    doc = _enrich(cat, cat.doc(notebook))
    pages = cat.pages_of(doc["id"], include_deleted=True)
    out = _notebook_dict(cat, {**doc, "page_count": len(pages)})
    out["pages"] = [_page_dict(cat, doc, p, i, pages) for i, p in enumerate(pages)]
    return out


def _is_deleted(doc, page):
    return bool(page.get("deleted")) or bool(doc.get("deleted"))


def _page_dict(cat, doc, page, index, siblings):
    return {
        "page_id": page["id"],
        "container_id": bump(page["id"]),
        "notebook_id": doc["id"],
        "notebook": doc["name"],
        "folder_path": cat.folder_path(doc.get("folder")) if doc.get("folder") else "",
        "page_index": index + 1,
        "position": page["position"],
        "created": _iso(page.get("created")),
        "modified": _iso(page.get("notes_updated")),
        "deleted": _is_deleted(doc, page),
    }


def _find_page(cat, page_id):
    pid = page_id.upper()
    for did, pages in cat.pages.items():
        for i, p in enumerate(pages):
            if p["id"] == pid:
                return _enrich(cat, cat.docs[did]), p, i, pages
    raise KeyError(f"no page {page_id}")


def get_page_info(page_id, snapshot=None):
    """Metadata for one page: notebook name, folder path, page index, modified, deleted."""
    cat = _catalog(snapshot)
    doc, page, i, pages = _find_page(cat, page_id)
    out = _page_dict(cat, doc, page, i, pages)
    recs = page_records(os.path.join(snapshot or DEFAULT_SNAPSHOT, "notes_main.db"),
                        page["id"], include_deleted=_is_deleted(doc, page))
    out["items"] = len(recs)
    return out


def get_page_records(page_id, snapshot=None, include_wal=True):
    """Return [(descriptor_bytes, item_bytes), ...] in draw order (bottom -> top).

    Uses the page's own deleted state: deleted pages read the `.deleted.` records.
    """
    cat = _catalog(snapshot)
    doc, page, _i, _pages = _find_page(cat, page_id)
    db = os.path.join(snapshot or DEFAULT_SNAPSHOT, "notes_main.db")
    recs = page_records(db, page["id"], include_deleted=_is_deleted(doc, page),
                        include_wal=include_wal)
    items = []
    for iu, states in recs.items():
        state = "normal" if "normal" in states else next(iter(states))
        d = states[state]
        if "descriptor" not in d or "item" not in d:
            continue
        pos = item_position(d["item"])
        items.append((pos if pos is not None else (-1, 0, 0), iu, d))
    items.sort(key=lambda x: (x[0], x[1]))
    return [(d["descriptor"], d["item"]) for _pos, _iu, d in items]


def get_page_bytes(page_id, snapshot=None, include_wal=True):
    """The page serialized like backup notes/<uuid>: varint-len + descriptor/item."""
    cat = _catalog(snapshot)
    doc, page, _i, _pages = _find_page(cat, page_id)
    db = os.path.join(snapshot or DEFAULT_SNAPSHOT, "notes_main.db")
    return page_bytes(db, page["id"], include_deleted=_is_deleted(doc, page),
                      include_wal=include_wal)


def _attachment_path(cat, attachment_id):
    aid = attachment_id.upper()
    row = cat.attachments.get(aid)
    if row is None:
        raise KeyError(f"unknown attachment {attachment_id}")
    p = row["path"] or aid
    if os.path.isabs(p):
        return p
    if p.startswith("attachments/") or p.startswith("Documents/"):
        return os.path.join(LIVE_LIBRARY, p)
    return os.path.join(LIVE_LIBRARY, "Attachments", p)


def get_attachment_info(attachment_id, snapshot=None):
    """Metadata for an attachment (no bytes); `path` points at the live file."""
    cat = _catalog(snapshot)
    aid = attachment_id.upper()
    row = cat.attachments.get(aid)
    if row is None:
        raise KeyError(f"unknown attachment {attachment_id}")
    path = _attachment_path(cat, aid)
    st = os.stat(path) if os.path.exists(path) else None
    return {**row, "path": path, "exists": st is not None,
            "size": st.st_size if st else None,
            "mtime": _iso(st.st_mtime * 1000) if st else None}


def get_attachment(attachment_id, snapshot=None, as_bytes=False):
    """Return the live attachment path (default) or its raw bytes.

    Read-only: the live container is never written. Raises FileNotFoundError if
    the attachment row exists but the file is unavailable locally.
    """
    info = get_attachment_info(attachment_id, snapshot=snapshot)
    if not info["exists"]:
        raise FileNotFoundError(info["path"])
    if as_bytes:
        with open(info["path"], "rb") as f:
            return f.read()
    return info["path"]


def get_page_background(page_id, snapshot=None):
    """Background for one page: attachment id, page index inside it, canvas size.

    The projection maps `pages.template_id -> templates.attachment_id /
    page_number / size_width / size_height`; this is the live equivalent of the
    backup's event type 54 (page -> background) + type 2 (background ->
    attachment, page, canvas size).
    """
    cat = _catalog(snapshot)
    doc, page, _i, _pages = _find_page(cat, page_id)
    t = cat.templates.get((page.get("template_id") or "").upper())
    base = {"page_id": page["id"], "notebook_id": doc["id"], "notebook": doc["name"]}
    if not t or not t.get("attachment_id"):
        return {**base, "blank": True, "attachment_id": None, "attachment_page": None,
                "canvas_width": None, "canvas_height": None, "path": None, "exists": False}
    aid = t["attachment_id"]
    path = _attachment_path(cat, aid)
    return {**base, "blank": False, "template_id": t["id"], "attachment_id": aid,
            "attachment_page": t["page_number"], "canvas_width": t["width"],
            "canvas_height": t["height"], "path": path, "exists": os.path.exists(path),
            "size": os.path.getsize(path) if os.path.exists(path) else None}


def page_container(page_id):
    return bump(page_id)

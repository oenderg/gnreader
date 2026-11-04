"""gntext — read typed text and text-bearing items from Goodnotes data.

Public API
----------
extract_text(records, include_nexus=True) -> list[dict]
    records: backup `notes/<uuid>` bytes | list[bytes] | list[(descriptor, item)]
    (the last form is exactly `gnstore.get_page_records(page_id)`).

extract_page(page_bytes) -> {"counts": {...}, "items": [...]}
extract_notebook(path) -> {"notebook", "title", "pages": [...], "metadata": {...}}
extract_metadata(events_bytes) -> {"title", "page_labels", "outlines", ...}

CLI: python -m gntext <file.goodnotes|page-bytes.bin> [--no-nexus]
"""
import os
import zipfile

from .items import extract_text, item_kinds, split_records
from . import schema_pb2 as S

__all__ = ["extract_text", "extract_page", "extract_notebook", "extract_metadata",
           "extract_store_page", "item_kinds", "split_records"]


def extract_page(page_bytes, include_nexus=True):
    """Text items + item-kind census for one page record stream."""
    return {"counts": item_kinds(page_bytes),
            "items": extract_text(page_bytes, include_nexus=include_nexus)}


def extract_store_page(page_id, snapshot=None, include_nexus=True):
    """Text items for one live-store page, via gnstore."""
    import gnstore
    recs = gnstore.get_page_records(page_id, snapshot=snapshot)
    return {"page_id": page_id, "counts": item_kinds(recs),
            "items": extract_text(recs, include_nexus=include_nexus)}


def _index_pages(zf):
    """[(page_id, notes_path)] in page order, from index.notes.pb."""
    out = []
    names = set(zf.namelist())
    if "index.notes.pb" in names:
        for rec in split_records(zf.read("index.notes.pb")):
            m = S.NotesItemGroupDescriptor()
            try:
                m.ParseFromString(rec)
            except Exception:
                continue
            path = m.path or ("notes/" + m.id)
            if path in names:
                out.append((m.id, path))
    if not out:
        out = [(n.rsplit("/", 1)[-1], n) for n in sorted(names) if n.startswith("notes/")]
    return out


def extract_metadata(events_bytes):
    """Document title, page labels and outlines from a backup index.events.pb."""
    title = None
    labels = {}
    outlines = []
    transcripts = []
    if not events_bytes:
        return {"title": None, "page_labels": labels, "outlines": outlines,
                "transcripts": transcripts}
    for rec in split_records(events_bytes):
        m = S.EventDescriptor()
        try:
            m.ParseFromString(rec)
        except Exception:
            continue
        which = m.WhichOneof("Event")
        if which in ("documentCreated", "documentRenamed"):
            ev = getattr(m, which)
            if ev.HasField("name") and ev.name.value:
                title = ev.name.value          # later events win (file is chronological)
        elif which == "pageLabelsDidSet":
            ev = m.pageLabelsDidSet
            if ev.HasField("labels"):
                labels[ev.pageId] = list(ev.labels.value)
        elif which == "pageOutlineCreated":
            ev = m.pageOutlineCreated
            outlines.append({
                "page_id": ev.pageId,
                "outline_id": ev.outlineId,
                "parent_outline_id": ev.parentOutlineId or None,
                "title": ev.title,
                "position": ev.position,
                "rect": _rect(ev.rect),
                "kind": _outline_kind(ev),
            })
        elif which == "transcriptionCreated":
            ev = m.transcriptionCreated
            transcripts.append({"transcription_id": ev.transcriptionId,
                                "document_id": ev.documentId,
                                "audio_id": ev.audioId,
                                "start": ev.audioStart, "end": ev.audioEnd,
                                "chars": len(ev.text.value) if ev.HasField("text") else 0})
    return {"title": title, "page_labels": labels, "outlines": outlines,
            "transcripts": transcripts}


def _rect(r):
    if not r.ListFields():
        return None
    return {"x": r.origin.x, "y": r.origin.y, "w": r.size.width, "h": r.size.height}


def _outline_kind(ev):
    if not ev.HasField("outlineType"):
        return None
    t = ev.outlineType
    if t.HasField("link"):
        return "link"
    if t.HasField("standard"):
        return "standard"
    return None


def extract_notebook(path, include_nexus=True):
    """All text-bearing items of a `.goodnotes` backup, in page order."""
    result = {"notebook": os.path.basename(path), "title": None, "pages": [],
              "metadata": {"title": None, "page_labels": {}, "outlines": [],
                           "transcripts": []}}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "index.events.pb" in names:
            result["metadata"] = extract_metadata(zf.read("index.events.pb"))
        for page_id, notes_path in _index_pages(zf):
            data = zf.read(notes_path)
            page = extract_page(data, include_nexus=include_nexus)
            page["page_id"] = page_id
            page["notes_path"] = notes_path
            result["pages"].append(page)
    base = os.path.basename(path)
    fallback = base[:-len(".goodnotes")] if base.endswith(".goodnotes") else base
    result["title"] = result["metadata"].get("title") or fallback
    return result

"""Page records from notes_main.db (RocksDB) for a SQLite page id.

Verified layout (see FORMAT.md):
  page container uuid = page id with the last byte +1
  keys: <container>.<normal|deleted>.<item-uuid>.<descriptor|item>
  values: the exact protobuf record bytes found inside backup `notes/<uuid>`
"""
import os, collections

from .store import iter_items
from .sst import varint


def bump(uuid_str):
    """Return the page notes-container id: last byte of the UUID +1 (with carry)."""
    b = bytearray.fromhex(uuid_str.replace("-", ""))
    i = 15
    while i >= 0:
        if b[i] != 0xFF:
            b[i] += 1
            break
        b[i] = 0
        i -= 1
    s = b.hex().upper()
    return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"


def putvarint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _skip_fields(buf):
    """Yield (field_number, value) for a protobuf message."""
    p = 0
    while p < len(buf):
        tag, p = varint(buf, p)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, p = varint(buf, p)
        elif wt == 2:
            ln, p = varint(buf, p)
            v = buf[p:p + ln]
            p += ln
        elif wt == 5:
            v = buf[p:p + 4]
            p += 4
        elif wt == 1:
            v = buf[p:p + 8]
            p += 8
        else:
            return
        yield fn, v


def item_position(item):
    """NotesZIndex position of an item: (session, batchID, batchIndex).

    Envelope: top-level field 21 (pen V2 / most items) or 7 (image/text) holds
    the item; field 7 inside it is NotesZIndex {1: NotesZIndexElement{1:session,
    2:batchID, 3:batchIndex}}. Higher session = drawn on top (verified vs PDF).
    """
    try:
        top = dict((fn, v) for fn, v in _skip_fields(item))
        inner = top.get(21) or top.get(7) or item
        inner_fields = dict((fn, v) for fn, v in _skip_fields(inner))
        z = inner_fields.get(7)
        if not z:
            return None
        elem = dict((fn, v) for fn, v in _skip_fields(z)).get(1)
        if not elem:
            return None
        el = dict((fn, v) for fn, v in _skip_fields(elem))
        return (el.get(1, 0), el.get(2, 0), el.get(3, 0))
    except Exception:
        return None


def descriptor_seq(descriptor):
    """The descriptor's f9 logical sequence (used only for deterministic order)."""
    try:
        for fn, v in _skip_fields(descriptor):
            if fn == 9 and isinstance(v, int):
                return v
    except Exception:
        pass
    return -1


def page_records(db_dir, page_id, include_deleted=False, states=None, include_wal=True):
    """Return {item_uuid: {state: {descriptor: bytes, item: bytes}}} for a page."""
    container = bump(page_id).encode()
    out = {}
    for k, seq, typ, v in iter_items(db_dir, include_wal=include_wal, prefix=container + b"."):
        if not k.startswith(container) or len(k) < 40:
            continue
        parts = k.split(b".")
        if len(parts) != 4:
            continue
        _, state, item_uuid, suffix = parts
        if states and state.decode() not in states:
            continue
        if not include_deleted and state == b"deleted":
            continue
        out.setdefault(item_uuid.decode(), {}).setdefault(state.decode(), {})[suffix.decode()] = v
    return out


def count_items(db_dir, page_ids, include_deleted=False, include_wal=True):
    """Count items for pages -> {page_id: n}; one pass for batches, seek for one page."""
    counts = collections.Counter()
    if len(page_ids) > 1:
        wanted = {bump(p).encode(): p for p in page_ids}
        for k, _seq, _typ, _v in iter_items(db_dir, include_wal=include_wal):
            if len(k) < 40:
                continue
            u = k[:36]
            if u not in wanted:
                continue
            if not include_deleted and k[37:37 + 7] == b"deleted":
                continue
            counts[u] += 1
    else:
        for pid in page_ids:
            cont = bump(pid).encode()
            for k, _seq, _typ, _v in iter_items(db_dir, include_wal=include_wal,
                                                prefix=cont + b"."):
                if not k.startswith(cont):
                    continue
                if not include_deleted and k[37:37 + 7] == b"deleted":
                    continue
                counts[cont] += 1
    return {pid: counts.get(bump(pid).encode(), 0) // 2 for pid in page_ids}


def page_bytes(db_dir, page_id, include_deleted=False, include_wal=True):
    """Serialize page records as the backup does: varint-len + message, per record.

    Item order: descriptor f9 descending, then item uuid (deterministic). The
    backup's own item order is not stored in the per-item records (see FORMAT.md).
    """
    recs = page_records(db_dir, page_id, include_deleted=include_deleted, include_wal=include_wal)
    items = []
    for item_uuid, states in recs.items():
        state = "normal" if "normal" in states else next(iter(states))
        d = states[state]
        if "descriptor" not in d or "item" not in d:
            continue
        pos = item_position(d["item"])
        items.append((pos if pos is not None else (-1, 0, 0), descriptor_seq(d["descriptor"]),
                      item_uuid, d))
    # draw order: lower position first, higher (later) on top
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    out = bytearray()
    for _, _, _, d in items:
        for suf in ("descriptor", "item"):
            rec = d[suf]
            out += putvarint(len(rec)) + rec
    return bytes(out), {"page_id": page_id, "container": bump(page_id),
                        "items": len(items), "records": len(out) and 2 * len(items)}

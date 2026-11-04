"""Read-only attachment lookup for live page records.

The store lane owns the catalog; gnreader only needs the bytes for an image
item.  Direct container paths cover every attachment whose file is present
locally, and gnstore.get_attachment is used as a fallback when its catalog is
available.
"""
from __future__ import annotations

import os

LIVE_LIBRARY = os.path.expanduser(
    "~/Library/Containers/com.goodnotesapp.x/Data/Library")


def load(attachment_id: str) -> bytes:
    aid = (attachment_id or "").strip().upper()
    if not aid:
        raise FileNotFoundError("empty attachment id")
    candidates = [
        os.path.join(LIVE_LIBRARY, "Attachments", aid),
        os.path.join(LIVE_LIBRARY, "attachments", aid),
        os.path.join(LIVE_LIBRARY, "Documents", aid),
    ]
    for p in candidates:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    # Catalog fallback when the direct Attachments/ layout differs.
    try:
        import gnstore
        return gnstore.get_attachment(aid, as_bytes=True)
    except Exception as exc:
        raise FileNotFoundError("attachment %s not found: %s" % (attachment_id, exc))
